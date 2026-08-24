from core.r3_hard_guard_readiness import evaluate_hard_guard_readiness
from schemas.candidate import KosisCandidateSchema


def _candidate(table_id, *, frequency):
    return KosisCandidateSchema(
        org_id="101",
        tbl_id=table_id,
        tbl_name=table_id,
        core_item_ids=["T1"],
        core_item_names=["취업자 수"],
        dimension_ids=[],
        dimension_names=[],
        dimension_members={},
        unit_names=["명"],
        frequency=frequency,
        start_period="2020.01" if frequency == "월" else "2020",
        end_period="2026.12" if frequency == "월" else "2026",
        metadata_status="OFFICIAL_METADATA_READY",
    )


def _slots(claim_id):
    return {
        "claim_id": claim_id,
        "indicator": "취업자 수",
        "value": 100.0,
        "unit": "명",
        "time": "2025년 1월",
        "frequency": "월",
        "region": "전국",
        "population": None,
        "dimension": None,
        "comparison": None,
        "calculation": "DIRECT_VALUE",
        "condition": None,
        "source_hint": None,
        "target_value_role_status": "MISSING_IN_FROZEN_R2_GOLD",
    }


def test_guard_readiness_counts_survivors_rejects_and_missing_catalog_separately():
    candidate_rows = [
        {"claim_id": "C1", "candidate_tbl_ids": "DT_PASS | DT_YEAR"},
        {"claim_id": "C2", "candidate_tbl_ids": "DT_YEAR"},
        {"claim_id": "C3", "candidate_tbl_ids": "DT_UNKNOWN"},
    ]
    claims = {claim_id: _slots(claim_id) for claim_id in ("C1", "C2", "C3")}
    catalog = {
        "DT_PASS": _candidate("DT_PASS", frequency="월"),
        "DT_YEAR": _candidate("DT_YEAR", frequency="년"),
    }

    rows, summary = evaluate_hard_guard_readiness(
        candidate_rows,
        claim_slots_by_id=claims,
        catalog_by_table_id=catalog,
    )

    assert [row["guard_route_status"] for row in rows] == [
        "STRUCTURAL_GUARD_SURVIVOR",
        "HOLD_ALL_NORMALIZED_CANDIDATES_REJECTED",
        "HOLD_NO_NORMALIZED_CATALOG_CANDIDATE",
    ]
    assert rows[0]["surviving_candidate_tbl_ids"] == "DT_PASS"
    assert rows[0]["selection_status"] == "HOLD_NO_TABLE_SELECTED"
    assert "FREQUENCY_CONFLICT" in rows[1]["reject_code_counts"]
    assert summary["input_claim_count"] == 3
    assert summary["claim_slot_join_count"] == 3
    assert summary["normalized_catalog_candidate_claim_count"] == 2
    assert summary["structural_guard_survivor_claim_count"] == 1
    assert summary["accuracy_status"] == "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
    assert summary["guard_scope"] == (
        "PARTIAL_NO_ARTICLE_CONTEXT_NO_TARGET_VALUE_ROLE"
    )
