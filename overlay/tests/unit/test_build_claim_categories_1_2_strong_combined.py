import json

from tools.build_claim_categories_1_2_strong_combined import build


def test_build_keeps_stage_metrics_separate(tmp_path):
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    one.write_text(json.dumps({
        "summary": {
            "original_status_counts": {"HOLD": 1},
            "final_status_counts": {"SUCCESS": 1},
            "success_improvement_vs_original": 1,
            "llm_validated_success_count": 1,
        },
        "context_results": [{"Claim번호": "A1", "최종실행상태": "SUCCESS"}],
        "llm_attempts": [{"최종채택": "YES"}],
    }), encoding="utf-8")
    two.write_text(json.dumps({
        "summary": {
            "original_status_counts": {"HOLD": 1},
            "final_status_counts": {"SUCCESS": 1},
            "success_improvement_vs_original": 1,
            "llm_validated_success_count": 1,
            "final_safe_child_count": 2,
        },
        "parent_results": [{"부모Claim번호": "A2", "최종실행상태": "SUCCESS"}],
        "child_results": [{}, {}],
        "llm_attempts": [{"최종채택": "YES"}],
    }), encoding="utf-8")
    output = tmp_path / "output"
    summary = build(
        category1_json=one, category2_json=two, output_dir=output,
        expected_category1_count=1, expected_category2_count=1,
    )
    assert summary["parent_claim_count"] == 2
    assert summary["llm_attempt_count"] == 2
    assert summary["llm_validated_accept_count"] == 2
    assert "do not add them as accuracy" in summary["stage_metric_warning"]
    payload = json.loads(
        (output / "CLAFACT_1번2번_강화_통합기록.json").read_text(encoding="utf-8")
    )
    assert payload["category1"]["context_results"][0]["Claim번호"] == "A1"
    assert payload["category2"]["parent_results"][0]["부모Claim번호"] == "A2"
