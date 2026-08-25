"""Evidence requirements and fail-closed routing for Claim categories 1-8."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CategoryEvidencePlan:
    category_no: int
    category_label: str
    evidence_plan: str
    required_scope: str
    minimum_official_value_count: int
    calculation_method: str


@dataclass(frozen=True, slots=True)
class EvidenceRoute:
    execution_status: str
    stop_stage: str
    reason_code: str
    next_action: str


_PLANS = {
    1: CategoryEvidencePlan(
        1, "문맥 보완", "CONTEXT_COMPLETION_THEN_12SLOT", "ONE_CONTEXTUAL_ATOMIC_CLAIM", 1,
        "R2_CONTEXT_REPARSE",
    ),
    2: CategoryEvidencePlan(
        2, "복수 Claim 분리", "ATOMIC_CHILDREN_THEN_12SLOT", "ALL_VALIDATED_ATOMIC_CHILDREN", 1,
        "R2_ATOMIC_SPLIT",
    ),
    3: CategoryEvidencePlan(
        3, "최고·최저 기록", "FULL_HISTORY_EXTREMA", "ALL_PERIODS", 2,
        "PYTHON_MIN_MAX_WITH_PERIOD",
    ),
    4: CategoryEvidencePlan(
        4, "순위", "FULL_PEER_RANK", "ALL_COMPARABLE_MEMBERS", 2,
        "PYTHON_COMPETITION_RANK",
    ),
    5: CategoryEvidencePlan(
        5, "비중·구성비", "NUMERATOR_DENOMINATOR", "NUMERATOR_AND_DENOMINATOR", 2,
        "PYTHON_SHARE_OR_RATIO",
    ),
    6: CategoryEvidencePlan(
        6, "증감량", "CURRENT_AND_COMPARISON", "TWO_PERIOD_VALUES", 2,
        "PYTHON_DIFFERENCE",
    ),
    7: CategoryEvidencePlan(
        7, "증감률", "CURRENT_AND_COMPARISON", "TWO_PERIOD_VALUES", 2,
        "PYTHON_GROWTH_RATE",
    ),
    8: CategoryEvidencePlan(
        8, "직접값", "ONE_OFFICIAL_CELL", "ONE_EXACT_EVIDENCE_CELL", 1,
        "PYTHON_DIRECT_VALUE_COMPARE",
    ),
}


def plan_for_category(category_no: int) -> CategoryEvidencePlan:
    try:
        return _PLANS[category_no]
    except KeyError as exc:
        raise ValueError(f"unsupported category number: {category_no}") from exc


def route_official_evidence(
    *,
    category_no: int,
    official_value_count: int,
    registry_reason: str,
    excluded: bool,
    article_asof_checked: bool,
    target_value_role_checked: bool,
) -> EvidenceRoute:
    """Route stored official evidence without turning coverage into accuracy."""
    if category_no not in range(3, 9):
        raise ValueError("official evidence routing applies only to categories 3-8")
    if official_value_count < 0:
        raise ValueError("official_value_count must be non-negative")
    if excluded:
        return EvidenceRoute(
            "EXCLUDED", "PRE_VERIFICATION", "PRE_VERIFICATION_RECLASSIFIED",
            "REVIEW_RECLASSIFICATION_ONLY",
        )
    plan = plan_for_category(category_no)
    if official_value_count == 0:
        reason = registry_reason.strip() or "OFFICIAL_VALUE_NOT_FETCHED"
        return EvidenceRoute("HOLD", _stop_stage_for_reason(reason), reason, _next_action_for_reason(reason))
    if category_no in range(3, 8) and official_value_count < plan.minimum_official_value_count:
        return EvidenceRoute(
            "PARTIAL_SUCCESS",
            "CALCULATION_EVIDENCE_PLAN",
            "ADDITIONAL_EVIDENCE_VALUES_REQUIRED",
            f"FETCH_{plan.required_scope}_WITH_SAME_COORDINATE_CONDITIONS",
        )
    if category_no in range(3, 8) and plan.required_scope.startswith("ALL_"):
        return EvidenceRoute(
            "PARTIAL_SUCCESS",
            "CALCULATION_EVIDENCE_PLAN",
            "FULL_EVIDENCE_SCOPE_NOT_CONFIRMED",
            f"FETCH_{plan.required_scope}_WITH_SAME_COORDINATE_CONDITIONS",
        )
    if not article_asof_checked and not target_value_role_checked:
        return EvidenceRoute(
            "PARTIAL_SUCCESS", "ARTICLE_ASOF_CHECK", "ARTICLE_ASOF_AND_TARGET_ROLE_UNCHECKED",
            "VERIFY_ARTICLE_ASOF_PUBLICATION_AND_TARGET_VALUE_ROLE",
        )
    if not article_asof_checked:
        return EvidenceRoute(
            "PARTIAL_SUCCESS", "ARTICLE_ASOF_CHECK", "ARTICLE_ASOF_UNCHECKED",
            "VERIFY_ARTICLE_ASOF_PUBLICATION",
        )
    if not target_value_role_checked:
        return EvidenceRoute(
            "PARTIAL_SUCCESS", "TARGET_VALUE_ROLE_CHECK", "TARGET_VALUE_ROLE_UNCHECKED",
            "VERIFY_TARGET_VALUE_ROLE",
        )
    return EvidenceRoute(
        "READY_FOR_VERDICT", "VERDICT", "OFFICIAL_EVIDENCE_PRECONDITIONS_READY",
        "RUN_DETERMINISTIC_CALCULATION_AND_GOLD_LINKED_EVALUATION",
    )


def _stop_stage_for_reason(reason: str) -> str:
    if reason in {"CONCEPT_REGISTRY_NOT_READY", "NO_REGISTRY_CANDIDATE"}:
        return "OFFICIAL_CATALOG_SEARCH"
    if reason in {"KOSIS_VALUE_INVALID_RESPONSE", "OFFICIAL_VALUE_NOT_FETCHED"}:
        return "OFFICIAL_VALUE_API"
    if reason == "TIME_NOT_AVAILABLE":
        return "CLAIM_PERIOD_RESOLUTION"
    return "EVIDENCE_CELL"


def _next_action_for_reason(reason: str) -> str:
    if reason in {"CONCEPT_REGISTRY_NOT_READY", "NO_REGISTRY_CANDIDATE"}:
        return "REGISTER_OR_RETRIEVE_OFFICIAL_CONCEPT_CANDIDATES"
    if reason == "TIME_NOT_AVAILABLE":
        return "RESOLVE_TARGET_PERIOD_FROM_ARTICLE_CONTEXT"
    if reason == "KOSIS_VALUE_INVALID_RESPONSE":
        return "REVIEW_STORED_VALUE_API_FAILURE_WITHOUT_AUTOMATIC_RETRY"
    return "RESOLVE_EXACT_EVIDENCE_CELL_COORDINATE"
