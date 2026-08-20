from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.r1_article_pipeline import normalized_sentence_hash
from core.mlops_gold_evaluation import (
    EvaluationOutputExistsError,
    build_review_queue_status,
    evaluate_r1,
    evaluate_r3,
    evaluate_r4,
    write_evaluation_artifacts,
)


def test_r1_true_gold_hash_join_calculates_candidate_recall() -> None:
    sentence_hash = normalized_sentence_hash("고용률은 63.5%로 상승했다.")
    result = evaluate_r1(
        gold_rows=[
            {
                "row_id": "R1-GOLD-001",
                "sentence_hash": sentence_hash,
                "is_claim_human": "TRUE",
            }
        ],
        candidate_rows=[{"candidate_id": "candidate-1", "sentence_hash": sentence_hash}],
    )

    assert result["join"]["gold_count"] == 1
    assert result["join"]["joined_count"] == 1
    assert result["join"]["coverage"] == 1.0
    assert result["metrics"]["true_candidate_recall"] == {
        "status": "EVALUATED",
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
    }


def test_r1_uncertain_only_does_not_create_precision_or_f1() -> None:
    sentence_hash = normalized_sentence_hash("수출이 크게 증가한 것으로 보인다.")
    result = evaluate_r1(
        gold_rows=[
            {
                "row_id": "R1-GOLD-UNCERTAIN",
                "sentence_hash": sentence_hash,
                "is_claim_human": "UNCERTAIN",
            }
        ],
        candidate_rows=[{"candidate_id": "candidate-uncertain", "sentence_hash": sentence_hash}],
    )

    assert result["gold_label_counts"] == {"TRUE": 0, "FALSE": 0, "UNCERTAIN": 1}
    assert result["metrics"]["uncertain_candidate_emission_rate"]["value"] == 1.0
    assert result["metrics"]["precision"] == {
        "status": "NOT_EVALUABLE",
        "value": None,
        "reason_code": "R1_FALSE_GOLD_REQUIRED_FOR_PRECISION_F1",
    }
    assert result["metrics"]["f1"] == result["metrics"]["precision"]


def test_r4_different_claim_cohort_does_not_create_accuracy_metrics() -> None:
    result = evaluate_r4(
        gold_rows=[
            {
                "claim_id": "NEWS_B-001",
                "expected_route": "AUTO",
                "expected_verdict": "MATCH",
                "gold_table_ids": ["DT_001"],
            }
        ],
        prediction_rows=[
            {
                "claim_id": "parent-001",
                "route_status": "AUTO",
                "verdict": "MATCH",
                "evidence_cells": [{"tbl_id": "DT_001"}],
            }
        ],
    )

    assert result["status"] == "NOT_EVALUABLE"
    assert result["reason_code"] == "NOT_EVALUABLE_DIFFERENT_CLAIM_COHORT"
    assert result["join"]["joined_count"] == 0
    assert result["metrics"] == {}


def test_r3_requires_gold_table_and_saved_ranked_candidates() -> None:
    result = evaluate_r3(
        gold_rows=[{"claim_id": "NEWS_B-001", "gold_table_id": "DT_GOLD"}],
        prediction_rows=[
            {
                "claim_id": "NEWS_B-001",
                "ranked_candidate_tbl_ids": ["DT_OTHER", "DT_GOLD"],
            }
        ],
    )

    assert result["status"] == "EVALUATED"
    assert result["metrics"]["hit_at_1"]["value"] == 0.0
    assert result["metrics"]["hit_at_3"]["value"] == 1.0
    assert result["metrics"]["mrr"]["value"] == 0.5


def test_r4_full_claim_cohort_join_calculates_route_verdict_and_table_metrics() -> None:
    gold_rows = [
        {
            "claim_id": "NEWS_B-001",
            "expected_route": "AUTO",
            "expected_verdict": "MATCH",
            "gold_table_ids": ["DT_001"],
            "gold_coordinates": [{"tbl_id": "DT_001", "itm_id": "ITM_A", "prd_de": "2024"}],
        },
        {
            "claim_id": "KOSIS_SEED-001",
            "expected_route": "HOLD",
            "expected_verdict": "UNDETERMINED",
            "gold_table_ids": [],
            "gold_coordinates": [],
        },
    ]
    prediction_rows = [
        {
            "claim_id": "NEWS_B-001",
            "route_status": "AUTO",
            "verdict": "MATCH",
            "evidence_cells": [
                {"tbl_id": "DT_001", "itm_id": "ITM_A", "prd_de": "2024"}
            ],
        },
        {
            "claim_id": "KOSIS_SEED-001",
            "route_status": "HOLD",
            "verdict": "UNDETERMINED",
            "evidence_cells": [],
        },
    ]

    result = evaluate_r4(gold_rows=gold_rows, prediction_rows=prediction_rows)

    assert result["status"] == "EVALUATED"
    assert result["join"]["coverage"] == 1.0
    assert result["metrics"]["route_accuracy"]["value"] == 1.0
    assert result["metrics"]["auto_precision"]["value"] == 1.0
    assert result["metrics"]["hold_recall"]["value"] == 1.0
    assert result["metrics"]["table_exact"]["value"] == 1.0
    assert result["metrics"]["evidence_coordinate_exact"]["value"] == 1.0
    assert result["metrics"]["verdict_macro_f1"]["value"] == 1.0


def test_gold_hash_candidate_is_marked_for_review_queue_exclusion() -> None:
    sentence_hash = normalized_sentence_hash("취업자 수는 전년보다 10만 명 늘었다.")
    rows = build_review_queue_status(
        gold_rows=[
            {
                "row_id": "R1-GOLD-001",
                "sentence_hash": sentence_hash,
                "is_claim_human": "TRUE",
            }
        ],
        candidate_rows=[
            {"candidate_id": "candidate-1", "sentence_hash": sentence_hash, "stage": "R1"}
        ],
    )

    assert rows == [
        {
            "stage": "R1",
            "record_id": "candidate-1",
            "sentence_hash": sentence_hash,
            "gold_labeled": True,
            "gold_row_id": "R1-GOLD-001",
            "review_queue_action": "EXCLUDE_GOLD_LABELED",
        }
    ]


def test_new_evaluation_artifacts_never_overwrite_existing_directory(tmp_path: Path) -> None:
    summary = {
        "evaluation_id": "EVAL-001",
        "operational_metrics": {},
        "gold_evaluation_metrics": {},
        "evaluation_status": "NOT_EVALUABLE",
        "not_evaluable_reason_counts": {},
    }
    first_dir = write_evaluation_artifacts(
        output_root=tmp_path,
        evaluation_id="EVAL-001",
        evaluation_summary=summary,
        join_results=[],
        review_queue_status=[],
        input_files={},
    )
    first_bytes = (first_dir / "evaluation_summary.json").read_bytes()

    with pytest.raises(EvaluationOutputExistsError, match="EVALUATION_OUTPUT_ALREADY_EXISTS"):
        write_evaluation_artifacts(
            output_root=tmp_path,
            evaluation_id="EVAL-001",
            evaluation_summary={**summary, "evaluation_status": "EVALUATED"},
            join_results=[],
            review_queue_status=[],
            input_files={},
        )

    assert (first_dir / "evaluation_summary.json").read_bytes() == first_bytes
    assert json.loads(first_bytes)["evaluation_status"] == "NOT_EVALUABLE"
