"""TDD tests for the Gold replay layer (written before the implementation)."""

from datetime import date
from pathlib import Path

import pytest

from core.gold_replay import (
    R1_REPLAY_KIND,
    GoldExpectedFieldLeakError,
    build_frozen_r2_gold_claims,
    replay_r1_gold30,
    replay_r3_gold20,
    replay_r4_gold20,
)
from core.mlops_gold_evaluation import evaluate_r1, evaluate_r3, evaluate_r4
from core.r1_article_pipeline import normalized_sentence_hash
from core.r3_evidence_pipeline import R3ReadyEvidence
from schemas.candidate import KosisCandidateSchema
from schemas.claim import ClaimSchema
from schemas.concept import StandardConceptSchema
from schemas.evidence import CalculationPlan, EvidenceCellSchema


def _gold30_row(row_id: str, sentence: str, label: str, article_date: str = "2025-04-01") -> dict[str, str]:
    return {
        "row_id": row_id,
        "sentence": sentence,
        "sentence_hash": normalized_sentence_hash(sentence),
        "article_date": article_date,
        "is_claim_human": label,
    }


def _gold30_rows() -> list[dict[str, str]]:
    rows = []
    for index in range(1, 31):
        label = "TRUE" if index <= 15 else "UNCERTAIN"
        sentence = f"{index}번 지표는 {index * 3}만 명으로 전년 대비 {index}.0% 증가했다."
        rows.append(_gold30_row(f"G{index:03d}", sentence, label))
    return rows


# --- 1. R1 Gold30 replay: 30/30 hash join and TRUE recall -----------------


def test_r1_gold30_replay_joins_30_of_30_and_computes_true_recall() -> None:
    gold = _gold30_rows()

    replay = replay_r1_gold30(gold)

    assert replay["replay_kind"] == R1_REPLAY_KIND
    assert replay["scope"]["synthetic_sentence_replay"] is True
    assert replay["scope"]["full_article_recall"] is False
    result = evaluate_r1(gold_rows=gold, candidate_rows=replay["candidates"])
    assert result["join"]["gold_count"] == 30
    assert result["join"]["joined_count"] == 30
    assert result["metrics"]["true_candidate_recall"]["status"] == "EVALUATED"
    assert result["metrics"]["true_candidate_recall"]["value"] == 1.0


# --- 2. No FALSE Gold -> precision/F1 NOT_EVALUABLE ------------------------


def test_r1_gold30_without_false_keeps_precision_f1_not_evaluable() -> None:
    gold = _gold30_rows()
    replay = replay_r1_gold30(gold)

    result = evaluate_r1(gold_rows=gold, candidate_rows=replay["candidates"])

    assert result["gold_label_counts"]["FALSE"] == 0
    assert result["metrics"]["precision"]["status"] == "NOT_EVALUABLE"
    assert result["metrics"]["f1"]["status"] == "NOT_EVALUABLE"
    # UNCERTAIN is reported as its own emission rate, never converted to FALSE.
    assert "uncertain_candidate_emission_rate" in result["metrics"]
    assert result["label_policy"].startswith("UNCERTAIN remains UNCERTAIN")


# --- 9(R1 부분). every replay candidate carries a sentence_hash ------------


def test_r1_replay_candidates_always_carry_sentence_hash() -> None:
    replay = replay_r1_gold30(_gold30_rows())

    assert replay["candidates"], "replay must emit candidates for numeric gold sentences"
    for candidate in replay["candidates"]:
        value = candidate.get("sentence_hash")
        assert isinstance(value, str) and len(value) == 64


# --- 7. Gold expected fields must never enter the inference payload --------


def test_frozen_r2_gold_claims_reject_expected_field_leakage() -> None:
    fixture_row = {
        "claim_id": "NEWS_B-001-A01",
        "article_id": "NEWS_B-001",
        "published_at": "2025-06-04",
        "gold_table_id": "DT_1J22003",  # forbidden in an inference payload
        "claim": {
            "claim_id": "NEWS_B-001-A01",
            "source_sentence": "5월 소비자 물가 지수는 116.27로 올랐다.",
            "indicator": "소비자물가지수",
            "value": 116.27,
            "unit": "2020=100",
            "time": "2025-05",
            "frequency": "월",
            "calculation": "DIRECT_VALUE",
            "parse_status": "AUTO_OK",
        },
    }

    with pytest.raises(GoldExpectedFieldLeakError):
        build_frozen_r2_gold_claims([fixture_row])


def test_frozen_r2_gold_claims_build_r2_ready_claims_with_flags() -> None:
    fixture_row = {
        "claim_id": "NEWS_B-001-A01",
        "article_id": "NEWS_B-001",
        "published_at": "2025-06-04",
        "claim": {
            "claim_id": "NEWS_B-001-A01",
            "source_sentence": "5월 소비자 물가 지수는 116.27로 올랐다.",
            "indicator": "소비자물가지수",
            "value": 116.27,
            "unit": "2020=100",
            "time": "2025-05",
            "frequency": "월",
            "calculation": "DIRECT_VALUE",
            "parse_status": "AUTO_OK",
        },
    }

    claims = build_frozen_r2_gold_claims([fixture_row])

    assert len(claims) == 1
    assert claims[0].claim_candidate_id == "NEWS_B-001-A01"
    assert claims[0].claim.claim_id == "NEWS_B-001-A01"


# --- 3/4. R3 Gold20 ranking metrics and HOLD handling ----------------------


def _concept(concept_id: str = "C_TEST") -> StandardConceptSchema:
    return StandardConceptSchema(
        concept_id=concept_id,
        canonical_name="시험 지표",
        status="MATCHED",
        standard_key="test_indicator",
    )


def _candidate(tbl_id: str) -> KosisCandidateSchema:
    return KosisCandidateSchema(
        org_id="101",
        tbl_id=tbl_id,
        tbl_name="시험 통계표",
        core_item_ids=["T1"],
        unit_names=["명"],
        frequency="월",
        metadata_status="READY",
    )


def test_r3_gold20_records_ranked_candidates_and_supports_hit_metrics() -> None:
    gold = [
        {"claim_id": "NEWS_B-901-A01", "expected_route": "AUTO", "gold_table_ids": ["DT_OK"]},
        {"claim_id": "NEWS_B-902-A01", "expected_route": "HOLD", "gold_table_ids": []},
    ]
    predictions = [
        {
            "claim_id": "NEWS_B-901-A01",
            "route_status": "HOLD",
            "reason_code": "R3_SEMANTIC_MATCH_HOLD",
            "ranked_candidate_tbl_ids": ["DT_OK", "DT_OTHER"],
        },
        {
            "claim_id": "NEWS_B-902-A01",
            "route_status": "HOLD",
            "reason_code": "R2_CLAIM_PARSE_NOT_AUTO_OK",
            "ranked_candidate_tbl_ids": None,
        },
    ]

    result = evaluate_r3(gold_rows=gold, prediction_rows=predictions)

    assert result["join"]["joined_count"] == 2
    assert result["metrics"]["hit_at_1"]["status"] == "EVALUATED"
    assert result["metrics"]["hit_at_1"]["value"] == 1.0
    assert result["metrics"]["mrr"]["value"] == 1.0
    # 4. the no-expected-table HOLD row is excluded from the ranking denominator
    #    with an explicit reason, not forced into Hit@k.
    assert result["metrics"]["hit_at_1"]["denominator"] == 1
    assert result["not_evaluable_reason_counts"].get("R3_GOLD_TABLE_MISSING") == 1


def test_r3_replay_preserves_gold20_claim_ids_and_scope_flags() -> None:
    claims = build_frozen_r2_gold_claims(
        [
            {
                "claim_id": "NEWS_B-903-A01",
                "article_id": "NEWS_B-903",
                "published_at": "2025-06-04",
                "claim": {
                    "claim_id": "NEWS_B-903-A01",
                    "source_sentence": "등록되지 않은 지표는 37.5를 기록했다.",
                    "indicator": "등록되지 않은 지표",
                    "value": 37.5,
                    "unit": "명",
                    "time": "2025-05",
                    "frequency": "월",
                    "calculation": "DIRECT_VALUE",
                    "parse_status": "AUTO_OK",
                },
            },
            {
                "claim_id": "NEWS_B-904-A01",
                "article_id": "NEWS_B-904",
                "published_at": "2025-06-04",
                "claim": {
                    "claim_id": "NEWS_B-904-A01",
                    "source_sentence": "복합 문장이라 구조화가 보류됐다.",
                    "parse_status": "HOLD",
                    "parse_reason": "R2_SLOT_INPUT_REQUIRED",
                },
            },
        ]
    )

    replay = replay_r3_gold20(claims, concepts=[], catalog=[], period_availability=None)

    assert "R3_CONDITIONAL_ON_FROZEN_R2_GOLD" in replay["scope_flags"]
    assert "NOT_END_TO_END" in replay["scope_flags"]
    assert replay["r2_input_source"] == "FROZEN_GOLD"
    by_id = {record["claim_id"]: record for record in replay["records"]}
    assert set(by_id) == {"NEWS_B-903-A01", "NEWS_B-904-A01"}
    assert by_id["NEWS_B-904-A01"]["route_status"] == "HOLD"
    assert by_id["NEWS_B-904-A01"]["reason_code"] == "R2_CLAIM_PARSE_NOT_AUTO_OK"
    # 9(R3 부분): claim_id preserved on every record.
    for record in replay["records"]:
        assert record["claim_id"].startswith("NEWS_B-9")


# --- 5/6. R4 snapshot-only replay ------------------------------------------


def _ready_evidence(claim_id: str, snapshot_cell: EvidenceCellSchema) -> R3ReadyEvidence:
    claim = ClaimSchema(
        claim_id=claim_id,
        source_sentence="5월 소비자 물가 지수는 116.27로 올랐다.",
        indicator="소비자물가지수",
        value=116.27,
        unit="2020=100",
        time="2025-05",
        frequency="월",
        calculation="DIRECT_VALUE",
        parse_status="AUTO_OK",
    )
    return R3ReadyEvidence(
        article_id=claim_id.rsplit("-", 1)[0],
        published_at="2025-06-04",
        claim_candidate_id=claim_id,
        claim=claim,
        concept=_concept(),
        candidate=_candidate(snapshot_cell.tbl_id),
        calculation_plan=CalculationPlan(calculation_type="DIRECT_VALUE", required_cells=[snapshot_cell]),
        evidence_profile="test_profile",
    )


def _write_verified_snapshot(path: Path, *, tbl_id: str, itm_id: str, prd_de: str, value: str) -> None:
    import hashlib
    import json as _json

    response = [
        {
            "ORG_ID": "101",
            "TBL_ID": tbl_id,
            "ITM_ID": itm_id,
            "PRD_SE": "M",
            "PRD_DE": prd_de,
            "C1": "T10",
            "DT": value,
            "LST_CHN_DE": "2025-06-03",
            "UNIT_NM": "2020=100",
        }
    ]
    canonical = _json.dumps(response, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = {
        "request_params": {
            "orgId": "101",
            "tblId": tbl_id,
            "itmId": itm_id,
            "prdSe": "M",
            "startPrdDe": prd_de,
            "endPrdDe": prd_de,
            "objL1": "T10",
        },
        "response": response,
        "retrieved_at": "2025-06-04T09:00:00+00:00",
        "response_hash": hashlib.sha256(canonical).hexdigest(),
    }
    path.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_r4_gold20_snapshot_replay_computes_route_and_verdict_metrics(tmp_path: Path) -> None:
    cell = EvidenceCellSchema(
        org_id="101",
        tbl_id="DT_TEST_CPI",
        itm_id="CPI_TOTAL",
        prd_se="M",
        prd_de="202505",
        dimension_codes={"C1": "T10"},
        canonical_key="index",
        status="CONFIRMED",
        unit="2020=100",
    )
    snapshot_path = tmp_path / "verified_snapshot.json"
    _write_verified_snapshot(
        snapshot_path, tbl_id="DT_TEST_CPI", itm_id="CPI_TOTAL", prd_de="202505", value="116.27"
    )
    ready = [_ready_evidence("NEWS_B-001-A01", cell)]
    replay = replay_r4_gold20(
        ready=ready,
        carried_holds=[{"claim_id": "NEWS_B-002-A01", "reason_code": "R2_CLAIM_PARSE_NOT_AUTO_OK"}],
        snapshot_paths=[snapshot_path],
    )

    rows = {row["claim_id"]: row for row in replay["verdict_rows"]}
    assert rows["NEWS_B-001-A01"]["verdict"] == "MATCH"
    assert rows["NEWS_B-001-A01"]["route_status"] == "AUTO"
    assert rows["NEWS_B-001-A01"]["evidence_cells"]
    assert rows["NEWS_B-002-A01"]["route_status"] == "HOLD"
    assert rows["NEWS_B-002-A01"]["verdict"] == "UNDETERMINED"

    gold = [
        {"claim_id": "NEWS_B-001-A01", "expected_route": "AUTO", "expected_verdict": "MATCH", "gold_table_ids": ["DT_TEST_CPI"]},
        {"claim_id": "NEWS_B-002-A01", "expected_route": "HOLD", "expected_verdict": "UNDETERMINED", "gold_table_ids": []},
    ]
    result = evaluate_r4(gold_rows=gold, prediction_rows=replay["verdict_rows"])
    assert result["status"] == "EVALUATED"
    assert result["join"]["joined_count"] == 2
    assert result["metrics"]["route_accuracy"]["value"] == 1.0
    assert result["metrics"]["hold_recall"]["value"] == 1.0


def test_r4_gold20_missing_snapshot_holds_and_never_uses_api(tmp_path: Path) -> None:
    cell = EvidenceCellSchema(
        org_id="101",
        tbl_id="DT_NO_SNAPSHOT",
        itm_id="NOPE",
        prd_se="M",
        prd_de="202505",
        canonical_key="missing",
        status="CONFIRMED",
    )
    replay = replay_r4_gold20(
        ready=[_ready_evidence("NEWS_B-905-A01", cell)],
        carried_holds=[],
        snapshot_paths=[Path("data/kosis_snapshots/official_goldset_v3_news_b001_index.json")],
    )

    row = replay["verdict_rows"][0]
    assert row["route_status"] == "HOLD"
    assert row["verdict"] == "UNDETERMINED"
    assert row["reason_code"] in {"NO_DATA", "AS_OF_UNAVAILABLE", "UNVERIFIED_SNAPSHOT", "VALUE_UNAVAILABLE"}
    assert replay["snapshot_only"] is True
    for provenance in replay["provenance_rows"]:
        assert provenance["source"] != "API"


def test_r4_evaluation_reports_unsafe_auto_count_and_rate(tmp_path: Path) -> None:
    gold = [
        {"claim_id": "A", "expected_route": "HOLD", "expected_verdict": "UNDETERMINED", "gold_table_ids": []},
        {"claim_id": "B", "expected_route": "AUTO", "expected_verdict": "MATCH", "gold_table_ids": ["DT_X"]},
    ]
    predictions = [
        {"claim_id": "A", "route_status": "AUTO", "verdict": "MATCH", "evidence_cells": [{"tbl_id": "DT_X"}]},
        {"claim_id": "B", "route_status": "AUTO", "verdict": "MATCH", "evidence_cells": [{"tbl_id": "DT_X"}]},
    ]

    result = evaluate_r4(gold_rows=gold, prediction_rows=predictions)

    unsafe = result["metrics"]["unsafe_auto"]
    assert unsafe["count"] == 1
    assert unsafe["value"] == 0.5
