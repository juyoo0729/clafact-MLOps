"""Compare all deterministic Claim split methods on the same category 2 parents."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from core.claim_split_methods import available_offline_methods, run_split_method
from tools.run_claim_categories_1_2_audit import execute_split_claim


OUTPUT_NAMES = ("method_scoreboard.json", "per_claim_method_results.jsonl", "summary.json")


def run(
    *,
    parent_csv: Path,
    output_dir: Path,
    expected_parent_count: int | None = 339,
    methods: Sequence[str] | None = None,
    method_runner: Callable[[str, str], Mapping[str, Any]] = run_split_method,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    parents = _read_csv(parent_csv)
    if expected_parent_count is not None and len(parents) != expected_parent_count:
        raise ValueError("unexpected parent count")
    selected_methods = tuple(methods or available_offline_methods())
    execution_time = datetime.now(timezone.utc).isoformat()
    scores: dict[str, Counter[str]] = {method: Counter() for method in selected_methods}
    reason_counts: dict[str, Counter[str]] = {method: Counter() for method in selected_methods}
    per_claim: list[dict[str, Any]] = []
    union_success_ids: set[str] = set()
    agreement_success_ids: set[str] = set()
    single_method_success_ids: set[str] = set()

    for source_parent in parents:
        source = _source_row(source_parent)
        method_results: dict[str, Any] = {}
        signature_methods: dict[str, list[str]] = defaultdict(list)
        successful_methods: list[str] = []
        for method in selected_methods:
            parent, children = execute_split_claim(
                source,
                execution_time=execution_time,
                split_method=method,
                split_runner=method_runner,
            )
            status = parent.get("최종실행상태", "")
            reason = parent.get("성공실패사유", "")
            safe_children = [child for child in children if child.get("자식검증상태") == "VALID"]
            scores[method]["parent_count"] += 1
            scores[method][f"status_{status}"] += 1
            scores[method]["child_count"] += len(children)
            scores[method]["safe_child_count"] += len(safe_children)
            reason_counts[method][reason] += 1
            signature = _children_signature(safe_children) if status == "SUCCESS" else ""
            if status == "SUCCESS":
                successful_methods.append(method)
                signature_methods[signature].append(method)
            method_results[method] = {
                "status": status,
                "reason": reason,
                "child_count": len(children),
                "safe_child_count": len(safe_children),
                "children_signature": signature,
                "children": [
                    {
                        "text": child.get("자식Claim", ""),
                        "target": child.get("정규화목표수치") or child.get("목표수치", ""),
                        "role": child.get("target_value_role", ""),
                        "validation": child.get("자식검증상태", ""),
                    }
                    for child in children
                ],
            }
        claim_id = source.get("Claim번호", "")
        if successful_methods:
            union_success_ids.add(claim_id)
        agreeing = [group for group in signature_methods.values() if len(group) >= 2]
        if agreeing:
            agreement_success_ids.add(claim_id)
        elif len(successful_methods) == 1:
            single_method_success_ids.add(claim_id)
        per_claim.append({
            "기사번호": source.get("기사번호", ""),
            "Claim번호": claim_id,
            "successful_methods": successful_methods,
            "agreement_groups": agreeing,
            "method_results": method_results,
        })

    scoreboard = {
        method: {
            **dict(scores[method]),
            "success_rate": round(scores[method].get("status_SUCCESS", 0) / len(parents), 6)
            if parents else 0.0,
            "top_reasons": reason_counts[method].most_common(10),
        }
        for method in selected_methods
    }
    summary = {
        "artifact": "clafact_claim_split_multimethod_benchmark_v1",
        "execution_time_utc": execution_time,
        "parent_count": len(parents),
        "methods": list(selected_methods),
        "method_count": len(selected_methods),
        "scoreboard": scoreboard,
        "validated_union_success_count": len(union_success_ids),
        "same_children_agreement_success_count": len(agreement_success_ids),
        "single_method_only_success_count": len(single_method_success_ids),
        "accuracy_status": "NOT_EVALUABLE_NO_PARENT_TO_CHILD_GOLD_FOR_339",
        "interpretation": "SUCCESS means deterministic safety invariants passed, not Gold accuracy.",
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps({"summary": summary, "scoreboard": scoreboard}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / OUTPUT_NAMES[1]).open("w", encoding="utf-8", newline="\n") as handle:
        for row in per_claim:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / OUTPUT_NAMES[2]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_manifest(output_dir, parent_csv)
    return summary


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "기사번호": row.get("기사번호", ""),
        "Claim번호": row.get("부모Claim번호") or row.get("Claim번호", ""),
        "작성일": row.get("작성일", ""),
        "제목": row.get("제목", ""),
        "URL": row.get("URL", ""),
        "원문": row.get("원문", ""),
        "앞문맥": row.get("앞문맥", ""),
        "뒤문맥": row.get("뒤문맥", ""),
        "하위유형": row.get("하위유형", ""),
        "기사내원문시작위치": "",
        "공식값상태": row.get("기존KOSIS상태", ""),
        "최종성공실패사유": row.get("기존KOSIS사유", ""),
    }


def _children_signature(children: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(
        (
            str(child.get("자식Claim", "")).strip(),
            str(child.get("정규화목표수치") or child.get("목표수치", "")).strip(),
            str(child.get("target_value_role", "")).strip(),
        )
        for child in children
    )
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _write_manifest(output_dir: Path, source: Path) -> None:
    payload = {
        "schema_version": "clafact_claim_split_multimethod_manifest_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": _file_record(source),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_REQUIRED_OFFLINE_METHODS",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-parent-count", type=int, default=339)
    args = parser.parse_args()
    result = run(
        parent_csv=args.parent_csv,
        output_dir=args.output_dir,
        expected_parent_count=args.expected_parent_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
