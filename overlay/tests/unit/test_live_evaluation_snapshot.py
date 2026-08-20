import json
from hashlib import sha256
from pathlib import Path

import pytest

from core.live_evaluation_snapshot import write_live_evaluation_snapshot


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _seed_operational_cycle(state_root: Path, pipeline_run_id: str = "run-001") -> tuple[Path, Path]:
    cycle_path = _write_json(
        state_root / "operational_cycles" / "20260820T010000Z.json",
        {
            "artifact": "clafact_operational_cycle_v1",
            "cycle_status": "PIPELINE_HOLD",
            "rss": {
                "NEW_ARTICLES": 10,
                "R1_READY": 10,
                "FEED_STATUS": {"status_counts": {"COLLECTED": 1}},
                "HOLD_OR_ERROR": {"hold_count": 0, "error_feed_count": 0},
                "source_url": "https://must-not-leak.example/rss",
                "raw_text": "must-not-leak",
            },
            "pipeline": {
                "pipeline_run_id": pipeline_run_id,
                "stages": [
                    {
                        "stage": "r1",
                        "status": "HOLD",
                        "counts": {"input": 10, "processed": 5, "candidates": 4, "holds": 2},
                        "reason_counts": {"R1_NUMERIC_CANDIDATE_NOT_FOUND": 2},
                    }
                ],
                "missing_stages": ["r2", "r3", "r4"],
            },
        },
    )
    manifest_path = _write_json(
        state_root / "runs" / pipeline_run_id / "run_manifest.json",
        {
            "schema_version": "clafact_run_manifest_v1",
            "pipeline_run_id": pipeline_run_id,
            "status": "HOLD",
            "stages": [],
            "source_url": "https://must-not-leak.example/article",
        },
    )
    return cycle_path, manifest_path


def _seed_post_run_evaluation(state_root: Path, pipeline_run_id: str) -> Path:
    return _write_json(
        state_root / "gold_evaluations" / "POST-001" / "evaluation_summary.json",
        {
            "schema_version": "clafact_linked_gold_evaluation_v1",
            "evaluation_id": "POST-001",
            "pipeline_run_id": pipeline_run_id,
            "evaluation_status": "PARTIAL",
            "gold_evaluation_metrics": {
                "r1": {
                    "status": "EVALUATED",
                    "join": {"gold_count": 30, "joined_count": 28, "coverage": 28 / 30},
                    "metrics": {
                        "true_candidate_recall": {
                            "status": "EVALUATED",
                            "numerator": 15,
                            "denominator": 15,
                            "value": 1.0,
                        }
                    },
                }
            },
            "not_evaluable_reason_counts": {"R1_FALSE_GOLD_REQUIRED_FOR_PRECISION_F1": 2},
            "raw_text": "must-not-leak",
        },
    )


def _seed_replay_evaluation(state_root: Path) -> Path:
    return _write_json(
        state_root / "gold_evaluations" / "REPLAY-001" / "evaluation_summary.json",
        {
            "schema_version": "clafact_gold_replay_evaluation_v1",
            "evaluation_id": "REPLAY-001",
            "evaluation_status": "PARTIAL",
            "gold_evaluation_metrics": {
                "r4": {
                    "status": "EVALUATED",
                    "join": {"gold_count": 20, "joined_count": 20, "coverage": 1.0},
                    "metrics": {
                        "route_accuracy": {
                            "status": "EVALUATED",
                            "numerator": 7,
                            "denominator": 20,
                            "value": 0.35,
                        }
                    },
                }
            },
            "not_evaluable_reason_counts": {"R4_GOLD_COORDINATE_NOT_MACHINE_COMPARABLE": 20},
            "scope": {"conditional_vs_e2e": "CONDITIONAL_REPLAY_NOT_END_TO_END"},
        },
    )


def test_live_snapshot_separates_operational_counts_from_linked_gold_metrics(tmp_path) -> None:
    state_root = tmp_path / "state"
    cycle_path, manifest_path = _seed_operational_cycle(state_root)
    post_path = _seed_post_run_evaluation(state_root, "run-001")

    output_dir = write_live_evaluation_snapshot(
        state_root=state_root,
        snapshot_id="LIVE-001",
    )

    payload = json.loads((output_dir / "live_evaluation_snapshot.json").read_text(encoding="utf-8"))
    assert payload["operational_metrics"]["metric_kind"] == "OPERATIONAL_COVERAGE_NOT_ACCURACY"
    assert payload["operational_metrics"]["rss_counts"]["new_articles"] == 10
    assert "accuracy" not in payload["operational_metrics"]
    post = payload["gold_evaluation_metrics"]["post_run"]
    assert post["pipeline_run_linked"] is True
    assert post["stages"]["r1"]["join"]["joined_count"] == 28
    assert post["stages"]["r1"]["metrics"]["true_candidate_recall"]["value"] == 1.0

    hashes = json.loads((output_dir / "sha256_manifest.json").read_text(encoding="utf-8"))
    assert hashes["inputs"]["operational_cycle"]["sha256"] == sha256(cycle_path.read_bytes()).hexdigest()
    assert hashes["inputs"]["run_manifest"]["sha256"] == sha256(manifest_path.read_bytes()).hexdigest()
    assert hashes["inputs"]["post_run_evaluation"]["sha256"] == sha256(post_path.read_bytes()).hexdigest()


def test_live_snapshot_does_not_copy_stale_post_run_accuracy(tmp_path) -> None:
    state_root = tmp_path / "state"
    _seed_operational_cycle(state_root, "run-current")
    _seed_post_run_evaluation(state_root, "run-stale")

    output_dir = write_live_evaluation_snapshot(state_root=state_root, snapshot_id="LIVE-STALE")
    payload = json.loads((output_dir / "live_evaluation_snapshot.json").read_text(encoding="utf-8"))

    post = payload["gold_evaluation_metrics"]["post_run"]
    assert post["evaluation_status"] == "NOT_EVALUABLE"
    assert post["stages"] == {}
    assert payload["not_evaluable_reason_counts"]["snapshot"][
        "POST_RUN_EVALUATION_NOT_LINKED_TO_LATEST_RUN"
    ] == 1


def test_live_snapshot_keeps_conditional_replay_separate_from_end_to_end(tmp_path) -> None:
    state_root = tmp_path / "state"
    _seed_operational_cycle(state_root)
    _seed_replay_evaluation(state_root)

    output_dir = write_live_evaluation_snapshot(state_root=state_root, snapshot_id="LIVE-REPLAY")
    payload = json.loads((output_dir / "live_evaluation_snapshot.json").read_text(encoding="utf-8"))

    replay = payload["gold_evaluation_metrics"]["conditional_replay"]
    assert replay["scope"] == "CONDITIONAL_REPLAY_NOT_END_TO_END"
    assert replay["stages"]["r4"]["metrics"]["route_accuracy"]["value"] == 0.35
    assert payload["evaluation_status"]["fresh_cohort_accuracy"] == "NOT_EVALUABLE_UNLESS_JOINED_GOLD"


def test_live_snapshot_is_immutable_and_summary_is_safe(tmp_path) -> None:
    state_root = tmp_path / "state"
    _seed_operational_cycle(state_root)
    _seed_post_run_evaluation(state_root, "run-001")

    output_dir = write_live_evaluation_snapshot(state_root=state_root, snapshot_id="LIVE-SAFE")

    with pytest.raises(FileExistsError, match="LIVE_SNAPSHOT_ALREADY_EXISTS"):
        write_live_evaluation_snapshot(state_root=state_root, snapshot_id="LIVE-SAFE")

    summary = (output_dir / "summary.txt").read_text(encoding="utf-8")
    payload_text = (output_dir / "live_evaluation_snapshot.json").read_text(encoding="utf-8")
    for forbidden in ("https://", "must-not-leak", "source_url", "raw_text", str(tmp_path)):
        assert forbidden not in summary
        assert forbidden not in payload_text
    assert "SCHEDULING: NOT_REGISTERED" in summary
    assert "NETWORK: STORED_ARTIFACTS_ONLY_NO_RSS_KOSIS_OR_LLM_API" in summary
