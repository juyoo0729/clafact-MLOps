import csv
import json

from tools.run_claim_category1_strong_context_replay import run


def _write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_strong_context_replay_accepts_only_grounded_period(tmp_path):
    source = tmp_path / "context.csv"
    _write_csv(source, [{
        "기사번호": "A1", "Claim번호": "A1_1", "작성일": "2025-01-03",
        "제목": "자동차 판매", "URL": "https://example.test",
        "원문": "내수 판매가 감소했다.",
        "앞문맥": "작년 자동차 내수 판매는 2023년 대비 줄었다.",
        "뒤문맥": "2022년 해외 판매 통계도 함께 소개했다.", "하위유형": "시점문맥필요형",
        "기사원문정확일치": "YES", "기사내원문시작위치": "10",
        "최종실행상태": "HOLD", "성공실패사유": "MULTIPLE_CONTEXT_PERIODS_UNRESOLVED",
    }])

    def proposer(row, model):
        assert model == "gpt-test"
        return {
            "route_status": "AUTO", "target_period": "2024",
            "comparison_period": "2023", "frequency": "년",
            "target_evidence": "작년 자동차 내수 판매는 2023년 대비 줄었다.",
            "comparison_evidence": "2023년 대비", "reason_code": "RESOLVED",
        }

    output = tmp_path / "output"
    summary = run(
        baseline_context_csv=source, output_dir=output, expected_count=1,
        llm_model="gpt-test", workers=1, proposer=proposer,
    )
    assert summary["llm_attempt_count"] == 1
    assert summary["llm_validated_success_count"] == 1
    assert summary["final_status_counts"] == {"SUCCESS": 1}
    payload = json.loads(
        (output / "CLAFACT_1번_강화문맥재실행_통합기록.json").read_text(encoding="utf-8")
    )
    result = payload["context_results"][0]
    assert result["보완기준기간"] == "2024"
    assert result["보완비교기간"] == "2023"
    assert result["최종채택방식"] == "LLM_VALIDATED"
