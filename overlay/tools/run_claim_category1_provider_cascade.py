"""Apply HCX only to category 1 context rows still unresolved after OpenAI."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import Request, urlopen

from config.settings import load_environment_file
from tools.run_claim_categories_1_2_audit import execute_context_claim
from tools.run_claim_category1_strong_context_replay import (
    ELIGIBLE_REASONS,
    STRONG_CONTEXT_COLUMNS,
    _INSTRUCTIONS,
    _PERIOD_SCHEMA,
    _validate_candidate,
)
from tools.run_claim_category2_provider_cascade import _build_rate_gate, _safe_error_type


CASCADE_COLUMNS = (*STRONG_CONTEXT_COLUMNS,
    "2차공급자", "2차공급자시도", "2차공급자상태", "2차공급자사유",
    "2차공급자오류", "2차최종채택방식",
)
OUTPUT_NAMES = (
    "CLAFACT_1번_다중공급자_통합기록.json",
    "category1_cascade_results.csv",
    "provider_cascade_attempts.jsonl",
    "summary.json",
)


def run(
    *,
    baseline_csv: Path,
    output_dir: Path,
    expected_count: int | None = 256,
    provider_model: str = "HCX-007",
    workers: int = 1,
    min_request_interval_seconds: float = 2.1,
    proposer: Callable[[Mapping[str, Any], str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(baseline_csv)
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError("unexpected context count")
    execution_time = _now()
    rate_gate = _build_rate_gate(min_request_interval_seconds)
    propose = proposer or _propose_hcx
    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(
            lambda row: _process(
                row,
                execution_time=execution_time,
                model=provider_model,
                proposer=propose,
                rate_gate=rate_gate,
            ),
            rows,
        ))
    results = [item[0] for item in processed]
    attempts = [item[1] for item in processed if item[1] is not None]
    baseline_status = Counter(row.get("최종실행상태", "") for row in rows)
    final_status = Counter(row.get("최종실행상태", "") for row in results)
    attempt_status = Counter(row.get("공급자상태", "") for row in attempts)
    summary = {
        "artifact": "clafact_category1_provider_cascade_v1",
        "execution_time_utc": execution_time,
        "claim_count": len(results),
        "baseline_status_counts": dict(sorted(baseline_status.items())),
        "provider_method": "llm_hcx",
        "provider_model": provider_model,
        "provider_attempt_count": len(attempts),
        "provider_attempt_status_counts": dict(sorted(attempt_status.items())),
        "provider_validated_success_count": sum(row.get("최종채택") == "YES" for row in attempts),
        "final_status_counts": dict(sorted(final_status.items())),
        "success_improvement": final_status.get("SUCCESS", 0) - baseline_status.get("SUCCESS", 0),
        "retry_count": 0,
        "min_request_interval_seconds": min_request_interval_seconds,
        "kosis_requery_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_CONTEXT_PERIOD_GOLD_FOR_256",
        "secret_handling": "KEY_USED_BY_PROVIDER_CLIENT_NOT_RECORDED",
    }
    _write_csv(output_dir / OUTPUT_NAMES[1], results, CASCADE_COLUMNS)
    _write_jsonl(output_dir / OUTPUT_NAMES[2], attempts)
    (output_dir / OUTPUT_NAMES[3]).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {
        "schema_version": "clafact_category1_provider_cascade_v1",
        "summary": summary,
        "method": {
            "baseline": "deterministic period resolver plus OpenAI guarded replay",
            "second_provider": "llm_hcx",
            "acceptance": "same deterministic evidence and date-normalization invariants",
        },
        "context_results": results,
        "provider_attempts": attempts,
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_manifest(output_dir, baseline_csv)
    return summary


def _process(
    row: Mapping[str, Any],
    *,
    execution_time: str,
    model: str,
    proposer: Callable[[Mapping[str, Any], str], Mapping[str, Any]],
    rate_gate: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if row.get("최종실행상태") == "SUCCESS":
        return ({
            **row,
            "2차공급자": "llm_hcx", "2차공급자시도": "NO",
            "2차공급자상태": "NOT_NEEDED", "2차공급자사유": "BASELINE_SUCCESS",
            "2차공급자오류": "", "2차최종채택방식": row.get("최종채택방식", "BASELINE"),
        }, None)
    baseline = execute_context_claim(row, execution_time=execution_time)
    if baseline.get("성공실패사유") not in ELIGIBLE_REASONS:
        return ({
            **row,
            "2차공급자": "llm_hcx", "2차공급자시도": "NO",
            "2차공급자상태": "NOT_ELIGIBLE", "2차공급자사유": baseline.get("성공실패사유", ""),
            "2차공급자오류": "", "2차최종채택방식": "BASELINE_RETAINED",
        }, None)
    attempted_at = _now()
    try:
        rate_gate()
        candidate = dict(proposer(row, model))
        valid, reason = _validate_candidate(row, candidate)
        accepted = valid and candidate.get("route_status") == "AUTO"
        if accepted:
            final = {
                **row,
                "감지기간표현": candidate["target_evidence"],
                "기간근거범위": "HCX_VALIDATED_ARTICLE_CONTEXT",
                "보완기준기간": candidate["target_period"],
                "보완비교기간": candidate["comparison_period"],
                "보완주기": candidate["frequency"],
                "최종실행상태": "SUCCESS",
                "성공실패사유": "HCX_CONTEXT_PERIOD_VALIDATED",
                "중단단계": "CONTEXT_COMPLETE",
                "KOSIS재조회상태": "NOT_RUN_PRECONDITION_REPARSED_12SLOTS_REQUIRED",
                "다음실행단계": "보완 Claim 12슬롯 재구조화 후 KOSIS 재조회",
                "문맥근거문구": candidate["target_evidence"],
            }
        else:
            final = dict(row)
        final.update({
            "2차공급자": "llm_hcx", "2차공급자시도": "YES",
            "2차공급자상태": "SUCCESS" if accepted else "HOLD",
            "2차공급자사유": reason, "2차공급자오류": "",
            "2차최종채택방식": "SECOND_PROVIDER_VALIDATED" if accepted else "BASELINE_RETAINED",
        })
        attempt = _attempt(row, attempted_at, model, candidate, "SUCCESS" if accepted else "HOLD", reason, accepted)
        return final, attempt
    except Exception as exc:
        error = _safe_error_type(exc)
        final = {
            **row,
            "2차공급자": "llm_hcx", "2차공급자시도": "YES",
            "2차공급자상태": "API_ERROR", "2차공급자사유": error,
            "2차공급자오류": error, "2차최종채택방식": "BASELINE_RETAINED",
        }
        return final, _attempt(row, attempted_at, model, {}, "API_ERROR", error, False)


def _propose_hcx(row: Mapping[str, Any], model: str) -> Mapping[str, Any]:
    key = os.environ.get("HCX_API_KEY")
    if not key:
        raise RuntimeError("HCX_API_KEY_NOT_CONFIGURED")
    source = {
        "article_date": row.get("작성일", ""),
        "target_claim": row.get("원문", ""),
        "context_before": row.get("앞문맥", ""),
        "context_after": row.get("뒤문맥", ""),
        "subtype": row.get("하위유형", ""),
    }
    body = {
        "messages": [
            {"role": "system", "content": _INSTRUCTIONS},
            {"role": "user", "content": json.dumps(source, ensure_ascii=False)},
        ],
        "temperature": 0,
        "maxCompletionTokens": 1024,
        "thinking": {"effort": "none"},
        "responseFormat": {"type": "json", "schema": _PERIOD_SCHEMA},
    }
    request = Request(
        f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{model}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read())
    content = str(payload["result"]["message"]["content"])
    candidate = json.loads(content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    if not isinstance(candidate, dict):
        raise ValueError("HCX_CONTEXT_PERIOD_CONTRACT_INVALID")
    return candidate


def _attempt(
    row: Mapping[str, Any], attempted_at: str, model: str, candidate: Mapping[str, Any],
    status: str, reason: str, accepted: bool,
) -> dict[str, Any]:
    return {
        "기사번호": row.get("기사번호", ""), "Claim번호": row.get("Claim번호", ""),
        "시도시각UTC": attempted_at, "공급자방법": "llm_hcx", "공급자모델": model,
        "기준상태": row.get("최종실행상태", ""), "기준사유": row.get("성공실패사유", ""),
        "공급자상태": status, "공급자사유": reason, "최종채택": "YES" if accepted else "NO",
        "재시도횟수": 0, "구조화결과SHA256": _json_hash(candidate) if candidate else "",
        "후보": candidate,
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _json_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _write_manifest(output_dir: Path, source: Path) -> None:
    payload = {
        "schema_version": "clafact_category1_provider_cascade_manifest_v1",
        "created_at": _now(), "input": _file_record(source),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=256)
    parser.add_argument("--provider-model", default="HCX-007")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--min-request-interval-seconds", type=float, default=2.1)
    args = parser.parse_args()
    load_environment_file(args.env_file, os.environ)
    result = run(
        baseline_csv=args.baseline_csv, output_dir=args.output_dir,
        expected_count=args.expected_count, provider_model=args.provider_model,
        workers=args.workers, min_request_interval_seconds=args.min_request_interval_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
