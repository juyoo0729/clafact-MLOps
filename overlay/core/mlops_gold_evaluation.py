"""Offline Gold evaluation linked to a CLAFACT operational MLOps run.

This module deliberately keeps operational coverage separate from Gold-based
performance.  It only reads already stored manifests, predictions, and Gold;
it never calls KOSIS or an LLM provider and it never invents a join.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from core.evaluation_metrics import classification_metrics, ranking_metrics
from core.r2_model_benchmark_v2 import score_r2_model_benchmark_v2


R1_FALSE_REQUIRED = "R1_FALSE_GOLD_REQUIRED_FOR_PRECISION_F1"
DIFFERENT_CLAIM_COHORT = "NOT_EVALUABLE_DIFFERENT_CLAIM_COHORT"
INCOMPLETE_CLAIM_COHORT = "NOT_EVALUABLE_INCOMPLETE_CLAIM_COHORT_JOIN"
OUTPUT_EXISTS = "EVALUATION_OUTPUT_ALREADY_EXISTS"


class EvaluationOutputExistsError(FileExistsError):
    """Raised when an evaluation would overwrite an existing artifact set."""


def evaluate_r1(
    *,
    gold_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate R1 emission by the stable sentence hash without coercing labels."""
    gold_by_hash: dict[str, Mapping[str, Any]] = {}
    duplicate_gold_hashes: set[str] = set()
    label_counts = Counter()
    for gold in gold_rows:
        label = _normalised_string(gold.get("is_claim_human")).upper()
        label_counts[label or "MISSING"] += 1
        sentence_hash = _valid_sha256(gold.get("sentence_hash"))
        if not sentence_hash:
            continue
        if sentence_hash in gold_by_hash:
            duplicate_gold_hashes.add(sentence_hash)
            continue
        gold_by_hash[sentence_hash] = gold

    candidate_hashes = {
        sentence_hash
        for candidate in candidate_rows
        if (sentence_hash := _valid_sha256(candidate.get("sentence_hash")))
    }
    joined_hashes = set(gold_by_hash).intersection(candidate_hashes)
    true_total = label_counts["TRUE"]
    uncertain_total = label_counts["UNCERTAIN"]
    true_joined = sum(
        1
        for sentence_hash in joined_hashes
        if _normalised_string(gold_by_hash[sentence_hash].get("is_claim_human")).upper() == "TRUE"
    )
    uncertain_joined = sum(
        1
        for sentence_hash in joined_hashes
        if _normalised_string(gold_by_hash[sentence_hash].get("is_claim_human")).upper() == "UNCERTAIN"
    )

    not_evaluable = Counter()
    not_evaluable["R1_GOLD_SENTENCE_HASH_MISSING"] = sum(
        1 for row in gold_rows if not _valid_sha256(row.get("sentence_hash"))
    )
    not_evaluable["R1_PREDICTION_SENTENCE_HASH_MISSING"] = sum(
        1 for row in candidate_rows if not _valid_sha256(row.get("sentence_hash"))
    )
    not_evaluable["R1_DUPLICATE_GOLD_SENTENCE_HASH"] = len(duplicate_gold_hashes)
    not_evaluable["R1_GOLD_HASH_NOT_EMITTED"] = len(set(gold_by_hash).difference(candidate_hashes))
    if label_counts["FALSE"] == 0:
        not_evaluable[R1_FALSE_REQUIRED] += 2

    row_results: list[dict[str, Any]] = []
    for gold in gold_rows:
        sentence_hash = _valid_sha256(gold.get("sentence_hash"))
        if not sentence_hash:
            join_status = "NOT_EVALUABLE"
            reason_code = "R1_GOLD_SENTENCE_HASH_MISSING"
        elif sentence_hash in joined_hashes:
            join_status = "JOINED"
            reason_code = None
        else:
            join_status = "NOT_JOINED"
            reason_code = "R1_GOLD_HASH_NOT_EMITTED"
        row_results.append(
            {
                "stage": "R1",
                "gold_row_id": _record_id(gold, "row_id", "claim_id"),
                "sentence_hash": sentence_hash,
                "gold_label": _normalised_string(gold.get("is_claim_human")).upper() or None,
                "join_status": join_status,
                "reason_code": reason_code,
            }
        )
    for candidate in candidate_rows:
        sentence_hash = _valid_sha256(candidate.get("sentence_hash"))
        if sentence_hash and sentence_hash in gold_by_hash:
            continue
        row_results.append(
            {
                "stage": "R1",
                "prediction_record_id": _record_id(
                    candidate, "candidate_id", "claim_candidate_id", "claim_id", "row_id"
                ),
                "sentence_hash": sentence_hash,
                "join_status": "NOT_EVALUABLE" if not sentence_hash else "NOT_JOINED",
                "reason_code": (
                    "R1_PREDICTION_SENTENCE_HASH_MISSING" if not sentence_hash else "R1_PREDICTION_HASH_NOT_IN_GOLD"
                ),
            }
        )

    if label_counts["FALSE"] == 0:
        not_evaluable_metric = _metric_not_evaluable(R1_FALSE_REQUIRED)
        precision_f1 = {"precision": not_evaluable_metric, "f1": dict(not_evaluable_metric)}
    else:
        precision_f1 = _r1_binary_precision_f1(gold_by_hash, candidate_hashes)
    if not joined_hashes:
        true_metric = _metric_not_evaluable("R1_GOLD_PREDICTION_HASH_JOIN_ZERO")
        uncertain_metric = _metric_not_evaluable("R1_GOLD_PREDICTION_HASH_JOIN_ZERO")
        not_evaluable["R1_GOLD_PREDICTION_HASH_JOIN_ZERO"] += len(gold_rows)
    else:
        true_metric = _rate_metric(true_joined, true_total)
        uncertain_metric = _rate_metric(uncertain_joined, uncertain_total)
    return {
        "stage": "R1",
        "status": "EVALUATED" if joined_hashes else "NOT_EVALUABLE",
        "gold_label_counts": {
            "TRUE": label_counts["TRUE"],
            "FALSE": label_counts["FALSE"],
            "UNCERTAIN": label_counts["UNCERTAIN"],
        },
        "join": {
            "gold_count": len(gold_rows),
            "prediction_count": len(candidate_rows),
            "joined_count": len(joined_hashes),
            "coverage": _safe_divide(len(joined_hashes), len(gold_rows)),
        },
        "metrics": {
            "true_candidate_recall": true_metric,
            "uncertain_candidate_emission_rate": uncertain_metric,
            "precision": precision_f1["precision"],
            "f1": precision_f1["f1"],
        },
        "not_evaluable_reason_counts": _positive_counts(not_evaluable),
        "row_results": row_results,
        "label_policy": "UNCERTAIN remains UNCERTAIN and is never converted to FALSE.",
    }


def evaluate_r2(
    *,
    gold_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
    split: str = "dev",
) -> dict[str, Any]:
    """Score R2 only after an exact, duplicate-free frozen-Gold cohort join."""
    if split == "test":
        return _unevaluable_stage("R2", "R2_TEST_SPLIT_EVALUATION_NOT_AUTHORISED")
    selected_gold = [row for row in gold_rows if row.get("split") == split]
    gold_ids, gold_duplicates = _id_set(selected_gold)
    prediction_ids, prediction_duplicates = _id_set(prediction_rows)
    joined = gold_ids.intersection(prediction_ids)
    missing = gold_ids.difference(prediction_ids)
    unexpected = prediction_ids.difference(gold_ids)
    join = _join_summary(len(selected_gold), len(prediction_rows), len(joined), len(missing), len(unexpected))
    row_results = _claim_join_rows("R2", gold_ids, prediction_ids)
    if gold_duplicates or prediction_duplicates or not selected_gold or missing or unexpected:
        reasons = Counter()
        reasons["R2_GOLD_COHORT_EMPTY"] = int(not selected_gold)
        reasons["R2_DUPLICATE_GOLD_CLAIM_ID"] = len(gold_duplicates)
        reasons["R2_DUPLICATE_PREDICTION_CLAIM_ID"] = len(prediction_duplicates)
        reasons["R2_INCOMPLETE_GOLD_JOIN"] = len(missing) + len(unexpected)
        return {
            "stage": "R2",
            "status": "NOT_EVALUABLE",
            "reason_code": "R2_INCOMPLETE_GOLD_JOIN",
            "frozen_gold_count": len(gold_rows),
            "selected_gold_count": len(selected_gold),
            "join": join,
            "metrics": {},
            "not_evaluable_reason_counts": _positive_counts(reasons),
            "row_results": row_results,
        }

    scored = score_r2_model_benchmark_v2(selected_gold, prediction_rows, split=split)
    total = scored["gold_count"]
    whole_exact_count = scored["whole_claim_exact_count"]
    return {
        "stage": "R2",
        "status": "EVALUATED",
        "frozen_gold_count": len(gold_rows),
        "selected_gold_count": len(selected_gold),
        "join": join,
        "metrics": {
            "slot_12_macro_accuracy": {
                "status": "EVALUATED",
                "value": scored["all_slot_metrics"]["macro_slot_accuracy"],
                "slot_count": len(scored["all_slot_metrics"]["per_slot"]),
            },
            "whole_claim_exact": _rate_metric(whole_exact_count, total),
            "response_rate": {
                "status": "EVALUATED",
                "value": scored["response_rate"],
                "numerator": scored["valid_output_count"],
                "denominator": total,
            },
        },
        "not_evaluable_reason_counts": {},
        "row_results": row_results,
        "split": split,
        "scope_note": "R2 Claim/12-slot dev result only; not KOSIS Evidence or Verdict accuracy.",
    }


def evaluate_r3(
    *,
    gold_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate stored ranked KOSIS candidates; a chosen single table is not a ranking."""
    gold_by_id = {_record_id(row, "claim_id"): row for row in gold_rows if _record_id(row, "claim_id")}
    prediction_by_id = {
        claim_id: row
        for row in prediction_rows
        if (claim_id := _prediction_claim_id(row))
    }
    joined_ids = set(gold_by_id).intersection(prediction_by_id)
    joined_rows: list[dict[str, Any]] = []
    row_results: list[dict[str, Any]] = []
    reasons = Counter()
    for claim_id, gold in gold_by_id.items():
        prediction = prediction_by_id.get(claim_id)
        gold_table = _gold_table_id(gold)
        ranked = _ranked_table_ids(prediction) if prediction else None
        reason_code = None
        if prediction is None:
            reason_code = "R3_PREDICTION_NOT_JOINED"
        elif not gold_table:
            reason_code = "R3_GOLD_TABLE_MISSING"
        elif not ranked:
            reason_code = "R3_RANKED_CANDIDATES_MISSING"
        if reason_code:
            reasons[reason_code] += 1
        else:
            joined_rows.append({"expected_tbl_id": gold_table, "ranked_candidate_tbl_ids": ranked})
        row_results.append(
            {
                "stage": "R3",
                "claim_id": claim_id,
                "join_status": "JOINED" if prediction is not None else "NOT_JOINED",
                "metric_status": "EVALUATED" if reason_code is None else "NOT_EVALUABLE",
                "reason_code": reason_code,
            }
        )
    unexpected_ids = set(prediction_by_id).difference(gold_by_id)
    reasons["R3_PREDICTION_CLAIM_ID_NOT_IN_GOLD"] += len(unexpected_ids)
    for claim_id in sorted(unexpected_ids):
        row_results.append(
            {
                "stage": "R3",
                "claim_id": claim_id,
                "join_status": "NOT_JOINED",
                "metric_status": "NOT_EVALUABLE",
                "reason_code": "R3_PREDICTION_CLAIM_ID_NOT_IN_GOLD",
            }
        )
    join = _join_summary(
        len(gold_by_id),
        len(prediction_by_id),
        len(joined_ids),
        len(set(gold_by_id).difference(prediction_by_id)),
        len(set(prediction_by_id).difference(gold_by_id)),
    )
    if not joined_rows:
        reason = DIFFERENT_CLAIM_COHORT if not joined_ids else "R3_TABLE_OR_RANKING_NOT_AVAILABLE"
        reasons[reason] += max(1, len(gold_by_id))
        return {
            "stage": "R3",
            "status": "NOT_EVALUABLE",
            "reason_code": reason,
            "join": join,
            "metrics": {},
            "not_evaluable_reason_counts": _positive_counts(reasons),
            "row_results": row_results,
        }
    scored = ranking_metrics(joined_rows, ks=(1, 3))
    return {
        "stage": "R3",
        "status": (
            "EVALUATED"
            if len(joined_rows) == len(gold_by_id) and not unexpected_ids
            else "PARTIAL"
        ),
        "join": join,
        "metrics": {
            "hit_at_1": _value_metric(scored["hit_at"]["1"], scored["evaluated_count"]),
            "hit_at_3": _value_metric(scored["hit_at"]["3"], scored["evaluated_count"]),
            "mrr": _value_metric(scored["mrr"], scored["evaluated_count"]),
        },
        "not_evaluable_reason_counts": _positive_counts(reasons),
        "row_results": row_results,
    }


def evaluate_r4(
    *,
    gold_rows: Sequence[Mapping[str, Any]],
    prediction_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate R4 only for an exact Gold/prediction claim-ID cohort."""
    gold_ids, gold_duplicates = _id_set(gold_rows)
    prediction_ids, prediction_duplicates = _id_set(prediction_rows)
    gold_by_id = {_record_id(row, "claim_id"): row for row in gold_rows if _record_id(row, "claim_id")}
    prediction_by_id = {
        claim_id: row for row in prediction_rows if (claim_id := _prediction_claim_id(row))
    }
    joined_ids = gold_ids.intersection(prediction_ids)
    missing = gold_ids.difference(prediction_ids)
    unexpected = prediction_ids.difference(gold_ids)
    join = _join_summary(len(gold_rows), len(prediction_rows), len(joined_ids), len(missing), len(unexpected))
    row_results = _claim_join_rows("R4", gold_ids, prediction_ids)

    if not joined_ids:
        return {
            "stage": "R4",
            "status": "NOT_EVALUABLE",
            "reason_code": DIFFERENT_CLAIM_COHORT,
            "join": join,
            "metrics": {},
            "not_evaluable_reason_counts": {DIFFERENT_CLAIM_COHORT: max(1, len(gold_rows))},
            "row_results": row_results,
        }
    if gold_duplicates or prediction_duplicates or missing or unexpected:
        reasons = Counter()
        reasons["R4_DUPLICATE_GOLD_CLAIM_ID"] = len(gold_duplicates)
        reasons["R4_DUPLICATE_PREDICTION_CLAIM_ID"] = len(prediction_duplicates)
        reasons[INCOMPLETE_CLAIM_COHORT] = len(missing) + len(unexpected)
        return {
            "stage": "R4",
            "status": "NOT_EVALUABLE",
            "reason_code": INCOMPLETE_CLAIM_COHORT,
            "join": join,
            "metrics": {},
            "not_evaluable_reason_counts": _positive_counts(reasons),
            "row_results": row_results,
        }

    metric_rows: list[dict[str, Any]] = []
    table_exact_count = 0
    coordinate_exact_count = 0
    coordinate_evaluated_count = 0
    coordinate_not_evaluable = Counter()
    for claim_id in sorted(gold_ids):
        gold = gold_by_id[claim_id]
        prediction = prediction_by_id[claim_id]
        expected_route = _expected_route(gold)
        predicted_route = _predicted_route(prediction)
        expected_verdict = _expected_verdict(gold)
        predicted_verdict = _normalised_string(prediction.get("verdict")).upper()
        expected_tables = _gold_table_ids(gold)
        predicted_cells = _prediction_evidence_cells(prediction)
        predicted_tables = {
            table_id for cell in predicted_cells if (table_id := _normalised_string(cell.get("tbl_id")))
        }
        table_exact_count += int(expected_tables == predicted_tables)
        gold_coordinates = _gold_coordinates(gold)
        if gold_coordinates is None:
            coordinate_not_evaluable["R4_GOLD_COORDINATE_NOT_MACHINE_COMPARABLE"] += 1
        elif not all(isinstance(cell, Mapping) for cell in predicted_cells):
            coordinate_not_evaluable["R4_PREDICTED_COORDINATE_NOT_MACHINE_COMPARABLE"] += 1
        else:
            coordinate_evaluated_count += 1
            coordinate_exact_count += int(
                _canonical_coordinate_set(gold_coordinates)
                == _canonical_coordinate_set([dict(cell) for cell in predicted_cells])
            )
        metric_rows.append(
            {
                "expected_route": expected_route,
                "predicted_route": predicted_route,
                "expected_verdict": expected_verdict,
                "predicted_verdict": predicted_verdict,
            }
        )

    total = len(metric_rows)
    route_correct = sum(row["expected_route"] == row["predicted_route"] for row in metric_rows)
    predicted_auto = [row for row in metric_rows if row["predicted_route"] == "AUTO"]
    auto_correct = sum(
        row["expected_route"] == "AUTO" and row["expected_verdict"] == row["predicted_verdict"]
        for row in predicted_auto
    )
    expected_hold = [row for row in metric_rows if row["expected_route"] == "HOLD"]
    hold_correct = sum(row["predicted_route"] == "HOLD" for row in expected_hold)
    unsafe_auto_count = sum(
        row["predicted_route"] == "AUTO" and row["expected_route"] == "HOLD" for row in metric_rows
    )
    verdict_labels = sorted(
        {str(row["expected_verdict"]) for row in metric_rows if row["expected_verdict"]}
    )
    verdict = classification_metrics(
        metric_rows,
        gold_key="expected_verdict",
        predicted_key="predicted_verdict",
        labels=verdict_labels,
    )
    coordinate_metric = (
        {
            "status": "EVALUATED",
            "value": _safe_divide(coordinate_exact_count, coordinate_evaluated_count),
            "numerator": coordinate_exact_count,
            "denominator": coordinate_evaluated_count,
            "not_evaluable_count": sum(coordinate_not_evaluable.values()),
            "not_evaluable_reason_counts": _positive_counts(coordinate_not_evaluable),
        }
        if coordinate_evaluated_count
        else _metric_not_evaluable("R4_GOLD_COORDINATE_NOT_MACHINE_COMPARABLE")
    )
    return {
        "stage": "R4",
        "status": "EVALUATED",
        "join": join,
        "metrics": {
            "route_accuracy": _rate_metric(route_correct, total),
            "auto_precision": _rate_metric(auto_correct, len(predicted_auto)),
            "hold_recall": _rate_metric(hold_correct, len(expected_hold)),
            "unsafe_auto": {
                "status": "EVALUATED",
                "count": unsafe_auto_count,
                "value": _safe_divide(unsafe_auto_count, total),
                "numerator": unsafe_auto_count,
                "denominator": total,
            },
            "table_exact": _rate_metric(table_exact_count, total),
            "evidence_coordinate_exact": coordinate_metric,
            "verdict_macro_f1": {
                "status": "EVALUATED",
                "value": verdict["macro_f1"],
                "denominator": verdict["evaluated_count"],
            },
        },
        "not_evaluable_reason_counts": _positive_counts(coordinate_not_evaluable),
        "row_results": row_results,
    }


def build_review_queue_status(
    *,
    gold_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Mark already labelled sentence hashes without changing source Gold or queues."""
    gold_by_hash = {
        sentence_hash: gold
        for gold in gold_rows
        if (sentence_hash := _valid_sha256(gold.get("sentence_hash")))
    }
    rows: list[dict[str, Any]] = []
    for candidate in candidate_rows:
        sentence_hash = _valid_sha256(candidate.get("sentence_hash"))
        gold = gold_by_hash.get(sentence_hash) if sentence_hash else None
        if not sentence_hash:
            action = "REQUIRES_SENTENCE_HASH_BACKFILL"
        elif gold is not None:
            action = "EXCLUDE_GOLD_LABELED"
        else:
            action = "ELIGIBLE_FOR_REVIEW_QUEUE"
        rows.append(
            {
                "stage": _normalised_string(candidate.get("stage")) or "R1",
                "record_id": _record_id(
                    candidate, "candidate_id", "claim_candidate_id", "claim_id", "row_id"
                ),
                "sentence_hash": sentence_hash,
                "gold_labeled": gold is not None,
                "gold_row_id": _record_id(gold, "row_id", "claim_id") if gold else None,
                "review_queue_action": action,
            }
        )
    return rows


def write_evaluation_artifacts(
    *,
    output_root: str | Path,
    evaluation_id: str,
    evaluation_summary: Mapping[str, Any],
    join_results: Sequence[Mapping[str, Any]],
    review_queue_status: Sequence[Mapping[str, Any]],
    input_files: Mapping[str, str | Path],
) -> Path:
    """Write one immutable evaluation directory plus input/output SHA-256 manifest."""
    safe_id = _normalised_string(evaluation_id)
    if not safe_id or safe_id in {".", ".."} or Path(safe_id).name != safe_id:
        raise ValueError("EVALUATION_ID_MUST_BE_A_SAFE_DIRECTORY_NAME")
    output_dir = Path(output_root).resolve() / safe_id
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise EvaluationOutputExistsError(f"{OUTPUT_EXISTS}: {safe_id}") from error

    summary_path = output_dir / "evaluation_summary.json"
    joins_path = output_dir / "join_results.jsonl"
    queue_path = output_dir / "review_queue_status.jsonl"
    human_path = output_dir / "summary.txt"
    summary_path.write_text(_json_text(evaluation_summary), encoding="utf-8")
    joins_path.write_text(_jsonl_text(join_results), encoding="utf-8")
    queue_path.write_text(_jsonl_text(review_queue_status), encoding="utf-8")
    human_path.write_text(_count_only_summary(evaluation_summary), encoding="utf-8")

    inputs = {}
    for logical_name, raw_path in sorted(input_files.items()):
        path = Path(raw_path).resolve()
        inputs[logical_name] = {
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": _file_sha256(path),
        }
    outputs = {
        path.name: {"size_bytes": path.stat().st_size, "sha256": _file_sha256(path)}
        for path in (summary_path, joins_path, queue_path, human_path)
    }
    manifest = {
        "artifact": "clafact_gold_evaluation_sha256_manifest",
        "evaluation_id": safe_id,
        "inputs": inputs,
        "outputs": outputs,
        "path_policy": "Only logical names and file names are recorded; absolute paths are omitted.",
    }
    (output_dir / "sha256_manifest.json").write_text(_json_text(manifest), encoding="utf-8")
    return output_dir


def _r1_binary_precision_f1(
    gold_by_hash: Mapping[str, Mapping[str, Any]], candidate_hashes: set[str]
) -> dict[str, dict[str, Any]]:
    true_positive = sum(
        sentence_hash in candidate_hashes
        for sentence_hash, gold in gold_by_hash.items()
        if _normalised_string(gold.get("is_claim_human")).upper() == "TRUE"
    )
    false_positive = sum(
        sentence_hash in candidate_hashes
        for sentence_hash, gold in gold_by_hash.items()
        if _normalised_string(gold.get("is_claim_human")).upper() == "FALSE"
    )
    false_negative = sum(
        sentence_hash not in candidate_hashes
        for sentence_hash, gold in gold_by_hash.items()
        if _normalised_string(gold.get("is_claim_human")).upper() == "TRUE"
    )
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    f1 = None if precision is None or recall is None else _safe_divide(2 * precision * recall, precision + recall)
    return {
        "precision": {"status": "EVALUATED", "value": precision},
        "f1": {"status": "EVALUATED", "value": f1},
    }


def _unevaluable_stage(stage: str, reason_code: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "NOT_EVALUABLE",
        "reason_code": reason_code,
        "join": _join_summary(0, 0, 0, 0, 0),
        "metrics": {},
        "not_evaluable_reason_counts": {reason_code: 1},
        "row_results": [],
    }


def _metric_not_evaluable(reason_code: str) -> dict[str, Any]:
    return {"status": "NOT_EVALUABLE", "value": None, "reason_code": reason_code}


def _rate_metric(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return _metric_not_evaluable("METRIC_DENOMINATOR_ZERO")
    return {
        "status": "EVALUATED",
        "value": numerator / denominator,
        "numerator": numerator,
        "denominator": denominator,
    }


def _value_metric(value: float | None, denominator: int) -> dict[str, Any]:
    if value is None:
        return _metric_not_evaluable("METRIC_DENOMINATOR_ZERO")
    return {"status": "EVALUATED", "value": value, "denominator": denominator}


def _join_summary(
    gold_count: int,
    prediction_count: int,
    joined_count: int,
    missing_prediction_count: int,
    unexpected_prediction_count: int,
) -> dict[str, Any]:
    return {
        "gold_count": gold_count,
        "prediction_count": prediction_count,
        "joined_count": joined_count,
        "coverage": _safe_divide(joined_count, gold_count),
        "missing_prediction_count": missing_prediction_count,
        "unexpected_prediction_count": unexpected_prediction_count,
    }


def _claim_join_rows(stage: str, gold_ids: set[str], prediction_ids: set[str]) -> list[dict[str, Any]]:
    rows = []
    for claim_id in sorted(gold_ids.union(prediction_ids)):
        in_gold = claim_id in gold_ids
        in_prediction = claim_id in prediction_ids
        if in_gold and in_prediction:
            status = "JOINED"
            reason = None
        elif in_gold:
            status = "NOT_JOINED"
            reason = f"{stage}_PREDICTION_CLAIM_ID_MISSING"
        else:
            status = "NOT_JOINED"
            reason = f"{stage}_PREDICTION_CLAIM_ID_NOT_IN_GOLD"
        rows.append({"stage": stage, "claim_id": claim_id, "join_status": status, "reason_code": reason})
    return rows


def _id_set(rows: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        claim_id = _prediction_claim_id(row)
        if not claim_id:
            continue
        if claim_id in seen:
            duplicates.add(claim_id)
        seen.add(claim_id)
    return seen, duplicates


def _prediction_claim_id(row: Mapping[str, Any]) -> str:
    direct = _normalised_string(row.get("claim_id"))
    if direct:
        return direct
    claim = row.get("claim")
    return _normalised_string(claim.get("claim_id")) if isinstance(claim, Mapping) else ""


def _expected_route(row: Mapping[str, Any]) -> str:
    return _normalised_string(row.get("expected_route") or row.get("gold_route")).upper()


def _predicted_route(row: Mapping[str, Any]) -> str:
    return _normalised_string(row.get("route_status") or row.get("route")).upper()


def _expected_verdict(row: Mapping[str, Any]) -> str:
    return _normalised_string(row.get("expected_verdict") or row.get("gold_verdict")).upper()


def _gold_table_id(row: Mapping[str, Any]) -> str:
    values = _gold_table_ids(row)
    return sorted(values)[0] if len(values) == 1 else ""


def _gold_table_ids(row: Mapping[str, Any]) -> set[str]:
    raw = row.get("gold_table_ids")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return {_normalised_string(value) for value in raw if _normalised_string(value)}
    direct = _normalised_string(row.get("gold_table_id") or row.get("expected_tbl_id"))
    if direct:
        return {direct}
    evidence = row.get("expected_evidence")
    if isinstance(evidence, Mapping):
        table = _normalised_string(evidence.get("tbl_id"))
        return {table} if table else set()
    return set()


def _ranked_table_ids(row: Mapping[str, Any] | None) -> list[str] | None:
    if not isinstance(row, Mapping):
        return None
    raw = row.get("ranked_candidate_tbl_ids") or row.get("ranked_candidates")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    values = []
    for candidate in raw:
        value = candidate if isinstance(candidate, str) else candidate.get("tbl_id") if isinstance(candidate, Mapping) else None
        table_id = _normalised_string(value)
        if table_id:
            values.append(table_id)
    return values or None


def _prediction_evidence_cells(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = row.get("evidence_cells")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return [cell for cell in raw if isinstance(cell, Mapping)]
    cell = row.get("evidence_cell")
    return [cell] if isinstance(cell, Mapping) else []


def _gold_coordinates(row: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    raw = row.get("gold_coordinates")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and all(
        isinstance(item, Mapping) for item in raw
    ):
        return [item for item in raw if isinstance(item, Mapping)]
    cell = row.get("expected_cell")
    if isinstance(cell, Mapping):
        return [cell]
    return None


def _canonical_coordinate_set(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    coordinate_keys = ("org_id", "tbl_id", "itm_id", "prd_se", "prd_de", "dimension_codes")
    projected = [
        {key: row.get(key) for key in coordinate_keys if key in row}
        for row in rows
    ]
    return tuple(
        sorted(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in projected
        )
    )


def _record_id(row: Mapping[str, Any] | None, *keys: str) -> str | None:
    if not isinstance(row, Mapping):
        return None
    for key in keys:
        value = _normalised_string(row.get(key))
        if value:
            return value
    return None


def _valid_sha256(value: Any) -> str | None:
    text = _normalised_string(value).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        return None
    return text


def _normalised_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_divide(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _positive_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return {key: value for key, value in sorted(counts.items()) if value > 0}


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _jsonl_text(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _count_only_summary(summary: Mapping[str, Any]) -> str:
    operational = summary.get("operational_metrics")
    gold = summary.get("gold_evaluation_metrics")
    reasons = summary.get("not_evaluable_reason_counts")
    lines = [
        "CLAFACT linked Gold evaluation (count-only)",
        f"evaluation_id: {_normalised_string(summary.get('evaluation_id'))}",
        f"evaluation_status: {_normalised_string(summary.get('evaluation_status'))}",
        "metric_boundary: operational coverage is not Gold accuracy",
    ]
    if isinstance(operational, Mapping):
        lines.append(f"operational_stage_count: {len(operational.get('stages', {})) if isinstance(operational.get('stages'), Mapping) else 0}")
    if isinstance(gold, Mapping):
        for stage in ("R1", "R2", "R3", "R4"):
            result = gold.get(stage.lower()) or gold.get(stage)
            if isinstance(result, Mapping):
                join = result.get("join") if isinstance(result.get("join"), Mapping) else {}
                lines.append(
                    f"{stage}: status={result.get('status')} gold={join.get('gold_count', 0)} "
                    f"joined={join.get('joined_count', 0)}"
                )
    if isinstance(reasons, Mapping):
        for reason, count in sorted(reasons.items()):
            lines.append(f"not_evaluable_reason[{reason}]: {count}")
    lines.append("safe_summary_policy: no RSS text, article URL, API key, or secret value")
    return "\n".join(lines) + "\n"
