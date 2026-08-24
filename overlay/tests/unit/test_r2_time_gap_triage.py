from core.r2_time_gap_triage import classify_absolute_time_gap


def test_triage_routes_multiple_years_or_ranges_to_review() -> None:
    result = classify_absolute_time_gap(
        "1960년에는 10명이었고 1970년에는 20명이었다."
    )
    assert result.subtype == "MULTIPLE_OR_RANGE_PERIODS_REVIEW"
    assert result.auto_fill_allowed is False


def test_triage_routes_decade_or_cohort_period_to_review() -> None:
    result = classify_absolute_time_gap(
        "1990년대 초반생이 출산 연령대에 진입했다."
    )
    assert result.subtype == "DECADE_OR_COHORT_PERIOD_REVIEW"
    assert result.auto_fill_allowed is False


def test_triage_routes_partial_month_to_target_year_review() -> None:
    result = classify_absolute_time_gap(
        "1991년 이후 4월 기준 가장 높았다."
    )
    assert result.subtype == "PARTIAL_MONTH_NEEDS_TARGET_YEAR_REVIEW"
    assert result.auto_fill_allowed is False


def test_triage_keeps_single_year_as_target_confirmation() -> None:
    result = classify_absolute_time_gap(
        "1930년 인구의 77.8%가 문맹이었다."
    )
    assert result.subtype == "SINGLE_YEAR_TARGET_CONFIRMATION"
    assert result.auto_fill_allowed is False
