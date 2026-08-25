"""Build the exact 12-slot re-entry queue from final category 1/2 successes."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REQUIRED_AUTO_SLOTS = (
    "indicator", "value", "target_value_role", "unit", "time", "frequency", "calculation",
)
CONDITIONAL_SLOTS = ("region", "population", "dimension", "comparison", "condition")
OUTPUT_NAMES = ("r2_12slot_reentry_queue.jsonl", "summary.json")


def build(
    *,
    combined_json: Path,
    output_dir: Path,
    expected_category1_success: int | None = 136,
    expected_category2_safe_children: int | None = 627,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(combined_json.read_text(encoding="utf-8"))
    one = [row for row in payload.get("final_category1_results") or [] if row.get("최종실행상태") == "SUCCESS"]
    parents = {row.get("부모Claim번호", ""): row for row in payload.get("final_category2_parent_results") or []}
    two = [
        row for row in payload.get("final_category2_child_results") or []
        if row.get("부모최종실행상태") == "SUCCESS" and row.get("자식검증상태") == "VALID"
    ]
    if expected_category1_success is not None and len(one) != expected_category1_success:
        raise ValueError("unexpected category 1 success count")
    if expected_category2_safe_children is not None and len(two) != expected_category2_safe_children:
        raise ValueError("unexpected category 2 safe child count")
    rows = [_category1_row(row) for row in one]
    rows.extend(_category2_row(row, parents.get(row.get("부모Claim번호", ""), {})) for row in two)
    ids = [str(row["claim_id"]) for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("blank or duplicate re-entry Claim ID")
    missing_counts = Counter(slot for row in rows for slot in row["missing_required_slots"])
    summary = {
        "artifact": "clafact_r2_multimethod_reentry_queue_v1",
        "created_at": _now(),
        "record_count": len(rows),
        "category1_context_claim_count": len(one),
        "category2_atomic_child_count": len(two),
        "unique_claim_id_count": len(set(ids)),
        "r3_auto_ready_count": sum(not row["missing_required_slots"] for row in rows),
        "missing_required_slot_counts": dict(sorted(missing_counts.items())),
        "required_auto_slots": list(REQUIRED_AUTO_SLOTS),
        "conditional_slots": list(CONDITIONAL_SLOTS),
        "kosis_query_count": 0,
        "kosis_status": "BLOCKED_UNTIL_R2_12SLOT_REVALIDATION",
        "accuracy_status": "NOT_EVALUABLE_12SLOT_REENTRY_NOT_EXECUTED",
    }
    with (output_dir / OUTPUT_NAMES[0]).open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / OUTPUT_NAMES[1]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": "clafact_r2_multimethod_reentry_manifest_v1",
        "created_at": _now(),
        "input": _file_record(combined_json),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _category1_row(row: Mapping[str, Any]) -> dict[str, Any]:
    known = {
        "indicator": None,
        "value": None,
        "target_value_role": None,
        "unit": None,
        "time": _text(row.get("보완기준기간") or row.get("원래Claim기간")) or None,
        "frequency": _text(row.get("보완주기") or row.get("원래Claim주기")) or None,
        "region": None,
        "population": None,
        "dimension": None,
        "comparison": {"period": _text(row.get("보완비교기간"))} if _text(row.get("보완비교기간")) else None,
        "calculation": None,
        "condition": None,
        "source_hint": _text(row.get("공식작성기관힌트")) or None,
    }
    return _queue_row(
        claim_id=_text(row.get("Claim번호")),
        article_id=_text(row.get("기사번호")),
        parent_claim_id="",
        source_type="CATEGORY1_CONTEXT_COMPLETED_PARENT",
        source_sentence=_text(row.get("원문")),
        published_at=_text(row.get("작성일")),
        context_before=_text(row.get("앞문맥")),
        context_after=_text(row.get("뒤문맥")),
        verbatim_target_value="",
        period_evidence=_text(row.get("문맥근거문구") or row.get("감지기간표현")),
        known_slots=known,
    )


def _category2_row(row: Mapping[str, Any], parent: Mapping[str, Any]) -> dict[str, Any]:
    known = {
        "indicator": None,
        "value": None,
        "target_value_role": _text(row.get("target_value_role")) or None,
        "unit": None,
        "time": None,
        "frequency": None,
        "region": None,
        "population": None,
        "dimension": None,
        "comparison": None,
        "calculation": None,
        "condition": None,
        "source_hint": None,
    }
    return _queue_row(
        claim_id=_text(row.get("자식Claim번호")),
        article_id=_text(row.get("기사번호")),
        parent_claim_id=_text(row.get("부모Claim번호")),
        source_type="CATEGORY2_VALIDATED_ATOMIC_CHILD",
        source_sentence=_text(row.get("자식Claim")),
        published_at=_text(parent.get("작성일")),
        context_before=_text(parent.get("앞문맥")),
        context_after=_text(parent.get("뒤문맥")),
        verbatim_target_value=_text(row.get("정규화목표수치") or row.get("목표수치")),
        period_evidence="",
        known_slots=known,
    )


def _queue_row(
    *,
    claim_id: str,
    article_id: str,
    parent_claim_id: str,
    source_type: str,
    source_sentence: str,
    published_at: str,
    context_before: str,
    context_after: str,
    verbatim_target_value: str,
    period_evidence: str,
    known_slots: Mapping[str, Any],
) -> dict[str, Any]:
    missing = [slot for slot in REQUIRED_AUTO_SLOTS if known_slots.get(slot) is None]
    return {
        "claim_id": claim_id,
        "article_id": article_id,
        "parent_claim_id": parent_claim_id,
        "source_type": source_type,
        "source_sentence": source_sentence,
        "published_at": published_at,
        "context_before": context_before,
        "context_after": context_after,
        "verbatim_target_value": verbatim_target_value,
        "period_evidence": period_evidence,
        "known_slots": dict(known_slots),
        "missing_required_slots": missing,
        "r2_reentry_status": "PENDING_STRUCTURED_REEXTRACTION" if missing else "R2_AUTO_READY",
        "kosis_status": "NOT_RUN_R2_PRECONDITION",
        "next_step": "STRUCTURED_12SLOT_EXTRACTION_THEN_REVALIDATE_AUTO_READINESS",
        "record_sha256": _record_hash(claim_id, source_sentence, known_slots),
    }


def _record_hash(claim_id: str, sentence: str, known_slots: Mapping[str, Any]) -> str:
    value = {"claim_id": claim_id, "source_sentence": sentence, "known_slots": known_slots}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--combined-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-category1-success", type=int, default=136)
    parser.add_argument("--expected-category2-safe-children", type=int, default=627)
    args = parser.parse_args()
    result = build(
        combined_json=args.combined_json, output_dir=args.output_dir,
        expected_category1_success=args.expected_category1_success,
        expected_category2_safe_children=args.expected_category2_safe_children,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
