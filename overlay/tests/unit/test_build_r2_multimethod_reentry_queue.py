import json

from tools.build_r2_multimethod_reentry_queue import build


def test_builds_12slot_reentry_queue_from_only_final_safe_rows(tmp_path) -> None:
    source = tmp_path / "combined.json"
    source.write_text(json.dumps({
        "final_category1_results": [{
            "기사번호": "A1", "Claim번호": "A1_1", "원문": "지난달 수출은 3% 증가했다.",
            "작성일": "2025-01-10", "보완기준기간": "2024-12", "보완주기": "MONTHLY",
            "최종실행상태": "SUCCESS",
        }],
        "final_category2_parent_results": [{
            "부모Claim번호": "A2_1", "작성일": "2025-01-10", "앞문맥": "앞", "뒤문맥": "뒤",
        }],
        "final_category2_child_results": [{
            "기사번호": "A2", "부모Claim번호": "A2_1", "자식Claim번호": "A2_1__S01",
            "자식Claim": "품목은 9개이다.", "정규화목표수치": "9개",
            "target_value_role": "CURRENT_VALUE", "부모최종실행상태": "SUCCESS",
            "자식검증상태": "VALID",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    summary = build(
        combined_json=source, output_dir=out,
        expected_category1_success=1, expected_category2_safe_children=1,
    )
    assert summary["record_count"] == 2
    assert summary["r3_auto_ready_count"] == 0
    rows = [json.loads(line) for line in (out / "r2_12slot_reentry_queue.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["known_slots"]["time"] == "2024-12"
    assert rows[1]["known_slots"]["target_value_role"] == "CURRENT_VALUE"
    assert rows[1]["verbatim_target_value"] == "9개"
    assert rows[1]["kosis_status"] == "NOT_RUN_R2_PRECONDITION"
