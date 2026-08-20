"""Build an offline, immutable snapshot of the latest CLAFACT MLOps state.

The snapshot reads stored operational manifests and Gold evaluation summaries
only.  It performs no RSS, KOSIS, or LLM calls and never presents operational
coverage as model accuracy.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SAFE_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_POST_RUN_SCHEMA = "clafact_linked_gold_evaluation_v1"
_REPLAY_SCHEMA = "clafact_gold_replay_evaluation_v1"
_NETWORK_POLICY = "STORED_ARTIFACTS_ONLY_NO_RSS_KOSIS_OR_LLM_API"
_REPLAY_SCOPE = "CONDITIONAL_REPLAY_NOT_END_TO_END"


def write_live_evaluation_snapshot(
    *,
    state_root: Path,
    snapshot_id: str,
    output_root: Path | None = None,
    created_at: datetime | None = None,
) -> Path:
    """Write one immutable, path-free live evaluation snapshot directory."""
    safe_snapshot_id = _validated_id(snapshot_id, "LIVE_SNAPSHOT_ID_INVALID")
    state_root = Path(state_root)
    target_root = Path(output_root) if output_root is not None else state_root / "live_evaluation_snapshots"
    output_dir = target_root / safe_snapshot_id
    if output_dir.exists():
        raise FileExistsError(f"LIVE_SNAPSHOT_ALREADY_EXISTS:{safe_snapshot_id}")

    cycle_path = _latest_file(state_root / "operational_cycles", "*.json")
    if cycle_path is None:
        raise FileNotFoundError("OPERATIONAL_CYCLE_NOT_FOUND")
    cycle = _load_json_object(cycle_path)
    operational, pipeline_run_id = _operational_view(cycle, cycle_path.stem)
    snapshot_reasons: Counter[str] = Counter()
    input_paths: dict[str, Path] = {"operational_cycle": cycle_path}

    run_manifest_path = (
        state_root / "runs" / pipeline_run_id / "run_manifest.json" if pipeline_run_id else None
    )
    if run_manifest_path is None or not run_manifest_path.is_file():
        snapshot_reasons["LATEST_RUN_MANIFEST_NOT_FOUND"] += 1
    else:
        run_manifest = _load_json_object(run_manifest_path)
        if run_manifest.get("pipeline_run_id") != pipeline_run_id:
            snapshot_reasons["LATEST_RUN_MANIFEST_PIPELINE_ID_MISMATCH"] += 1
        else:
            input_paths["run_manifest"] = run_manifest_path
            operational["run_manifest_status"] = _safe_status(run_manifest.get("status"))

    evaluations, invalid_evaluation_count = _load_evaluation_summaries(
        state_root / "gold_evaluations"
    )
    if invalid_evaluation_count:
        snapshot_reasons["EVALUATION_SUMMARY_INVALID"] += invalid_evaluation_count

    post_records = [row for row in evaluations if row[1].get("schema_version") == _POST_RUN_SCHEMA]
    linked_post_records = [
        row for row in post_records if pipeline_run_id and row[1].get("pipeline_run_id") == pipeline_run_id
    ]
    if linked_post_records and run_manifest_path is not None and run_manifest_path.is_file():
        post_path, post_payload = linked_post_records[-1]
        input_paths["post_run_evaluation"] = post_path
        post_run = _evaluation_view(post_payload, pipeline_run_linked=True)
    else:
        reason = (
            "POST_RUN_EVALUATION_NOT_LINKED_TO_LATEST_RUN"
            if post_records
            else "POST_RUN_EVALUATION_NOT_FOUND"
        )
        snapshot_reasons[reason] += 1
        post_run = {
            "evaluation_id": None,
            "evaluation_status": "NOT_EVALUABLE",
            "pipeline_run_linked": False,
            "stages": {},
        }

    replay_records = [row for row in evaluations if row[1].get("schema_version") == _REPLAY_SCHEMA]
    if replay_records:
        replay_path, replay_payload = replay_records[-1]
        input_paths["conditional_replay_evaluation"] = replay_path
        conditional_replay = _evaluation_view(replay_payload)
        conditional_replay["scope"] = _REPLAY_SCOPE
    else:
        snapshot_reasons["CONDITIONAL_REPLAY_EVALUATION_NOT_FOUND"] += 1
        conditional_replay = {
            "evaluation_id": None,
            "evaluation_status": "NOT_EVALUABLE",
            "scope": _REPLAY_SCOPE,
            "stages": {},
        }

    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    payload = {
        "schema_version": "clafact_live_evaluation_snapshot_v1",
        "snapshot_id": safe_snapshot_id,
        "created_at": timestamp,
        "operational_metrics": operational,
        "gold_evaluation_metrics": {
            "post_run": post_run,
            "conditional_replay": conditional_replay,
        },
        "evaluation_status": {
            "operational": operational["cycle_status"],
            "post_run": post_run["evaluation_status"],
            "conditional_replay": conditional_replay["evaluation_status"],
            "fresh_cohort_accuracy": "NOT_EVALUABLE_UNLESS_JOINED_GOLD",
        },
        "not_evaluable_reason_counts": {
            "snapshot": dict(sorted(snapshot_reasons.items())),
            "post_run": post_run.get("not_evaluable_reason_counts", {}),
            "conditional_replay": conditional_replay.get("not_evaluable_reason_counts", {}),
        },
        "scope": {
            "network_policy": _NETWORK_POLICY,
            "scheduling": "NOT_REGISTERED",
            "fresh_operational_accuracy": "PROHIBITED",
            "conditional_replay": _REPLAY_SCOPE,
            "metric_boundary": (
                "Operational RSS and R1-R4 counts are coverage. Gold metrics are copied only from "
                "a stored evaluation linked to the same pipeline run or from a separately labelled "
                "conditional replay."
            ),
        },
        "safe_summary_policy": "No RSS text, article URL, API key, secret value, or local path is copied.",
        "offline_only": True,
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    snapshot_path = output_dir / "live_evaluation_snapshot.json"
    summary_path = output_dir / "summary.txt"
    _atomic_write(snapshot_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(summary_path, _render_summary(payload))

    hash_manifest = {
        "schema_version": "clafact_live_evaluation_snapshot_sha256_v1",
        "snapshot_id": safe_snapshot_id,
        "inputs": {
            logical_name: {"sha256": _sha256_file(path)}
            for logical_name, path in sorted(input_paths.items())
        },
        "outputs": {
            snapshot_path.name: {"sha256": _sha256_file(snapshot_path)},
            summary_path.name: {"sha256": _sha256_file(summary_path)},
        },
    }
    _atomic_write(
        output_dir / "sha256_manifest.json",
        json.dumps(hash_manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return output_dir


def _operational_view(cycle: Mapping[str, Any], cycle_id: str) -> tuple[dict[str, Any], str]:
    rss = cycle.get("rss") if isinstance(cycle.get("rss"), Mapping) else {}
    pipeline = cycle.get("pipeline") if isinstance(cycle.get("pipeline"), Mapping) else {}
    pipeline_run_id = _safe_optional_id(pipeline.get("pipeline_run_id"))
    stage_views: dict[str, Any] = {}
    raw_stages = pipeline.get("stages")
    if isinstance(raw_stages, list):
        for raw_stage in raw_stages:
            if not isinstance(raw_stage, Mapping):
                continue
            stage = str(raw_stage.get("stage") or "").lower()
            if stage not in {"r1", "r2", "r3", "r4"}:
                continue
            stage_views[stage] = {
                "status": _safe_status(raw_stage.get("status")),
                "counts": _integer_mapping(raw_stage.get("counts")),
                "reason_counts": _reason_counts(raw_stage.get("reason_counts")),
            }
    feed_status = rss.get("FEED_STATUS") if isinstance(rss.get("FEED_STATUS"), Mapping) else {}
    hold_or_error = (
        rss.get("HOLD_OR_ERROR") if isinstance(rss.get("HOLD_OR_ERROR"), Mapping) else {}
    )
    missing_stages = pipeline.get("missing_stages")
    return (
        {
            "metric_kind": "OPERATIONAL_COVERAGE_NOT_ACCURACY",
            "cycle_id": _safe_optional_id(cycle_id),
            "cycle_status": _safe_status(cycle.get("cycle_status")),
            "pipeline_run_id": pipeline_run_id or None,
            "rss_counts": {
                "new_articles": _integer(rss.get("NEW_ARTICLES")),
                "r1_ready": _integer(rss.get("R1_READY")),
                "hold_count": _integer(hold_or_error.get("hold_count")),
                "error_feed_count": _integer(hold_or_error.get("error_feed_count")),
                "feed_status_counts": _integer_mapping(feed_status.get("status_counts")),
            },
            "stages": stage_views,
            "missing_stages": [
                stage
                for stage in (str(value).lower() for value in missing_stages or [])
                if stage in {"r1", "r2", "r3", "r4"}
            ],
        },
        pipeline_run_id,
    )


def _evaluation_view(
    evaluation: Mapping[str, Any], *, pipeline_run_linked: bool | None = None
) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    raw_stages = evaluation.get("gold_evaluation_metrics")
    if isinstance(raw_stages, Mapping):
        for stage, raw_stage in sorted(raw_stages.items()):
            if not isinstance(raw_stage, Mapping):
                continue
            stage_name = str(stage).lower()
            if stage_name not in {"r1", "r2", "r3", "r4"}:
                continue
            stages[stage_name] = {
                "status": _safe_status(raw_stage.get("status")),
                "join": _join_view(raw_stage.get("join")),
                "metrics": _metrics_view(raw_stage.get("metrics")),
                "not_evaluable_reason_counts": _reason_counts(
                    raw_stage.get("not_evaluable_reason_counts")
                ),
            }
    result: dict[str, Any] = {
        "evaluation_id": _safe_optional_id(evaluation.get("evaluation_id")) or None,
        "evaluation_status": _safe_status(evaluation.get("evaluation_status")),
        "stages": stages,
        "not_evaluable_reason_counts": _reason_counts(
            evaluation.get("not_evaluable_reason_counts")
        ),
    }
    if pipeline_run_linked is not None:
        result["pipeline_run_linked"] = pipeline_run_linked
        result["pipeline_run_id"] = _safe_optional_id(evaluation.get("pipeline_run_id")) or None
    return result


def _join_view(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "gold_count",
        "joined_count",
        "prediction_count",
        "missing_prediction_count",
        "unexpected_prediction_count",
    ):
        if key in value:
            result[key] = _integer(value.get(key))
    coverage = value.get("coverage")
    if isinstance(coverage, (int, float)) and not isinstance(coverage, bool):
        result["coverage"] = float(coverage)
    return result


def _metrics_view(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for metric_name, raw_metric in sorted(value.items()):
        if not isinstance(metric_name, str) or not isinstance(raw_metric, Mapping):
            continue
        metric: dict[str, Any] = {"status": _safe_status(raw_metric.get("status"))}
        raw_value = raw_metric.get("value")
        if raw_value is None or (
            isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
        ):
            metric["value"] = raw_value
        for count_key in ("numerator", "denominator", "count", "slot_count"):
            if count_key in raw_metric:
                metric[count_key] = _integer(raw_metric.get(count_key))
        reason_code = raw_metric.get("reason_code")
        if isinstance(reason_code, str):
            metric["reason_code"] = (
                reason_code if _SAFE_REASON.fullmatch(reason_code) else "UNSAFE_REASON_CODE_REDACTED"
            )
        result[metric_name] = metric
    return result


def _load_evaluation_summaries(root: Path) -> tuple[list[tuple[Path, dict[str, Any]]], int]:
    records: list[tuple[Path, dict[str, Any]]] = []
    invalid_count = 0
    if not root.is_dir():
        return records, invalid_count
    for path in sorted(root.glob("*/evaluation_summary.json"), key=lambda item: item.parent.name):
        try:
            records.append((path, _load_json_object(path)))
        except (OSError, ValueError, json.JSONDecodeError):
            invalid_count += 1
    return records, invalid_count


def _render_summary(payload: Mapping[str, Any]) -> str:
    operational = payload["operational_metrics"]
    rss = operational["rss_counts"]
    lines = [
        f"CLAFACT_LIVE_EVALUATION_SNAPSHOT: {payload['snapshot_id']}",
        (
            f"OPERATIONAL: {operational['cycle_status']} | new_articles={rss['new_articles']} | "
            f"r1_ready={rss['r1_ready']} | hold_count={rss['hold_count']} | "
            f"error_feed_count={rss['error_feed_count']}"
        ),
    ]
    for stage, row in operational["stages"].items():
        counts = ", ".join(f"{key}={value}" for key, value in sorted(row["counts"].items())) or "none"
        lines.append(f"{stage.upper()}_OPERATIONAL: {row['status']} | {counts}")

    post = payload["gold_evaluation_metrics"]["post_run"]
    lines.append(
        f"POST_RUN_GOLD: {post['evaluation_status']} | pipeline_run_linked="
        f"{str(post.get('pipeline_run_linked', False)).lower()}"
    )
    for stage, row in post["stages"].items():
        join = row["join"]
        lines.append(
            f"{stage.upper()}_POST_RUN_JOIN: {row['status']} | "
            f"joined={join.get('joined_count', 0)}/{join.get('gold_count', 0)}"
        )

    replay = payload["gold_evaluation_metrics"]["conditional_replay"]
    lines.append(f"CONDITIONAL_REPLAY: {replay['evaluation_status']} | scope={_REPLAY_SCOPE}")
    for stage, row in replay["stages"].items():
        join = row["join"]
        lines.append(
            f"{stage.upper()}_REPLAY_JOIN: {row['status']} | "
            f"joined={join.get('joined_count', 0)}/{join.get('gold_count', 0)}"
        )
    lines.extend(
        [
            "FRESH_COHORT_ACCURACY: NOT_EVALUABLE_UNLESS_JOINED_GOLD",
            f"NETWORK: {_NETWORK_POLICY}",
            "SCHEDULING: NOT_REGISTERED",
        ]
    )
    snapshot_reasons = payload["not_evaluable_reason_counts"]["snapshot"]
    for reason, count in sorted(snapshot_reasons.items()):
        lines.append(f"NOT_EVALUABLE_REASON: {reason}={count}")
    return "\n".join(lines) + "\n"


def _latest_file(root: Path, pattern: str) -> Path | None:
    if not root.is_dir():
        return None
    paths = sorted(path for path in root.glob(pattern) if path.is_file())
    return paths[-1] if paths else None


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _reason_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: Counter[str] = Counter()
    for reason, count in value.items():
        key = reason if isinstance(reason, str) and _SAFE_REASON.fullmatch(reason) else "UNSAFE_REASON_CODE_REDACTED"
        result[key] += _integer(count)
    return dict(sorted(result.items()))


def _integer_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _integer(inner) for key, inner in sorted(value.items(), key=lambda row: str(row[0]))}


def _integer(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _safe_status(value: Any) -> str:
    text = str(value or "NOT_EVALUABLE").upper()
    return text if _SAFE_REASON.fullmatch(text) else "UNSAFE_STATUS_REDACTED"


def _safe_optional_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if text and _SAFE_ID.fullmatch(text) else ""


def _validated_id(value: str, reason: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise ValueError(reason)
    return text


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)

