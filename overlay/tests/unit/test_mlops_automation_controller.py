"""TDD contract tests for the single-mode CLAFACT automation controller."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from core.mlops_automation_controller import (
    CONDITIONAL_REPLAY_SCOPE,
    AutomationConfigError,
    build_controller_summary,
    build_execution_plan,
    build_external_summary,
    execute_controller_mode,
    validate_controller_config,
)
from core.mlops_gold_evaluation import (
    EvaluationOutputExistsError,
    build_review_queue_status,
    evaluate_r4,
    write_evaluation_artifacts,
)
from core.r1_article_pipeline import normalized_sentence_hash


def _base_config(tmp_path: Path) -> dict:
    gate = tmp_path / "quality_gate.json"
    gate.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    files = {}
    for name in (
        "rss.json",
        "run_manifest.json",
        "r1_gold.csv",
        "r2_gold.jsonl",
        "r2_predictions.jsonl",
        "r3_predictions.jsonl",
        "r4_predictions.jsonl",
        "frozen_r2_claims.json",
        "concepts.json",
        "catalog.json",
        "periods.json",
        "gold20.json",
        "gold20_routes.json",
        "gold20_report.json",
        "r2_input.jsonl",
    ):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        files[name] = str(path)
    return {
        "schema_version": "clafact_mlops_automation_controller_v1",
        "scheduling": {"enabled": False},
        "controller_output_root": str(tmp_path / "controller_runs"),
        "modes": {
            "operational_cycle": {
                "enabled": True,
                "quality_gate_report": str(gate),
                "rss_approved": True,
                "approved_rss_config": files["rss.json"],
                "state_root": str(tmp_path / "state"),
                "max_records": 5,
                "through": "r4",
            },
            "post_run_gold_evaluation": {
                "enabled": True,
                "run_manifest": files["run_manifest.json"],
                "r1_gold": files["r1_gold.csv"],
                "r2_gold": files["r2_gold.jsonl"],
                "r2_predictions": files["r2_predictions.jsonl"],
                "r3_predictions": files["r3_predictions.jsonl"],
                "r4_predictions": files["r4_predictions.jsonl"],
                "gold20_fixture": files["gold20.json"],
                "gold20_routes": files["gold20_routes.json"],
                "gold20_route_report": files["gold20_report.json"],
                "evaluation_root": str(tmp_path / "evaluations"),
                "evaluation_id": "POST-EVAL-001",
                "r2_split": "dev",
            },
            "gold_replay_evaluation": {
                "enabled": True,
                "run_id": "REPLAY-001",
                "replay_run_root": str(tmp_path / "replay_runs"),
                "r1_gold": files["r1_gold.csv"],
                "frozen_r2_claims": files["frozen_r2_claims.json"],
                "concepts": files["concepts.json"],
                "catalog": files["catalog.json"],
                "period_snapshot": files["periods.json"],
                "snapshot_dir": str(tmp_path / "snapshots"),
                "evaluation_root": str(tmp_path / "evaluations"),
                "evaluation_id": "REPLAY-EVAL-001",
                "gold20_fixture": files["gold20.json"],
                "gold20_routes": files["gold20_routes.json"],
                "gold20_route_report": files["gold20_report.json"],
            },
            "r2_dev_experiment": {
                "enabled": True,
                "quality_gate_report": str(gate),
                "approved_single_change": True,
                "experiment_id": "R2-DEV-001",
                "provider": "openai",
                "expected_model": "gpt-test",
                "prompt_version": "prompt-v1",
                "change_reason": "one approved normalizer change",
                "input": files["r2_input.jsonl"],
                "gold": files["r2_gold.jsonl"],
                "output_root": str(tmp_path / "r2_runs"),
                "split": "dev",
                "smoke_limit": 0,
            },
        },
    }


# 1. Fresh operational execution never creates Gold accuracy.
def test_operational_mode_has_no_gold_accuracy_command_or_metric(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    plan = build_execution_plan("operational_cycle", config, project_root=tmp_path, python_executable="python")
    command_text = " ".join(plan["commands"][0])

    assert len(plan["commands"]) == 1
    assert "run_clafact_operational_cycle.py" in command_text
    assert "gold" not in command_text.lower()
    summary = build_controller_summary(
        mode="operational_cycle",
        status="PIPELINE_HOLD",
        operational_metrics={"stages": {"r1": {"processed": 5}}},
        gold_evaluation_metrics={},
        evaluation_status="NOT_RUN",
        not_evaluable_reason_counts={},
        scope={"fresh_news_accuracy": "PROHIBITED"},
        artifact_hashes={"operational_cycle": "a" * 64},
    )
    assert summary["gold_evaluation_metrics"] == {}


# 2. Post-run evaluation dispatches the stored-artifact-only evaluator.
def test_post_run_mode_has_no_network_or_api_command(tmp_path: Path) -> None:
    plan = build_execution_plan(
        "post_run_gold_evaluation",
        _base_config(tmp_path),
        project_root=tmp_path,
        python_executable="python",
    )
    command_text = " ".join(plan["commands"][0])

    assert plan["network_calls_allowed"] is False
    assert len(plan["commands"]) == 1
    assert "run_mlops_gold_evaluation.py" in command_text
    for forbidden in ("--rss-config", "--use-kosis-api", "--use-kosis-live-discovery", "--provider"):
        assert forbidden not in command_text


# 3. Zero Gold join remains NOT_EVALUABLE.
def test_zero_join_never_becomes_an_accuracy_metric() -> None:
    result = evaluate_r4(
        gold_rows=[{"claim_id": "NEWS_B-001", "expected_route": "AUTO", "expected_verdict": "MATCH"}],
        prediction_rows=[{"claim_id": "parent-001", "route_status": "AUTO", "verdict": "MATCH"}],
    )
    assert result["status"] == "NOT_EVALUABLE"
    assert result["metrics"] == {}


# 4. Conditional replay scope survives the controller boundary.
def test_gold_replay_mode_keeps_conditional_scope(tmp_path: Path) -> None:
    plan = build_execution_plan(
        "gold_replay_evaluation",
        _base_config(tmp_path),
        project_root=tmp_path,
        python_executable="python",
    )
    assert len(plan["commands"]) == 2
    assert plan["scope"] == CONDITIONAL_REPLAY_SCOPE
    assert "run_gold_replay.py" in " ".join(plan["commands"][0])
    assert "run_gold_replay_evaluation.py" in " ".join(plan["commands"][1])


# 5. Evaluation IDs are immutable.
def test_evaluation_collision_never_overwrites(tmp_path: Path) -> None:
    summary = build_controller_summary(
        mode="post_run_gold_evaluation",
        status="NOT_EVALUABLE",
        operational_metrics={},
        gold_evaluation_metrics={},
        evaluation_status="NOT_EVALUABLE",
        not_evaluable_reason_counts={},
        scope={},
        artifact_hashes={},
    )
    kwargs = dict(
        output_root=tmp_path,
        evaluation_id="COLLISION-001",
        evaluation_summary=summary,
        join_results=[],
        review_queue_status=[],
        input_files={},
    )
    write_evaluation_artifacts(**kwargs)
    with pytest.raises(EvaluationOutputExistsError):
        write_evaluation_artifacts(**kwargs)


# 6/7. Review queue exclusion and hash-less backfill are both retained.
def test_review_queue_excludes_gold_hash_and_preserves_hashless_backfill() -> None:
    sentence_hash = normalized_sentence_hash("지표는 10만 명으로 증가했다.")
    rows = build_review_queue_status(
        gold_rows=[{"row_id": "G001", "sentence_hash": sentence_hash, "is_claim_human": "TRUE"}],
        candidate_rows=[
            {"candidate_id": "same", "sentence_hash": sentence_hash, "stage": "R1"},
            {"candidate_id": "legacy", "stage": "R1"},
        ],
    )
    assert rows[0]["review_queue_action"] == "EXCLUDE_GOLD_LABELED"
    assert rows[0]["gold_labeled"] is True
    assert rows[1]["review_queue_action"] == "REQUIRES_SENTENCE_HASH_BACKFILL"
    assert rows[1]["gold_labeled"] is False


# 8. Operational coverage and Gold metrics remain different fields.
def test_controller_and_external_summaries_keep_metric_namespaces_separate() -> None:
    summary = build_controller_summary(
        mode="post_run_gold_evaluation",
        status="PARTIAL",
        operational_metrics={"stages": {"r1": {"processed": 5}}},
        gold_evaluation_metrics={"r1": {"metrics": {"true_candidate_recall": {"value": 1.0}}}},
        evaluation_status="PARTIAL",
        not_evaluable_reason_counts={"R4_DIFFERENT_COHORT": 20},
        scope={"conditional_vs_e2e": "NOT_APPLICABLE"},
        artifact_hashes={"evaluation": "b" * 64},
    )
    assert set(summary) >= {"operational_metrics", "gold_evaluation_metrics"}
    external = build_external_summary(summary)
    assert set(external) == {
        "mode",
        "status",
        "stage_counts",
        "metric_values",
        "reason_codes",
        "artifact_hashes",
        "scope",
    }
    serialized = json.dumps(external, ensure_ascii=False)
    assert "C:\\" not in serialized
    assert "article_url" not in serialized
    assert "source_sentence" not in serialized


# 9. Scheduling cannot be enabled and no retry chain is planned.
def test_controller_rejects_scheduler_and_uses_no_retry(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["scheduling"]["enabled"] = True
    with pytest.raises(AutomationConfigError, match="SCHEDULING_NOT_ALLOWED"):
        validate_controller_config(config)

    config["scheduling"]["enabled"] = False
    plan = build_execution_plan("operational_cycle", config, project_root=tmp_path, python_executable="python")
    assert plan["retry_policy"] == "NONE"


def test_r2_experiment_rejects_locked_test_split(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    config["modes"]["r2_dev_experiment"]["split"] = "test"
    with pytest.raises(AutomationConfigError, match="R2_LOCKED_TEST_SPLIT_FORBIDDEN"):
        build_execution_plan("r2_dev_experiment", config, project_root=tmp_path, python_executable="python")


def test_operational_execution_captures_child_path_and_writes_only_safe_summary(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    cycle = tmp_path / "cycle.json"
    cycle.write_text(
        json.dumps(
            {
                "cycle_status": "PIPELINE_HOLD",
                "rss": {"NEW_ARTICLES": 5, "R1_READY": 5},
                "pipeline": {
                    "stages": [
                        {
                            "stage": "r1",
                            "status": "HOLD",
                            "counts": {"processed": 5, "holds": 2},
                            "reason_counts": {"R1_HOLD": 2},
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def runner(command, cwd):
        calls.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, stdout=str(cycle), stderr="raw child output")

    run_dir, external = execute_controller_mode(
        mode="operational_cycle",
        config=config,
        controller_run_id="CTRL-OP-001",
        project_root=tmp_path,
        python_executable="python",
        runner=runner,
    )

    assert len(calls) == 1
    assert external["metric_values"] == {}
    assert external["stage_counts"]["operational"]["r1"] == {"holds": 2, "processed": 5}
    serialized = (run_dir / "external_summary.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "raw child output" not in serialized


def test_failed_r2_child_stops_before_score_tracking_or_retry(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    calls = []

    def runner(command, cwd):
        calls.append(command)
        return subprocess.CompletedProcess(command, 7, stdout="", stderr="provider raw failure")

    _, external = execute_controller_mode(
        mode="r2_dev_experiment",
        config=config,
        controller_run_id="CTRL-R2-FAIL-001",
        project_root=tmp_path,
        python_executable="python",
        runner=runner,
    )

    assert len(calls) == 1
    assert external["status"] == "FAILED"
    assert external["metric_values"] == {}
