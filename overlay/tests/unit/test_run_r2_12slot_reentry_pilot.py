import json

from schemas.claim import ClaimSchema
from tools.run_r2_12slot_reentry_pilot import run


class FakeExtractor:
    def extract(self, sentence):
        if "수출액" in sentence:
            return ClaimSchema(
                claim_id="provider", source_sentence=sentence, indicator="수출액",
                value=49_100_000_000, target_value_role="CURRENT_VALUE", unit="달러",
                time=None, frequency=None, calculation="DIRECT_VALUE", parse_status="AUTO_OK",
            )
        return ClaimSchema(
            claim_id="provider", source_sentence=sentence, indicator="품목 수",
            value=9, target_value_role="CURRENT_VALUE", unit="개",
            time="2025", frequency="년", calculation="DIRECT_VALUE", parse_status="AUTO_OK",
        )


class AnnualExtractor:
    def extract(self, sentence):
        return ClaimSchema(
            claim_id="provider", source_sentence=sentence, indicator="수출액",
            value=49_100_000_000, target_value_role="CURRENT_VALUE", unit="달러",
            time="2025년", frequency="년", calculation="DIRECT_VALUE", parse_status="AUTO_OK",
        )


def test_reentry_enriches_known_period_and_requires_expected_child_value(tmp_path) -> None:
    source = tmp_path / "pilot.jsonl"
    rows = [
        {
            "claim_id": "A1_1", "article_id": "A1", "parent_claim_id": "",
            "source_type": "CATEGORY1_CONTEXT_COMPLETED_PARENT",
            "source_sentence": "수출액은 491억달러였다.", "published_at": "2025-02-10",
            "context_before": "", "context_after": "", "verbatim_target_value": "",
            "period_evidence": "지난달", "known_slots": {
                "time": "2025-01", "frequency": "월", "target_value_role": None,
            },
        },
        {
            "claim_id": "A2_1__S01", "article_id": "A2", "parent_claim_id": "A2_1",
            "source_type": "CATEGORY2_VALIDATED_ATOMIC_CHILD",
            "source_sentence": "품목 수는 9개이다.", "published_at": "2025-01-10",
            "context_before": "", "context_after": "", "verbatim_target_value": "9개",
            "period_evidence": "", "known_slots": {
                "time": None, "frequency": None, "target_value_role": "CURRENT_VALUE",
            },
        },
    ]
    source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    summary = run(
        pilot_jsonl=source, output_dir=tmp_path / "out", extractor=FakeExtractor(),
        provider_name="fake", provider_model="fake", min_request_interval_seconds=0,
        expected_count=2,
    )

    assert summary["status_counts"] == {"R3_READY": 2}
    results = [json.loads(line) for line in (tmp_path / "out" / "r2_12slot_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert results[0]["final_claim"]["time"] == "2025-01"
    assert results[0]["final_claim"]["frequency"] == "월"
    assert results[1]["gates"]["expected_value_trace"] == "PASS"
    assert results[1]["kosis_status"] == "NOT_RUN_PILOT_ONLY"


def test_reentry_holds_an_upstream_role_outside_the_claim_contract(tmp_path) -> None:
    source = tmp_path / "pilot.jsonl"
    row = {
        "claim_id": "A3_1__S01", "article_id": "A3", "parent_claim_id": "A3_1",
        "source_type": "CATEGORY2_VALIDATED_ATOMIC_CHILD",
        "source_sentence": "품목 수는 9개이다.", "published_at": "2025-01-10",
        "context_before": "", "context_after": "", "verbatim_target_value": "9개",
        "period_evidence": "", "known_slots": {
            "time": None, "frequency": None, "target_value_role": "RANGE_VALUE",
        },
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = run(
        pilot_jsonl=source, output_dir=tmp_path / "out", extractor=FakeExtractor(),
        provider_name="fake", provider_model="fake", min_request_interval_seconds=0,
        expected_count=1,
    )

    assert summary["status_counts"] == {"HOLD": 1}
    result = json.loads((tmp_path / "out" / "r2_12slot_results.jsonl").read_text(encoding="utf-8"))
    assert result["reason_code"] == "UPSTREAM_TARGET_VALUE_ROLE_INVALID"
    assert result["kosis_query_count"] == 0


def test_reentry_holds_unsupported_context_frequency(tmp_path) -> None:
    source = tmp_path / "pilot.jsonl"
    row = {
        "claim_id": "A4_1", "article_id": "A4", "parent_claim_id": "",
        "source_type": "CATEGORY1_CONTEXT_COMPLETED_PARENT",
        "source_sentence": "수출액은 491억달러였다.", "published_at": "2025-02-10",
        "context_before": "", "context_after": "", "verbatim_target_value": "",
        "period_evidence": "1~20일", "known_slots": {
            "time": "2025-01-01/2025-01-20", "frequency": "부분기간",
            "target_value_role": None,
        },
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = run(
        pilot_jsonl=source, output_dir=tmp_path / "out", extractor=FakeExtractor(),
        provider_name="fake", provider_model="fake", min_request_interval_seconds=0,
        expected_count=1,
    )

    assert summary["status_counts"] == {"HOLD": 1}
    result = json.loads((tmp_path / "out" / "r2_12slot_results.jsonl").read_text(encoding="utf-8"))
    assert result["gates"]["known_context_consistency"] == "FAIL:frequency_unsupported"
    assert result["reason_code"] == "KNOWN_CONTEXT_CONFLICT"


def test_reentry_treats_iso_year_and_korean_year_as_the_same_period(tmp_path) -> None:
    source = tmp_path / "pilot.jsonl"
    row = {
        "claim_id": "A5_1", "article_id": "A5", "parent_claim_id": "",
        "source_type": "CATEGORY1_CONTEXT_COMPLETED_PARENT",
        "source_sentence": "수출액은 491억달러였다.", "published_at": "2025-12-10",
        "context_before": "", "context_after": "", "verbatim_target_value": "",
        "period_evidence": "올해", "known_slots": {
            "time": "2025", "frequency": "annual", "target_value_role": None,
        },
    }
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = run(
        pilot_jsonl=source, output_dir=tmp_path / "out", extractor=AnnualExtractor(),
        provider_name="fake", provider_model="fake", min_request_interval_seconds=0,
        expected_count=1,
    )

    assert summary["status_counts"] == {"R3_READY": 1}
