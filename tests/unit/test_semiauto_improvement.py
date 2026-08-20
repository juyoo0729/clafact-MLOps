from datetime import UTC, datetime

import pytest

from core.semiauto_improvement import (
    approve_dev_experiment,
    build_failure_triage,
    build_improvement_proposal,
    evaluate_dev_candidate,
    write_immutable_json,
)


SHA256 = "a" * 64
NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _proposal():
    return build_improvement_proposal(
        {
            "artifact": "r2_dev_error_queue_v1",
            "split": "dev",
            "error_row_count": 120,
            "mismatch_slot_counts": {"time": 70, "indicator": 80, "region": 80},
        },
        source_sha256=SHA256,
        proposal_id="r2-improvement-001",
        created_at=NOW,
    )


def _scoreboard():
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
    return {
        "artifact": "r2_dev_experiment_scoreboard_v1",
        "selected_baseline": baseline,
        "rows": [baseline],
    }


def _approval():
    return approve_dev_experiment(
        _proposal(),
        _scoreboard(),
        proposal_sha256=SHA256,
        scoreboard_sha256=SHA256,
        experiment_id="exp-indicator-001",
        provider="openai",
        model="gpt-5.6-luna",
        prompt_version="r2_benchmark_prompt_v2",
        change_scope="prompt",
        change_reason_code="INDICATOR_ALIAS_V1",
        approved_at=NOW,
    )


def _candidate(*, response_rate=0.99, parse_f1=0.41, slot_accuracy=0.31):
    return {
        "benchmark": "r2_model_benchmark_v2",
        "split": "dev",
        "response_rate": response_rate,
        "run_identity": {
            "providers": ["openai"],
            "models": ["gpt-5.6-luna"],
            "prompt_versions": ["r2_benchmark_prompt_v2"],
            "comparable_single_model_run": True,
        },
        "parse_status_metrics": {"macro_f1": parse_f1},
        "all_slot_metrics": {"macro_slot_accuracy": slot_accuracy},
    }


def test_infrastructure_failure_requires_repair_not_training() -> None:
    report = build_failure_triage(
        {"reason_code": "CLAIM_PROVIDER_SECRET_MISSING"},
        source_sha256=SHA256,
    )

    assert report.status == "REPAIR_REQUIRED"
    assert report.automatic_training_allowed is False
    assert report.repair_reason_counts == {"CLAIM_PROVIDER_SECRET_MISSING": 1}
    assert report.next_action_code == "REPAIR_AND_RERUN_QUALITY_GATE"


def test_operational_quality_signal_requires_gold_review() -> None:
    report = build_failure_triage(
        {
            "pipeline": {
                "stages": [
                    {
                        "reason_counts": {
                            "R2_CLAIM_PARSE_NOT_AUTO_OK": 4,
                            "R2_BATCH_LIMIT_REACHED": 3,
                        }
                    }
                ]
            }
        },
        source_sha256=SHA256,
    )

    assert report.status == "GOLD_REVIEW_REQUIRED"
    assert report.gold_review_reason_counts == {"R2_CLAIM_PARSE_NOT_AUTO_OK": 4}
    assert report.normal_stop_reason_counts == {"R2_BATCH_LIMIT_REACHED": 3}


def test_proposal_selects_exactly_one_top_gold_error_slot_deterministically() -> None:
    proposal = _proposal()

    assert proposal.status == "AWAITING_HUMAN_APPROVAL"
    assert proposal.target_error_slot == "indicator"
    assert proposal.observed_error_count == 80
    assert proposal.automatic_code_change_allowed is False
    assert proposal.automatic_deployment_allowed is False


def test_approval_captures_baseline_and_locks_experiment_to_dev() -> None:
    approval = _approval()

    assert approval.status == "APPROVED_FOR_DEV_EXPERIMENT"
    assert approval.allowed_split == "dev"
    assert approval.locked_test_must_remain_unused is True
    assert approval.baseline_metrics.all_12_slot_macro_accuracy == 0.30
    assert approval.automatic_deployment_allowed is False


def test_improved_dev_candidate_stops_at_human_promotion_review() -> None:
    decision = evaluate_dev_candidate(
        _approval(),
        _candidate(),
        approval_sha256=SHA256,
        candidate_report_sha256=SHA256,
        evaluated_at=NOW,
    )

    assert decision.status == "PROMOTION_REVIEW_REQUIRED"
    assert decision.reason_codes == ["HUMAN_PROMOTION_APPROVAL_REQUIRED"]
    assert decision.automatic_promotion_allowed is False
    assert decision.locked_test_used is False


def test_regressed_or_low_response_candidate_is_not_promotable() -> None:
    decision = evaluate_dev_candidate(
        _approval(),
        _candidate(response_rate=0.90, parse_f1=0.39, slot_accuracy=0.29),
        approval_sha256=SHA256,
        candidate_report_sha256=SHA256,
        evaluated_at=NOW,
    )

    assert decision.status == "NOT_PROMOTABLE"
    assert decision.reason_codes == [
        "RESPONSE_RATE_BELOW_THRESHOLD",
        "ALL_12_SLOT_MACRO_ACCURACY_NOT_IMPROVED",
        "PARSE_STATUS_MACRO_F1_REGRESSED",
    ]


def test_artifacts_are_immutable_but_identical_retry_is_idempotent(tmp_path) -> None:
    proposal = _proposal()
    path = tmp_path / "proposal.json"

    write_immutable_json(path, proposal)
    write_immutable_json(path, proposal)

    changed = proposal.model_copy(update={"observed_error_count": 81})
    with pytest.raises(FileExistsError, match="SEMIAUTO_ARTIFACT_IMMUTABLE"):
        write_immutable_json(path, changed)


def test_proposal_id_cannot_escape_the_state_directory() -> None:
    with pytest.raises(ValueError, match="SEMIAUTO_PROPOSAL_ID_INVALID"):
        build_improvement_proposal(
            {
                "artifact": "r2_dev_error_queue_v1",
                "split": "dev",
                "error_row_count": 1,
                "mismatch_slot_counts": {"indicator": 1},
            },
            source_sha256=SHA256,
            proposal_id="../outside",
            created_at=NOW,
        )
