"""Reclassify frozen R2 Gold rows into actionable 12-slot work queues."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


R2_SCORABLE_REQUIRED_SLOTS = (
    "indicator",
    "value",
    "unit",
    "time",
    "frequency",
    "calculation",
)
R2_CONTEXT_ENRICHABLE_SLOTS = frozenset({"time", "frequency"})


def replay_r2_flexible_gold(
    gold_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return one actionable route per frozen R2 Gold Claim.

    The frozen dataset predates ``target_value_role``. This replay diagnoses
    the 12 semantic slots only and never bypasses the fresh-runtime role gate.
    """
    source_rows = list(gold_rows)
    seen_claim_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        claim_id = _required_text(source.get("claim_id"), "claim_id")
        if claim_id in seen_claim_ids:
            raise ValueError(f"Duplicate claim_id in R2 Gold: {claim_id}")
        seen_claim_ids.add(claim_id)
        slots = source.get("expected_claim_slots")
        if not isinstance(slots, dict):
            raise ValueError(f"R2 Gold row {claim_id} has no expected_claim_slots object")
        original_status = _required_text(source.get("expected_parse_status"), "expected_parse_status")
        if original_status not in {"AUTO_OK", "HOLD", "HUMAN_REVIEW"}:
            raise ValueError(f"Unsupported expected_parse_status for {claim_id}: {original_status}")
        missing_slots = tuple(
            slot for slot in R2_SCORABLE_REQUIRED_SLOTS if _is_missing(slots.get(slot))
        )
        route, route_reason, next_action = _route(original_status, missing_slots)
        rows.append(
            {
                "claim_id": claim_id,
                "article_id": _text(source.get("article_id")),
                "split": _text(source.get("split")),
                "gold_time_source": _text(source.get("gold_time_source")),
                "original_parse_status": original_status,
                "missing_required_slots": list(missing_slots),
                "flexible_route": route,
                "route_reason": route_reason,
                "next_action": next_action,
                "target_value_role_status": "MISSING_IN_FROZEN_R2_GOLD",
                "runtime_r3_admission": (
                    "BLOCKED_UNTIL_TARGET_VALUE_ROLE"
                    if route == "R2_SLOT_READY"
                    else "NOT_READY"
                ),
            }
        )
    return rows, summarize_r2_flexible_gold(rows)


def summarize_r2_flexible_gold(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    row_list = list(rows)
    return {
        "artifact": "r2_12slot_flexible_gold_replay_v1",
        "purpose": "Actionable R2 12-slot queue replay over frozen Gold.",
        "evaluation_boundary": (
            "NOT_AN_ACCURACY_SCORE_OR_LIVE_MODEL_RUN: expected Gold slots are replay inputs. "
            "The frozen dataset has no target_value_role and cannot authorize R3/KOSIS/Verdict AUTO."
        ),
        "input_claim_count": len(row_list),
        "original_parse_status_counts": _counts(row_list, "original_parse_status"),
        "flexible_route_counts": _counts(row_list, "flexible_route"),
        "missing_required_slot_counts": _missing_counts(row_list),
        "route_by_split": _cross_counts(row_list, "split", "flexible_route"),
        "route_by_gold_time_source": _cross_counts(row_list, "gold_time_source", "flexible_route"),
        "runtime_r3_admission_counts": _counts(row_list, "runtime_r3_admission"),
    }


def _route(original_status: str, missing_slots: tuple[str, ...]) -> tuple[str, str, str]:
    missing = frozenset(missing_slots)
    if original_status == "HUMAN_REVIEW":
        return "HUMAN_REVIEW", "GOLD_HUMAN_REVIEW_PRESERVED", "HUMAN_CONFIRM_ATOMICITY_AND_SEMANTICS"
    if original_status == "AUTO_OK":
        if missing:
            return "HOLD", "GOLD_AUTO_REQUIRED_SLOT_INCONSISTENT", "AUDIT_GOLD_STATUS_AND_REQUIRED_SLOTS"
        return (
            "R2_SLOT_READY",
            "ALL_R2_SCORABLE_REQUIRED_SLOTS_PRESENT",
            "ADD_TARGET_VALUE_ROLE_IN_FRESH_RUNTIME_BEFORE_R3",
        )
    if missing and missing <= R2_CONTEXT_ENRICHABLE_SLOTS:
        return (
            "ENRICHMENT_REQUIRED",
            "R2_SLOT_ENRICHMENT_REQUIRED",
            "RECOVER_TIME_FREQUENCY_FROM_ARTICLE_CONTEXT",
        )
    if missing:
        return "HOLD", "R2_CORE_SLOT_MISSING", "REEXTRACT_CORE_SLOTS_FROM_ATOMIC_CLAIM"
    return (
        "HOLD",
        "SEMANTIC_HOLD_REASON_NOT_RECORDED_IN_FROZEN_GOLD",
        "RECONSTRUCT_SEMANTIC_HOLD_REASON",
    )


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _required_text(value: Any, field: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"R2 Gold row has no {field}")
    return text


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _counts(rows: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field) or "(none)") for row in rows).items()))


def _missing_counts(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(",".join(row["missing_required_slots"]) or "NONE" for row in rows)
    return dict(sorted(counts.items()))


def _cross_counts(
    rows: Iterable[dict[str, Any]],
    group_field: str,
    count_field: str,
) -> dict[str, dict[str, int]]:
    row_list = list(rows)
    return {
        group: _counts(
            [row for row in row_list if str(row.get(group_field) or "(none)") == group],
            count_field,
        )
        for group in sorted({str(row.get(group_field) or "(none)") for row in row_list})
    }
