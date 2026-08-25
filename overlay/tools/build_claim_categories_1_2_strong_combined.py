"""Combine category 1 and 2 strong replay artifacts without merging stage metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


OUTPUT_NAMES = ("CLAFACT_1번2번_강화_통합기록.json", "summary.json")


def build(
    *,
    category1_json: Path,
    category2_json: Path,
    output_dir: Path,
    expected_category1_count: int | None = 256,
    expected_category2_count: int | None = 339,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    one = json.loads(category1_json.read_text(encoding="utf-8"))
    two = json.loads(category2_json.read_text(encoding="utf-8"))
    one_rows = list(one.get("context_results") or [])
    two_parents = list(two.get("parent_results") or [])
    two_children = list(two.get("child_results") or [])
    one_attempts = list(one.get("llm_attempts") or [])
    two_attempts = list(two.get("llm_attempts") or [])
    if expected_category1_count is not None and len(one_rows) != expected_category1_count:
        raise ValueError("unexpected category 1 count")
    if expected_category2_count is not None and len(two_parents) != expected_category2_count:
        raise ValueError("unexpected category 2 count")
    one_ids = {str(row.get("Claim번호") or "") for row in one_rows}
    two_ids = {str(row.get("부모Claim번호") or "") for row in two_parents}
    if "" in one_ids or "" in two_ids or one_ids & two_ids:
        raise ValueError("blank or overlapping Claim IDs")
    one_summary = dict(one.get("summary") or {})
    two_summary = dict(two.get("summary") or {})
    summary = {
        "artifact": "clafact_categories_1_2_strong_combined_v1",
        "created_at": _now(),
        "parent_claim_count": len(one_rows) + len(two_parents),
        "category1_claim_count": len(one_rows),
        "category1_original_status_counts": one_summary.get("original_status_counts", {}),
        "category1_final_status_counts": one_summary.get("final_status_counts", {}),
        "category1_success_improvement": one_summary.get("success_improvement_vs_original", 0),
        "category2_parent_count": len(two_parents),
        "category2_original_status_counts": two_summary.get("original_status_counts", {}),
        "category2_final_status_counts": two_summary.get("final_status_counts", {}),
        "category2_success_improvement": two_summary.get("success_improvement_vs_original", 0),
        "category2_final_child_count": len(two_children),
        "category2_final_safe_child_count": two_summary.get("final_safe_child_count", 0),
        "llm_attempt_count": len(one_attempts) + len(two_attempts),
        "llm_validated_accept_count": (
            one_summary.get("llm_validated_success_count", 0)
            + two_summary.get("llm_validated_success_count", 0)
        ),
        "kosis_requery_count": 0,
        "stage_metric_warning": (
            "Category 1 context completion and category 2 atomic splitting are different metrics; "
            "do not add them as accuracy."
        ),
        "accuracy_status": "NOT_EVALUABLE_STAGE_SPECIFIC_GOLD_MISSING",
        "secret_handling": "KEY_VALUES_NOT_RECORDED",
    }
    payload = {
        "schema_version": "clafact_categories_1_2_strong_combined_v1",
        "summary": summary,
        "category1": one,
        "category2": two,
        "source_artifacts": {
            "category1": _file_record(category1_json),
            "category2": _file_record(category2_json),
        },
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[1]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": "clafact_categories_1_2_strong_combined_manifest_v1",
        "created_at": _now(),
        "inputs": {
            "category1": _file_record(category1_json),
            "category2": _file_record(category2_json),
        },
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path), "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category1-json", type=Path, required=True)
    parser.add_argument("--category2-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-category1-count", type=int, default=256)
    parser.add_argument("--expected-category2-count", type=int, default=339)
    args = parser.parse_args()
    result = build(
        category1_json=args.category1_json,
        category2_json=args.category2_json,
        output_dir=args.output_dir,
        expected_category1_count=args.expected_category1_count,
        expected_category2_count=args.expected_category2_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
