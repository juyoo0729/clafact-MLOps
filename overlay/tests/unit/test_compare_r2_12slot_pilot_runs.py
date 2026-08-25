import json

from tools.compare_r2_12slot_pilot_runs import compare


def _write(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_compares_only_stored_claim_contracts_without_reexecuting(tmp_path) -> None:
    first = tmp_path / "v1.jsonl"
    second = tmp_path / "v2.jsonl"
    _write(first, [
        {"claim_id": "A", "status": "R3_READY", "reason_code": "R3_READY", "final_claim": {"value": 1}},
        {"claim_id": "B", "status": "API_ERROR", "reason_code": "HTTPError_429", "final_claim": None},
    ])
    _write(second, [
        {"claim_id": "A", "status": "HOLD", "reason_code": "SOURCE_VALUE_TRACE_FAILED", "final_claim": {"value": 2}},
        {"claim_id": "B", "status": "R3_READY", "reason_code": "R3_READY", "final_claim": {"value": 3}},
    ])

    summary = compare(first_results=first, second_results=second, output_dir=tmp_path / "out")

    assert summary["joined_claim_count"] == 2
    assert summary["both_provider_success_count"] == 1
    assert summary["final_claim_exact_changed_count"] == 1
    assert summary["status_changed_with_both_provider_success_count"] == 1
    assert summary["kosis_query_count"] == 0
