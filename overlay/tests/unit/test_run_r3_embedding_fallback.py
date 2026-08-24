import csv
import json

from tools.run_r3_embedding_fallback import run


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_runner_compares_embedding_with_prior_proposal_without_calling_it_gold(tmp_path):
    candidates = tmp_path / "candidates.csv"
    baseline = tmp_path / "baseline.csv"
    output = tmp_path / "output"
    _write_csv(
        candidates,
        [
            {
                "claim_id": "C1",
                "split": "dev",
                "indicator": "고령인구 수",
                "unit": "명",
                "frequency": "년",
                "calculation": "DIRECT_VALUE",
                "confidence_status": "LOW_LEXICAL_CONFIDENCE",
                "candidate_tbl_ids": "DT_A | DT_B",
                "candidate_tbl_names": "노인복지시설 수 | 주요 연령계층별 추계인구",
            }
        ],
    )
    _write_csv(
        baseline,
        [
            {
                "claim_id": "C1",
                "automatic_route_status": "HOLD_CANDIDATES_ATTACHED",
                "candidate_tbl_ids": "DT_B",
            }
        ],
    )

    def encoder(_texts):
        return [[1.0, 0.0], [0.2, 0.8], [0.9, 0.1]]

    summary = run(
        candidate_attachment_csv=candidates,
        baseline_route_csv=baseline,
        output_dir=output,
        encoder=encoder,
        model_id="test/model",
        model_revision="abc123",
    )

    assert summary["low_lexical_claim_count"] == 1
    assert summary["lexical_top1_prior_proposal_overlap_count"] == 0
    assert summary["embedding_top1_prior_proposal_overlap_count"] == 1
    assert summary["experiment_result"] == "IMPROVED_PROXY_ONLY"
    assert summary["accuracy_status"] == "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model"] == {"id": "test/model", "revision": "abc123"}
