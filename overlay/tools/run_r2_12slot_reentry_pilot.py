"""Run a fail-closed 12-slot re-entry pilot without calling KOSIS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from config.settings import load_environment_file
from core.claim_parser import missing_auto_required_slots, parse_claim, revalidate_auto_readiness
from core.claim_time_resolver import resolve_relative_time
from core.claim_value_trace import validate_claim_value_trace, validate_expected_candidate_value
from core.frequency_normalizer import normalize_frequency
from core.hcx_claim_extractor import HcxClaimExtractor
from schemas.claim import ClaimSchema


CATEGORY1 = "CATEGORY1_CONTEXT_COMPLETED_PARENT"
CATEGORY2 = "CATEGORY2_VALIDATED_ATOMIC_CHILD"
ALLOWED_ROLES = {
    "CURRENT_VALUE",
    "PRIOR_VALUE",
    "CHANGE_VALUE",
    "RANK_VALUE",
    "SHARE_VALUE",
    "RATIO_VALUE",
    "THRESHOLD_VALUE",
}
ALLOWED_FREQUENCIES = {"일", "월", "분기", "년"}
AMBIGUOUS_TARGET_MARKERS = ("약", "~", "∼", "～", "대가량", "안팎", "내외")
OUTPUT_NAMES = ("r2_12slot_results.jsonl", "summary.json", "safe_summary.txt")


class Extractor(Protocol):
    def extract(self, sentence: str) -> ClaimSchema: ...


class _ContextExtractor:
    """Pass context to the provider while keeping the article sentence as the contract source."""

    def __init__(self, delegate: Extractor, row: Mapping[str, Any]) -> None:
        self.delegate = delegate
        self.row = row

    def extract(self, _source_sentence: str) -> ClaimSchema:
        prompt = _context_prompt(self.row)
        return self.delegate.extract(prompt)


def run(
    *,
    pilot_jsonl: Path,
    output_dir: Path,
    extractor: Extractor,
    provider_name: str,
    provider_model: str,
    min_request_interval_seconds: float = 0.0,
    expected_count: int | None = 20,
) -> dict[str, Any]:
    """Execute each selected row once and retain only deterministic R3-ready rows."""
    if min_request_interval_seconds < 0:
        raise ValueError("min_request_interval_seconds must be non-negative")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    rows = _read_jsonl(pilot_jsonl)
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"unexpected pilot record count: {len(rows)}")
    claim_ids = [str(row.get("claim_id") or "") for row in rows]
    if any(not value for value in claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ValueError("pilot contains blank or duplicate Claim IDs")

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    last_started = 0.0
    for row in rows:
        remaining = min_request_interval_seconds - (time.monotonic() - last_started)
        if remaining > 0:
            time.sleep(remaining)
        last_started = time.monotonic()
        results.append(
            _process_row(
                row,
                extractor=extractor,
                provider_name=provider_name,
                provider_model=provider_model,
            )
        )
    _write_jsonl(output_dir / OUTPUT_NAMES[0], results)

    status_counts = Counter(str(row["status"]) for row in results)
    reason_counts = Counter(str(row["reason_code"]) for row in results)
    source_status: dict[str, Counter[str]] = defaultdict(Counter)
    for row in results:
        source_status[str(row["source_type"])][str(row["status"])] += 1
    required_coverage = Counter()
    for row in results:
        final_claim = row.get("final_claim") or {}
        for slot in (
            "indicator", "value", "target_value_role", "unit", "time", "frequency", "calculation"
        ):
            if final_claim.get(slot) is not None and final_claim.get(slot) != "":
                required_coverage[slot] += 1
    summary = {
        "artifact": "clafact_r2_12slot_reentry_pilot_run_v1",
        "created_at": _now(),
        "record_count": len(results),
        "provider_name": provider_name,
        "provider_model": provider_model,
        "provider_attempt_count": len(results),
        "provider_api_error_count": status_counts.get("API_ERROR", 0),
        "retry_count": 0,
        "status_counts": dict(sorted(status_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "source_status_counts": {
            source: dict(sorted(counts.items())) for source, counts in sorted(source_status.items())
        },
        "required_slot_nonmissing_counts": dict(sorted(required_coverage.items())),
        "kosis_query_count": 0,
        "kosis_status": "NOT_RUN_PILOT_ONLY",
        "accuracy_status": "NOT_EVALUABLE_NO_LINKED_12SLOT_GOLD",
        "secret_handling": "PROVIDER_KEY_USED_IN_MEMORY_NOT_RECORDED",
    }
    (output_dir / OUTPUT_NAMES[1]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[2]).write_text(_safe_summary(summary), encoding="utf-8")
    manifest = {
        "schema_version": "clafact_r2_12slot_reentry_pilot_run_manifest_v1",
        "created_at": _now(),
        "input": _file_record(pilot_jsonl),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
        "raw_provider_response": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _process_row(
    row: Mapping[str, Any], *, extractor: Extractor, provider_name: str, provider_model: str
) -> dict[str, Any]:
    attempted_at = _now()
    gates: dict[str, str] = {
        "provider_contract": "NOT_RUN",
        "required_slots": "NOT_RUN",
        "source_value_trace": "NOT_RUN",
        "indicator_grounding": "NOT_RUN",
        "known_context_consistency": "NOT_RUN",
        "expected_role": "NOT_APPLICABLE",
        "expected_value_trace": "NOT_APPLICABLE",
        "expected_unit": "NOT_APPLICABLE",
    }
    provenance: dict[str, str] = {}
    try:
        context_extractor = _ContextExtractor(extractor, row)
        claim = parse_claim(str(row.get("source_sentence") or ""), context_extractor)
        gates["provider_contract"] = "PASS"
        claim = resolve_relative_time(claim, _parse_date(row.get("published_at")))
        claim, context_gate, provenance = _enrich_known_context(claim, row.get("known_slots") or {})
        gates["known_context_consistency"] = context_gate
        claim = revalidate_auto_readiness(claim)

        missing = list(missing_auto_required_slots(claim))
        gates["required_slots"] = "PASS" if not missing else "FAIL"
        trace = validate_claim_value_trace(claim)
        gates["source_value_trace"] = "PASS" if trace.passed else "FAIL"
        gates["indicator_grounding"] = (
            "PASS" if _indicator_is_grounded(claim.indicator, row) else "FAIL"
        )
        _apply_category2_gates(gates, claim, row)
        reason = _first_failure_reason(claim, gates)
        status = "R3_READY" if reason == "R3_READY" else "HOLD"
        result: dict[str, Any] = {
            "claim_id": str(row.get("claim_id") or ""),
            "article_id": str(row.get("article_id") or ""),
            "parent_claim_id": str(row.get("parent_claim_id") or ""),
            "source_type": str(row.get("source_type") or ""),
            "attempted_at": attempted_at,
            "provider_name": provider_name,
            "provider_model": provider_model,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "status": status,
            "reason_code": reason,
            "failure_stage": "" if status == "R3_READY" else _failure_stage(reason),
            "missing_required_slots": missing,
            "gates": gates,
            "slot_provenance": provenance,
            "final_claim": claim.model_dump(mode="json"),
            "kosis_status": "NOT_RUN_PILOT_ONLY",
            "kosis_query_count": 0,
            "accuracy_status": "NOT_EVALUABLE_NO_LINKED_12SLOT_GOLD",
            "error_type": "",
        }
    except Exception as exc:
        error_type = _safe_error_type(exc)
        result = {
            "claim_id": str(row.get("claim_id") or ""),
            "article_id": str(row.get("article_id") or ""),
            "parent_claim_id": str(row.get("parent_claim_id") or ""),
            "source_type": str(row.get("source_type") or ""),
            "attempted_at": attempted_at,
            "provider_name": provider_name,
            "provider_model": provider_model,
            "provider_attempt_count": 1,
            "retry_count": 0,
            "status": "API_ERROR",
            "reason_code": error_type,
            "failure_stage": "STRUCTURED_EXTRACTION",
            "missing_required_slots": [],
            "gates": gates,
            "slot_provenance": provenance,
            "final_claim": None,
            "kosis_status": "NOT_RUN_PILOT_ONLY",
            "kosis_query_count": 0,
            "accuracy_status": "NOT_EVALUABLE_NO_LINKED_12SLOT_GOLD",
            "error_type": error_type,
        }
    result["result_sha256"] = _json_hash(result)
    return result


def _enrich_known_context(
    claim: ClaimSchema, known_slots: Mapping[str, Any]
) -> tuple[ClaimSchema, str, dict[str, str]]:
    updates: dict[str, str] = {}
    provenance: dict[str, str] = {
        slot: "PROVIDER"
        for slot in type(claim).model_fields
        if getattr(claim, slot, None) is not None
    }
    conflicts: list[str] = []
    known_time = _text(known_slots.get("time"))
    if known_time:
        if not claim.time:
            updates["time"] = known_time
            provenance["time"] = "ARTICLE_CONTEXT_PERIOD_EVIDENCE"
        elif _normalized_time(claim.time) != _normalized_time(known_time):
            conflicts.append("time")
    known_frequency_raw = _text(known_slots.get("frequency"))
    if known_frequency_raw:
        known_frequency = normalize_frequency(known_frequency_raw)
        if known_frequency not in ALLOWED_FREQUENCIES:
            conflicts.append("frequency_unsupported")
        elif not claim.frequency:
            updates["frequency"] = known_frequency
            provenance["frequency"] = "ARTICLE_CONTEXT_PERIOD_EVIDENCE"
        elif normalize_frequency(claim.frequency) != known_frequency:
            conflicts.append("frequency")
    if updates:
        claim = claim.model_copy(update=updates)
    if conflicts:
        return claim, "FAIL:" + ",".join(conflicts), provenance
    return claim, "PASS", provenance


def _apply_category2_gates(
    gates: dict[str, str], claim: ClaimSchema, row: Mapping[str, Any]
) -> None:
    if row.get("source_type") != CATEGORY2:
        return
    expected_role = _text((row.get("known_slots") or {}).get("target_value_role"))
    if expected_role not in ALLOWED_ROLES:
        gates["expected_role"] = "FAIL:UPSTREAM_TARGET_VALUE_ROLE_INVALID"
    elif claim.target_value_role != expected_role:
        gates["expected_role"] = "FAIL:TARGET_VALUE_ROLE_MISMATCH"
    else:
        gates["expected_role"] = "PASS"

    asserted = _text(row.get("verbatim_target_value"))
    if any(marker in asserted for marker in AMBIGUOUS_TARGET_MARKERS) or re.search(r"%\s*대", asserted):
        gates["expected_value_trace"] = "FAIL:AMBIGUOUS_TARGET_VALUE_EXPRESSION"
    else:
        expected_trace = validate_expected_candidate_value(claim, asserted)
        gates["expected_value_trace"] = (
            "PASS" if expected_trace.passed else f"FAIL:{expected_trace.reason_code}"
        )

    expected_unit = _expected_base_unit(asserted)
    if expected_unit is None:
        gates["expected_unit"] = "FAIL:TARGET_UNIT_UNPARSEABLE"
    elif _normalized_unit(claim.unit) != _normalized_unit(expected_unit):
        gates["expected_unit"] = "FAIL:TARGET_UNIT_MISMATCH"
    else:
        gates["expected_unit"] = "PASS"


def _first_failure_reason(claim: ClaimSchema, gates: Mapping[str, str]) -> str:
    if gates.get("known_context_consistency", "").startswith("FAIL"):
        return "KNOWN_CONTEXT_CONFLICT"
    if claim.parse_status != "AUTO_OK":
        return _safe_reason(claim.parse_reason, "PROVIDER_PARSE_NOT_AUTO_OK")
    order = (
        ("required_slots", "MISSING_REQUIRED_SLOTS"),
        ("source_value_trace", "SOURCE_VALUE_TRACE_FAILED"),
        ("indicator_grounding", "INDICATOR_NOT_GROUNDED"),
        ("expected_role", "TARGET_VALUE_ROLE_FAILED"),
        ("expected_value_trace", "EXPECTED_VALUE_TRACE_FAILED"),
        ("expected_unit", "EXPECTED_UNIT_FAILED"),
    )
    for gate_name, default in order:
        value = gates.get(gate_name, "")
        if value.startswith("FAIL"):
            detail = value.partition(":")[2]
            return _safe_reason(detail, default)
    return "R3_READY"


def _indicator_is_grounded(indicator: str | None, row: Mapping[str, Any]) -> bool:
    normalized_indicator = _normalize_text(indicator)
    if len(normalized_indicator) < 2:
        return False
    context = " ".join(
        _text(row.get(key)) for key in ("source_sentence", "context_before", "context_after")
    )
    normalized_context = _normalize_text(context)
    if normalized_indicator in normalized_context:
        return True
    tokens = [token for token in re.findall(r"[가-힣A-Za-z0-9]+", indicator or "") if len(token) >= 2]
    return bool(tokens) and all(_normalize_text(token) in normalized_context for token in tokens)


def _expected_base_unit(asserted: str) -> str | None:
    normalized = asserted.replace(",", "").replace(" ", "")
    if "%" in normalized or "퍼센트" in normalized:
        return "%"
    match = re.search(
        r"(?:\d+(?:\.\d+)?)(?:조|억|만)?(?:\d+(?:\.\d+)?(?:천|백|십|만)?)?"
        r"(?P<unit>달러|유로|원|명|개|건|곳|대|가구|호|위|배|포인트|톤|㎏|kg|킬로그램|리터|L|명분|개사)",
        normalized,
        flags=re.IGNORECASE,
    )
    return match.group("unit") if match else None


def _normalized_unit(value: str | None) -> str:
    normalized = _normalize_text(value).casefold()
    aliases = {"퍼센트": "%", "percent": "%", "percentage": "%", "킬로그램": "kg", "㎏": "kg"}
    return aliases.get(normalized, normalized)


def _context_prompt(row: Mapping[str, Any]) -> str:
    return (
        "다음 검증 대상 문장 하나만 12슬롯 Claim으로 구조화하세요. "
        "앞뒤 문맥과 작성일은 생략된 지표·기간을 복원하는 근거로만 사용하고, "
        "검증 대상 문장에 없는 수치를 value로 선택하지 마세요.\n"
        f"작성일: {_text(row.get('published_at')) or '미상'}\n"
        f"앞 문맥: {_text(row.get('context_before')) or '없음'}\n"
        f"검증 대상 문장: {_text(row.get('source_sentence'))}\n"
        f"뒤 문맥: {_text(row.get('context_after')) or '없음'}"
    )


def _parse_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(?P<year>\d{4})[-./년]\s*(?P<month>\d{1,2})[-./월]\s*(?P<day>\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def _normalized_time(value: str | None) -> str:
    text = _text(value)
    year_match = re.fullmatch(r"(?P<year>\d{4})\s*년?", text)
    if year_match:
        return f"{year_match['year']}년"
    match = re.fullmatch(r"(?P<year>\d{4})-(?P<month>0?[1-9]|1[0-2])", text)
    if match:
        return f"{match['year']}년{int(match['month'])}월"
    return _normalize_text(text)


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣%㎏]+", "", _text(value))


def _safe_reason(value: str | None, default: str) -> str:
    return value if value and re.fullmatch(r"[A-Z][A-Z0-9_:,-]{2,255}", value) else default


def _failure_stage(reason: str) -> str:
    if reason.startswith("MISSING_REQUIRED_SLOTS") or reason in {
        "PROVIDER_PARSE_NOT_AUTO_OK", "FREQUENCY_TIME_CONFLICT", "CALCULATION_UNSUPPORTED"
    }:
        return "12SLOT_STRUCTURING"
    if reason.startswith("KNOWN_"):
        return "CONTEXT_ENRICHMENT"
    return "DETERMINISTIC_GUARD"


def _safe_error_type(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}_{code}" if code is not None else type(exc).__name__


def _safe_summary(summary: Mapping[str, Any]) -> str:
    return (
        f"전체: {summary['record_count']}건\n"
        f"상태: {json.dumps(summary['status_counts'], ensure_ascii=False, sort_keys=True)}\n"
        f"실패 사유: {json.dumps(summary['reason_counts'], ensure_ascii=False, sort_keys=True)}\n"
        f"공급자 호출: {summary['provider_attempt_count']}건, 재시도: 0건\n"
        "KOSIS 호출: 0건 (R2 전용 파일럿)\n"
        "정확도: NOT_EVALUABLE_NO_LINKED_12SLOT_GOLD\n"
    )


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


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--provider", choices=("hcx",), default="hcx")
    parser.add_argument("--model", default="HCX-007")
    parser.add_argument("--expected-count", type=int, default=20)
    parser.add_argument("--min-request-interval-seconds", type=float, default=2.1)
    args = parser.parse_args()
    load_environment_file(args.env_file, os.environ)
    if not os.environ.get("HCX_API_KEY"):
        raise RuntimeError("HCX_API_KEY is not configured")
    summary = run(
        pilot_jsonl=args.pilot_jsonl,
        output_dir=args.output_dir,
        extractor=HcxClaimExtractor(api_key=os.environ.get("HCX_API_KEY"), model=args.model),
        provider_name=args.provider,
        provider_model=args.model,
        min_request_interval_seconds=args.min_request_interval_seconds,
        expected_count=args.expected_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
