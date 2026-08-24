"""Deterministic R2 required-slot revalidation and queue routing helpers."""

from __future__ import annotations

from schemas.claim import ClaimSchema


AUTO_REQUIRED_SLOTS = (
    "indicator",
    "value",
    "target_value_role",
    "unit",
    "time",
    "frequency",
    "calculation",
)
ENRICHABLE_AUTO_SLOTS = frozenset({"time", "frequency"})


def missing_auto_required_slots(claim: ClaimSchema) -> tuple[str, ...]:
    """Return current required-slot gaps without changing Claim meaning."""
    return tuple(slot for slot in AUTO_REQUIRED_SLOTS if _is_missing(getattr(claim, slot)))


def revalidate_auto_readiness(claim: ClaimSchema) -> ClaimSchema:
    """Refresh only missing-slot readiness after deterministic enrichment.

    Semantic conflicts and HUMAN_REVIEW decisions remain fail-closed.  This
    function may clear a HOLD only when that HOLD was caused exclusively by
    missing required slots and all of those slots are now present.
    """
    if claim.parse_status == "HUMAN_REVIEW":
        return claim
    if claim.parse_status == "HOLD" and not (claim.parse_reason or "").startswith(
        "MISSING_REQUIRED_SLOTS:"
    ):
        return claim
    missing_slots = missing_auto_required_slots(claim)
    if missing_slots:
        return claim.model_copy(
            update={
                "parse_status": "HOLD",
                "parse_reason": f"MISSING_REQUIRED_SLOTS:{','.join(missing_slots)}",
            }
        )
    return claim.model_copy(update={"parse_status": "AUTO_OK", "parse_reason": None})


def _is_missing(value: object | None) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())
