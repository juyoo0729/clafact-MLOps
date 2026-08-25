import csv

from tools.run_claim_category2_provider_cascade import run


def _write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def test_provider_cascade_keeps_success_and_resolves_remaining_hold(tmp_path):
    parents = tmp_path / "parents.csv"; children = tmp_path / "children.csv"
    common = {"기사번호": "A1", "작성일": "2025-01-01", "제목": "기사", "URL": "u", "앞문맥": "", "뒤문맥": "", "하위유형": "복수"}
    _write_csv(parents, [
        {**common, "부모Claim번호": "A1_1", "원문": "취업자는 100만 명이고 실업자는 20만 명이다.", "최종실행상태": "SUCCESS", "성공실패사유": "ATOMIC_SPLIT_VALIDATED"},
        {**common, "부모Claim번호": "A1_2", "원문": "이 9개 품목은 수출의 60%를 차지한다.", "최종실행상태": "HOLD", "성공실패사유": "SINGLE"},
    ])
    _write_csv(children, [
        {"부모Claim번호": "A1_1", "부모최종실행상태": "SUCCESS", "자식Claim번호": "A1_1__S01", "자식Claim": "취업자는 100만 명이다.", "자식검증상태": "VALID"},
        {"부모Claim번호": "A1_1", "부모최종실행상태": "SUCCESS", "자식Claim번호": "A1_1__S02", "자식Claim": "실업자는 20만 명이다.", "자식검증상태": "VALID"},
        {"부모Claim번호": "A1_2", "부모최종실행상태": "HOLD", "자식Claim번호": "A1_2__S01", "자식Claim": "이 9개 품목은 수출의 60%를 차지한다.", "자식검증상태": "HOLD"},
    ])

    def provider(method, sentence, model=None):
        assert method == "llm_hcx" and model == "hcx-test"
        return {"route_status": "AUTO", "reason_code": "SPLIT", "children": [
            {"text": "품목 수는 9개다.", "target_value_text": "9", "target_value_role": "CURRENT_VALUE"},
            {"text": "전체 수출액에서 차지하는 비율은 60%다.", "target_value_text": "60%", "target_value_role": "SHARE_VALUE"},
        ]}

    output = tmp_path / "output"
    summary = run(
        baseline_parent_csv=parents, baseline_child_csv=children, output_dir=output,
        provider_method="llm_hcx", provider_model="hcx-test", expected_parent_count=2,
        workers=2, provider_runner=provider,
    )
    assert summary["provider_attempt_count"] == 1
    assert summary["provider_validated_success_count"] == 1
    assert summary["final_status_counts"] == {"SUCCESS": 2}
    assert summary["final_safe_child_count"] == 4
