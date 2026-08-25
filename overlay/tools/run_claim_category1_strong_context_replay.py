"""Re-run category 1 period holds with LLM proposals and deterministic evidence checks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import Request, urlopen

from config.settings import load_environment_file
from tools.run_claim_categories_1_2_audit import (
    CONTEXT_COLUMNS,
    _comparison_period,
    _parse_date,
    _resolve_period_in_text,
    execute_context_claim,
)


ELIGIBLE_REASONS = {
    "MULTIPLE_CONTEXT_PERIODS_UNRESOLVED",
    "PERIOD_CONTEXT_UNRESOLVED",
    "CONTEXT_ABSOLUTE_PERIOD_CANDIDATE_REQUIRES_SEMANTIC_LINK",
    "COMPARISON_CONTEXT_UNRESOLVED",
}
STRONG_CONTEXT_COLUMNS = (*CONTEXT_COLUMNS,
    "기존실행상태", "기존실행사유", "LLM강화시도", "LLM강화상태",
    "LLM강화사유", "LLM강화오류", "최종채택방식", "문맥근거문구",
)
OUTPUT_NAMES = (
    "CLAFACT_1번_강화문맥재실행_통합기록.json",
    "category1_strong_context_results.csv",
    "llm_context_attempts.jsonl",
    "summary.json",
)
_PERIOD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "route_status": {"type": "string", "enum": ["AUTO", "HUMAN_REVIEW"]},
        "target_period": {"type": "string"},
        "comparison_period": {"type": "string"},
        "frequency": {"type": "string"},
        "target_evidence": {"type": "string"},
        "comparison_evidence": {"type": "string"},
        "reason_code": {"type": "string"},
    },
    "required": [
        "route_status", "target_period", "comparison_period", "frequency",
        "target_evidence", "comparison_evidence", "reason_code",
    ],
}
_INSTRUCTIONS = (
    "Resolve the verification period for exactly one Korean numeric news Claim. "
    "Use the article date only to normalize explicit relative expressions such as 작년, 지난달, 올해. "
    "Choose a period only when the nearby article context semantically refers to the target Claim. "
    "target_evidence must be a verbatim substring that includes both a period expression and an indicator anchor. "
    "Do not use an unrelated publication date, advertisement, reporter profile, or another statistic. "
    "Use YYYY, YYYY-MM, or YYYY-MM-DD..YYYY-MM-DD. If ambiguous, return HUMAN_REVIEW."
)
_STOPWORDS = {
    "지난달", "이달", "이번달", "올해", "작년", "지난해", "기간", "같은",
    "증가", "감소", "늘었다", "줄었다", "나타났다", "것으로", "가운데",
}


def run(
    *,
    baseline_context_csv: Path,
    output_dir: Path,
    expected_count: int | None = 256,
    llm_model: str = "gpt-5.6-luna",
    workers: int = 4,
    proposer: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(baseline_context_csv)
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(f"unexpected context count: {len(rows)} != {expected_count}")
    ids = [row.get("Claim번호", "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("blank or duplicate Claim ID")
    execution_time = _now()
    propose = proposer or _propose_period
    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(
            lambda row: _process_row(
                row, execution_time=execution_time, model=llm_model, proposer=propose
            ),
            rows,
        ))
    results = [item[0] for item in processed]
    attempts = [item[1] for item in processed if item[1] is not None]
    original = Counter(row.get("최종실행상태", "") for row in rows)
    final = Counter(row.get("최종실행상태", "") for row in results)
    attempt_status = Counter(attempt.get("검증상태", "") for attempt in attempts)
    summary = {
        "artifact": "clafact_category1_strong_context_replay_v1",
        "execution_time_utc": execution_time,
        "claim_count": len(results),
        "original_status_counts": dict(sorted(original.items())),
        "llm_attempt_count": len(attempts),
        "llm_attempt_status_counts": dict(sorted(attempt_status.items())),
        "llm_validated_success_count": sum(
            attempt.get("최종채택") == "YES" for attempt in attempts
        ),
        "final_status_counts": dict(sorted(final.items())),
        "success_improvement_vs_original": final.get("SUCCESS", 0) - original.get("SUCCESS", 0),
        "llm_model": llm_model,
        "llm_retry_count": 0,
        "kosis_requery_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_CONTEXT_PERIOD_GOLD_FOR_256",
        "secret_handling": "KEY_USED_BY_PROVIDER_CLIENT_NOT_RECORDED",
    }
    _write_csv(output_dir / OUTPUT_NAMES[1], results, STRONG_CONTEXT_COLUMNS)
    _write_jsonl(output_dir / OUTPUT_NAMES[2], attempts)
    (output_dir / OUTPUT_NAMES[3]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    consolidated = {
        "schema_version": "clafact_category1_strong_context_replay_v1",
        "summary": summary,
        "method": {
            "proposal": "OpenAI strict structured output",
            "acceptance": [
                "verbatim context evidence",
                "article-date deterministic normalization",
                "Claim-evidence indicator anchor overlap",
                "comparison period required for comparison subtype",
            ],
        },
        "context_results": results,
        "llm_attempts": attempts,
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps(consolidated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_manifest(output_dir, baseline_context_csv)
    return summary


def _process_row(
    row: Mapping[str, Any],
    *,
    execution_time: str,
    model: str,
    proposer: Callable[[Mapping[str, Any], str], Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    baseline = execute_context_claim(row, execution_time=execution_time)
    common = {
        "기존실행상태": row.get("최종실행상태", ""),
        "기존실행사유": row.get("성공실패사유", ""),
    }
    if baseline["성공실패사유"] not in ELIGIBLE_REASONS:
        return ({
            **baseline, **common,
            "LLM강화시도": "NO", "LLM강화상태": "NOT_ELIGIBLE",
            "LLM강화사유": baseline["성공실패사유"], "LLM강화오류": "",
            "최종채택방식": "DETERMINISTIC_BASELINE", "문맥근거문구": "",
        }, None)
    attempted_at = _now()
    try:
        candidate = dict(proposer(row, model))
        valid, reason = _validate_candidate(row, candidate)
        accepted = valid and candidate.get("route_status") == "AUTO"
        if accepted:
            final = {
                **baseline,
                "감지기간표현": candidate["target_evidence"],
                "기간근거범위": "LLM_VALIDATED_ARTICLE_CONTEXT",
                "보완기준기간": candidate["target_period"],
                "보완비교기간": candidate["comparison_period"],
                "보완주기": candidate["frequency"],
                "최종실행상태": "SUCCESS",
                "성공실패사유": "LLM_CONTEXT_PERIOD_VALIDATED",
                "중단단계": "CONTEXT_COMPLETE",
                "KOSIS재조회상태": "NOT_RUN_PRECONDITION_REPARSED_12SLOTS_REQUIRED",
                "다음실행단계": "보완 Claim 12슬롯 재구조화 후 KOSIS 재조회",
                "무엇을": (
                    f"문맥 보완({baseline['하위유형']}); 기준기간={candidate['target_period']}; "
                    f"비교기간={candidate['comparison_period'] or '미확정'}"
                ),
                "어떻게": "OPENAI_STRUCTURED_PERIOD_PROPOSAL + DETERMINISTIC_EVIDENCE_VALIDATOR_V1",
                "왜": "LLM_CONTEXT_PERIOD_VALIDATED",
            }
        else:
            final = baseline
        final = {
            **final, **common,
            "LLM강화시도": "YES",
            "LLM강화상태": "SUCCESS" if accepted else "HOLD",
            "LLM강화사유": reason,
            "LLM강화오류": "",
            "최종채택방식": "LLM_VALIDATED" if accepted else "BASELINE_RETAINED",
            "문맥근거문구": candidate.get("target_evidence", ""),
        }
        attempt = _attempt_record(
            row, attempted_at, model, baseline, candidate,
            validation_status="SUCCESS" if accepted else "HOLD",
            validation_reason=reason,
            accepted=accepted,
        )
        return final, attempt
    except Exception as exc:
        error_type = _safe_error_type(exc)
        final = {
            **baseline, **common,
            "LLM강화시도": "YES", "LLM강화상태": "API_ERROR",
            "LLM강화사유": error_type, "LLM강화오류": error_type,
            "최종채택방식": "BASELINE_RETAINED", "문맥근거문구": "",
        }
        attempt = _attempt_record(
            row, attempted_at, model, baseline, {},
            validation_status="API_ERROR", validation_reason=error_type, accepted=False,
        )
        return final, attempt


def _propose_period(row: Mapping[str, Any], model: str) -> Mapping[str, Any]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY_NOT_CONFIGURED")
    source = {
        "article_date": row.get("작성일", ""),
        "target_claim": row.get("원문", ""),
        "context_before": row.get("앞문맥", ""),
        "context_after": row.get("뒤문맥", ""),
        "subtype": row.get("하위유형", ""),
    }
    body = {
        "model": model,
        "instructions": _INSTRUCTIONS,
        "input": json.dumps(source, ensure_ascii=False),
        "text": {"format": {
            "type": "json_schema", "name": "context_period_resolution",
            "strict": True, "schema": _PERIOD_SCHEMA,
        }},
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    text = _output_text(payload)
    candidate = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    if not isinstance(candidate, dict):
        raise ValueError("CONTEXT_PERIOD_CONTRACT_INVALID")
    return candidate


def _validate_candidate(
    row: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, str]:
    if candidate.get("route_status") != "AUTO":
        return False, str(candidate.get("reason_code") or "LLM_HUMAN_REVIEW")
    article_date = _parse_date(row.get("작성일"))
    evidence = str(candidate.get("target_evidence") or "").strip()
    target = str(candidate.get("target_period") or "").strip()
    comparison = str(candidate.get("comparison_period") or "").strip()
    source = " ".join(str(row.get(key) or "") for key in ("앞문맥", "원문", "뒤문맥"))
    if article_date is None or not evidence or evidence not in source:
        return False, "TARGET_EVIDENCE_NOT_SOURCE_GROUNDED"
    resolved = _resolve_period_in_text(evidence, article_date)
    if resolved is None or resolved.get("target_period") != target:
        return False, "TARGET_PERIOD_DETERMINISTIC_NORMALIZATION_MISMATCH"
    if not _indicator_anchor_overlap(str(row.get("원문") or ""), evidence):
        return False, "CLAIM_CONTEXT_INDICATOR_ANCHOR_MISSING"
    comparison_evidence = str(candidate.get("comparison_evidence") or "").strip()
    if comparison_evidence and comparison_evidence not in source:
        return False, "COMPARISON_EVIDENCE_NOT_SOURCE_GROUNDED"
    if comparison:
        expected = _comparison_period(f"{evidence} {comparison_evidence}", target)
        if expected != comparison:
            return False, "COMPARISON_PERIOD_DETERMINISTIC_NORMALIZATION_MISMATCH"
    if row.get("하위유형") == "비교기준복원형" and not comparison:
        return False, "COMPARISON_PERIOD_REQUIRED"
    if not re.fullmatch(r"20\d{2}(?:-\d{2})?|20\d{2}-\d{2}-\d{2}\.\.20\d{2}-\d{2}-\d{2}", target):
        return False, "TARGET_PERIOD_FORMAT_INVALID"
    return True, "LLM_CONTEXT_PERIOD_VALIDATED"


def _indicator_anchor_overlap(claim: str, evidence: str) -> bool:
    if evidence in claim:
        return True
    claim_tokens = _content_tokens(claim)
    evidence_tokens = _content_tokens(evidence)
    for left in claim_tokens:
        for right in evidence_tokens:
            if left == right or (len(left) >= 3 and left[:3] in right) or (len(right) >= 3 and right[:3] in left):
                return True
    return False


def _content_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[가-힣A-Za-z]{2,}", text))
    return {token for token in tokens if token not in _STOPWORDS}


def _attempt_record(
    row: Mapping[str, Any], attempted_at: str, model: str,
    baseline: Mapping[str, Any], candidate: Mapping[str, Any],
    *, validation_status: str, validation_reason: str, accepted: bool,
) -> dict[str, Any]:
    return {
        "기사번호": row.get("기사번호", ""),
        "Claim번호": row.get("Claim번호", ""),
        "시도시각UTC": attempted_at,
        "모델": model,
        "기준상태": baseline.get("최종실행상태", ""),
        "기준사유": baseline.get("성공실패사유", ""),
        "검증상태": validation_status,
        "검증사유": validation_reason,
        "최종채택": "YES" if accepted else "NO",
        "구조화결과SHA256": _json_hash(candidate) if candidate else "",
        "후보": candidate,
    }


def _output_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output") or []:
        for part in item.get("content") or []:
            value = part.get("text")
            if isinstance(value, str) and value.strip():
                return value
    raise ValueError("OPENAI_CONTEXT_OUTPUT_TEXT_MISSING")


def _safe_error_type(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}_{code}" if code is not None else type(exc).__name__


def _json_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _write_manifest(output_dir: Path, source: Path) -> None:
    payload = {
        "schema_version": "clafact_category1_strong_context_manifest_v1",
        "created_at": _now(), "input": _file_record(source),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-context-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=256)
    parser.add_argument("--llm-model", default="gpt-5.6-luna")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    load_environment_file(args.env_file, os.environ)
    result = run(
        baseline_context_csv=args.baseline_context_csv,
        output_dir=args.output_dir,
        expected_count=args.expected_count,
        llm_model=args.llm_model,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
