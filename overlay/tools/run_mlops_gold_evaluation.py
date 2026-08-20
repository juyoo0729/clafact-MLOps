"""Link one stored CLAFACT MLOps run to offline Gold evaluation artifacts.

The command reads local manifests, saved predictions, and frozen Gold only.
It never calls RSS, KOSIS, or an LLM API.  Operational coverage remains a
separate section and is never presented as accuracy.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.mlops_gold_evaluation import (  # noqa: E402
    build_review_queue_status,
    evaluate_r1,
    evaluate_r2,
    evaluate_r3,
    evaluate_r4,
    write_evaluation_artifacts,
)


DEFAULT_R2_GOLD = PROJECT / "data" / "model_benchmarks" / "r2_12slot_v2" / "gold.jsonl"
DEFAULT_GOLD20_FIXTURE = PROJECT / "tests" / "goldset" / "fixtures" / "pilot20.json"
DEFAULT_GOLD20_ROUTES = PROJECT / "tests" / "goldset" / "fixtures" / "pilot20_expected_routes.json"
DEFAULT_GOLD20_ROUTE_REPORT = PROJECT / "data" / "goldset_reports" / "pilot20_route_report.json"
DEFAULT_OUTPUT_ROOT = PROJECT / "data" / "gold_evaluations"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--r1-gold", required=True, type=Path)
    parser.add_argument("--r2-gold", type=Path, default=DEFAULT_R2_GOLD)
    parser.add_argument("--r2-predictions", required=True, type=Path)
    parser.add_argument("--r2-split", choices=("train", "dev"), default="dev")
    parser.add_argument("--gold20-fixture", type=Path, default=DEFAULT_GOLD20_FIXTURE)
    parser.add_argument("--gold20-routes", type=Path, default=DEFAULT_GOLD20_ROUTES)
    parser.add_argument("--gold20-route-report", type=Path, default=DEFAULT_GOLD20_ROUTE_REPORT)
    parser.add_argument("--r3-predictions", required=True, type=Path)
    parser.add_argument("--r4-predictions", required=True, type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evaluation-id", help="Immutable output directory name; generated when omitted.")
    args = parser.parse_args()

    output_dir = run_linked_evaluation(
        run_manifest_path=args.run_manifest,
        r1_gold_path=args.r1_gold,
        r2_gold_path=args.r2_gold,
        r2_predictions_path=args.r2_predictions,
        r2_split=args.r2_split,
        gold20_fixture_path=args.gold20_fixture,
        gold20_routes_path=args.gold20_routes,
        gold20_route_report_path=args.gold20_route_report,
        r3_predictions_path=args.r3_predictions,
        r4_predictions_path=args.r4_predictions,
        output_root=args.output_root,
        evaluation_id=args.evaluation_id,
    )
    print(output_dir)
    return 0


def run_linked_evaluation(
    *,
    run_manifest_path: Path,
    r1_gold_path: Path,
    r2_gold_path: Path,
    r2_predictions_path: Path,
    r2_split: str,
    gold20_fixture_path: Path,
    gold20_routes_path: Path,
    gold20_route_report_path: Path,
    r3_predictions_path: Path,
    r4_predictions_path: Path,
    output_root: Path,
    evaluation_id: str | None = None,
) -> Path:
    """Build one immutable, count-safe linked evaluation artifact set."""
    required_paths = {
        "run_manifest": run_manifest_path,
        "r1_gold": r1_gold_path,
        "r2_gold": r2_gold_path,
        "r2_predictions": r2_predictions_path,
        "gold20_fixture": gold20_fixture_path,
        "gold20_expected_routes": gold20_routes_path,
        "gold20_route_report": gold20_route_report_path,
        "r3_predictions": r3_predictions_path,
        "r4_predictions": r4_predictions_path,
    }
    for logical_name, path in required_paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"EVALUATION_INPUT_NOT_FOUND[{logical_name}]: {path}")

    run_manifest = _load_json_object(run_manifest_path)
    run_dir = run_manifest_path.resolve().parent
    pipeline_run_id = _required_string(run_manifest, "pipeline_run_id")
    verified_stage_paths = _verify_declared_stage_outputs(run_dir, run_manifest)
    r1_candidates, r1_paths = _load_verified_stage_outputs(
        run_dir, "r1", ("r1_candidates.jsonl",)
    )
    r2_review_rows, r2_paths = _load_verified_stage_outputs(
        run_dir, "r2", ("r3_ready_claims.jsonl", "r2_holds.jsonl")
    )

    r1_gold = _load_csv(r1_gold_path)
    r2_gold = _load_jsonl(r2_gold_path)
    r2_predictions = _load_jsonl(r2_predictions_path)
    r3_predictions = _load_jsonl(r3_predictions_path)
    r4_predictions = _load_jsonl(r4_predictions_path)
    gold20 = _load_gold20_contract(
        fixture_path=gold20_fixture_path,
        routes_path=gold20_routes_path,
        route_report_path=gold20_route_report_path,
    )

    r1_result = evaluate_r1(gold_rows=r1_gold, candidate_rows=r1_candidates)
    r2_result = evaluate_r2(gold_rows=r2_gold, prediction_rows=r2_predictions, split=r2_split)
    r3_result = evaluate_r3(gold_rows=gold20, prediction_rows=r3_predictions)
    r4_result = evaluate_r4(gold_rows=gold20, prediction_rows=r4_predictions)
    gold_results = {"r1": r1_result, "r2": r2_result, "r3": r3_result, "r4": r4_result}
    review_inputs = [
        {**row, "stage": "R1"} for row in r1_candidates
    ] + [
        {**row, "stage": "R2"} for row in r2_review_rows
    ]
    review_status = build_review_queue_status(gold_rows=r1_gold, candidate_rows=review_inputs)
    not_evaluable_reason_counts = _aggregate_reason_counts(gold_results)
    evaluation_status = _aggregate_evaluation_status(gold_results)
    safe_evaluation_id = evaluation_id or _new_evaluation_id(pipeline_run_id)
    operational_metrics = _operational_metrics(run_manifest)
    evaluation_summary = {
        "schema_version": "clafact_linked_gold_evaluation_v1",
        "evaluation_id": safe_evaluation_id,
        "pipeline_run_id": pipeline_run_id,
        "evaluation_status": evaluation_status,
        "operational_metrics": operational_metrics,
        "gold_evaluation_metrics": {
            stage: _without_row_results(result) for stage, result in gold_results.items()
        },
        "not_evaluable_reason_counts": not_evaluable_reason_counts,
        "scope": {
            "evaluation_kind": "POST_RUN_OFFLINE_GOLD_EVALUATION",
            "fresh_news_accuracy": "PROHIBITED",
            "r2_split": r2_split,
            "network_policy": "STORED_ARTIFACTS_ONLY_NO_RSS_KOSIS_OR_LLM_API",
            "coordinate_policy": "HUMAN_READABLE_COORDINATE_NOT_MACHINE_EXACT",
        },
        "review_queue_status_counts": dict(
            sorted(Counter(row["review_queue_action"] for row in review_status).items())
        ),
        "metric_boundary": {
            "operational_metrics": "RSS and R1-R4 processing/route counts; never an accuracy score.",
            "gold_evaluation_metrics": "Metrics exist only where saved predictions join the declared Gold key/cohort.",
        },
        "offline_only": True,
        "safe_summary_policy": "No RSS text, article URL, API key, or secret value is copied.",
    }
    join_results = [
        row
        for result in gold_results.values()
        for row in result.get("row_results", [])
        if isinstance(row, dict)
    ]
    input_files = {
        **required_paths,
        **{f"verified_{key}": value for key, value in verified_stage_paths.items()},
        **{f"stage_input_{key}": value for key, value in {**r1_paths, **r2_paths}.items()},
    }
    return write_evaluation_artifacts(
        output_root=output_root,
        evaluation_id=safe_evaluation_id,
        evaluation_summary=evaluation_summary,
        join_results=join_results,
        review_queue_status=review_status,
        input_files=input_files,
    )


def _load_verified_stage_outputs(
    run_dir: Path,
    stage: str,
    file_names: tuple[str, ...],
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    stage_dir = run_dir / stage
    manifest_path = stage_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"STAGE_MANIFEST_NOT_FOUND[{stage}]: {manifest_path}")
    manifest = _load_json_object(manifest_path)
    if manifest.get("stage") != stage:
        raise ValueError(f"STAGE_MANIFEST_NAME_MISMATCH[{stage}]")
    expected_hashes = manifest.get("output_sha256")
    if not isinstance(expected_hashes, dict):
        raise ValueError(f"STAGE_OUTPUT_SHA256_MISSING[{stage}]")
    rows: list[dict[str, Any]] = []
    paths: dict[str, Path] = {f"{stage}_manifest": manifest_path}
    for file_name in file_names:
        path = stage_dir / file_name
        if not path.is_file():
            raise FileNotFoundError(f"STAGE_OUTPUT_NOT_FOUND[{stage}/{file_name}]: {path}")
        expected = expected_hashes.get(file_name)
        actual = _file_sha256(path)
        if not isinstance(expected, str) or actual != expected.lower():
            raise ValueError(f"STAGE_OUTPUT_SHA256_MISMATCH[{stage}/{file_name}]")
        rows.extend(_load_jsonl(path))
        paths[f"{stage}_{file_name}"] = path
    return rows, paths


def _verify_declared_stage_outputs(
    run_dir: Path,
    run_manifest: dict[str, Any],
) -> dict[str, Path]:
    """Verify every output declared by every stage before any Gold scoring."""
    raw_stages = run_manifest.get("stages")
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ValueError("RUN_MANIFEST_STAGES_MISSING")
    verified: dict[str, Path] = {}
    for stage_entry in raw_stages:
        if not isinstance(stage_entry, dict) or not isinstance(stage_entry.get("stage"), str):
            raise ValueError("RUN_MANIFEST_STAGE_INVALID")
        stage = stage_entry["stage"]
        stage_dir = run_dir / stage
        manifest_path = stage_dir / "manifest.json"
        manifest = _load_json_object(manifest_path)
        expected_hashes = manifest.get("output_sha256")
        if not isinstance(expected_hashes, dict):
            raise ValueError(f"STAGE_OUTPUT_SHA256_MISSING[{stage}]")
        verified[f"{stage}_manifest"] = manifest_path
        for file_name, expected in expected_hashes.items():
            path = stage_dir / str(file_name)
            if not path.is_file():
                raise FileNotFoundError(f"STAGE_OUTPUT_NOT_FOUND[{stage}/{file_name}]: {path}")
            if not isinstance(expected, str) or _file_sha256(path) != expected.lower():
                raise ValueError(f"STAGE_OUTPUT_SHA256_MISMATCH[{stage}/{file_name}]")
            verified[f"{stage}_{file_name}"] = path
    return verified


def _load_gold20_contract(
    *, fixture_path: Path, routes_path: Path, route_report_path: Path
) -> list[dict[str, Any]]:
    fixtures = _load_json_list(fixture_path)
    routes = _load_json_list(routes_path)
    report = _load_json_object(route_report_path)
    report_rows = report.get("results")
    if not isinstance(report_rows, list) or not all(isinstance(row, dict) for row in report_rows):
        raise ValueError("GOLD20_ROUTE_REPORT_RESULTS_INVALID")
    fixture_by_id = _unique_by_claim_id(fixtures, "GOLD20_FIXTURE")
    route_by_id = _unique_by_claim_id(routes, "GOLD20_EXPECTED_ROUTES")
    report_by_id = _unique_by_claim_id(report_rows, "GOLD20_ROUTE_REPORT")
    if set(fixture_by_id) != set(route_by_id) or set(route_by_id) != set(report_by_id):
        raise ValueError("GOLD20_CONTRACT_CLAIM_IDS_MISMATCH")

    rows: list[dict[str, Any]] = []
    for claim_id in sorted(route_by_id):
        fixture = fixture_by_id[claim_id]
        route = route_by_id[claim_id]
        report_row = report_by_id[claim_id]
        expected_route = _required_string(route, "expected_route").upper()
        expected_verdict = _required_string(route, "expected_verdict").upper()
        if str(report_row.get("route_status") or "").upper() != expected_route:
            raise ValueError(f"GOLD20_ROUTE_CONTRACT_MISMATCH[{claim_id}]")
        if str(report_row.get("verdict") or "").upper() != expected_verdict:
            raise ValueError(f"GOLD20_VERDICT_CONTRACT_MISMATCH[{claim_id}]")
        table_id = str(report_row.get("gold_table_id") or fixture.get("gold_table_id") or "").strip()
        rows.append(
            {
                "claim_id": claim_id,
                "expected_route": expected_route,
                "expected_verdict": expected_verdict,
                "gold_table_ids": [table_id] if table_id else [],
                # Gold20 currently stores a human-readable coordinate string.
                # Keep that provenance fact, but do not coerce it into a machine coordinate.
                "gold_coordinate_machine_comparable": isinstance(fixture.get("gold_coordinate"), dict),
                "gold_coordinates": (
                    [fixture["gold_coordinate"]] if isinstance(fixture.get("gold_coordinate"), dict) else None
                ),
            }
        )
    return rows


def _operational_metrics(run_manifest: dict[str, Any]) -> dict[str, Any]:
    stages = {}
    raw_stages = run_manifest.get("stages")
    if isinstance(raw_stages, list):
        for row in raw_stages:
            if not isinstance(row, dict) or not isinstance(row.get("stage"), str):
                continue
            stages[row["stage"]] = {
                "status": row.get("status"),
                "counts": row.get("counts") if isinstance(row.get("counts"), dict) else {},
                "reason_counts": (
                    row.get("reason_counts") if isinstance(row.get("reason_counts"), dict) else {}
                ),
            }
    return {
        "metric_kind": "OPERATIONAL_COVERAGE_NOT_ACCURACY",
        "run_status": run_manifest.get("status"),
        "stages": stages,
    }


def _aggregate_reason_counts(results: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = Counter()
    for result in results.values():
        stage_counts = result.get("not_evaluable_reason_counts")
        if isinstance(stage_counts, dict):
            for reason, count in stage_counts.items():
                if isinstance(reason, str) and isinstance(count, int):
                    counts[reason] += count
    return dict(sorted(counts.items()))


def _aggregate_evaluation_status(results: dict[str, dict[str, Any]]) -> str:
    statuses = [result.get("status") for result in results.values()]
    if statuses and all(status == "EVALUATED" for status in statuses):
        return "EVALUATED"
    if any(status in {"EVALUATED", "PARTIAL"} for status in statuses):
        return "PARTIAL"
    return "NOT_EVALUABLE"


def _without_row_results(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "row_results"}


def _unique_by_claim_id(rows: list[dict[str, Any]], source: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        claim_id = _required_string(row, "claim_id")
        if claim_id in result:
            raise ValueError(f"{source}_DUPLICATE_CLAIM_ID[{claim_id}]")
        result[claim_id] = row
    return result


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL_ROW_MUST_BE_OBJECT[{path.name}:{line_number}]")
        rows.append(value)
    return rows


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED[{path.name}]")
    return value


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"JSON_OBJECT_LIST_REQUIRED[{path.name}]")
    return value


def _required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"REQUIRED_STRING_MISSING[{key}]")
    return value.strip()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _new_evaluation_id(pipeline_run_id: str) -> str:
    created = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"gold-eval-{created}-{pipeline_run_id[-8:]}"


if __name__ == "__main__":
    raise SystemExit(main())
