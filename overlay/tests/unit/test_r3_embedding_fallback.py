from core.r3_embedding_fallback import rerank_embedding_fallback


def test_low_lexical_candidates_are_reranked_but_never_selected():
    def encoder(texts):
        assert len(texts) == 3
        return [
            [1.0, 0.0],
            [0.2, 0.8],
            [0.9, 0.1],
        ]

    result = rerank_embedding_fallback(
        indicator="고령인구 수",
        unit="명",
        frequency="년",
        calculation="DIRECT_VALUE",
        confidence_status="LOW_LEXICAL_CONFIDENCE",
        candidates=[
            {"tbl_id": "DT_A", "tbl_name": "노인복지시설 수"},
            {"tbl_id": "DT_B", "tbl_name": "주요 연령계층별 추계인구"},
        ],
        encoder=encoder,
        model_id="test/model",
        model_revision="abc123",
    )

    assert result.status == "EMBEDDING_SHORTLIST_ATTACHED"
    assert [candidate.tbl_id for candidate in result.candidates] == ["DT_B", "DT_A"]
    assert result.model_id == "test/model"
    assert result.model_revision == "abc123"
    assert result.selection_status == "HOLD_NO_TABLE_SELECTED"
    assert result.mandatory_next_gate == (
        "OFFICIAL_METADATA -> HARD_GUARD -> EVIDENCE_CELL"
    )


def test_lexical_ready_rows_do_not_call_embedding_model():
    def encoder(_texts):
        raise AssertionError("embedding must not run for lexical-ready rows")

    result = rerank_embedding_fallback(
        indicator="출생아 수",
        unit="명",
        frequency="월",
        calculation="DIRECT_VALUE",
        confidence_status="LEXICAL_READY",
        candidates=[{"tbl_id": "DT_BIRTH", "tbl_name": "월별 출생아수"}],
        encoder=encoder,
        model_id="test/model",
        model_revision="abc123",
    )

    assert result.status == "NOT_RUN_LEXICAL_READY"
    assert result.candidates == ()
