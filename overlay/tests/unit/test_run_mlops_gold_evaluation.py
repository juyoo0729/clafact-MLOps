from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import pytest

from tools.run_mlops_gold_evaluation import _load_gold20_contract, _verify_declared_stage_outputs


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
