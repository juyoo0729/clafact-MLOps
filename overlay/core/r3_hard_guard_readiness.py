"""Read-only structural Hard Guard replay for already-attached KOSIS candidates.

The replay intentionally omits article sentences and does not select a table.
It measures which normalized candidates survive the current structural Guard
and which reject codes require slot or catalog remediation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from core.hard_guard import apply_hard_guard
from schemas.candidate import KosisCandidateSchema
from schemas.claim import ClaimSchema
from schemas.period_availability import PeriodAvailabilitySnapshot


GUARD_SCOPE = "PARTIAL_NO_ARTICLE_CONTEXT_NO_TARGET_VALUE_ROLE"
ACCURACY_STATUS = "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
MANDATORY_NEXT_GATE = "SEMANTIC_MATCH -> EVIDENCE_CELL -> OFFICIAL_VALUE"


def evaluate_hard_guard_readiness(
    candidate_rows: Iterable[Mapping[str, object]],
    *,
    claim_slots_by_id: Mapping[str, Mapping[str, object]],
    catalog_by_table_id: Mapping[str, KosisCandidateSchema],
    period_availability: PeriodAvailabilitySnapshot | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Evaluate candidate structure without granting R3 table-selection AUTO."""
    output: list[dict[str, str]] = []
    global_rejects: Counter[str] = Counter()
    for candidate_row in candidate_rows:
        claim_id = _text(candidate_row.get("claim_id"))
        source_slots = claim_slots_by_id.get(claim_id)
        candidate_ids = _split(candidate_row.get("candidate_tbl_ids"))
        normalized = [
            catalog_by_table_id[table_id]
            for table_id in candidate_ids
            if table_id in catalog_by_table_id
        ]
        rejects: Counter[str] = Counter()
        survivors: list[str] = []
        schema_error = ""

        if source_slots is None:
            route = "HOLD_CLAIM_SLOTS_NOT_JOINED"
        elif not normalized:
            route = "HOLD_NO_NORMALIZED_CATALOG_CANDIDATE"
        else:
            try:
                claim = _claim_from_slots(claim_id, source_slots)
            except (TypeError, ValueError) as error:
                route = "HOLD_CLAIM_SLOTS_INVALID"
                schema_error = type(error).__name__
            else:
                for candidate in normalized:
                    result = apply_hard_guard(
                        claim,
                        candidate,
                        period_availability=period_availability,
                    )
                    if result.passed:
                        survivors.append(candidate.tbl_id)
                    else:
                        rejects.update(result.reject_codes)
                global_rejects.update(rejects)
                route = (
                    "STRUCTURAL_GUARD_SURVIVOR"
                    if survivors
                    else "HOLD_ALL_NORMALIZED_CANDIDATES_REJECTED"
                )

        output.append(
            {
                "claim_id": claim_id,
                "split": _text(candidate_row.get("split")),
                "guard_route_status": route,
                "attached_candidate_count": str(len(candidate_ids)),
                "normalized_candidate_count": str(len(normalized)),
                "structural_pass_count": str(len(survivors)),
                "surviving_candidate_tbl_ids": " | ".join(survivors),
                "reject_code_counts": _format_counts(rejects),
                "claim_schema_error": schema_error,
                "source_context_status": "NOT_READ_ARTICLE_CONTEXT_UNCHECKED",
                "target_value_role_status": _text(
                    source_slots.get("target_value_role_status")
                    if source_slots is not None
                    else ""
                ),
                "selection_status": "HOLD_NO_TABLE_SELECTED",
                "next_system_gate": MANDATORY_NEXT_GATE,
            }
        )

    route_counts = Counter(row["guard_route_status"] for row in output)
    return output, {
        "artifact": "r3_hard_guard_readiness_v1",
        "input_claim_count": len(output),
        "claim_slot_join_count": sum(
            row["guard_route_status"] != "HOLD_CLAIM_SLOTS_NOT_JOINED"
            for row in output
        ),
        "normalized_catalog_candidate_claim_count": sum(
            int(row["normalized_candidate_count"]) > 0 for row in output
        ),
        "structural_guard_survivor_claim_count": route_counts.get(
            "STRUCTURAL_GUARD_SURVIVOR", 0
        ),
        "all_normalized_candidates_rejected_claim_count": route_counts.get(
            "HOLD_ALL_NORMALIZED_CANDIDATES_REJECTED", 0
        ),
        "no_normalized_catalog_candidate_claim_count": route_counts.get(
            "HOLD_NO_NORMALIZED_CATALOG_CANDIDATE", 0
        ),
        "guard_route_status_counts": dict(sorted(route_counts.items())),
        "guard_reject_code_counts": dict(sorted(global_rejects.items())),
        "guard_scope": GUARD_SCOPE,
        "source_context_status": "NOT_READ_ARTICLE_CONTEXT_UNCHECKED",
        "target_value_role_permission": "NOT_GRANTED",
        "table_selection_count": 0,
        "selection_status": "HOLD_NO_TABLE_SELECTED",
        "accuracy_status": ACCURACY_STATUS,
        "interpretation_boundary": (
            "A structural Guard survivor is ready for semantic and Evidence Cell "
            "review; it is not an expected-table match or R3 accuracy result."
        ),
    }


def _claim_from_slots(claim_id: str, slots: Mapping[str, object]) -> ClaimSchema:
    return ClaimSchema(
        claim_id=claim_id,
        source_sentence="",
        indicator=_optional_text(slots.get("indicator")),
        value=_optional_float(slots.get("value")),
        target_value_role=None,
        unit=_optional_text(slots.get("unit")),
        time=_optional_text(slots.get("time")),
        frequency=_optional_text(slots.get("frequency")),
        region=_optional_text(slots.get("region")),
        population=_optional_text(slots.get("population")),
        dimension=_optional_mapping(slots.get("dimension")),
        comparison=_optional_mapping(slots.get("comparison")),
        calculation=_optional_text(slots.get("calculation")),
        condition=_optional_mapping(slots.get("condition")),
        source_hint=_optional_text(slots.get("source_hint")),
        parse_status="AUTO_OK",
        parse_reason=None,
    )


def _optional_mapping(value: object) -> dict[str, str] | None:
    if value in (None, "", "null", "None"):
        return None
    if not isinstance(value, Mapping):
        raise TypeError("slot must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _optional_float(value: object) -> float | None:
    if value in (None, "", "null", "None"):
        return None
    return float(value)


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _format_counts(counts: Counter[str]) -> str:
    return " | ".join(f"{key}:{counts[key]}" for key in sorted(counts))


def _split(value: object) -> list[str]:
    return [part.strip() for part in _text(value).split("|") if part.strip()]


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
