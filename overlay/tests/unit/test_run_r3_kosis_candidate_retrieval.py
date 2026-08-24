import csv
import json

from tools.run_r3_kosis_candidate_retrieval import run


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_runner_writes_hashed_dev_artifact_without_accuracy_claim(tmp_path):
    replay = tmp_path / "replay.csv"
    candidates = tmp_path / "candidates.jsonl"
    baseline = tmp_path / "baseline.csv"
    output = tmp_path / "output"
    _write_csv(
        replay,
        [
            {
                "claim_id": "C1",
                "split": "dev",
                "r3_reason_code": "CONCEPT_UNREGISTERED",
                "indicator": "출생아 수",
                "unit": "명",
                "frequency": "월",
                "calculation": "DIRECT_VALUE",
            }
        ],
    )
    candidates.write_text(
        json.dumps(
            {
                "indicator": "출생아 수",
                "candidates": [
                    {"org_id": "101", "tbl_id": "DT_BIRTH", "tbl_name": "월별 출생아수"}
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_csv(
        baseline,
        [{"claim_id": "C1", "automatic_route_status": "HOLD_NO_SAFE_SHORTLIST"}],
    )

    summary = run(
        replay_csv=replay,
        candidate_jsonl=candidates,
        baseline_route_csv=baseline,
        output_dir=output,
    )

    assert summary["attachment_coverage_before"] == 0.0
    assert summary["attachment_coverage_after"] == 1.0
    assert summary["accuracy_status"] == "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["candidate_attachment_csv"]["sha256"]
    assert (output / "candidate_attachment.csv").exists()
