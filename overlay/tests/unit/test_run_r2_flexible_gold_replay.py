import hashlib
import json

from tools.run_r2_flexible_gold_replay import run


def test_runner_verifies_gold_and_writes_privacy_safe_ledger(tmp_path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "claim_id": "C1",
                "article_id": "A1",
                "split": "dev",
                "gold_time_source": "context_required",
                "sentence": "공개 출력에 복사하지 않을 기사 문장",
                "expected_parse_status": "HOLD",
                "expected_claim_slots": {
                    "indicator": "취업자 수",
                    "value": 10,
                    "unit": "명",
                    "time": None,
                    "frequency": None,
                    "calculation": "DIRECT_VALUE",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "gold_count": 1,
                "gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "output"
    summary = run(gold_path=gold, gold_manifest_path=manifest, output_dir=output)

    ledger = (output / "r2_12slot_flexible_ledger.jsonl").read_text(encoding="utf-8")
    assert summary["flexible_route_counts"] == {"ENRICHMENT_REQUIRED": 1}
    assert "공개 출력에 복사하지 않을 기사 문장" not in ledger
    assert (output / "manifest.json").exists()
