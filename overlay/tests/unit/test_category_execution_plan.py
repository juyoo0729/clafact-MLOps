from core.category_execution_plan import plan_for_category, route_official_evidence


def test_multi_value_categories_require_the_right_evidence_shape() -> None:
    assert plan_for_category(3).required_scope == "ALL_PERIODS"
    assert plan_for_category(4).required_scope == "ALL_COMPARABLE_MEMBERS"
    assert plan_for_category(5).minimum_official_value_count == 2
    assert plan_for_category(6).minimum_official_value_count == 2
    assert plan_for_category(7).minimum_official_value_count == 2
    assert plan_for_category(8).minimum_official_value_count == 1


def test_single_value_never_completes_a_multi_value_claim() -> None:
    routed = route_official_evidence(
        category_no=6,
        official_value_count=1,
        registry_reason="",
        excluded=False,
        article_asof_checked=False,
        target_value_role_checked=False,
    )
    assert routed.execution_status == "PARTIAL_SUCCESS"
    assert routed.reason_code == "ADDITIONAL_EVIDENCE_VALUES_REQUIRED"
    assert routed.stop_stage == "CALCULATION_EVIDENCE_PLAN"


def test_direct_value_still_requires_article_asof_and_target_role() -> None:
    routed = route_official_evidence(
        category_no=8,
        official_value_count=1,
        registry_reason="",
        excluded=False,
        article_asof_checked=False,
        target_value_role_checked=False,
    )
    assert routed.execution_status == "PARTIAL_SUCCESS"
    assert routed.reason_code == "ARTICLE_ASOF_AND_TARGET_ROLE_UNCHECKED"
    assert routed.stop_stage == "ARTICLE_ASOF_CHECK"


def test_preverification_exclusion_is_not_reintroduced() -> None:
    routed = route_official_evidence(
        category_no=8,
        official_value_count=1,
        registry_reason="",
        excluded=True,
        article_asof_checked=False,
        target_value_role_checked=False,
    )
    assert routed.execution_status == "EXCLUDED"
    assert routed.reason_code == "PRE_VERIFICATION_RECLASSIFIED"
