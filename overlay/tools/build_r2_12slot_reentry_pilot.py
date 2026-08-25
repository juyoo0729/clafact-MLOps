"""Select a fixed, balanced 20-row pilot from the R2 12-slot re-entry queue."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


CATEGORY1 = "CATEGORY1_CONTEXT_COMPLETED_PARENT"
CATEGORY2 = "CATEGORY2_VALIDATED_ATOMIC_CHILD"
ROLE_PRIORITY = (
    "CURRENT_VALUE",
    "CHANGE_VALUE",
    "PRIOR_VALUE",
    "SHARE_VALUE",
    "THRESHOLD_VALUE",
    "RANK_VALUE",
    "RATIO_VALUE",
    # RANGE_VALUE is intentionally retained as one negative-control row.  It
    # is not a ClaimSchema role and the runner must route it to HOLD.
    "RANGE_VALUE",
)
OUTPUT_NAMES = ("pilot_input.jsonl", "summary.json")


def build(
    *, queue_jsonl: Path, output_dir: Path, per_source_count: int = 10
) -> dict[str, Any]:
    """Write one immutable, deterministic category 1/category 2 pilot."""
    if per_source_count < 1:
        raise ValueError("per_source_count must be positive")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    rows = _read_jsonl(queue_jsonl)
    category1 = [row for row in rows if row.get("source_type") == CATEGORY1]
    category2 = [row for row in rows if row.get("source_type") == CATEGORY2]
    if len(category1) < per_source_count or len(category2) < per_source_count:
        raise ValueError("queue does not contain enough rows for a balanced pilot")

    selected1 = _stable_rows(category1)[:per_source_count]
    selected2 = _role_covered_rows(category2, per_source_count)
    selected = selected1 + selected2
    claim_ids = [str(row.get("claim_id") or "") for row in selected]
    if any(not value for value in claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ValueError("pilot contains blank or duplicate Claim IDs")

    output_dir.mkdir(parents=True, exist_ok=True)
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(selected, start=1):
        record = dict(row)
        record["pilot_index"] = index
        record["pilot_selection_sha256"] = _selection_hash(record)
        enriched.append(record)
    _write_jsonl(output_dir / OUTPUT_NAMES[0], enriched)

    source_counts = Counter(str(row.get("source_type") or "") for row in enriched)
    role_counts = Counter(
        str((row.get("known_slots") or {}).get("target_value_role") or "MISSING")
        for row in enriched
        if row.get("source_type") == CATEGORY2
    )
    summary = {
        "artifact": "clafact_r2_12slot_reentry_pilot_input_v1",
        "created_at": _now(),
        "record_count": len(enriched),
        "source_type_counts": dict(sorted(source_counts.items())),
        "category2_role_counts": dict(sorted(role_counts.items())),
        "selection_policy": {
            "category1": "SHA256_CLAIM_ID_FIRST_N",
            "category2": "ONE_PER_ROLE_THEN_SHA256_FILL",
            "role_priority": list(ROLE_PRIORITY),
            "per_source_count": per_source_count,
        },
        "kosis_query_count": 0,
        "kosis_status": "NOT_RUN_R2_PILOT_INPUT_ONLY",
        "accuracy_status": "NOT_EVALUABLE_NO_12SLOT_GOLD",
    }
    (output_dir / OUTPUT_NAMES[1]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "schema_version": "clafact_r2_12slot_reentry_pilot_input_manifest_v1",
        "created_at": _now(),
        "input": _file_record(queue_jsonl),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_USED",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _role_covered_rows(rows: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    stable = _stable_rows(rows)
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for role in ROLE_PRIORITY:
        match = next(
            (
                row
                for row in stable
                if str((row.get("known_slots") or {}).get("target_value_role") or "") == role
                and str(row.get("claim_id") or "") not in selected_ids
            ),
            None,
        )
        if match is not None:
            selected.append(match)
            selected_ids.add(str(match.get("claim_id") or ""))
        if len(selected) == count:
            return selected
    for row in stable:
        claim_id = str(row.get("claim_id") or "")
        if claim_id not in selected_ids:
            selected.append(row)
            selected_ids.add(claim_id)
        if len(selected) == count:
            return selected
    raise ValueError("not enough distinct category 2 rows")


def _stable_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows),
        key=lambda row: hashlib.sha256(
            str(row.get("claim_id") or "").encode("utf-8")
        ).hexdigest(),
    )


def _selection_hash(row: Mapping[str, Any]) -> str:
    controlled = {
        "claim_id": row.get("claim_id"),
        "record_sha256": row.get("record_sha256"),
        "source_type": row.get("source_type"),
    }
    return hashlib.sha256(
        json.dumps(controlled, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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
    parser.add_argument("--queue-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-source-count", type=int, default=10)
    args = parser.parse_args()
    summary = build(
        queue_jsonl=args.queue_jsonl,
        output_dir=args.output_dir,
        per_source_count=args.per_source_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
