from core.r3_official_value_pilot import (
    PilotTarget,
    evaluate_official_value_rows,
    prepare_official_coordinate,
    prepare_registered_control_coordinate,
    select_single_guard_controls,
    select_pilot_targets,
)


def test_selects_missing_catalog_tables_before_existing_controls():
    candidate_rows = [
        {
            "claim_id": "C1",
            "split": "dev",
            "candidate_tbl_ids": "DT_NEW_A | DT_OLD",
        },
        {
            "claim_id": "C2",
            "split": "dev",
            "candidate_tbl_ids": "DT_NEW_A | DT_NEW_B",
        },
    ]
    slots = {
        "C1": {"time": "2025년 1월", "unit": "명", "frequency": "월"},
        "C2": {
            "time": "2025년 1월",
            "unit": "명",
            "frequency": "월",
            "region": "전국",
        },
    }
    identities = {
        "DT_NEW_A": {"org_id": "101", "tbl_name": "신규 A"},
        "DT_NEW_B": {"org_id": "101", "tbl_name": "신규 B"},
        "DT_OLD": {"org_id": "101", "tbl_name": "기존"},
    }

    targets = select_pilot_targets(
        candidate_rows,
        catalog_table_ids={"DT_OLD"},
        identity_by_table=identities,
        claim_slots_by_id=slots,
        split="dev",
        limit=20,
    )

    assert [target.table_id for target in targets] == ["DT_NEW_A", "DT_NEW_B"]
    assert targets[0].claim_id == "C2"
    assert all(target.catalog_scope == "EXPANSION_OUTSIDE_CATALOG" for target in targets)


def test_control_selection_requires_exactly_one_structural_guard_survivor():
    controls = select_single_guard_controls(
        [
            {
                "claim_id": "C1",
                "split": "dev",
                "guard_route_status": "STRUCTURAL_GUARD_SURVIVOR",
                "surviving_candidate_tbl_ids": "DT_CONTROL",
            },
            {
                "claim_id": "C2",
                "split": "dev",
                "guard_route_status": "STRUCTURAL_GUARD_SURVIVOR",
                "surviving_candidate_tbl_ids": "DT_A | DT_B",
            },
        ],
        [
            {"claim_id": "C1", "split": "dev", "indicator": "취업자 수"},
            {"claim_id": "C2", "split": "dev", "indicator": "인구"},
        ],
        identity_by_table={
            "DT_CONTROL": {"org_id": "101", "tbl_name": "취업자 수"},
            "DT_A": {"org_id": "101", "tbl_name": "A"},
            "DT_B": {"org_id": "101", "tbl_name": "B"},
        },
        claim_slots_by_id={"C1": {"time": "2025년 1월"}, "C2": {"time": "2025"}},
        split="dev",
        limit=2,
    )

    assert [target.table_id for target in controls] == ["DT_CONTROL"]
    assert controls[0].catalog_scope == "CONTROL_SINGLE_GUARD_SURVIVOR"


def test_prepares_exact_item_total_dimension_and_reads_one_official_value():
    target = PilotTarget(
        claim_id="C1",
        split="dev",
        indicator="취업자 수",
        table_id="DT_NEW",
        table_name="월별 취업자 수",
        org_id="101",
        catalog_scope="EXPANSION_OUTSIDE_CATALOG",
        candidate_claim_count=3,
    )
    slots = {
        "indicator": "취업자 수",
        "unit": "명",
        "time": "2025년 1월",
        "frequency": "월",
        "region": "전국",
        "population": None,
        "dimension": None,
        "condition": None,
    }
    item_rows = [
        {
            "ORG_ID": "101",
            "TBL_ID": "DT_NEW",
            "OBJ_ID": "ITEM",
            "OBJ_NM": "항목",
            "ITM_ID": "T1",
            "ITM_NM": "취업자 수",
            "UNIT_NM": "명",
        },
        {
            "ORG_ID": "101",
            "TBL_ID": "DT_NEW",
            "OBJ_ID": "A",
            "OBJ_NM": "지역별",
            "OBJ_ID_SN": "1",
            "ITM_ID": "A0",
            "ITM_NM": "전국",
        },
        {
            "ORG_ID": "101",
            "TBL_ID": "DT_NEW",
            "OBJ_ID": "A",
            "OBJ_NM": "지역별",
            "OBJ_ID_SN": "1",
            "ITM_ID": "A1",
            "ITM_NM": "서울",
        },
    ]
    period_rows = [
        {"PRD_SE": "M", "STRT_PRD_DE": "202001", "END_PRD_DE": "202612"}
    ]

    prepared = prepare_official_coordinate(target, slots, item_rows, period_rows)

    assert prepared.status == "COORDINATE_READY_FOR_VALUE_FETCH"
    assert prepared.item_id == "T1"
    assert prepared.object_codes == ("A0",)
    assert prepared.period_type == "M"
    assert prepared.period == "202501"

    result = evaluate_official_value_rows(
        prepared,
        slots,
        [
            {
                "ORG_ID": "101",
                "TBL_ID": "DT_NEW",
                "ITM_ID": "T1",
                "PRD_SE": "M",
                "PRD_DE": "202501",
                "C1": "A0",
                "C1_NM": "전국",
                "UNIT_NM": "명",
                "DT": "28,000,000",
            }
        ],
    )

    assert result["official_value_status"] == "OFFICIAL_VALUE_FETCHED"
    assert result["official_value"] == "28000000"
    assert result["verdict_status"] == "NOT_EVALUATED_NO_INDEPENDENT_VALUE_GOLD"


def test_registered_control_is_revalidated_against_live_item_member_and_period():
    target = PilotTarget(
        claim_id="REGISTERED::DT_CONTROL",
        split="control",
        indicator="취업자 수",
        table_id="DT_CONTROL",
        table_name="취업자 수",
        org_id="101",
        catalog_scope="REGISTERED_EVIDENCE_CONTROL",
        candidate_claim_count=0,
    )
    coordinate = prepare_registered_control_coordinate(
        target,
        {
            "itm_id": "T1",
            "dimension_members": {"B": "계", "J": "계"},
        },
        [
            {
                "tbl_id": "DT_CONTROL",
                "dimension_id": "B",
                "member_name": "계",
                "member_code": "0",
            },
            {
                "tbl_id": "DT_CONTROL",
                "dimension_id": "J",
                "member_name": "계",
                "member_code": "00",
            },
        ],
        [
            {"OBJ_ID": "ITEM", "ITM_ID": "T1", "ITM_NM": "취업자 수", "UNIT_NM": "천명"},
            {"OBJ_ID": "B", "OBJ_NM": "성별", "ITM_ID": "0", "ITM_NM": "계"},
            {"OBJ_ID": "J", "OBJ_NM": "연령별", "ITM_ID": "00", "ITM_NM": "계"},
        ],
        [{"PRD_SE": "M", "STRT_PRD_DE": "2020.01", "END_PRD_DE": "2026.07"}],
    )

    assert coordinate.status == "COORDINATE_READY_FOR_VALUE_FETCH"
    assert coordinate.object_codes == ("0", "00")
    assert coordinate.period_type == "M"
    assert coordinate.period == "202607"
