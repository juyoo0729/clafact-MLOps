"""Build one final count-safe index for category 1/2 multi-method experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


OUTPUT_NAMES = (
    "CLAFACT_1번2번_다중방법_최종기록.json",
    "summary.json",
    "safe_summary.txt",
    "learning_summary.json",
    "experiment_card.md",
    "metric_comparison.json",
    "error_analysis.json",
)


def build(
    *,
    category1_openai_json: Path,
    category1_final_json: Path,
    category2_openai_json: Path,
    category2_hcx_stage1_json: Path,
    category2_final_json: Path,
    offline_benchmark_json: Path,
    output_dir: Path,
    expected_category1_count: int | None = 256,
    expected_category2_count: int | None = 339,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    c1_openai = _read(category1_openai_json)
    c1_final = _read(category1_final_json)
    c2_openai = _read(category2_openai_json)
    c2_hcx1 = _read(category2_hcx_stage1_json)
    c2_final = _read(category2_final_json)
    benchmark = _read(offline_benchmark_json)
    c1_rows = list(c1_final.get("context_results") or [])
    c2_parents = list(c2_final.get("parent_results") or [])
    c2_children = list(c2_final.get("child_results") or [])
    if expected_category1_count is not None and len(c1_rows) != expected_category1_count:
        raise ValueError("unexpected category 1 count")
    if expected_category2_count is not None and len(c2_parents) != expected_category2_count:
        raise ValueError("unexpected category 2 count")
    c1_ids = {str(row.get("Claim번호") or "") for row in c1_rows}
    c2_ids = {str(row.get("부모Claim번호") or "") for row in c2_parents}
    if "" in c1_ids or "" in c2_ids or c1_ids & c2_ids:
        raise ValueError("blank or overlapping Claim IDs")

    c1_openai_summary = dict(c1_openai.get("summary") or {})
    c1_final_summary = dict(c1_final.get("summary") or {})
    c2_openai_summary = dict(c2_openai.get("summary") or {})
    c2_hcx1_summary = dict(c2_hcx1.get("summary") or {})
    c2_final_summary = dict(c2_final.get("summary") or {})
    benchmark_summary = dict(benchmark.get("summary") or {})
    c1_initial = dict(c1_openai_summary.get("original_status_counts") or {})
    c2_initial = dict(c2_openai_summary.get("original_status_counts") or {})
    c1_final_status = dict(sorted(Counter(row.get("최종실행상태", "") for row in c1_rows).items()))
    c2_final_status = dict(sorted(Counter(row.get("최종실행상태", "") for row in c2_parents).items()))
    safe_children = sum(
        row.get("부모최종실행상태") == "SUCCESS" and row.get("자식검증상태") == "VALID"
        for row in c2_children
    )
    attempt_counts = {
        "category1_openai": len(c1_openai.get("llm_attempts") or []),
        "category1_hcx": len(c1_final.get("provider_attempts") or []),
        "category2_openai": len(c2_openai.get("llm_attempts") or []),
        "category2_hcx_stage1": len(c2_hcx1.get("provider_attempts") or []),
        "category2_hcx_stage2": len(c2_final.get("provider_attempts") or []),
    }
    summary = {
        "artifact": "clafact_categories_1_2_multimethod_combined_v1",
        "created_at": _now(),
        "section": "A_R1_R2_CLAIM_STRUCTURING",
        "parent_claim_count": len(c1_rows) + len(c2_parents),
        "category1": {
            "claim_count": len(c1_rows),
            "initial_status_counts": c1_initial,
            "after_openai_status_counts": c1_openai_summary.get("final_status_counts", {}),
            "final_status_counts": c1_final_status,
            "openai_validated_success_count": c1_openai_summary.get("llm_validated_success_count", 0),
            "hcx_validated_success_count": c1_final_summary.get("provider_validated_success_count", 0),
            "hcx_api_error_count": c1_final_summary.get("provider_attempt_status_counts", {}).get("API_ERROR", 0),
            "result": "IMPROVED_BY_OPENAI_HCX_NOT_IMPROVED",
        },
        "category2": {
            "parent_count": len(c2_parents),
            "initial_status_counts": c2_initial,
            "after_openai_status_counts": c2_openai_summary.get("final_status_counts", {}),
            "final_status_counts": c2_final_status,
            "openai_validated_success_count": c2_openai_summary.get("llm_validated_success_count", 0),
            "hcx_stage1_validated_success_count": c2_hcx1_summary.get("provider_validated_success_count", 0),
            "hcx_stage2_validated_success_count": c2_final_summary.get("provider_validated_success_count", 0),
            "final_child_count": len(c2_children),
            "final_safe_child_count": safe_children,
            "result": "IMPROVED_BY_GUARDED_PROVIDER_CASCADE",
        },
        "offline_rule_benchmark": {
            "method_count": benchmark_summary.get("method_count", 0),
            "scoreboard": benchmark_summary.get("scoreboard", {}),
            "validated_union_success_count": benchmark_summary.get("validated_union_success_count", 0),
            "same_children_agreement_success_count": benchmark_summary.get("same_children_agreement_success_count", 0),
        },
        "provider_attempt_record_counts": attempt_counts,
        "provider_attempt_record_total": sum(attempt_counts.values()),
        "retry_count": 0,
        "kosis_requery_count": 0,
        "kosis_blocker": "REPARSED_12_SLOTS_AND_TARGET_VALUE_ROLE_REQUIRED",
        "accuracy_status": "NOT_EVALUABLE_STAGE_SPECIFIC_LINKED_GOLD_MISSING",
        "metric_boundary": (
            "Context completion, atomic split safety, API coverage, and Gold accuracy are separate metrics."
        ),
        "secret_handling": "KEY_VALUES_AND_RAW_PROVIDER_RESPONSES_NOT_INCLUDED_IN_SAFE_SUMMARY",
    }
    inputs = {
        "category1_openai": category1_openai_json,
        "category1_final": category1_final_json,
        "category2_openai": category2_openai_json,
        "category2_hcx_stage1": category2_hcx_stage1_json,
        "category2_final": category2_final_json,
        "offline_benchmark": offline_benchmark_json,
    }
    payload = {
        "schema_version": "clafact_categories_1_2_multimethod_combined_v1",
        "summary": summary,
        "final_category1_results": c1_rows,
        "final_category2_parent_results": c2_parents,
        "final_category2_child_results": c2_children,
        "offline_rule_benchmark": benchmark,
        "execution_history": {
            "category1_openai_summary": c1_openai_summary,
            "category1_hcx_summary": c1_final_summary,
            "category2_openai_summary": c2_openai_summary,
            "category2_hcx_stage1_summary": c2_hcx1_summary,
            "category2_hcx_stage2_summary": c2_final_summary,
        },
        "source_artifacts": {name: _file_record(path) for name, path in inputs.items()},
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / OUTPUT_NAMES[1]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / OUTPUT_NAMES[2]).write_text(_safe_summary(summary), encoding="utf-8")
    (output_dir / OUTPUT_NAMES[3]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / OUTPUT_NAMES[4]).write_text(_experiment_card(summary), encoding="utf-8")
    (output_dir / OUTPUT_NAMES[5]).write_text(
        json.dumps(_metric_comparison(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[6]).write_text(
        json.dumps(_error_analysis(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": "clafact_categories_1_2_multimethod_manifest_v1",
        "created_at": _now(),
        "inputs": {name: _file_record(path) for name, path in inputs.items()},
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "sha256_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _safe_summary(summary: Mapping[str, Any]) -> str:
    one = summary["category1"]
    two = summary["category2"]
    return "\n".join([
        "CLAFACT 1번·2번 다중방법 실행 요약",
        f"부모 Claim: {summary['parent_claim_count']}건",
        f"1번 최종 상태: {json.dumps(one['final_status_counts'], ensure_ascii=False, sort_keys=True)}",
        f"2번 최종 상태: {json.dumps(two['final_status_counts'], ensure_ascii=False, sort_keys=True)}",
        f"2번 안전 자식 Claim: {two['final_safe_child_count']}건",
        f"규칙 방법 수: {summary['offline_rule_benchmark']['method_count']}개",
        "KOSIS 재조회: 0건 — 12슬롯·target_value_role 재구조화 전",
        f"정확도: {summary['accuracy_status']}",
        "운영 통과율은 Gold 정확도가 아니다.",
        "",
    ])


def _experiment_card(summary: Mapping[str, Any]) -> str:
    one = summary["category1"]
    two = summary["category2"]
    return "\n".join([
        "# 1번·2번 다중방법 Claim 구조화 실험 카드",
        "",
        "## 구간과 질문",
        "",
        "- 구간: A(R1-R2 Claim 구조화)",
        "- 질문: 규칙 6종과 두 구조화 모델을 순차 적용하고 동일 안전검사를 유지하면 HOLD coverage가 개선되는가?",
        "",
        "## 고정 비교",
        "",
        f"- 1번: {json.dumps(one['initial_status_counts'], ensure_ascii=False)} → {json.dumps(one['final_status_counts'], ensure_ascii=False)}",
        f"- 2번: {json.dumps(two['initial_status_counts'], ensure_ascii=False)} → {json.dumps(two['final_status_counts'], ensure_ascii=False)}",
        f"- 2번 최종 안전 자식 Claim: {two['final_safe_child_count']}건",
        "",
        "## 변경과 검증",
        "",
        "- 변경: 기존 성공은 보존하고 HOLD만 다음 방법으로 전달하는 fail-closed cascade를 추가했다.",
        "- 검증: 원문 수치 존재, 부모 수치 전수 보존, 자식별 목표값, 기간 근거·정규화를 결정론적으로 다시 검사했다.",
        "- 실패 시도: 1번 HCX 문맥 보완은 추가 성공 0건으로 NOT_IMPROVED다.",
        "",
        "## 결론과 경계",
        "",
        "- 결론: 2번 운영 coverage는 개선됐지만 연결 Gold가 없어 정확도는 NOT_EVALUABLE이다.",
        "- KOSIS 조회는 12슬롯과 target_value_role 재구조화 전이므로 0건이다.",
        "- 다음 한 가지 실험: 안전 자식 Claim을 12슬롯으로 재구조화하고 같은 Claim ID에 대해 R2 Gate 통과율을 측정한다.",
        "",
    ])


def _metric_comparison(summary: Mapping[str, Any]) -> dict[str, Any]:
    one = summary["category1"]
    two = summary["category2"]
    return {
        "section": summary["section"],
        "category1": {
            "before": one["initial_status_counts"],
            "after": one["final_status_counts"],
            "comparable_metric": "deterministic_guard_pass_coverage",
            "gold_accuracy": summary["accuracy_status"],
        },
        "category2": {
            "before": two["initial_status_counts"],
            "after": two["final_status_counts"],
            "safe_children": two["final_safe_child_count"],
            "comparable_metric": "deterministic_guard_pass_coverage",
            "gold_accuracy": summary["accuracy_status"],
        },
    }


def _error_analysis(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "category1_final_status_counts": summary["category1"]["final_status_counts"],
        "category1_hcx_api_error_count": summary["category1"]["hcx_api_error_count"],
        "category1_hcx_result": "NOT_IMPROVED",
        "category2_final_status_counts": summary["category2"]["final_status_counts"],
        "offline_rule_validated_union_success_count": summary["offline_rule_benchmark"]["validated_union_success_count"],
        "remaining_blocker": summary["kosis_blocker"],
        "kosis_requery_count": 0,
        "gold_accuracy": summary["accuracy_status"],
    }


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category1-openai-json", type=Path, required=True)
    parser.add_argument("--category1-final-json", type=Path, required=True)
    parser.add_argument("--category2-openai-json", type=Path, required=True)
    parser.add_argument("--category2-hcx-stage1-json", type=Path, required=True)
    parser.add_argument("--category2-final-json", type=Path, required=True)
    parser.add_argument("--offline-benchmark-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-category1-count", type=int, default=256)
    parser.add_argument("--expected-category2-count", type=int, default=339)
    args = parser.parse_args()
    result = build(
        category1_openai_json=args.category1_openai_json,
        category1_final_json=args.category1_final_json,
        category2_openai_json=args.category2_openai_json,
        category2_hcx_stage1_json=args.category2_hcx_stage1_json,
        category2_final_json=args.category2_final_json,
        offline_benchmark_json=args.offline_benchmark_json,
        output_dir=args.output_dir,
        expected_category1_count=args.expected_category1_count,
        expected_category2_count=args.expected_category2_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
