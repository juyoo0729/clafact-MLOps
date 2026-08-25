import csv

import tools.run_claim_category1_provider_cascade as cascade
from tools.run_claim_category1_provider_cascade import run
from tools.run_claim_category1_strong_context_replay import STRONG_CONTEXT_COLUMNS


def test_category1_provider_cascade_preserves_success_and_accepts_valid_hcx(tmp_path, monkeypatch) -> None:
    source = tmp_path / "context.csv"
    rows = [
        {
            "기사번호": "A1", "Claim번호": "A1_1", "작성일": "2025-01-10",
            "원문": "지난달 수출은 3% 증가했다.", "앞문맥": "", "뒤문맥": "",
            "하위유형": "상대기간복원형", "최종실행상태": "HOLD",
            "성공실패사유": "PERIOD_CONTEXT_UNRESOLVED",
        },
        {
            "기사번호": "A2", "Claim번호": "A2_1", "작성일": "2025-01-10",
            "원문": "지난달 고용은 2% 증가했다.", "앞문맥": "", "뒤문맥": "",
            "하위유형": "상대기간복원형", "최종실행상태": "SUCCESS",
            "성공실패사유": "LLM_CONTEXT_PERIOD_VALIDATED",
        },
    ]
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STRONG_CONTEXT_COLUMNS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)

    def fake_proposer(row, model):
        assert model == "HCX-007"
        return {
            "route_status": "AUTO", "target_period": "2024-12", "comparison_period": "",
            "frequency": "MONTHLY", "target_evidence": "지난달 수출은 3% 증가했다.",
            "comparison_evidence": "", "reason_code": "TEST",
        }

    monkeypatch.setattr(
        cascade,
        "execute_context_claim",
        lambda row, execution_time: {"성공실패사유": "PERIOD_CONTEXT_UNRESOLVED"},
    )

    summary = run(
        baseline_csv=source, output_dir=tmp_path / "out", expected_count=2,
        min_request_interval_seconds=0, proposer=fake_proposer,
    )
    assert summary["baseline_status_counts"] == {"HOLD": 1, "SUCCESS": 1}
    assert summary["final_status_counts"] == {"SUCCESS": 2}
    assert summary["provider_attempt_count"] == 1
    assert summary["provider_validated_success_count"] == 1
