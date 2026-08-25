import csv
import json

from tools.run_claim_category2_strong_replay import run


def _write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _rule_runner(method, sentence):
    if sentence.startswith("성공"):
        return {
            "route_status": "AUTO", "reason_code": "RULE",
            "children": [
                {"text": "취업자는 100만 명이다."},
                {"text": "실업자는 20만 명이다."},
            ],
        }
    return {
        "route_status": "HUMAN_REVIEW", "reason_code": "SHARED_PREDICATE",
        "children": [{"text": sentence}],
    }


def _llm_runner(method, sentence, model=None):
    assert method == "llm_openai"
    assert model == "gpt-test"
    return {
        "route_status": "AUTO", "reason_code": "LLM_SPLIT",
        "children": [
            {"text": "수출은 -0.8%포인트였다.", "target_value_text": "-0.8%포인트", "target_value_role": "PRIOR_VALUE"},
            {"text": "수출은 0.1%포인트다.", "target_value_text": "0.1%포인트", "target_value_role": "CURRENT_VALUE"},
        ],
    }


def test_strong_replay_uses_llm_only_after_rule_hold(tmp_path):
    source = tmp_path / "parents.csv"
    common = {
        "기사번호": "A1", "작성일": "2025-01-01", "제목": "기사",
        "URL": "https://example.test", "앞문맥": "", "뒤문맥": "",
        "하위유형": "복수", "기존KOSIS상태": "HOLD", "기존KOSIS사유": "NO_COORDINATE",
        "최종실행상태": "HOLD", "성공실패사유": "OLD_HOLD",
    }
    _write_csv(source, [
        {**common, "부모Claim번호": "A1_1", "원문": "성공: 취업자는 100만 명이고 실업자는 20만 명이다."},
        {**common, "부모Claim번호": "A1_2", "원문": "수출은 -0.8%포인트에서 0.1%포인트로 올랐다."},
    ])
    output = tmp_path / "output"
    summary = run(
        baseline_parent_csv=source,
        output_dir=output,
        expected_parent_count=2,
        llm_model="gpt-test",
        workers=2,
        rule_runner=_rule_runner,
        llm_runner=_llm_runner,
    )
    assert summary["llm_attempt_count"] == 1
    assert summary["llm_validated_success_count"] == 1
    assert summary["final_status_counts"] == {"SUCCESS": 2}
    assert summary["success_improvement_vs_original"] == 2
    payload = json.loads(
        (output / "CLAFACT_2번_강화재실행_통합기록.json").read_text(encoding="utf-8")
    )
    assert len(payload["parent_results"]) == 2
    assert len(payload["child_results"]) == 4
    assert len(payload["llm_attempts"]) == 1
    assert payload["llm_attempts"][0]["최종채택"] == "YES"
