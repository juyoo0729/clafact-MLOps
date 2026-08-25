from core.r3_official_value_pilot import PilotTarget, PreparedCoordinate
from core.r3_registry_full_value_replay import (
    RegistryCandidate,
    build_composite_signature,
    choose_coordinate,
    coordinate_cache_key,
    merge_candidate_identities,
    profile_matches_claim,
    rank_catalog_candidates,
)


def _coordinate(table_id: str, *, scope: str = "LOCAL_CATALOG") -> PreparedCoordinate:
    return PreparedCoordinate(
        target=PilotTarget(
            claim_id="C1",
            split="dev",
            indicator="취업자 수",
            table_id=table_id,
            table_name="취업자",
            org_id="101",
            catalog_scope=scope,
            candidate_claim_count=1,
        ),
        status="COORDINATE_READY_FOR_VALUE_FETCH",
        reason_code="",
        item_id="T30",
        item_name="취업자",
        unit="천명",
        object_codes=("0", "00"),
        dimension_members=(("B", "성별", "계"), ("J", "종사상지위별", "계")),
        period_type="M",
        period="202501",
    )


def test_composite_signature_keeps_semantic_and_coordinate_controls():
    signature = build_composite_signature(
        {
            "indicator": "취업자 수",
            "unit": "명",
            "frequency": "월",
            "region": "전국",
            "population": "15세 이상",
            "dimension": {"성별": "계"},
            "calculation": '{"type":"DIRECT_VALUE"}',
        },
        {"standard_key": "EMPLOYED_PERSON_COUNT"},
    )

    assert signature.startswith("employedpersoncount|")
    assert "directvalue" in signature
    assert "전국" in signature
    assert "15세이상" in signature


def test_catalog_ranking_applies_frequency_and_unit_before_lexical_score():
    ranked = rank_catalog_candidates(
        {"indicator": "취업자 수", "unit": "명", "frequency": "월"},
        [
            {
                "ORG_ID": "101",
                "TBL_ID": "DT_MONTHLY",
                "TBL_NM_META": "성별 취업자",
                "PRD_CODE_NORMALIZED": "M",
                "CORE_ITEM_NAMES": "취업자",
                "INDICATOR_CANDIDATES": "취업자 수",
                "UNIT_NAMES_FINAL": "천명",
            },
            {
                "ORG_ID": "101",
                "TBL_ID": "DT_YEARLY",
                "TBL_NM_META": "취업자 수",
                "PRD_CODE_NORMALIZED": "Y",
                "CORE_ITEM_NAMES": "취업자 수",
                "INDICATOR_CANDIDATES": "취업자 수",
                "UNIT_NAMES_FINAL": "명",
            },
            {
                "ORG_ID": "101",
                "TBL_ID": "DT_WRONG_UNIT",
                "TBL_NM_META": "취업자 수",
                "PRD_CODE_NORMALIZED": "M",
                "CORE_ITEM_NAMES": "취업자 수",
                "INDICATOR_CANDIDATES": "취업자 수",
                "UNIT_NAMES_FINAL": "원",
            },
        ],
        top_k=5,
    )

    assert [candidate.table_id for candidate in ranked] == ["DT_MONTHLY"]


def test_merge_candidates_deduplicates_and_preserves_registered_priority():
    merged = merge_candidate_identities(
        [
            RegistryCandidate("101", "DT_A", "A", "LOCAL_CATALOG", 0.8),
            RegistryCandidate("101", "DT_B", "B", "OFFICIAL_SEARCH", 0.9),
        ],
        [RegistryCandidate("101", "DT_A", "A", "REGISTERED_COORDINATE", 1.0)],
    )

    assert [(row.table_id, row.source) for row in merged] == [
        ("DT_A", "REGISTERED_COORDINATE"),
        ("DT_B", "OFFICIAL_SEARCH"),
    ]


def test_registered_ready_coordinate_wins_over_provisional_ready_coordinate():
    decision = choose_coordinate(
        [
            _coordinate("DT_PROVISIONAL"),
            _coordinate("DT_REGISTERED", scope="REGISTERED_COORDINATE"),
        ]
    )

    assert decision.status == "REGISTERED_COORDINATE_READY"
    assert decision.coordinate is not None
    assert decision.coordinate.target.table_id == "DT_REGISTERED"


def test_multiple_provisional_coordinates_remain_hold():
    decision = choose_coordinate([_coordinate("DT_A"), _coordinate("DT_B")])

    assert decision.status == "HOLD_COORDINATE_AMBIGUOUS"
    assert decision.reason_code == "MULTIPLE_OFFICIAL_COORDINATES_READY"
    assert decision.coordinate is None


def test_coordinate_cache_key_deduplicates_the_same_official_cell():
    left = _coordinate("DT_A")
    right = _coordinate("DT_A")

    assert coordinate_cache_key(left) == coordinate_cache_key(right)


def test_registered_profile_matches_change_suffix_but_enforces_calculation_type():
    cpi_profile = {
        "indicator_aliases": ["소비자물가"],
        "calculation_types": ["DIRECT_VALUE", "GROWTH_RATE"],
    }
    employment_profile = {
        "indicator_aliases": ["취업자 수"],
        "calculation_types": ["DIRECT_VALUE"],
    }

    assert profile_matches_claim(
        cpi_profile,
        {
            "indicator": "소비자물가 상승률",
            "calculation": '{"type":"GROWTH_RATE"}',
        },
    )
    assert not profile_matches_claim(
        employment_profile,
        {
            "indicator": "취업자 수",
            "calculation": '{"type":"DIFFERENCE"}',
        },
    )
