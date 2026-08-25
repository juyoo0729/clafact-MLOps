"""Apply a second structured-output provider only to remaining category 2 HOLDs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from config.settings import load_environment_file
from core.claim_split_methods import run_split_method
from tools.run_claim_categories_1_2_audit import CHILD_COLUMNS, execute_split_claim
from tools.run_claim_category2_strong_replay import STRONG_PARENT_COLUMNS


CASCADE_PARENT_COLUMNS = (*STRONG_PARENT_COLUMNS,
    "2차공급자", "2차공급자시도", "2차공급자상태", "2차공급자사유",
    "2차공급자오류", "2차최종채택방식",
)
OUTPUT_NAMES = (
    "CLAFACT_2번_다중공급자_통합기록.json",
    "category2_cascade_parent_results.csv",
    "category2_cascade_child_results.csv",
    "provider_cascade_attempts.jsonl",
    "summary.json",
)


def run(
    *,
    baseline_parent_csv: Path,
    baseline_child_csv: Path,
    output_dir: Path,
    provider_method: str = "llm_hcx",
    provider_model: str = "HCX-007",
    expected_parent_count: int | None = 339,
    workers: int = 4,
    min_request_interval_seconds: float = 0.0,
    provider_runner: Callable[..., Mapping[str, Any]] = run_split_method,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    if min_request_interval_seconds < 0:
        raise ValueError("rate-limit setting must be non-negative")
    output_dir.mkdir(parents=True, exist_ok=True)
    parents = _read_csv(baseline_parent_csv)
    children = _read_csv(baseline_child_csv)
    if expected_parent_count is not None and len(parents) != expected_parent_count:
        raise ValueError("unexpected parent count")
    child_by_parent: dict[str, list[dict[str, str]]] = defaultdict(list)
    for child in children:
        child_by_parent[child.get("부모Claim번호", "")].append(child)
    execution_time = _now()
    rate_gate = _build_rate_gate(min_request_interval_seconds)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(
            lambda parent: _process(
                parent,
                child_by_parent.get(parent.get("부모Claim번호", ""), []),
                execution_time=execution_time,
                provider_method=provider_method,
                provider_model=provider_model,
                provider_runner=provider_runner,
                rate_gate=rate_gate,
            ),
            parents,
        ))
    final_parents = [item[0] for item in processed]
    final_children = [child for item in processed for child in item[1]]
    attempts = [item[2] for item in processed if item[2] is not None]
    baseline_status = Counter(row.get("최종실행상태", "") for row in parents)
    final_status = Counter(row.get("최종실행상태", "") for row in final_parents)
    attempt_status = Counter(attempt.get("공급자상태", "") for attempt in attempts)
    summary = {
        "artifact": "clafact_category2_provider_cascade_v1",
        "execution_time_utc": execution_time,
        "parent_count": len(final_parents),
        "baseline_status_counts": dict(sorted(baseline_status.items())),
        "provider_method": provider_method,
        "provider_model": provider_model,
        "provider_attempt_count": len(attempts),
        "provider_attempt_status_counts": dict(sorted(attempt_status.items())),
        "provider_validated_success_count": sum(
            attempt.get("최종채택") == "YES" for attempt in attempts
        ),
        "final_status_counts": dict(sorted(final_status.items())),
        "success_improvement": final_status.get("SUCCESS", 0) - baseline_status.get("SUCCESS", 0),
        "final_child_count": len(final_children),
        "final_safe_child_count": sum(
            child.get("부모최종실행상태") == "SUCCESS"
            and child.get("자식검증상태") == "VALID"
            for child in final_children
        ),
        "retry_count": 0,
        "min_request_interval_seconds": min_request_interval_seconds,
        "kosis_requery_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_PARENT_TO_CHILD_GOLD_FOR_339",
        "secret_handling": "KEY_USED_BY_PROVIDER_CLIENT_NOT_RECORDED",
    }
    _write_csv(output_dir / OUTPUT_NAMES[1], final_parents, CASCADE_PARENT_COLUMNS)
    _write_csv(output_dir / OUTPUT_NAMES[2], final_children, CHILD_COLUMNS)
    _write_jsonl(output_dir / OUTPUT_NAMES[3], attempts)
    (output_dir / OUTPUT_NAMES[4]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    payload = {
        "schema_version": "clafact_category2_provider_cascade_v1",
        "summary": summary,
        "method": {
            "baseline": "rule ensemble plus OpenAI guarded replay",
            "second_provider": provider_method,
            "acceptance": "same deterministic number and child invariants",
        },
        "parent_results": final_parents,
        "child_results": final_children,
        "provider_attempts": attempts,
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_manifest(output_dir, baseline_parent_csv, baseline_child_csv)
    return summary


def _process(
    parent: Mapping[str, Any], baseline_children: Sequence[Mapping[str, Any]],
    *, execution_time: str, provider_method: str, provider_model: str,
    provider_runner: Callable[..., Mapping[str, Any]],
    rate_gate: Callable[[], None],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    if parent.get("최종실행상태") == "SUCCESS":
        return ({
            **parent,
            "2차공급자": provider_method, "2차공급자시도": "NO",
            "2차공급자상태": "NOT_NEEDED", "2차공급자사유": "BASELINE_SUCCESS",
            "2차공급자오류": "", "2차최종채택방식": parent.get("최종채택방식", "BASELINE"),
        }, [dict(child) for child in baseline_children], None)
    source = _source_row(parent)
    attempted_at = _now()
    try:
        def invoke(method: str, sentence: str) -> Mapping[str, Any]:
            rate_gate()
            return provider_runner(method, sentence, model=provider_model)

        proposed_parent, proposed_children = execute_split_claim(
            source,
            execution_time=execution_time,
            split_method=provider_method,
            split_runner=invoke,
        )
        accepted = proposed_parent["최종실행상태"] == "SUCCESS"
        selected_parent = proposed_parent if accepted else dict(parent)
        selected_children = proposed_children if accepted else [dict(child) for child in baseline_children]
        final_parent = {
            **selected_parent,
            "2차공급자": provider_method, "2차공급자시도": "YES",
            "2차공급자상태": proposed_parent["최종실행상태"],
            "2차공급자사유": proposed_parent["성공실패사유"], "2차공급자오류": "",
            "2차최종채택방식": "SECOND_PROVIDER_VALIDATED" if accepted else "BASELINE_HOLD_RETAINED",
        }
        candidate = {"parent": proposed_parent, "children": proposed_children}
        attempt = {
            "기사번호": parent.get("기사번호", ""),
            "Claim번호": parent.get("부모Claim번호", ""),
            "시도시각UTC": attempted_at,
            "공급자방법": provider_method,
            "공급자모델": provider_model,
            "기준상태": parent.get("최종실행상태", ""),
            "기준사유": parent.get("성공실패사유", ""),
            "공급자상태": proposed_parent["최종실행상태"],
            "공급자사유": proposed_parent["성공실패사유"],
            "공급자자식수": len(proposed_children),
            "최종채택": "YES" if accepted else "NO",
            "재시도횟수": 0,
            "오류유형": "",
            "구조화결과SHA256": _json_hash(candidate),
            "후보자식": proposed_children,
        }
        return final_parent, selected_children, attempt
    except Exception as exc:
        error = _safe_error_type(exc)
        final_parent = {
            **parent,
            "2차공급자": provider_method, "2차공급자시도": "YES",
            "2차공급자상태": "API_ERROR", "2차공급자사유": error,
            "2차공급자오류": error, "2차최종채택방식": "BASELINE_HOLD_RETAINED",
        }
        attempt = {
            "기사번호": parent.get("기사번호", ""), "Claim번호": parent.get("부모Claim번호", ""),
            "시도시각UTC": attempted_at, "공급자방법": provider_method, "공급자모델": provider_model,
            "기준상태": parent.get("최종실행상태", ""), "기준사유": parent.get("성공실패사유", ""),
            "공급자상태": "API_ERROR", "공급자사유": error, "공급자자식수": 0,
            "최종채택": "NO", "재시도횟수": 0, "오류유형": error,
            "구조화결과SHA256": "", "후보자식": [],
        }
        return final_parent, [dict(child) for child in baseline_children], attempt


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "기사번호": row.get("기사번호", ""), "Claim번호": row.get("부모Claim번호", ""),
        "작성일": row.get("작성일", ""), "제목": row.get("제목", ""), "URL": row.get("URL", ""),
        "원문": row.get("원문", ""), "앞문맥": row.get("앞문맥", ""), "뒤문맥": row.get("뒤문맥", ""),
        "하위유형": row.get("하위유형", ""), "기사내원문시작위치": "",
        "공식값상태": row.get("기존KOSIS상태", ""), "최종성공실패사유": row.get("기존KOSIS사유", ""),
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


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _safe_error_type(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__}_{code}" if code is not None else type(exc).__name__


def _build_rate_gate(min_interval_seconds: float) -> Callable[[], None]:
    lock = threading.Lock()
    last_started = [0.0]

    def wait_for_slot() -> None:
        with lock:
            remaining = min_interval_seconds - (time.monotonic() - last_started[0])
            if remaining > 0:
                time.sleep(remaining)
            last_started[0] = time.monotonic()

    return wait_for_slot


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _write_manifest(output_dir: Path, parent_source: Path, child_source: Path) -> None:
    payload = {
        "schema_version": "clafact_category2_provider_cascade_manifest_v1", "created_at": _now(),
        "inputs": {"parents": _file_record(parent_source), "children": _file_record(child_source)},
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-parent-csv", type=Path, required=True)
    parser.add_argument("--baseline-child-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--provider-method", choices=("llm_hcx", "llm_openai"), default="llm_hcx")
    parser.add_argument("--provider-model", default="HCX-007")
    parser.add_argument("--expected-parent-count", type=int, default=339)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-request-interval-seconds", type=float, default=0.0)
    args = parser.parse_args()
    load_environment_file(args.env_file, os.environ)
    result = run(
        baseline_parent_csv=args.baseline_parent_csv, baseline_child_csv=args.baseline_child_csv,
        output_dir=args.output_dir, provider_method=args.provider_method,
        provider_model=args.provider_model, expected_parent_count=args.expected_parent_count,
        workers=args.workers, min_request_interval_seconds=args.min_request_interval_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
