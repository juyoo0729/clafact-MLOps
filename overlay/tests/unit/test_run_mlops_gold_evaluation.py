from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import pytest

import tools.run_mlops_gold_evaluation as linked_evaluation
from tools.run_mlops_gold_evaluation import (
    _load_gold20_contract,
    _verify_declared_stage_outputs,
    run_linked_evaluation,
)


PROJECT = Path(__file__).resolve().parents[2]


def test_real_gold20_sources_form_one_consistent_route_contract() -> None:
    rows = _load_gold20_contract(
        fixture_path=PROJECT / "tests" / "goldset" / "fixtures" / "pilot20.json",
        routes_path=PROJECT / "tests" / "goldset" / "fixtures" / "pilot20_expected_routes.json",
        route_report_path=PROJECT / "data" / "goldset_reports" / "pilot20_route_report.json",
    )

    assert len(rows) == 20
    assert len({row["claim_id"] for row in rows}) == 20
    assert Counter(row["expected_route"] for row in rows) == {"AUTO": 13, "HOLD": 7}
    assert Counter(row["expected_verdict"] for row in rows) == {
        "MATCH": 13,
        "UNDETERMINED": 7,
    }
    assert all(row["gold_coordinates"] is None for row in rows)


def test_post_run_verifies_every_declared_stage_output_before_scoring(tmp_path: Path) -> None:
    stage_dir = tmp_path / "r1"
    stage_dir.mkdir()
    output = stage_dir / "r1_candidates.jsonl"
    output.write_text("{}\n", encoding="utf-8")
    digest = sha256(output.read_bytes()).hexdigest()
    (stage_dir / "manifest.json").write_text(
        json.dumps({"stage": "r1", "output_sha256": {output.name: digest}}),
        encoding="utf-8",
    )
    run_manifest = {"stages": [{"stage": "r1"}]}

    verified = _verify_declared_stage_outputs(tmp_path, run_manifest)
    assert verified["r1_r1_candidates.jsonl"] == output

    output.write_text('{"changed": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="STAGE_OUTPUT_SHA256_MISMATCH"):
        _verify_declared_stage_outputs(tmp_path, run_manifest)


def test_missing_r3_r4_prediction_artifacts_are_explicitly_not_evaluable() -> None:
    gold_rows = [{"claim_id": "NEWS_B-001"}, {"claim_id": "KOSIS_SEED-001"}]

    r3 = linked_evaluation._missing_prediction_result("R3", gold_rows)
    r4 = linked_evaluation._missing_prediction_result("R4", gold_rows)

    assert r3["status"] == "NOT_EVALUABLE"
    assert r3["reason_code"] == "R3_PREDICTION_ARTIFACT_MISSING"
    assert r3["metrics"] == {}
    assert r3["join"]["joined_count"] == 0
    assert r4["status"] == "NOT_EVALUABLE"
    assert r4["reason_code"] == "R4_PREDICTION_ARTIFACT_MISSING"
    assert r4["metrics"] == {}
    assert r4["join"]["joined_count"] == 0


def test_linked_evaluation_writes_artifacts_when_r3_r4_predictions_are_missing(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run-001"
    stage_outputs = {
        "r1": {"r1_candidates.jsonl": ""},
        "r2": {"r3_ready_claims.jsonl": "", "r2_holds.jsonl": ""},
    }
    stages = []
    for stage, outputs in stage_outputs.items():
        stage_dir = run_dir / stage
        stage_dir.mkdir(parents=True)
        output_hashes = {}
        for name, content in outputs.items():
            path = stage_dir / name
            path.write_text(content, encoding="utf-8")
            output_hashes[name] = sha256(path.read_bytes()).hexdigest()
        (stage_dir / "manifest.json").write_text(
            json.dumps({"stage": stage, "output_sha256": output_hashes}),
            encoding="utf-8",
        )
        stages.append({"stage": stage, "status": "HOLD", "counts": {}})
    run_manifest = run_dir / "run_manifest.json"
    run_manifest.write_text(
        json.dumps({"pipeline_run_id": "run-001", "status": "PIPELINE_FAILED", "stages": stages}),
        encoding="utf-8",
    )
    r1_gold = tmp_path / "r1_gold.csv"
    r1_gold.write_text("row_id,sentence_hash,is_claim_human\n", encoding="utf-8")
    r2_gold = tmp_path / "r2_gold.jsonl"
    r2_predictions = tmp_path / "r2_predictions.jsonl"
    r2_gold.write_text("", encoding="utf-8")
    r2_predictions.write_text("", encoding="utf-8")

    output_dir = run_linked_evaluation(
        run_manifest_path=run_manifest,
        r1_gold_path=r1_gold,
        r2_gold_path=r2_gold,
        r2_predictions_path=r2_predictions,
        r2_split="dev",
        gold20_fixture_path=PROJECT / "tests" / "goldset" / "fixtures" / "pilot20.json",
        gold20_routes_path=(
            PROJECT / "tests" / "goldset" / "fixtures" / "pilot20_expected_routes.json"
        ),
        gold20_route_report_path=(
            PROJECT / "data" / "goldset_reports" / "pilot20_route_report.json"
        ),
        r3_predictions_path=None,
        r4_predictions_path=None,
        output_root=tmp_path / "evaluations",
        evaluation_id="MISSING-R3-R4-001",
    )

    summary = json.loads((output_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert summary["gold_evaluation_metrics"]["r3"]["reason_code"] == (
        "R3_PREDICTION_ARTIFACT_MISSING"
    )
    assert summary["gold_evaluation_metrics"]["r4"]["reason_code"] == (
        "R4_PREDICTION_ARTIFACT_MISSING"
    )
    assert summary["gold_evaluation_metrics"]["r3"]["metrics"] == {}
    assert summary["gold_evaluation_metrics"]["r4"]["metrics"] == {}
    assert (output_dir / "join_results.jsonl").is_file()
    assert (output_dir / "sha256_manifest.json").is_file()
