from core.r3_kosis_candidate_retrieval import (
    evaluate_candidate_attachment,
    shortlist_kosis_candidates,
)


def test_official_search_and_lexical_rank_create_metadata_shortlist_without_selecting_table():
    result = shortlist_kosis_candidates(
        "물가상승률",
        [
            {"org_id": "101", "tbl_id": "DT_CPI", "tbl_name": "소비자물가지수"},
            {"org_id": "101", "tbl_id": "DT_CPI_RATE", "tbl_name": "월별 소비자물가 등락률"},
            {"org_id": "101", "tbl_id": "DT_HOUSE", "tbl_name": "주택매매가격지수"},
            {"org_id": "101", "tbl_id": "DT_CPI_RATE", "tbl_name": "중복 후보"},
        ],
        top_k=2,
    )

    assert result.status == "CANDIDATES_ATTACHED_FOR_METADATA"
    assert result.confidence_status == "LEXICAL_READY"
    assert result.next_retrieval_method == "OFFICIAL_METADATA_HYDRATION"
    assert result.selection_status == "HOLD_NO_TABLE_SELECTED"
    assert [candidate.tbl_id for candidate in result.candidates] == [
        "DT_CPI_RATE",
        "DT_CPI",
    ]
    assert result.candidates[0].retrieval_methods == (
        "KOSIS_OFFICIAL_SEARCH_RANK",
        "DETERMINISTIC_LEXICAL",
    )
    assert result.mandatory_next_gate == (
        "OFFICIAL_METADATA -> HARD_GUARD -> EVIDENCE_CELL"
    )


def test_fixed_rows_report_attachment_coverage_separately_from_r3_accuracy():
    replay_rows = [
        {
            "claim_id": "C1",
            "split": "dev",
            "r3_reason_code": "CONCEPT_UNREGISTERED",
            "indicator": "출생아 수",
            "unit": "명",
            "frequency": "월",
            "calculation": "DIRECT_VALUE",
        },
        {
            "claim_id": "C2",
            "split": "dev",
            "r3_reason_code": "LOW_SEMANTIC_SCORE",
            "indicator": "고용률",
            "unit": "%",
            "frequency": "월",
            "calculation": "DIRECT_VALUE",
        },
        {
            "claim_id": "C3",
            "split": "dev",
            "r3_reason_code": "NO_HARD_GUARD_CANDIDATE",
            "indicator": "인구",
            "unit": "명",
            "frequency": "년",
            "calculation": "DIRECT_VALUE",
        },
    ]
    candidates = {
        "출생아 수": [
            {"org_id": "101", "tbl_id": "DT_BIRTH", "tbl_name": "월별 출생아수"}
        ],
        "고용률": [],
    }

    rows, summary = evaluate_candidate_attachment(
        replay_rows,
        candidates,
        baseline_attached_claim_ids={"C1"},
        split="dev",
    )

    assert [row["claim_id"] for row in rows] == ["C1", "C2"]
    assert summary["eligible_claim_count"] == 2
    assert summary["baseline_attachment_count"] == 1
    assert summary["candidate_attachment_count"] == 1
    assert summary["attachment_coverage_before"] == 0.5
    assert summary["attachment_coverage_after"] == 0.5
    assert summary["lexical_ready_count"] == 1
    assert summary["embedding_fallback_count"] == 0
    assert summary["accuracy_status"] == "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
    assert rows[0]["selection_status"] == "HOLD_NO_TABLE_SELECTED"
    assert rows[1]["route_status"] == "HOLD_NO_OFFICIAL_CANDIDATE"


def test_low_lexical_score_is_kept_as_candidate_but_routed_to_embedding_review():
    result = shortlist_kosis_candidates(
        "건설투자",
        [
            {"org_id": "101", "tbl_id": "DT_EQUIP", "tbl_name": "설비투자규모 전망"},
            {"org_id": "101", "tbl_id": "DT_ENERGY", "tbl_name": "신재생에너지 건설업 현황"},
        ],
        minimum_lexical_ready_score=0.7,
    )

    assert result.status == "CANDIDATES_ATTACHED_FOR_METADATA"
    assert result.confidence_status == "LOW_LEXICAL_CONFIDENCE"
    assert result.next_retrieval_method == "EMBEDDING_TOP_K_REVIEW"
    assert result.selection_status == "HOLD_NO_TABLE_SELECTED"
