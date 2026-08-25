"""Fail-closed registry reinforcement for full-ledger KOSIS value lookups.

The helpers build provisional composite-signature registry entries from official
metadata.  A registered coordinate has priority; otherwise a Claim proceeds only
when exactly one candidate produces an exact Evidence Cell.  Official values
never select a table and never create a Verdict without independent Gold.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from core.frequency_normalizer import normalize_frequency
from core.period_normalizer import normalize_period_code
from core.r3_official_value_pilot import PilotTarget, PreparedCoordinate
from core.unit_normalizer import compatible_units


_FREQUENCY_CODE = {"일": "D", "월": "M", "분기": "Q", "년": "Y"}
_SOURCE_PRIORITY = {
    "REGISTERED_COORDINATE": 0,
    "OFFICIAL_SEARCH": 1,
    "LOCAL_CATALOG": 2,
}


@dataclass(frozen=True, slots=True)
class RegistryCandidate:
    org_id: str
    table_id: str
    table_name: str
    source: str
    score: float


@dataclass(frozen=True, slots=True)
class CoordinateDecision:
    status: str
    reason_code: str
    coordinate: PreparedCoordinate | None
    ready_candidate_count: int


def build_composite_signature(
    slots: Mapping[str, object], concept: Mapping[str, object]
) -> str:
    """Create a stable registry key from semantic and coordinate controls."""
    calculation = _calculation_type(slots.get("calculation"))
    dimension = _stable_mapping(slots.get("dimension"))
    condition = _stable_mapping(slots.get("condition"))
    parts = (
        concept.get("standard_key") or slots.get("indicator"),
        slots.get("unit"),
        normalize_frequency(_text(slots.get("frequency"))) or slots.get("frequency"),
        slots.get("region"),
        slots.get("population"),
        dimension,
        condition,
        calculation,
    )
    return "|".join(_normalize(_text(part)) or "_" for part in parts)


def rank_catalog_candidates(
    slots: Mapping[str, object],
    catalog_rows: Iterable[Mapping[str, object]],
    *,
    top_k: int = 5,
    minimum_score: float = 0.6,
) -> list[RegistryCandidate]:
    """Rank structurally compatible local catalog identities without selecting."""
    indicator = _text(slots.get("indicator"))
    claim_frequency = normalize_frequency(_text(slots.get("frequency")))
    claim_unit = _text(slots.get("unit"))
    ranked: list[RegistryCandidate] = []
    for row in catalog_rows:
        org_id = _text(row.get("ORG_ID") or row.get("org_id"))
        table_id = _text(row.get("TBL_ID") or row.get("tbl_id"))
        table_name = _text(
            row.get("TBL_NM_META")
            or row.get("TBL_NM_INPUT")
            or row.get("tbl_name")
        )
        if not org_id or not table_id or not table_name:
            continue
        table_frequency = normalize_frequency(
            _text(row.get("PRD_CODE_NORMALIZED") or row.get("PRD_SE_META"))
        )
        if claim_frequency and table_frequency and claim_frequency != table_frequency:
            continue
        units = _split_values(row.get("UNIT_NAMES_FINAL"))
        if claim_unit and units and not any(
            compatible_units(claim_unit, unit) for unit in units
        ):
            continue
        semantic_values = [
            table_name,
            *_split_values(row.get("CORE_ITEM_NAMES")),
            *_split_values(row.get("INDICATOR_CANDIDATES")),
        ]
        score = max((_similarity(indicator, value) for value in semantic_values), default=0)
        if score < minimum_score:
            continue
        ranked.append(
            RegistryCandidate(
                org_id=org_id,
                table_id=table_id,
                table_name=table_name,
                source="LOCAL_CATALOG",
                score=round(score, 6),
            )
        )
    ranked.sort(key=lambda row: (-row.score, row.table_id))
    return ranked[:top_k]


def merge_candidate_identities(
    *groups: Iterable[RegistryCandidate],
) -> list[RegistryCandidate]:
    """Deduplicate table identities while preserving the strongest provenance."""
    chosen: dict[tuple[str, str], RegistryCandidate] = {}
    for group in groups:
        for candidate in group:
            key = (candidate.org_id, candidate.table_id)
            current = chosen.get(key)
            if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(
                current
            ):
                chosen[key] = candidate
    return sorted(chosen.values(), key=_candidate_sort_key)


def profile_matches_claim(
    profile: Mapping[str, object], slots: Mapping[str, object]
) -> bool:
    """Match a reviewed profile alias while enforcing its calculation contract."""
    aliases = profile.get("indicator_aliases")
    if not isinstance(aliases, list) or not aliases:
        return False
    indicator = _normalize(_text(slots.get("indicator")))
    indicator_base = _change_base(indicator)
    alias_values = {_normalize(_text(alias)) for alias in aliases if _text(alias)}
    alias_bases = {_change_base(alias) for alias in alias_values}
    if indicator not in alias_values and indicator_base not in alias_bases:
        return False
    allowed_calculations = profile.get("calculation_types")
    if isinstance(allowed_calculations, list) and allowed_calculations:
        calculation = _calculation_type(slots.get("calculation")).upper()
        allowed = {_text(value).upper() for value in allowed_calculations}
        if calculation not in allowed:
            return False
    allowed_comparisons = profile.get("comparison_types")
    if isinstance(allowed_comparisons, list) and allowed_comparisons:
        comparison = _mapping_type(slots.get("comparison")) or "NONE"
        allowed = {_text(value).upper() for value in allowed_comparisons}
        if comparison.upper() not in allowed:
            return False
    return True


def prepare_registered_claim_coordinate(
    target: PilotTarget,
    slots: Mapping[str, object],
    coordinate_profile: Mapping[str, object],
    member_code_rows: Iterable[Mapping[str, object]],
    item_rows: Iterable[Mapping[str, object]],
    period_rows: Iterable[Mapping[str, object]],
) -> PreparedCoordinate:
    """Validate a registered table/item/member profile for the Claim period."""
    rows = [dict(row) for row in item_rows]
    item_id = _text(coordinate_profile.get("itm_id"))
    item_matches = [
        row
        for row in rows
        if _text(row.get("OBJ_ID")).casefold() == "item"
        and _text(row.get("ITM_ID")) == item_id
    ]
    if len(item_matches) != 1:
        return _hold(target, "REGISTERED_ITEM_NOT_IN_CURRENT_METADATA")
    dimensions = coordinate_profile.get("dimension_members")
    if not isinstance(dimensions, Mapping) or not dimensions:
        return _hold(target, "REGISTERED_DIMENSIONS_REQUIRED")
    registry = {
        (_text(row.get("dimension_id")), _text(row.get("member_name"))): _text(
            row.get("member_code")
        )
        for row in member_code_rows
        if _text(row.get("tbl_id")) == target.table_id
    }
    object_codes: list[str] = []
    selected: list[tuple[str, str, str]] = []
    for dimension_id_raw, member_name_raw in dimensions.items():
        dimension_id = _text(dimension_id_raw)
        member_name = _text(member_name_raw)
        code = registry.get((dimension_id, member_name), "")
        live = [
            row
            for row in rows
            if _text(row.get("OBJ_ID")) == dimension_id
            and _text(row.get("ITM_ID")) == code
            and _normalize(_text(row.get("ITM_NM"))) == _normalize(member_name)
        ]
        if not code or len(live) != 1:
            return _hold(target, "REGISTERED_MEMBER_NOT_IN_CURRENT_METADATA")
        object_codes.append(code)
        selected.append((dimension_id, _text(live[0].get("OBJ_NM")), member_name))

    frequency = normalize_frequency(_text(slots.get("frequency")))
    period_type = _FREQUENCY_CODE.get(frequency or "")
    period = normalize_period_code(_text(slots.get("time")), frequency)
    if not period_type or not period:
        return _hold(target, "EVIDENCE_PERIOD_UNRESOLVED")
    if not _period_is_available(period_type, period, period_rows):
        return _hold(target, "TIME_NOT_AVAILABLE")
    item = item_matches[0]
    claim_unit = _text(slots.get("unit"))
    official_unit = _text(item.get("UNIT_NM"))
    if claim_unit and official_unit and not compatible_units(claim_unit, official_unit):
        return _hold(target, "UNIT_CONFLICT")
    return PreparedCoordinate(
        target=target,
        status="COORDINATE_READY_FOR_VALUE_FETCH",
        reason_code="",
        item_id=item_id,
        item_name=_text(item.get("ITM_NM")),
        unit=official_unit,
        object_codes=tuple(object_codes),
        dimension_members=tuple(selected),
        period_type=period_type,
        period=period,
    )


def choose_coordinate(coordinates: Iterable[PreparedCoordinate]) -> CoordinateDecision:
    """Prefer a validated registered coordinate; otherwise require uniqueness."""
    ready = [
        coordinate
        for coordinate in coordinates
        if coordinate.status == "COORDINATE_READY_FOR_VALUE_FETCH"
    ]
    registered = [
        coordinate
        for coordinate in ready
        if coordinate.target.catalog_scope == "REGISTERED_COORDINATE"
    ]
    if len(registered) == 1:
        return CoordinateDecision(
            status="REGISTERED_COORDINATE_READY",
            reason_code="",
            coordinate=registered[0],
            ready_candidate_count=len(ready),
        )
    if len(registered) > 1:
        return CoordinateDecision(
            status="HOLD_COORDINATE_AMBIGUOUS",
            reason_code="MULTIPLE_REGISTERED_COORDINATES_READY",
            coordinate=None,
            ready_candidate_count=len(ready),
        )
    if len(ready) == 1:
        return CoordinateDecision(
            status="PROVISIONAL_UNIQUE_METADATA_COORDINATE_READY",
            reason_code="",
            coordinate=ready[0],
            ready_candidate_count=1,
        )
    if len(ready) > 1:
        return CoordinateDecision(
            status="HOLD_COORDINATE_AMBIGUOUS",
            reason_code="MULTIPLE_OFFICIAL_COORDINATES_READY",
            coordinate=None,
            ready_candidate_count=len(ready),
        )
    holds = [coordinate.reason_code for coordinate in coordinates if coordinate.reason_code]
    return CoordinateDecision(
        status="HOLD_COORDINATE_UNRESOLVED",
        reason_code=_dominant_reason(holds) or "NO_OFFICIAL_COORDINATE_READY",
        coordinate=None,
        ready_candidate_count=0,
    )


def coordinate_cache_key(coordinate: PreparedCoordinate) -> tuple[object, ...]:
    """Return the exact KOSIS cell request identity for call deduplication."""
    return (
        coordinate.target.org_id,
        coordinate.target.table_id,
        coordinate.item_id,
        coordinate.period_type,
        coordinate.period,
        *coordinate.object_codes,
    )


def _candidate_sort_key(candidate: RegistryCandidate) -> tuple[object, ...]:
    return (
        _SOURCE_PRIORITY.get(candidate.source, 99),
        -candidate.score,
        candidate.table_id,
    )


def _calculation_type(value: object) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("type"))
    text = _text(value)
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(parsed, Mapping):
            return _text(parsed.get("type"))
    return text


def _mapping_type(value: object) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("type"))
    text = _text(value)
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, Mapping):
            return _text(parsed.get("type"))
    return ""


def _stable_mapping(value: object) -> str:
    if value in (None, "", {}):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _text(value)


def _period_is_available(
    period_type: str,
    period: str,
    rows: Iterable[Mapping[str, object]],
) -> bool:
    expected_frequency = _kosis_frequency(period_type)
    for row in rows:
        if _kosis_frequency(_text(row.get("PRD_SE"))) != expected_frequency:
            continue
        exact = _period_digits(row.get("PRD_DE"))
        if exact and exact == period:
            return True
        start = _period_digits(row.get("STRT_PRD_DE"))
        end = _period_digits(row.get("END_PRD_DE"))
        if start and end and start <= period <= end:
            return True
    return False


def _dominant_reason(reasons: Iterable[str]) -> str:
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return min(counts, key=lambda reason: (-counts[reason], reason)) if counts else ""


def _hold(target: PilotTarget, reason: str) -> PreparedCoordinate:
    return PreparedCoordinate(
        target=target,
        status="HOLD_COORDINATE_UNRESOLVED",
        reason_code=reason,
    )


def _similarity(left: str, right: str) -> float:
    left_normalized, right_normalized = _normalize(left), _normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return 0.9
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _split_values(value: object) -> list[str]:
    return [part.strip() for part in _text(value).split("|") if part.strip()]


def _period_digits(value: object) -> str:
    return re.sub(r"[^0-9]", "", _text(value))


def _kosis_frequency(value: object) -> str | None:
    text = _text(value)
    if text.casefold() == "a":
        return "년"
    return normalize_frequency(text)


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", value).casefold()


def _change_base(value: str) -> str:
    for suffix in ("증가율", "감소율", "상승률", "하락률", "등락률", "증감률", "변동률"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
