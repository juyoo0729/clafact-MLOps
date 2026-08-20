"""TDD tests for the Gold replay run writer (written before the implementation)."""

import json
from pathlib import Path

import pytest

from tools.run_gold_replay import (
    GoldReplayRunExistsError,
    GoldReplaySentenceHashMissingError,
    write_gold_replay_run,
)


def _minimal_results() -> dict[str, dict]:
    r1 = {
        "replay_kind": "R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL",
        "scope": {"synthetic_sentence_replay": True, "full_article_recall": False},
        "input_count": 1,
        "candidates": [
            {
                "article_id": "R1G-G001",
                "gold_row_id": "G001",
                "claim_candidate_id": "candidate_x",
                "source_sentence": "지표는 3만 명으로 1.0% 증가했다.",
                "sentence_hash": "a" * 64,
            }
        ],
        "holds": [],
    }
    r3 = {
        "scope_flags": ["R3_CONDITIONAL_ON_FROZEN_R2_GOLD", "NOT_END_TO_END"],
        "r2_input_source": "FROZEN_GOLD",
        "records": [
            {
                "claim_id": "NEWS_B-001-A01",
                "route_status": "HOLD",
                "reason_code": "CONCEPT_UNREGISTERED",
                "ranked_candidate_tbl_ids": None,
            }
        ],
        "holds": [{"claim_id": "NEWS_B-001-A01", "reason_code": "CONCEPT_UNREGISTERED"}],
    }
    r4 = {
        "snapshot_only": True,
        "verdict_rows": [
            {
                "claim_id": "NEWS_B-001-A01",
                "route_status": "HOLD",
                "verdict": "UNDETERMINED",
                "reason_code": "R2_CLAIM_PARSE_NOT_AUTO_OK",
                "evidence_cells": [],
            }
        ],
        "provenance_rows": [],
    }
    return {"r1": r1, "r3": r3, "r4": r4}


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    gold = tmp_path / "r1_gold.csv"
    gold.write_text("row_id,sentence\nG001,문장\n", encoding="utf-8")
    return {"r1_gold": gold}


def test_gold_replay_run_writes_versioned_artifacts(tmp_path: Path) -> None:
    results = _minimal_results()

    run_dir = write_gold_replay_run(
        run_root=tmp_path / "gold_replay_runs",
        run_id="GOLD-REPLAY-TEST-001",
        r1_result=results["r1"],
        r3_result=results["r3"],
        r4_result=results["r4"],
        input_files=_write_inputs(tmp_path),
        gold_sources={"r1_gold_version": "R1_ActiveReview_30_v_test"},
    )

    assert (run_dir / "r1" / "manifest.json").is_file()
    assert (run_dir / "r1" / "candidates.jsonl").is_file()
    assert (run_dir / "r3" / "manifest.json").is_file()
    assert (run_dir / "r3" / "ranked_candidates.jsonl").is_file()
    assert (run_dir / "r3" / "holds.jsonl").is_file()
    assert (run_dir / "r4" / "manifest.json").is_file()
    assert (run_dir / "r4" / "verdicts.jsonl").is_file()
    assert (run_dir / "r4" / "provenance.jsonl").is_file()
    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert run_manifest["run_id"] == "GOLD-REPLAY-TEST-001"
    assert run_manifest["scope"]["conditional_vs_e2e"] == "CONDITIONAL_REPLAY_NOT_END_TO_END"
    assert run_manifest["scope"]["r2_input_source"] == "FROZEN_GOLD"
    assert "input_sha256" in run_manifest and "output_sha256" in run_manifest
    r3_manifest = json.loads((run_dir / "r3" / "manifest.json").read_text(encoding="utf-8"))
    assert r3_manifest["hold_reason_counts"] == {"CONCEPT_UNREGISTERED": 1}


# --- 8. a new replay run never overwrites existing artifacts ---------------


def test_gold_replay_run_refuses_existing_run_id(tmp_path: Path) -> None:
    results = _minimal_results()
    kwargs = dict(
        run_root=tmp_path / "gold_replay_runs",
        r1_result=results["r1"],
        r3_result=results["r3"],
        r4_result=results["r4"],
        input_files=_write_inputs(tmp_path),
        gold_sources={},
    )
    write_gold_replay_run(run_id="GOLD-REPLAY-TEST-002", **kwargs)

    with pytest.raises(GoldReplayRunExistsError):
        write_gold_replay_run(run_id="GOLD-REPLAY-TEST-002", **kwargs)


# --- 9(E 부분). new R1 outputs must always carry a sentence hash ------------


def test_gold_replay_run_rejects_r1_candidates_without_sentence_hash(tmp_path: Path) -> None:
    results = _minimal_results()
    results["r1"]["candidates"][0]["sentence_hash"] = ""

    with pytest.raises(GoldReplaySentenceHashMissingError):
        write_gold_replay_run(
            run_root=tmp_path / "gold_replay_runs",
            run_id="GOLD-REPLAY-TEST-003",
            r1_result=results["r1"],
            r3_result=results["r3"],
            r4_result=results["r4"],
            input_files=_write_inputs(tmp_path),
            gold_sources={},
        )
