"""Bounded R3 pilot helpers for official KOSIS structure and value lookups.

The module never selects a table from value agreement.  It prepares an exact
ITEM/OBJ/PRD coordinate from official metadata and keeps Verdict evaluation
disabled until independent table, cell, value, and calculation Gold exists.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from core.frequency_normalizer import normalize_frequency
from core.period_normalizer import normalize_period_code
from core.unit_normalizer import compatible_units


ACCURACY_STATUS = "NOT_EVALUABLE_NO_INDEPENDENT_R3_R4_GOLD"
VERDICT_STATUS = "NOT_EVALUATED_NO_INDEPENDENT_VALUE_GOLD"
_FREQUENCY_CODE = {"일": "D", "월": "M", "분기": "Q", "년": "Y"}
_TOTAL_MEMBERS = {"계", "전체", "합계", "총계", "소계", "전국", "대한민국", "한국"}


@dataclass(frozen=True, slots=True)
class PilotTarget:
    claim_id: str
    split: str
    indicator: str
    table_id: str
    table_name: str
    org_id: str
    catalog_scope: str
    candidate_claim_count: int


@dataclass(frozen=True, slots=True)
class PreparedCoordinate:
    target: PilotTarget
    status: str
    reason_code: str
    item_id: str = ""
    item_name: str = ""
    unit: str = ""
    object_codes: tuple[str, ...] = ()
    dimension_members: tuple[tuple[str, str, str], ...] = ()
    period_type: str = ""
    period: str = ""


def prepare_registered_control_coordinate(
    target: PilotTarget,
    coordinate_profile: Mapping[str, object],
    member_code_rows: Iterable[Mapping[str, object]],
    item_rows: Iterable[Mapping[str, object]],
    period_rows: Iterable[Mapping[str, object]],
) -> PreparedCoordinate:
    """Validate a registered coordinate against current official metadata."""
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
    for dimension_id, member_name_raw in dimensions.items():
        member_name = _text(member_name_raw)
        code = registry.get((_text(dimension_id), member_name), "")
        live = [
            row
            for row in rows
            if _text(row.get("OBJ_ID")) == _text(dimension_id)
            and _text(row.get("ITM_ID")) == code
            and _normalize(_text(row.get("ITM_NM"))) == _normalize(member_name)
        ]
        if not code or len(live) != 1:
            return _hold(target, "REGISTERED_MEMBER_NOT_IN_CURRENT_METADATA")
        object_codes.append(code)
        selected.append(
            (
                _text(dimension_id),
                _text(live[0].get("OBJ_NM")),
                member_name,
            )
        )
    latest = _latest_regular_period(period_rows)
    if latest is None:
        return _hold(target, "REGISTERED_PERIOD_UNAVAILABLE")
    period_type, period = latest
    item = item_matches[0]
    return PreparedCoordinate(
        target=target,
        status="COORDINATE_READY_FOR_VALUE_FETCH",
        reason_code="",
        item_id=item_id,
        item_name=_text(item.get("ITM_NM")),
        unit=_text(item.get("UNIT_NM")),
        object_codes=tuple(object_codes),
        dimension_members=tuple(selected),
        period_type=period_type,
        period=period,
    )


def select_pilot_targets(
    candidate_rows: Iterable[Mapping[str, object]],
    *,
    catalog_table_ids: Set[str],
    identity_by_table: Mapping[str, Mapping[str, object]],
    claim_slots_by_id: Mapping[str, Mapping[str, object]],
    split: str = "dev",
    limit: int = 20,
) -> list[PilotTarget]:
    """Select outside-catalog tables on frozen split without reading articles."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    materialized = [
        row for row in candidate_rows if _text(row.get("split")) == split
    ]
    by_table: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    gap_claims_by_table: dict[str, set[str]] = defaultdict(set)
    for row in materialized:
        table_ids = _split_ids(row.get("candidate_tbl_ids"))
        has_catalog_candidate = bool(set(table_ids).intersection(catalog_table_ids))
        for table_id in table_ids:
            if table_id in catalog_table_ids:
                continue
            by_table[table_id].append(row)
            if not has_catalog_candidate:
                gap_claims_by_table[table_id].add(_text(row.get("claim_id")))

    ranked_ids = sorted(
        by_table,
        key=lambda table_id: (
            -len(gap_claims_by_table[table_id]),
            -len({_text(row.get("claim_id")) for row in by_table[table_id]}),
            table_id,
        ),
    )
    output: list[PilotTarget] = []
    for table_id in ranked_ids:
        identity = identity_by_table.get(table_id)
        if identity is None:
            continue
        representative = max(
            by_table[table_id],
            key=lambda row: (
                _slot_completeness(
                    claim_slots_by_id.get(_text(row.get("claim_id")), {})
                ),
                _text(row.get("claim_id")),
            ),
        )
        claim_id = _text(representative.get("claim_id"))
        slots = claim_slots_by_id.get(claim_id, {})
        output.append(
            PilotTarget(
                claim_id=claim_id,
                split=split,
                indicator=_text(representative.get("indicator"))
                or _text(slots.get("indicator")),
                table_id=table_id,
                table_name=_text(identity.get("tbl_name")),
                org_id=_text(identity.get("org_id")),
                catalog_scope="EXPANSION_OUTSIDE_CATALOG",
                candidate_claim_count=len(
                    {_text(row.get("claim_id")) for row in by_table[table_id]}
                ),
            )
        )
        if len(output) >= limit:
            break
    return output


def select_single_guard_controls(
    guard_rows: Iterable[Mapping[str, object]],
    candidate_rows: Iterable[Mapping[str, object]],
    *,
    identity_by_table: Mapping[str, Mapping[str, object]],
    claim_slots_by_id: Mapping[str, Mapping[str, object]],
    split: str = "dev",
    limit: int = 2,
) -> list[PilotTarget]:
    """Select only single-survivor Guard rows as positive pipeline controls."""
    if limit <= 0:
        return []
    candidates_by_claim = {
        _text(row.get("claim_id")): row
        for row in candidate_rows
        if _text(row.get("split")) == split
    }
    output: list[PilotTarget] = []
    used_tables: set[str] = set()
    for row in sorted(guard_rows, key=lambda item: _text(item.get("claim_id"))):
        if _text(row.get("split")) != split:
            continue
        if _text(row.get("guard_route_status")) != "STRUCTURAL_GUARD_SURVIVOR":
            continue
        table_ids = _split_ids(row.get("surviving_candidate_tbl_ids"))
        if len(table_ids) != 1 or table_ids[0] in used_tables:
            continue
        claim_id, table_id = _text(row.get("claim_id")), table_ids[0]
        candidate = candidates_by_claim.get(claim_id)
        identity = identity_by_table.get(table_id)
        slots = claim_slots_by_id.get(claim_id, {})
        if candidate is None or identity is None or not slots:
            continue
        output.append(
            PilotTarget(
                claim_id=claim_id,
                split=split,
                indicator=_text(candidate.get("indicator"))
                or _text(slots.get("indicator")),
                table_id=table_id,
                table_name=_text(identity.get("tbl_name")),
                org_id=_text(identity.get("org_id")),
                catalog_scope="CONTROL_SINGLE_GUARD_SURVIVOR",
                candidate_claim_count=1,
            )
        )
        used_tables.add(table_id)
        if len(output) >= limit:
            break
    return output


def prepare_official_coordinate(
    target: PilotTarget,
    slots: Mapping[str, object],
    item_rows: Iterable[Mapping[str, object]],
    period_rows: Iterable[Mapping[str, object]],
) -> PreparedCoordinate:
    """Prepare a fetchable coordinate only from explicit official metadata."""
    rows = [dict(row) for row in item_rows]
    if not rows:
        return _hold(target, "OFFICIAL_ITEM_METADATA_EMPTY")
    if any(
        _text(row.get("TBL_ID")) not in {"", target.table_id}
        or _text(row.get("ORG_ID")) not in {"", target.org_id}
        for row in rows
    ):
        return _hold(target, "OFFICIAL_METADATA_IDENTITY_MISMATCH")

    item = _select_item(rows, target.indicator, _text(slots.get("unit")))
    if item is None:
        return _hold(target, "EVIDENCE_ITEM_UNRESOLVED")
    if item == {}:
        return _hold(target, "EVIDENCE_ITEM_AMBIGUOUS")

    dimension_result = _resolve_dimensions(rows, slots)
    if dimension_result is None:
        return _hold(target, "EVIDENCE_DIMENSION_UNRESOLVED")
    object_codes, dimension_members = dimension_result
    if not object_codes:
        return _hold(target, "KOSIS_OBJECT_CODES_REQUIRED")

    frequency = normalize_frequency(_text(slots.get("frequency")))
    period_type = _FREQUENCY_CODE.get(frequency or "")
    period = normalize_period_code(_text(slots.get("time")), frequency)
    if not period_type or not period:
        return _hold(target, "EVIDENCE_PERIOD_UNRESOLVED")
    if not _period_is_available(period_type, period, period_rows):
        return _hold(target, "TIME_NOT_AVAILABLE")

    return PreparedCoordinate(
        target=target,
        status="COORDINATE_READY_FOR_VALUE_FETCH",
        reason_code="",
        item_id=_text(item.get("ITM_ID")),
        item_name=_text(item.get("ITM_NM")),
        unit=_text(item.get("UNIT_NM")),
        object_codes=object_codes,
        dimension_members=dimension_members,
        period_type=period_type,
        period=period,
    )


def evaluate_official_value_rows(
    coordinate: PreparedCoordinate,
    slots: Mapping[str, object],
    rows: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    """Accept one exact official row, while withholding any Verdict score."""
    if coordinate.status != "COORDINATE_READY_FOR_VALUE_FETCH":
        return _value_hold(coordinate.reason_code or "EVIDENCE_CELL_UNRESOLVED")
    materialized = [dict(row) for row in rows]
    matches = [row for row in materialized if _matches_coordinate(row, coordinate)]
    if not matches:
        return _value_hold("OFFICIAL_VALUE_ROW_NOT_FOUND", len(materialized))
    if len(matches) != 1:
        return _value_hold("OFFICIAL_VALUE_ROWS_AMBIGUOUS", len(materialized))
    row = matches[0]
    official_unit = _text(row.get("UNIT_NM")) or coordinate.unit
    claim_unit = _text(slots.get("unit"))
    if claim_unit and official_unit and not compatible_units(claim_unit, official_unit):
        return _value_hold("UNIT_CONFLICT", len(materialized), official_unit)
    value = _decimal_text(row.get("DT"))
    if value is None:
        return _value_hold("OFFICIAL_VALUE_NOT_NUMERIC", len(materialized), official_unit)
    return {
        "official_value_status": "OFFICIAL_VALUE_FETCHED",
        "reason_code": "",
        "official_value": value,
        "official_unit": official_unit,
        "response_row_count": len(materialized),
        "verdict_status": VERDICT_STATUS,
    }


def _select_item(
    rows: list[dict[str, object]], indicator: str, claim_unit: str
) -> dict[str, object] | None:
    unique: dict[str, dict[str, object]] = {}
    for row in rows:
        if _text(row.get("OBJ_ID")).casefold() != "item":
            continue
        item_id = _text(row.get("ITM_ID"))
        item_name = _text(row.get("ITM_NM"))
        if item_id and item_name:
            unique.setdefault(item_id, row)
    scored = []
    for row in unique.values():
        score = _similarity(indicator, _text(row.get("ITM_NM")))
        unit = _text(row.get("UNIT_NM"))
        unit_bonus = 0.02 if claim_unit and unit and compatible_units(claim_unit, unit) else 0
        scored.append((score + unit_bonus, score, _text(row.get("ITM_ID")), row))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[2]))
    if scored[0][1] < 0.6:
        return None
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.05:
        return {}
    return scored[0][3]


def _resolve_dimensions(
    rows: list[dict[str, object]], slots: Mapping[str, object]
) -> tuple[tuple[str, ...], tuple[tuple[str, str, str], ...]] | None:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        object_id = _text(row.get("OBJ_ID"))
        if object_id and object_id.casefold() != "item":
            grouped[object_id].append(row)
    ordered = sorted(
        grouped.items(),
        key=lambda item: (_object_order(item[1]), item[0]),
    )
    object_codes: list[str] = []
    selected: list[tuple[str, str, str]] = []
    for object_id, members in ordered:
        object_name = _text(members[0].get("OBJ_NM"))
        member = _select_member(object_id, object_name, members, slots)
        if member is None:
            return None
        code = _text(member.get("ITM_ID"))
        name = _text(member.get("ITM_NM"))
        if not code or not name:
            return None
        object_codes.append(code)
        selected.append((object_id, object_name, name))
    return tuple(object_codes), tuple(selected)


def _select_member(
    object_id: str,
    object_name: str,
    members: list[dict[str, object]],
    slots: Mapping[str, object],
) -> dict[str, object] | None:
    explicit_by_key = _explicit_dimension_values(slots)
    object_keys = {_dimension_key(object_id), _dimension_key(object_name)}
    explicit = [
        value
        for key, values in explicit_by_key.items()
        if _dimension_key(key) in object_keys
        for value in values
    ]
    general = [
        value
        for value in (
            _text(slots.get("region")),
            _text(slots.get("population")),
        )
        if value
    ]
    wanted = {_normalize(value) for value in explicit or general}
    exact = [
        member
        for member in members
        if _normalize(_text(member.get("ITM_NM"))) in wanted
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1 or explicit:
        return None
    totals = [
        member
        for member in members
        if _normalize(_text(member.get("ITM_NM")))
        in {_normalize(value) for value in _TOTAL_MEMBERS}
    ]
    if len(totals) == 1:
        return totals[0]
    unique = {
        _text(member.get("ITM_ID")): member
        for member in members
        if _text(member.get("ITM_ID"))
    }
    return next(iter(unique.values())) if len(unique) == 1 else None


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


def _latest_regular_period(
    rows: Iterable[Mapping[str, object]],
) -> tuple[str, str] | None:
    by_frequency: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        frequency = normalize_frequency(_text(row.get("PRD_SE")))
        period_type = _FREQUENCY_CODE.get(frequency or "")
        if not period_type:
            continue
        for field in ("PRD_DE", "END_PRD_DE"):
            period = _period_digits(row.get(field))
            if period:
                by_frequency[period_type].append(period)
    for period_type in ("M", "Q", "Y", "D"):
        if by_frequency.get(period_type):
            return period_type, max(by_frequency[period_type])
    return None


def _matches_coordinate(
    row: Mapping[str, object], coordinate: PreparedCoordinate
) -> bool:
    if _text(row.get("ORG_ID")) not in {"", coordinate.target.org_id}:
        return False
    if _text(row.get("TBL_ID")) not in {"", coordinate.target.table_id}:
        return False
    if _text(row.get("ITM_ID")) != coordinate.item_id:
        return False
    if _kosis_frequency(_text(row.get("PRD_SE"))) != _kosis_frequency(
        coordinate.period_type
    ):
        return False
    if _period_digits(row.get("PRD_DE")) != coordinate.period:
        return False
    return all(
        _text(row.get(f"C{index}")) == code
        for index, code in enumerate(coordinate.object_codes, start=1)
    )


def _explicit_dimension_values(
    slots: Mapping[str, object]
) -> dict[str, list[str]]:
    output: dict[str, list[str]] = defaultdict(list)
    for field in ("dimension", "condition"):
        value = slots.get(field)
        if isinstance(value, Mapping):
            for key, item in value.items():
                text = _text(item)
                if text:
                    output[_text(key)].append(text)
    return output


def _slot_completeness(slots: Mapping[str, object]) -> int:
    fields = (
        "indicator",
        "unit",
        "time",
        "frequency",
        "region",
        "population",
        "dimension",
        "condition",
    )
    score = sum(slots.get(field) not in (None, "", {}, []) for field in fields)
    if _normalize(_text(slots.get("calculation"))) == "directvalue":
        score += 2
    return score


def _similarity(left: str, right: str) -> float:
    left_normalized, right_normalized = _normalize(left), _normalize(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if left_normalized == right_normalized:
        return 1.0
    if left_normalized in right_normalized or right_normalized in left_normalized:
        return 0.9
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _object_order(rows: list[dict[str, object]]) -> tuple[int, str]:
    raw = _text(rows[0].get("OBJ_ID_SN"))
    return (int(raw), raw) if raw.isdigit() else (999, raw)


def _dimension_key(value: str) -> str:
    normalized = _normalize(value)
    aliases = {
        "성": "성별",
        "남녀": "성별",
        "시도": "지역",
        "행정구역": "지역",
        "국가": "지역",
        "연령별": "연령",
    }
    if normalized.endswith("별"):
        normalized = normalized[:-1]
    return aliases.get(normalized, normalized)


def _decimal_text(value: object) -> str | None:
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _period_digits(value: object) -> str:
    return re.sub(r"[^0-9]", "", _text(value))


def _kosis_frequency(value: object) -> str | None:
    text = _text(value)
    if text.casefold() == "a":
        return "년"
    return normalize_frequency(text)


def _value_hold(
    reason: str, response_count: int = 0, official_unit: str = ""
) -> dict[str, object]:
    return {
        "official_value_status": "HOLD",
        "reason_code": reason,
        "official_value": "",
        "official_unit": official_unit,
        "response_row_count": response_count,
        "verdict_status": VERDICT_STATUS,
    }


def _hold(target: PilotTarget, reason: str) -> PreparedCoordinate:
    return PreparedCoordinate(
        target=target,
        status="HOLD_COORDINATE_UNRESOLVED",
        reason_code=reason,
    )


def _split_ids(value: object) -> list[str]:
    return [part.strip() for part in _text(value).split("|") if part.strip()]


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z가-힣]+", "", value).casefold()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
