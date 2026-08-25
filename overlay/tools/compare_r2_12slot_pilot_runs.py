"""Compare two stored R2 pilot runs without any provider or KOSIS call."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


OUTPUT_NAMES = ("comparison.json", "summary.json")


def compare(
    *, first_results: Path, second_results: Path, output_dir: Path
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    first = _indexed(_read_jsonl(first_results))
    second = _indexed(_read_jsonl(second_results))
    if set(first) != set(second):
        raise ValueError("run Claim ID sets differ")
    claim_ids = sorted(first)
    records: list[dict[str, Any]] = []
    both_success = 0
    exact_changed = 0
    status_changed_both_success = 0
    for claim_id in claim_ids:
        before, after = first[claim_id], second[claim_id]
        before_claim, after_claim = before.get("final_claim"), after.get("final_claim")
        comparable = before_claim is not None and after_claim is not None
        changed = comparable and before_claim != after_claim
        status_changed = before.get("status") != after.get("status")
        both_success += int(comparable)
        exact_changed += int(changed)
        status_changed_both_success += int(comparable and status_changed)
        records.append({
            "claim_id": claim_id,
            "first_status": before.get("status"),
            "first_reason_code": before.get("reason_code"),
            "second_status": after.get("status"),
            "second_reason_code": after.get("reason_code"),
            "both_provider_success": comparable,
            "final_claim_exact_changed": changed,
            "status_changed": status_changed,
            "first_final_claim_sha256": _optional_json_hash(before_claim),
            "second_final_claim_sha256": _optional_json_hash(after_claim),
        })
    first_status = Counter(str(row.get("status") or "") for row in first.values())
    second_status = Counter(str(row.get("status") or "") for row in second.values())
    summary = {
        "artifact": "clafact_r2_12slot_pilot_comparison_v1",
        "created_at": _now(),
        "joined_claim_count": len(claim_ids),
        "both_provider_success_count": both_success,
        "final_claim_exact_changed_count": exact_changed,
        "final_claim_exact_changed_rate_among_comparable": (
            exact_changed / both_success if both_success else None
        ),
        "status_changed_with_both_provider_success_count": status_changed_both_success,
        "first_status_counts": dict(sorted(first_status.items())),
        "second_status_counts": dict(sorted(second_status.items())),
        "provider_reexecution_count": 0,
        "kosis_query_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_LINKED_12SLOT_GOLD",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / OUTPUT_NAMES[1]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": "clafact_r2_12slot_pilot_comparison_manifest_v1",
        "created_at": _now(),
        "inputs": {
            "first": _file_record(first_results),
            "second": _file_record(second_results),
        },
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_USED",
        "provider_calls": 0,
        "kosis_calls": 0,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _indexed(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {str(row.get("claim_id") or ""): dict(row) for row in rows}
    if "" in result or len(result) != len(rows):
        raise ValueError("blank or duplicate Claim ID")
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _optional_json_hash(value: Any) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-results", type=Path, required=True)
    parser.add_argument("--second-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = compare(
        first_results=args.first_results,
        second_results=args.second_results,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
