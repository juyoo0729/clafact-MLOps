"""Privacy-safe triage for absolute-in-sentence R2 time-slot gaps."""

from __future__ import annotations

import re
from dataclasses import dataclass


_YEAR = re.compile(r"(?:19|20)[0-9]{2}년")
_YEAR_RANGE = re.compile(
    r"(?:19|20)[0-9]{2}\s*[~～-]\s*(?:(?:19|20)[0-9]{2})?년"
)
_DECADE = re.compile(r"(?:19|20)[0-9]{2}년대")
_MONTH = re.compile(r"(?:^|[^0-9])(?:1[0-2]|[1-9])월")


@dataclass(frozen=True)
class AbsoluteTimeGapTriage:
    subtype: str
    next_action: str
    auto_fill_allowed: bool = False


def classify_absolute_time_gap(source_sentence: str) -> AbsoluteTimeGapTriage:
    """Classify period shape without deciding which mention is the target.

    Even a single explicit year may be a historical event or comparison basis,
    so every subtype remains review-only until the target period is confirmed.
    """
    years = set(_YEAR.findall(source_sentence))
    if _YEAR_RANGE.search(source_sentence) or len(years) > 1:
        return AbsoluteTimeGapTriage(
            subtype="MULTIPLE_OR_RANGE_PERIODS_REVIEW",
            next_action="SPLIT_OR_CONFIRM_TARGET_AND_COMPARISON_PERIODS",
        )
    if _DECADE.search(source_sentence):
        return AbsoluteTimeGapTriage(
            subtype="DECADE_OR_COHORT_PERIOD_REVIEW",
            next_action="CONFIRM_PERIOD_IS_TARGET_NOT_COHORT_OR_COMPARATOR",
        )
    if _MONTH.search(source_sentence):
        return AbsoluteTimeGapTriage(
            subtype="PARTIAL_MONTH_NEEDS_TARGET_YEAR_REVIEW",
            next_action="CONFIRM_MONTH_TARGET_AND_RECOVER_YEAR_FROM_CONTEXT",
        )
    if len(years) == 1:
        return AbsoluteTimeGapTriage(
            subtype="SINGLE_YEAR_TARGET_CONFIRMATION",
            next_action="CONFIRM_YEAR_IS_TARGET_NOT_EVENT_CONTEXT",
        )
    return AbsoluteTimeGapTriage(
        subtype="OTHER_TIME_GAP_REVIEW",
        next_action="REVIEW_UNCLASSIFIED_TIME_EXPRESSION",
    )
