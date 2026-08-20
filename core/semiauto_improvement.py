"""Fail-closed state transitions for CLAFACT semi-automatic improvement."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_SAFE_CODE = re.compile(r"[A-Z0-9_]{2,128}")
_SEMANTIC_SLOTS = {
    "calculation",
    "comparison",
    "condition",
    "dimension",
    "frequency",
    "indicator",
    "population",
    "region",
    "source_hint",
    "time",
    "unit",
    "value",
}

_REPAIR_REASONS = {
    "CLAIM_PROVIDER_SECRET_MISSING",
    "CLAIM_PROVIDER_UNSUPPORTED",
    "PYTEST_FAILED",
    "PYTEST_SUMMARY_UNPARSEABLE",
    "PYTEST_TIMEOUT",
    "PYTHON_3_12_REQUIRED",
    "REQUIRED_SECRET_MISSING",
    "R2_STRUCTURED_EXTRACTOR_FAILED",
    "WORKSPACE_NOT_FOUND",
}
_GOLD_REVIEW_REASONS = {
    "CLAIM_SPLIT_CONTEXT_UNRESOLVED",
    "R1_NUMERIC_CANDIDATE_NOT_FOUND",
    "R2_CLAIM_PARSE_NOT_AUTO_OK",
}
_NORMAL_STOP_REASONS = {
    "NO_R1_READY",
    "R3_NO_INPUT_RECORDS",
    "R4_NO_INPUT_RECORDS",
    "RSS_COLLECTION_NOT_DUE",
}


class MetricSnapshot(BaseModel):
    """Comparable count-free metrics captured at approval or evaluation time."""

    model_config = ConfigDict(frozen=True)

    response_rate: float = Field(ge=0.0, le=1.0)
    parse_status_macro_f1: float = Field(ge=0.0, le=1.0)
    all_12_slot_macro_accuracy: float = Field(ge=0.0, le=1.0)


class FailureTriageReport(BaseModel):
    """Aggregate-only failure classification; never authorizes learning."""

    model_config = ConfigDict(frozen=True)

    artifact: Literal["clafact_semiauto_failure_triage_v1"] = "clafact_semiauto_failure_triage_v1"
    status: Literal["REPAIR_REQUIRED", "GOLD_REVIEW_REQUIRED", "HUMAN_TRIAGE_REQUIRED", "NO_ACTION"]
    source_sha256: str
    reason_counts: dict[str, int]
    repair_reason_counts: dict[str, int]
    gold_review_reason_counts: dict[str, int]
    normal_stop_reason_counts: dict[str, int]
    unknown_reason_counts: dict[str, int]
    automatic_training_allowed: Literal[False] = False
    next_action_code: str


class ImprovementProposal(BaseModel):
    """One text-free Gold error signal waiting for an explicit human approval."""

    model_config = ConfigDict(frozen=True)

    artifact: Literal["clafact_semiauto_improvement_proposal_v1"] = "clafact_semiauto_improvement_proposal_v1"
    proposal_id: str
    status: Literal["AWAITING_HUMAN_APPROVAL"] = "AWAITING_HUMAN_APPROVAL"
    source_error_summary_sha256: str
    required_split: Literal["dev"] = "dev"
    target_stage: Literal["r2"] = "r2"
    target_error_slot: str
    observed_error_count: int = Field(gt=0)
    total_error_rows: int = Field(gt=0)
    automatic_code_change_allowed: Literal[False] = False
    automatic_training_allowed: Literal[False] = False
    automatic_deployment_allowed: Literal[False] = False
    created_at: datetime

    @field_validator("proposal_id")
    @classmethod
    def _proposal_id_is_safe(cls, value: str) -> str:
        return _validated_id(value, "SEMIAUTO_PROPOSAL_ID_INVALID")

    @field_validator("source_error_summary_sha256")
    @classmethod
    def _source_hash_is_valid(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("target_error_slot")
    @classmethod
    def _slot_is_known(cls, value: str) -> str:
        if value not in _SEMANTIC_SLOTS:
            raise ValueError("SEMIAUTO_ERROR_SLOT_INVALID")
        return value


class ApprovalRecord(BaseModel):
    """Explicit authorization for one dev-only experiment, not deployment."""

    model_config = ConfigDict(frozen=True)

    artifact: Literal["clafact_semiauto_experiment_approval_v1"] = "clafact_semiauto_experiment_approval_v1"
    proposal_id: str
    proposal_sha256: str
    scoreboard_sha256: str
    experiment_id: str
    status: Literal["APPROVED_FOR_DEV_EXPERIMENT"] = "APPROVED_FOR_DEV_EXPERIMENT"
    provider: str
    model: str
    prompt_version: str
    change_scope: Literal["prompt", "rule", "postprocess"]
    change_reason_code: str
    baseline_source: str
    baseline_provider: str
    baseline_model: str
    baseline_prompt_version: str
    baseline_metrics: MetricSnapshot
    allowed_split: Literal["dev"] = "dev"
    locked_test_must_remain_unused: Literal[True] = True
    automatic_deployment_allowed: Literal[False] = False
    approved_at: datetime

    @field_validator(
        "proposal_id",
        "experiment_id",
        "provider",
        "prompt_version",
        "baseline_provider",
        "baseline_prompt_version",
    )
    @classmethod
    def _ids_are_safe(cls, value: str) -> str:
        return _validated_id(value, "SEMIAUTO_IDENTIFIER_INVALID")

    @field_validator("model", "baseline_model")
    @classmethod
    def _model_ids_are_safe(cls, value: str) -> str:
        if not _SAFE_MODEL_ID.fullmatch(value) or ".." in value.split("/"):
            raise ValueError("SEMIAUTO_MODEL_IDENTIFIER_INVALID")
        return value

    @field_validator("change_reason_code")
    @classmethod
    def _reason_code_is_safe(cls, value: str) -> str:
        if not _SAFE_CODE.fullmatch(value):
            raise ValueError("SEMIAUTO_CHANGE_REASON_CODE_INVALID")
        return value

    @field_validator("proposal_sha256", "scoreboard_sha256")
    @classmethod
    def _source_hashes_are_valid(cls, value: str) -> str:
        return _validated_sha256(value)

    @field_validator("baseline_source")
    @classmethod
    def _baseline_source_is_a_name(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or Path(value).name != value:
            raise ValueError("SEMIAUTO_BASELINE_SOURCE_INVALID")
        return value


class EvaluationDecision(BaseModel):
    """Dev comparison result that still requires human promotion approval."""

    model_config = ConfigDict(frozen=True)

    artifact: Literal["clafact_semiauto_evaluation_decision_v1"] = "clafact_semiauto_evaluation_decision_v1"
    proposal_id: str
    experiment_id: str
    approval_sha256: str
    status: Literal["PROMOTION_REVIEW_REQUIRED", "NOT_PROMOTABLE"]
    candidate_report_sha256: str
    baseline_metrics: MetricSnapshot
    candidate_metrics: MetricSnapshot
    minimum_response_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    reason_codes: list[str]
    evaluated_split: Literal["dev"] = "dev"
    locked_test_used: Literal[False] = False
    automatic_promotion_allowed: Literal[False] = False
    evaluated_at: datetime

    @field_validator("approval_sha256", "candidate_report_sha256")
    @classmethod
    def _source_hashes_are_valid(cls, value: str) -> str:
        return _validated_sha256(value)


def build_failure_triage(payload: Mapping[str, Any], *, source_sha256: str) -> FailureTriageReport:
    """Classify aggregate failures without turning operational data into training data."""
    reasons = _extract_reason_counts(payload)
    repair = {key: value for key, value in reasons.items() if key in _REPAIR_REASONS}
    gold_review = {key: value for key, value in reasons.items() if key in _GOLD_REVIEW_REASONS}
    normal = {
        key: value
        for key, value in reasons.items()
        if key in _NORMAL_STOP_REASONS or key.endswith("_BATCH_LIMIT_REACHED")
    }
    classified = set(repair) | set(gold_review) | set(normal)
    unknown = {key: value for key, value in reasons.items() if key not in classified}

    if repair:
        status = "REPAIR_REQUIRED"
        next_action = "REPAIR_AND_RERUN_QUALITY_GATE"
    elif gold_review:
        status = "GOLD_REVIEW_REQUIRED"
        next_action = "LABEL_OR_CONFIRM_GOLD_BEFORE_EXPERIMENT"
    elif unknown:
        status = "HUMAN_TRIAGE_REQUIRED"
        next_action = "CLASSIFY_REASON_BEFORE_EXPERIMENT"
    else:
        status = "NO_ACTION"
        next_action = "WAIT_FOR_NEXT_APPROVED_CYCLE"

    return FailureTriageReport(
        status=status,
        source_sha256=_validated_sha256(source_sha256),
        reason_counts=dict(sorted(reasons.items())),
        repair_reason_counts=dict(sorted(repair.items())),
        gold_review_reason_counts=dict(sorted(gold_review.items())),
        normal_stop_reason_counts=dict(sorted(normal.items())),
        unknown_reason_counts=dict(sorted(unknown.items())),
        next_action_code=next_action,
    )


def build_improvement_proposal(
    error_summary: Mapping[str, Any],
    *,
    source_sha256: str,
    proposal_id: str,
    created_at: datetime | None = None,
) -> ImprovementProposal:
    """Select exactly one top R2 dev Gold mismatch slot for human review."""
    if error_summary.get("artifact") != "r2_dev_error_queue_v1" or error_summary.get("split") != "dev":
        raise ValueError("SEMIAUTO_DEV_GOLD_ERROR_SUMMARY_REQUIRED")
    counts = error_summary.get("mismatch_slot_counts")
    if not isinstance(counts, Mapping):
        raise ValueError("SEMIAUTO_MISMATCH_SLOT_COUNTS_REQUIRED")
    valid_counts = {
        str(slot): count
        for slot, count in counts.items()
        if slot in _SEMANTIC_SLOTS and _is_positive_int(count)
    }
    if not valid_counts:
        raise ValueError("SEMIAUTO_NO_GOLD_ERROR_SIGNAL")
    target_slot, observed_count = sorted(valid_counts.items(), key=lambda item: (-item[1], item[0]))[0]
    total_rows = error_summary.get("error_row_count")
    if not _is_positive_int(total_rows):
        raise ValueError("SEMIAUTO_ERROR_ROW_COUNT_INVALID")
    return ImprovementProposal(
        proposal_id=proposal_id,
        source_error_summary_sha256=_validated_sha256(source_sha256),
        target_error_slot=target_slot,
        observed_error_count=observed_count,
        total_error_rows=total_rows,
        created_at=created_at or datetime.now(UTC),
    )


def approve_dev_experiment(
    proposal: ImprovementProposal,
    scoreboard: Mapping[str, Any],
    *,
    proposal_sha256: str,
    scoreboard_sha256: str,
    experiment_id: str,
    provider: str,
    model: str,
    prompt_version: str,
    change_scope: Literal["prompt", "rule", "postprocess"],
    change_reason_code: str,
    baseline_source: str | None = None,
    approved_at: datetime | None = None,
) -> ApprovalRecord:
    """Capture explicit approval and an immutable comparable baseline snapshot."""
    if scoreboard.get("artifact") != "r2_dev_experiment_scoreboard_v1":
        raise ValueError("SEMIAUTO_R2_DEV_SCOREBOARD_REQUIRED")
    baseline = _select_baseline(scoreboard, baseline_source)
    if baseline.get("selection_eligible") is not True:
        raise ValueError("SEMIAUTO_BASELINE_NOT_ELIGIBLE")
    if baseline.get("provider") != provider or baseline.get("model") != model:
        raise ValueError("SEMIAUTO_BASELINE_TARGET_MISMATCH")
    for field in ("source", "provider", "model", "prompt_version"):
        if not isinstance(baseline.get(field), str):
            raise ValueError("SEMIAUTO_BASELINE_IDENTITY_INVALID")
    return ApprovalRecord(
        proposal_id=proposal.proposal_id,
        proposal_sha256=_validated_sha256(proposal_sha256),
        scoreboard_sha256=_validated_sha256(scoreboard_sha256),
        experiment_id=experiment_id,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        change_scope=change_scope,
        change_reason_code=change_reason_code,
        baseline_source=baseline["source"],
        baseline_provider=baseline["provider"],
        baseline_model=baseline["model"],
        baseline_prompt_version=baseline["prompt_version"],
        baseline_metrics=_metrics_from_scoreboard_row(baseline),
        approved_at=approved_at or datetime.now(UTC),
    )


def evaluate_dev_candidate(
    approval: ApprovalRecord,
    candidate_report: Mapping[str, Any],
    *,
    approval_sha256: str,
    candidate_report_sha256: str,
    evaluated_at: datetime | None = None,
) -> EvaluationDecision:
    """Compare one approved dev result and stop before any automatic promotion."""
    if candidate_report.get("benchmark") != "r2_model_benchmark_v2" or candidate_report.get("split") != "dev":
        raise ValueError("SEMIAUTO_CANDIDATE_DEV_REPORT_REQUIRED")
    identity = candidate_report.get("run_identity")
    if not isinstance(identity, Mapping) or identity.get("comparable_single_model_run") is not True:
        raise ValueError("SEMIAUTO_CANDIDATE_NOT_COMPARABLE")
    if _single(identity.get("providers")) != approval.provider:
        raise ValueError("SEMIAUTO_CANDIDATE_PROVIDER_MISMATCH")
    if _single(identity.get("models")) != approval.model:
        raise ValueError("SEMIAUTO_CANDIDATE_MODEL_MISMATCH")
    if _single(identity.get("prompt_versions")) != approval.prompt_version:
        raise ValueError("SEMIAUTO_CANDIDATE_PROMPT_VERSION_MISMATCH")

    candidate = _metrics_from_candidate_report(candidate_report)
    reasons: list[str] = []
    if candidate.response_rate < 0.95:
        reasons.append("RESPONSE_RATE_BELOW_THRESHOLD")
    if candidate.all_12_slot_macro_accuracy <= approval.baseline_metrics.all_12_slot_macro_accuracy:
        reasons.append("ALL_12_SLOT_MACRO_ACCURACY_NOT_IMPROVED")
    if candidate.parse_status_macro_f1 < approval.baseline_metrics.parse_status_macro_f1:
        reasons.append("PARSE_STATUS_MACRO_F1_REGRESSED")
    status = "PROMOTION_REVIEW_REQUIRED" if not reasons else "NOT_PROMOTABLE"
    if not reasons:
        reasons.append("HUMAN_PROMOTION_APPROVAL_REQUIRED")
    return EvaluationDecision(
        proposal_id=approval.proposal_id,
        experiment_id=approval.experiment_id,
        approval_sha256=_validated_sha256(approval_sha256),
        status=status,
        candidate_report_sha256=_validated_sha256(candidate_report_sha256),
        baseline_metrics=approval.baseline_metrics,
        candidate_metrics=candidate,
        reason_codes=reasons,
        evaluated_at=evaluated_at or datetime.now(UTC),
    )


def write_immutable_json(path: Path, artifact: BaseModel) -> Path:
    """Write a versioned state artifact once; identical retries are idempotent."""
    payload = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") == payload:
            return path
        raise FileExistsError("SEMIAUTO_ARTIFACT_IMMUTABLE")
    with path.open("x", encoding="utf-8") as handle:
        handle.write(payload)
    return path


def _extract_reason_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    root_reason = payload.get("reason_code")
    if isinstance(root_reason, str) and _SAFE_CODE.fullmatch(root_reason):
        reasons[root_reason] = reasons.get(root_reason, 0) + 1
    pipeline = payload.get("pipeline")
    stages = pipeline.get("stages") if isinstance(pipeline, Mapping) else None
    if isinstance(stages, list):
        for stage in stages:
            if not isinstance(stage, Mapping):
                continue
            counts = stage.get("reason_counts")
            if not isinstance(counts, Mapping):
                continue
            for reason, count in counts.items():
                if isinstance(reason, str) and _SAFE_CODE.fullmatch(reason) and _is_positive_int(count):
                    reasons[reason] = reasons.get(reason, 0) + count
    return reasons


def _select_baseline(scoreboard: Mapping[str, Any], source: str | None) -> Mapping[str, Any]:
    selected = scoreboard.get("selected_baseline")
    if source is None:
        if not isinstance(selected, Mapping):
            raise ValueError("SEMIAUTO_BASELINE_REQUIRED")
        return selected
    rows = scoreboard.get("rows")
    if not isinstance(rows, list):
        raise ValueError("SEMIAUTO_SCOREBOARD_ROWS_REQUIRED")
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("source") == source]
    if len(matches) != 1:
        raise ValueError("SEMIAUTO_BASELINE_SOURCE_NOT_FOUND")
    return matches[0]


def _metrics_from_scoreboard_row(row: Mapping[str, Any]) -> MetricSnapshot:
    return MetricSnapshot(
        response_rate=_metric(row.get("response_rate"), "SEMIAUTO_BASELINE_RESPONSE_RATE_INVALID"),
        parse_status_macro_f1=_metric(
            row.get("parse_status_macro_f1"), "SEMIAUTO_BASELINE_PARSE_STATUS_MACRO_F1_INVALID"
        ),
        all_12_slot_macro_accuracy=_metric(
            row.get("all_12_slot_macro_accuracy"), "SEMIAUTO_BASELINE_SLOT_ACCURACY_INVALID"
        ),
    )


def _metrics_from_candidate_report(report: Mapping[str, Any]) -> MetricSnapshot:
    parse = report.get("parse_status_metrics")
    slots = report.get("all_slot_metrics")
    if not isinstance(parse, Mapping) or not isinstance(slots, Mapping):
        raise ValueError("SEMIAUTO_CANDIDATE_METRICS_REQUIRED")
    return MetricSnapshot(
        response_rate=_metric(report.get("response_rate"), "SEMIAUTO_CANDIDATE_RESPONSE_RATE_INVALID"),
        parse_status_macro_f1=_metric(parse.get("macro_f1"), "SEMIAUTO_CANDIDATE_PARSE_STATUS_MACRO_F1_INVALID"),
        all_12_slot_macro_accuracy=_metric(
            slots.get("macro_slot_accuracy"), "SEMIAUTO_CANDIDATE_SLOT_ACCURACY_INVALID"
        ),
    )


def _metric(value: object, error_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error_code)
    metric = float(value)
    if not 0.0 <= metric <= 1.0:
        raise ValueError(error_code)
    return metric


def _single(value: object) -> str | None:
    return value[0] if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str) else None


def _validated_id(value: str, error_code: str) -> str:
    if not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(error_code)
    return value


def _validated_sha256(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("SEMIAUTO_SOURCE_SHA256_INVALID")
    return value


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
