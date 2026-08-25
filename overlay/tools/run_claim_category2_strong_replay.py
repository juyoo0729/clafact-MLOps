"""Re-run category 2 with rule ensemble, LLM fallback, and hard invariants."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from config.settings import load_environment_file
from core.claim_split_methods import run_split_method
from tools.run_claim_categories_1_2_audit import (
    CHILD_COLUMNS,
    PARENT_COLUMNS,
    execute_split_claim,
)


STRONG_PARENT_COLUMNS = (*PARENT_COLUMNS,
    "기존실행상태", "기존실행사유", "강화기준상태", "강화기준사유",
    "LLM강화시도", "LLM강화상태", "LLM강화사유", "LLM강화오류",
    "최종채택방식",
)
ATTEMPT_COLUMNS = (
    "기사번호", "Claim번호", "시도시각UTC", "모델", "기준상태", "기준사유",
    "기준자식수", "LLM상태", "LLM사유", "LLM자식수", "최종채택",
    "오류유형", "구조화결과SHA256", "후보자식JSON",
)
OUTPUT_NAMES = (
    "CLAFACT_2번_강화재실행_통합기록.json",
    "category2_strong_parent_results.csv",
    "category2_strong_child_results.csv",
    "llm_fallback_attempts.jsonl",
    "summary.json",
)


def run(
    *,
    baseline_parent_csv: Path,
    output_dir: Path,
    expected_parent_count: int | None = 339,
    llm_model: str = "gpt-5.6-luna",
    workers: int = 4,
    rule_runner: Callable[..., Mapping[str, Any]] = run_split_method,
    llm_runner: Callable[..., Mapping[str, Any]] = run_split_method,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(baseline_parent_csv)
    if expected_parent_count is not None and len(rows) != expected_parent_count:
        raise ValueError(f"unexpected parent count: {len(rows)} != {expected_parent_count}")
    claim_ids = [row.get("부모Claim번호", "") for row in rows]
    if any(not value for value in claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ValueError("blank or duplicate parent Claim ID")

    execution_time = _now()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(
            lambda row: _process_row(
                row,
                execution_time=execution_time,
                llm_model=llm_model,
                rule_runner=rule_runner,
                llm_runner=llm_runner,
            ),
            rows,
        ))

    parents = [item[0] for item in processed]
    children = [child for item in processed for child in item[1]]
    attempts = [item[2] for item in processed if item[2] is not None]
    original_status = Counter(row.get("최종실행상태", "") for row in rows)
    rule_status = Counter(parent.get("강화기준상태", "") for parent in parents)
    final_status = Counter(parent.get("최종실행상태", "") for parent in parents)
    attempt_status = Counter(attempt.get("LLM상태", "") for attempt in attempts)
    safe_children = sum(
        child.get("부모최종실행상태") == "SUCCESS"
        and child.get("자식검증상태") == "VALID"
        for child in children
    )
    summary = {
        "artifact": "clafact_category2_strong_replay_v1",
        "execution_time_utc": execution_time,
        "parent_count": len(parents),
        "original_status_counts": dict(sorted(original_status.items())),
        "recalculated_rule_status_counts": dict(sorted(rule_status.items())),
        "llm_attempt_count": len(attempts),
        "llm_attempt_status_counts": dict(sorted(attempt_status.items())),
        "llm_validated_success_count": sum(
            attempt.get("최종채택") == "YES" for attempt in attempts
        ),
        "final_status_counts": dict(sorted(final_status.items())),
        "success_improvement_vs_original": (
            final_status.get("SUCCESS", 0) - original_status.get("SUCCESS", 0)
        ),
        "final_child_count": len(children),
        "final_safe_child_count": safe_children,
        "llm_model": llm_model,
        "llm_retry_count": 0,
        "kosis_requery_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_PARENT_TO_CHILD_GOLD_FOR_339",
        "secret_handling": "KEY_USED_BY_PROVIDER_CLIENT_NOT_RECORDED",
    }

    _write_csv(output_dir / OUTPUT_NAMES[1], parents, STRONG_PARENT_COLUMNS)
    _write_csv(output_dir / OUTPUT_NAMES[2], children, CHILD_COLUMNS)
    _write_jsonl(output_dir / OUTPUT_NAMES[3], attempts)
    (output_dir / OUTPUT_NAMES[4]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    consolidated = {
        "schema_version": "clafact_category2_strong_replay_v1",
        "summary": summary,
        "method": {
            "first_pass": "rule_ensemble",
            "fallback": "OpenAI strict structured output",
            "acceptance": [
                "two or more child Claims",
                "one target quantity per child",
                "target copied from the parent source",
                "exact parent-child number coverage or explicit 각각 reuse",
            ],
        },
        "parent_results": parents,
        "child_results": children,
        "llm_attempts": attempts,
    }
    (output_dir / OUTPUT_NAMES[0]).write_text(
        json.dumps(consolidated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_manifest(output_dir, baseline_parent_csv)
    return summary


def _process_row(
    row: Mapping[str, Any],
    *,
    execution_time: str,
    llm_model: str,
    rule_runner: Callable[..., Mapping[str, Any]],
    llm_runner: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    source = _source_row(row)
    rule_parent, rule_children = execute_split_claim(
        source,
        execution_time=execution_time,
        split_method="rule_ensemble",
        split_runner=rule_runner,
    )
    common = {
        "기존실행상태": row.get("최종실행상태", ""),
        "기존실행사유": row.get("성공실패사유", ""),
        "강화기준상태": rule_parent["최종실행상태"],
        "강화기준사유": rule_parent["성공실패사유"],
    }
    if rule_parent["최종실행상태"] == "SUCCESS":
        parent = {
            **rule_parent, **common,
            "LLM강화시도": "NO", "LLM강화상태": "NOT_NEEDED",
            "LLM강화사유": "RULE_ENSEMBLE_VALIDATED", "LLM강화오류": "",
            "최종채택방식": "RULE_ENSEMBLE",
        }
        return parent, rule_children, None

    attempted_at = _now()
    try:
        def invoke(method: str, sentence: str) -> Mapping[str, Any]:
            return llm_runner(method, sentence, model=llm_model)

        llm_parent, llm_children = execute_split_claim(
            source,
            execution_time=execution_time,
            split_method="llm_openai",
            split_runner=invoke,
        )
        accepted = llm_parent["최종실행상태"] == "SUCCESS"
        selected_parent = llm_parent if accepted else rule_parent
        selected_children = llm_children if accepted else rule_children
        parent = {
            **selected_parent, **common,
            "LLM강화시도": "YES", "LLM강화상태": llm_parent["최종실행상태"],
            "LLM강화사유": llm_parent["성공실패사유"], "LLM강화오류": "",
            "최종채택방식": "LLM_VALIDATED" if accepted else "RULE_HOLD_RETAINED",
        }
        candidate_payload = {"parent": llm_parent, "children": llm_children}
        attempt = {
            "기사번호": row.get("기사번호", ""),
            "Claim번호": row.get("부모Claim번호", ""),
            "시도시각UTC": attempted_at,
            "모델": llm_model,
            "기준상태": rule_parent["최종실행상태"],
            "기준사유": rule_parent["성공실패사유"],
            "기준자식수": len(rule_children),
            "LLM상태": llm_parent["최종실행상태"],
            "LLM사유": llm_parent["성공실패사유"],
            "LLM자식수": len(llm_children),
            "최종채택": "YES" if accepted else "NO",
            "오류유형": "",
            "구조화결과SHA256": _json_hash(candidate_payload),
            "후보자식JSON": json.dumps(llm_children, ensure_ascii=False),
        }
        return parent, selected_children, attempt
    except Exception as exc:  # one attempt only; retain fail-closed rule result
        error_type = _safe_error_type(exc)
        parent = {
            **rule_parent, **common,
            "LLM강화시도": "YES", "LLM강화상태": "API_ERROR",
            "LLM강화사유": error_type, "LLM강화오류": error_type,
            "최종채택방식": "RULE_HOLD_RETAINED",
        }
        attempt = {
            "기사번호": row.get("기사번호", ""),
            "Claim번호": row.get("부모Claim번호", ""),
            "시도시각UTC": attempted_at,
            "모델": llm_model,
            "기준상태": rule_parent["최종실행상태"],
            "기준사유": rule_parent["성공실패사유"],
            "기준자식수": len(rule_children),
            "LLM상태": "API_ERROR", "LLM사유": error_type, "LLM자식수": 0,
            "최종채택": "NO", "오류유형": error_type,
            "구조화결과SHA256": "", "후보자식JSON": "[]",
        }
        return parent, rule_children, attempt


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "기사번호": row.get("기사번호", ""),
        "Claim번호": row.get("부모Claim번호", ""),
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
    return {
        "path": str(path), "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _write_manifest(output_dir: Path, source: Path) -> None:
    payload = {
        "schema_version": "clafact_category2_strong_replay_manifest_v1",
        "created_at": _now(),
        "input": _file_record(source),
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_RECORDED",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-parent-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--expected-parent-count", type=int, default=339)
    parser.add_argument("--llm-model", default="gpt-5.6-luna")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    load_environment_file(args.env_file, os.environ)
    result = run(
        baseline_parent_csv=args.baseline_parent_csv,
        output_dir=args.output_dir,
        expected_parent_count=args.expected_parent_count,
        llm_model=args.llm_model,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
