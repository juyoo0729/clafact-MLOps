"""Freeze an exact execution-history failure cohort for bounded replay."""

from __future__ import annotations

import csv
import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def build_failure_cohort(
    *,
    history_path: str | Path,
    source_batch_path: str | Path,
    failure_cause: str,
    expected_history_count: int,
    expected_cohort_count: int,
) -> dict[str, Any]:
    """Return the exact Claim subset with one failure cause.

    The history cardinality and unique Claim IDs are checked before filtering,
    so a different run or an append-only multi-run history cannot silently be
    substituted for the fixed baseline.
    """
    history_path = Path(history_path)
    source_batch_path = Path(source_batch_path)
    with history_path.open(newline="", encoding="utf-8-sig") as handle:
        history = list(csv.DictReader(handle))
    if len(history) != expected_history_count:
        raise ValueError(
            f"HISTORY_COUNT_MISMATCH:{len(history)}!={expected_history_count}"
        )

    claim_ids = [str(row.get("claim_id") or "").strip() for row in history]
    if any(not claim_id for claim_id in claim_ids):
        raise ValueError("HISTORY_CLAIM_ID_MISSING")
    duplicates = sorted(
        claim_id for claim_id, count in Counter(claim_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError(f"DUPLICATE_HISTORY_CLAIM_IDS:{duplicates[:3]}")

    source_batch = json.loads(source_batch_path.read_text(encoding="utf-8-sig"))
    source_claims = source_batch.get("claims")
    if not isinstance(source_claims, list):
        raise ValueError("SOURCE_BATCH_CLAIMS_REQUIRED")
    source_by_id = {
        str(row.get("claim_id") or "").strip(): row
        for row in source_claims
        if isinstance(row, dict) and str(row.get("claim_id") or "").strip()
    }

    selected_ids = [
        claim_id
        for claim_id, row in zip(claim_ids, history, strict=True)
        if str(row.get("failure_cause") or "").strip() == failure_cause
    ]
    if len(selected_ids) != expected_cohort_count:
        raise ValueError(
            f"COHORT_COUNT_MISMATCH:{len(selected_ids)}!={expected_cohort_count}"
        )
    missing = [claim_id for claim_id in selected_ids if claim_id not in source_by_id]
    if missing:
        raise ValueError(f"COHORT_CLAIMS_MISSING_IN_SOURCE_BATCH:{missing[:3]}")

    source_batch_id = str(source_batch.get("batch_id") or "SOURCE_BATCH")
    return {
        "batch_id": f"{source_batch_id}__{failure_cause}__{len(selected_ids)}",
        "definition": (
            f"Fixed replay cohort from {expected_history_count}-Claim history; "
            f"failure_cause={failure_cause}"
        ),
        "source_batch_id": source_batch_id,
        "source_history_sha256": _digest(history_path),
        "source_batch_sha256": _digest(source_batch_path),
        "claims": [source_by_id[claim_id] for claim_id in selected_ids],
    }


def write_failure_cohort(path: str | Path, cohort: dict[str, Any]) -> Path:
    """Write one immutable replay-batch definition."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"REFUSING_TO_OVERWRITE:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cohort, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
