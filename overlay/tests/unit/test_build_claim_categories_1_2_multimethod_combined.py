import json

from tools.build_claim_categories_1_2_multimethod_combined import build


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_builds_final_multimethod_index_without_adding_stage_metrics(tmp_path) -> None:
    c1_openai = _dump(tmp_path / "c1o.json", {
        "summary": {"original_status_counts": {"HOLD": 1}, "final_status_counts": {"SUCCESS": 1}, "llm_validated_success_count": 1},
        "llm_attempts": [{}],
    })
    c1_final = _dump(tmp_path / "c1f.json", {
        "summary": {"provider_validated_success_count": 0, "provider_attempt_status_counts": {"HOLD": 1}},
        "context_results": [{"Claim번호": "A1", "최종실행상태": "SUCCESS"}],
        "provider_attempts": [{}],
    })
    c2_openai = _dump(tmp_path / "c2o.json", {
        "summary": {"original_status_counts": {"HOLD": 1}, "final_status_counts": {"SUCCESS": 1}, "llm_validated_success_count": 1},
        "llm_attempts": [{}],
    })
    c2_hcx1 = _dump(tmp_path / "c2h1.json", {"summary": {"provider_validated_success_count": 0}, "provider_attempts": [{}]})
    c2_final = _dump(tmp_path / "c2f.json", {
        "summary": {"provider_validated_success_count": 0},
        "parent_results": [{"부모Claim번호": "A2", "최종실행상태": "SUCCESS"}],
        "child_results": [{"부모최종실행상태": "SUCCESS", "자식검증상태": "VALID"}],
        "provider_attempts": [{}],
    })
    benchmark = _dump(tmp_path / "b.json", {"summary": {"method_count": 6, "scoreboard": {}, "validated_union_success_count": 1}})
    summary = build(
        category1_openai_json=c1_openai, category1_final_json=c1_final,
        category2_openai_json=c2_openai, category2_hcx_stage1_json=c2_hcx1,
        category2_final_json=c2_final, offline_benchmark_json=benchmark,
        output_dir=tmp_path / "out", expected_category1_count=1, expected_category2_count=1,
    )
    assert summary["parent_claim_count"] == 2
    assert summary["category2"]["final_safe_child_count"] == 1
    assert summary["kosis_requery_count"] == 0
    assert summary["accuracy_status"].startswith("NOT_EVALUABLE")
    assert "운영 통과율은 Gold 정확도가 아니다" in (tmp_path / "out" / "safe_summary.txt").read_text(encoding="utf-8")
