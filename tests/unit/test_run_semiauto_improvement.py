import json
import sys

from tools.run_semiauto_improvement import main


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cli_runs_proposal_approval_and_evaluation_without_auto_promotion(tmp_path, monkeypatch) -> None:
    error_summary = tmp_path / "error_summary.json"
    scoreboard = tmp_path / "scoreboard.json"
    candidate = tmp_path / "candidate_report.json"
    state_root = tmp_path / "state"
    baseline = {
        "source": "openai_dev_baseline_report.json",
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "prompt_version": "r2_benchmark_prompt_v2",
        "response_rate": 0.98,
        "parse_status_macro_f1": 0.40,
        "all_12_slot_macro_accuracy": 0.30,
        "selection_eligible": True,
    }
    _write_json(
        error_summary,
        {
            "artifact": "r2_dev_error_queue_v1",
            "split": "dev",
            "error_row_count": 10,
            "mismatch_slot_counts": {"indicator": 8, "time": 7},
        },
    )
    _write_json(
        scoreboard,
        {
            "artifact": "r2_dev_experiment_scoreboard_v1",
            "selected_baseline": baseline,
            "rows": [baseline],
        },
    )
    _write_json(
        candidate,
        {
            "benchmark": "r2_model_benchmark_v2",
            "split": "dev",
            "response_rate": 0.99,
            "run_identity": {
                "providers": ["openai"],
                "models": ["gpt-5.6-luna"],
                "prompt_versions": ["r2_benchmark_prompt_v2"],
                "comparable_single_model_run": True,
            },
            "parse_status_metrics": {"macro_f1": 0.41},
            "all_slot_metrics": {"macro_slot_accuracy": 0.31},
        },
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_semiauto_improvement.py",
            "propose",
            "--error-summary",
            str(error_summary),
            "--state-root",
            str(state_root),
            "--proposal-id",
            "proposal-001",
        ],
    )
    assert main() == 0
    proposal = state_root / "semiauto_improvements" / "proposal-001" / "proposal.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_semiauto_improvement.py",
            "approve",
            "--proposal",
            str(proposal),
            "--scoreboard",
            str(scoreboard),
            "--experiment-id",
            "experiment-001",
            "--provider",
            "openai",
            "--model",
            "gpt-5.6-luna",
            "--prompt-version",
            "r2_benchmark_prompt_v2",
            "--change-scope",
            "prompt",
            "--change-reason-code",
            "INDICATOR_ALIAS_V1",
        ],
    )
    assert main() == 0
    approval = proposal.parent / "approval.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_semiauto_improvement.py",
            "evaluate",
            "--approval",
            str(approval),
            "--candidate-report",
            str(candidate),
        ],
    )
    assert main() == 0
    decision = json.loads((proposal.parent / "decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "PROMOTION_REVIEW_REQUIRED"
    assert decision["automatic_promotion_allowed"] is False
