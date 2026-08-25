"""Build one immutable 1,542-Claim execution ledger for categories 1-8."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.category_execution_plan import plan_for_category, route_official_evidence


SHEET_CATEGORY = {
    "01_문맥보완": 1,
    "02_복수Claim분리": 2,
    "03_최고최저기록": 3,
    "04_순위": 4,
    "05_비중구성비": 5,
    "06_증감량": 6,
    "07_증감률": 7,
    "08_직접값": 8,
}
EXPECTED_CATEGORY_COUNTS = {1: 256, 2: 339, 3: 113, 4: 27, 5: 46, 6: 54, 7: 326, 8: 381}
OUTPUT_NAMES = ("execution_records.jsonl", "summary.json", "safe_summary.txt")


def build(
    *,
    workbook_rows_json: Path,
    article_audit_csv: Path,
    registry_results_csv: Path,
    category12_results_json: Path,
    r2_pilot_results_jsonl: Path,
    output_dir: Path,
    expected_claim_count: int | None = 1542,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    workbook_rows = json.loads(workbook_rows_json.read_text(encoding="utf-8"))
    rows = _workbook_records(
        workbook_rows,
        enforce_production_counts=expected_claim_count == sum(EXPECTED_CATEGORY_COUNTS.values()),
    )
    if expected_claim_count is not None and len(rows) != expected_claim_count:
        raise ValueError(f"unexpected workbook Claim count: {len(rows)}")
    claim_ids = [row["Claim번호"] for _, _, row in rows]
    if any(not claim_id for claim_id in claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ValueError("blank or duplicate Claim ID in workbook")

    audit = _indexed(_read_csv(article_audit_csv), "Claim번호")
    registry = _indexed(_read_csv(registry_results_csv), "claim_id")
    if set(claim_ids) - set(audit):
        raise ValueError("article audit join is incomplete")
    if set(claim_ids) - set(registry):
        raise ValueError("registry result join is incomplete")
    category12 = json.loads(category12_results_json.read_text(encoding="utf-8"))
    category1 = _indexed(category12.get("final_category1_results") or [], "Claim번호")
    category2 = _indexed(category12.get("final_category2_parent_results") or [], "부모Claim번호")
    children: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for child in category12.get("final_category2_child_results") or []:
        children[_text(child.get("부모Claim번호"))].append(child)
    pilot_by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for result in _read_jsonl(r2_pilot_results_jsonl):
        pilot_by_parent[_text(result.get("parent_claim_id") or result.get("claim_id"))].append(result)

    execution_time = _now()
    batch_id = "CLAFACT_CATEGORIES_1_8_20260825_V1"
    records = []
    for sheet_name, category_no, source_row in rows:
        claim_id = _text(source_row.get("Claim번호"))
        if category_no == 1:
            record = _category12_record(
                sheet_name=sheet_name, category_no=category_no, source_row=source_row,
                stage_result=category1.get(claim_id), children=[], pilot=pilot_by_parent.get(claim_id, []),
                batch_id=batch_id, execution_time=execution_time,
            )
        elif category_no == 2:
            record = _category12_record(
                sheet_name=sheet_name, category_no=category_no, source_row=source_row,
                stage_result=category2.get(claim_id), children=children.get(claim_id, []),
                pilot=pilot_by_parent.get(claim_id, []), batch_id=batch_id, execution_time=execution_time,
            )
        else:
            record = _official_record(
                sheet_name=sheet_name, category_no=category_no, source_row=source_row,
                audit=audit[claim_id], registry=registry[claim_id], batch_id=batch_id,
                execution_time=execution_time,
            )
        record["result_sha256"] = _json_hash(record)
        records.append(record)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / OUTPUT_NAMES[0], records)
    summary = _summary(records, audit, registry, claim_ids, execution_time, batch_id)
    (output_dir / OUTPUT_NAMES[1]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[2]).write_text(_safe_summary(summary), encoding="utf-8")
    manifest = {
        "schema_version": "clafact_categories_1_8_execution_manifest_v1",
        "created_at": execution_time,
        "inputs": {
            "workbook_rows": _file_record(workbook_rows_json),
            "article_audit": _file_record(article_audit_csv),
            "registry_results": _file_record(registry_results_csv),
            "category12_results": _file_record(category12_results_json),
            "r2_pilot_results": _file_record(r2_pilot_results_jsonl),
        },
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": "NOT_USED_OR_RECORDED",
        "new_provider_calls": 0,
        "new_kosis_calls": 0,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _category12_record(
    *, sheet_name: str, category_no: int, source_row: Mapping[str, Any],
    stage_result: Mapping[str, Any] | None, children: Sequence[Mapping[str, Any]],
    pilot: Sequence[Mapping[str, Any]], batch_id: str, execution_time: str,
) -> dict[str, Any]:
    if stage_result is None:
        raise ValueError(f"category {category_no} stage result missing: {source_row.get('Claim번호')}")
    plan = plan_for_category(category_no)
    stage_status = _text(stage_result.get("최종실행상태"))
    execution_status = {
        "SUCCESS": "R2_STAGE_SUCCESS",
        "PARTIAL_SUCCESS": "R2_STAGE_PARTIAL_SUCCESS",
    }.get(stage_status, "HOLD")
    reason = _text(stage_result.get("성공실패사유")) or "R2_STAGE_RESULT_UNAVAILABLE"
    valid_children = sum(
        _text(child.get("부모최종실행상태")) == "SUCCESS"
        and _text(child.get("자식검증상태")) == "VALID"
        for child in children
    )
    pilot_counts = Counter(_text(item.get("status")) for item in pilot)
    pilot_reasons = Counter(_text(item.get("reason_code")) for item in pilot)
    next_action = (
        "RUN_ACCEPTED_CLAIMS_THROUGH_12SLOT_REENTRY"
        if execution_status == "R2_STAGE_SUCCESS"
        else "REVIEW_CONTEXT_OR_ATOMIC_SPLIT_HOLD"
    )
    return _base_record(
        sheet_name=sheet_name, category_no=category_no, source_row=source_row,
        batch_id=batch_id, execution_time=execution_time,
        execution_status=execution_status,
        stop_stage="R2_12SLOT_REENTRY" if execution_status == "R2_STAGE_SUCCESS" else "R2_CLAIM_STRUCTURING",
        reason_code=reason,
        evidence_plan=plan.evidence_plan,
        required_scope=plan.required_scope,
        required_official_value_count=plan.minimum_official_value_count,
        official_values_secured=0,
        kosis_reach_status="NOT_RUN_R2_REENTRY_PRECONDITION",
        coordinate_status="NOT_RUN_R2_REENTRY_PRECONDITION",
        coordinate={},
        official_value_status="NOT_REUSED_AFTER_CLAIM_CHANGE",
        official_value=None,
        official_unit=None,
        article_asof_status="NOT_RUN",
        target_value_role_status=("PILOT_SAMPLE_EXECUTED" if pilot else "NOT_RUN_FULL_COHORT"),
        verdict_status="NOT_EVALUATED_STAGE_A_ONLY",
        response_sha256="",
        evidence_url="",
        next_action=next_action,
        calculation_method=plan.calculation_method,
        extra={
            "r2_stage_status": stage_status,
            "atomic_child_count": len(children),
            "valid_atomic_child_count": valid_children,
            "pilot_status_counts": dict(sorted(pilot_counts.items())),
            "pilot_reason_counts": dict(sorted(pilot_reasons.items())),
        },
    )


def _official_record(
    *, sheet_name: str, category_no: int, source_row: Mapping[str, Any],
    audit: Mapping[str, Any], registry: Mapping[str, Any], batch_id: str,
    execution_time: str,
) -> dict[str, Any]:
    plan = plan_for_category(category_no)
    excluded = (
        _text(source_row.get("현재결과상태")) == "RECLASSIFIED"
        or _text(source_row.get("분류확신도")) == "검증 대상 제외"
    )
    official_count = int(_text(registry.get("official_value_status")) == "OFFICIAL_VALUE_FETCHED")
    route = route_official_evidence(
        category_no=category_no,
        official_value_count=official_count,
        registry_reason=_text(registry.get("reason_code")),
        excluded=excluded,
        article_asof_checked=False,
        target_value_role_checked=False,
    )
    coordinate = {
        key: _text(registry.get(source_key))
        for key, source_key in (
            ("org_id", "org_id"), ("table_id", "table_id"), ("item_id", "item_id"),
            ("object_codes", "object_codes"), ("period_type", "period_type"), ("period", "period"),
        )
        if _text(registry.get(source_key))
    }
    value = _number_or_text(registry.get("official_value")) if official_count else None
    return _base_record(
        sheet_name=sheet_name, category_no=category_no, source_row=source_row,
        batch_id=batch_id, execution_time=execution_time,
        execution_status=route.execution_status,
        stop_stage=route.stop_stage,
        reason_code=route.reason_code,
        evidence_plan=plan.evidence_plan,
        required_scope=plan.required_scope,
        required_official_value_count=(
            plan.required_scope if plan.required_scope.startswith("ALL_") else plan.minimum_official_value_count
        ),
        official_values_secured=official_count,
        kosis_reach_status=_kosis_reach(audit),
        coordinate_status=_text(registry.get("coordinate_status")) or "HOLD_COORDINATE_UNRESOLVED",
        coordinate=coordinate,
        official_value_status=_text(registry.get("official_value_status")) or "HOLD",
        official_value=value,
        official_unit=_text(registry.get("official_unit")) or None,
        article_asof_status="UNCHECKED_CURRENT_RELEASE_ONLY",
        target_value_role_status="NOT_USED_FOR_VERDICT",
        verdict_status="NOT_EVALUATED_NO_INDEPENDENT_VALUE_GOLD",
        response_sha256=(
            _text(registry.get("value_response_sha256"))
            or _text(audit.get("KOSIS응답SHA256"))
        ),
        evidence_url=_text(audit.get("KOSIS조회경로") or source_row.get("공식근거URL")),
        next_action=route.next_action,
        calculation_method=plan.calculation_method,
        extra={"registry_reason": _text(registry.get("reason_code"))},
    )


def _base_record(
    *, sheet_name: str, category_no: int, source_row: Mapping[str, Any], batch_id: str,
    execution_time: str, execution_status: str, stop_stage: str, reason_code: str,
    evidence_plan: str, required_scope: str, required_official_value_count: int | str,
    official_values_secured: int, kosis_reach_status: str, coordinate_status: str,
    coordinate: Mapping[str, Any], official_value_status: str, official_value: Any,
    official_unit: str | None, article_asof_status: str, target_value_role_status: str,
    verdict_status: str, response_sha256: str, evidence_url: str, next_action: str,
    calculation_method: str, extra: Mapping[str, Any],
) -> dict[str, Any]:
    claim_id = _text(source_row.get("Claim번호"))
    why = reason_code
    method = f"{evidence_plan} -> deterministic guards -> fail closed"
    return {
        "claim_id": claim_id,
        "sheet_name": sheet_name,
        "category_no": category_no,
        "category_label": plan_for_category(category_no).category_label,
        "source_status": _text(source_row.get("현재결과상태")),
        "source_verdict": _text(source_row.get("현재판정")),
        "execution_batch_id": batch_id,
        "executed_at_utc": execution_time,
        "execution_status": execution_status,
        "stop_stage": stop_stage,
        "reason_code": reason_code,
        "evidence_plan": evidence_plan,
        "required_scope": required_scope,
        "required_official_value_count": required_official_value_count,
        "official_values_secured": official_values_secured,
        "kosis_reach_status": kosis_reach_status,
        "coordinate_status": coordinate_status,
        "coordinate": dict(coordinate),
        "official_value_status": official_value_status,
        "official_value": official_value,
        "official_unit": official_unit,
        "calculation_method": calculation_method,
        "article_asof_status": article_asof_status,
        "target_value_role_status": target_value_role_status,
        "verdict_status": verdict_status,
        "response_sha256": response_sha256,
        "evidence_url": evidence_url,
        "next_action": next_action,
        "six_w": {
            "who": "CLAFACT-AUTO + Codex",
            "when": execution_time,
            "where": "LOCAL_IMMUTABLE_ARTIFACT_REPLAY",
            "what": f"Category {category_no} Claim {claim_id} execution and evidence routing",
            "how": method,
            "why": why,
        },
        "extra": dict(extra),
    }


def _summary(
    records: Sequence[Mapping[str, Any]], audit: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]], claim_ids: Sequence[str],
    execution_time: str, batch_id: str,
) -> dict[str, Any]:
    category_summaries: dict[str, Any] = {}
    for number in range(1, 9):
        cohort = [row for row in records if row["category_no"] == number]
        category_summaries[str(number)] = {
            "category_label": plan_for_category(number).category_label,
            "claim_count": len(cohort),
            "status_counts": dict(sorted(Counter(row["execution_status"] for row in cohort).items())),
            "reason_counts": dict(sorted(Counter(row["reason_code"] for row in cohort).items())),
            "kosis_reach_counts": dict(sorted(Counter(row["kosis_reach_status"] for row in cohort).items())),
            "official_value_fetched_count": sum(row["official_value_status"] == "OFFICIAL_VALUE_FETCHED" for row in cohort),
            "verdict_status_counts": dict(sorted(Counter(row["verdict_status"] for row in cohort).items())),
        }
    source_auto_rerouted = sum(
        row["category_no"] >= 3
        and row["source_status"] == "AUTO"
        and row["execution_status"] != "READY_FOR_VERDICT"
        for row in records
    )
    category12_ids = {row["claim_id"] for row in records if row["category_no"] in {1, 2}}
    prior_category12_values = sum(
        registry[claim_id].get("official_value_status") == "OFFICIAL_VALUE_FETCHED"
        for claim_id in category12_ids
    )
    return {
        "artifact": "clafact_categories_1_8_execution_v1",
        "execution_batch_id": batch_id,
        "created_at": execution_time,
        "claim_count": len(records),
        "unique_claim_id_count": len(set(claim_ids)),
        "article_context_exact_join_count": len(set(claim_ids) & set(audit)),
        "registry_result_join_count": len(set(claim_ids) & set(registry)),
        "category_summaries": category_summaries,
        "overall_status_counts": dict(sorted(Counter(row["execution_status"] for row in records).items())),
        "official_value_fetched_category3_to8_count": sum(
            row["category_no"] >= 3 and row["official_value_status"] == "OFFICIAL_VALUE_FETCHED"
            for row in records
        ),
        "prior_category1_to2_official_values_not_reused_count": prior_category12_values,
        "unsafe_prior_auto_rerouted_count": source_auto_rerouted,
        "ready_for_verdict_count": sum(row["execution_status"] == "READY_FOR_VERDICT" for row in records),
        "new_provider_call_count": 0,
        "new_kosis_query_count": 0,
        "accuracy_status": "NOT_EVALUABLE_NO_LINKED_CATEGORY_SPECIFIC_GOLD",
        "verdict_boundary": "Official-value coverage is not Claim correctness; article-as-of and target-role checks remain required.",
        "secret_handling": "NO_SECRET_USED_OR_RECORDED",
    }


def _workbook_records(
    payload: Mapping[str, Any], *, enforce_production_counts: bool
) -> list[tuple[str, int, dict[str, Any]]]:
    rows: list[tuple[str, int, dict[str, Any]]] = []
    for sheet_name, category_no in SHEET_CATEGORY.items():
        matrix = payload.get(sheet_name)
        if not isinstance(matrix, list) or len(matrix) < 5:
            raise ValueError(f"missing workbook sheet rows: {sheet_name}")
        headers = matrix[3]
        cohort = []
        for values in matrix[4:]:
            row = dict(zip(headers, values))
            if _text(row.get("Claim번호")):
                cohort.append((sheet_name, category_no, row))
        if enforce_production_counts and len(cohort) != EXPECTED_CATEGORY_COUNTS[category_no]:
            raise ValueError(f"unexpected category {category_no} count: {len(cohort)}")
        rows.extend(cohort)
    return rows


def _kosis_reach(audit: Mapping[str, Any]) -> str:
    if _text(audit.get("KOSIS값조회시도")) == "YES":
        return "VALUE_API_ATTEMPTED"
    attempts = sum(_int(audit.get(name)) for name in ("통계표검색시도", "항목정보조회시도", "기간정보조회시도"))
    return "METADATA_API_ATTEMPTED" if attempts else "NOT_REACHED"


def _number_or_text(value: Any) -> Any:
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _int(value: Any) -> int:
    try:
        return int(float(_text(value) or "0"))
    except ValueError:
        return 0


def _indexed(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result = {_text(row.get(key)): dict(row) for row in rows}
    if "" in result or len(result) != len(rows):
        raise ValueError(f"blank or duplicate index: {key}")
    return result


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe_summary(summary: Mapping[str, Any]) -> str:
    lines = [
        f"전체 Claim: {summary['claim_count']}건",
        f"전체 실행상태: {json.dumps(summary['overall_status_counts'], ensure_ascii=False, sort_keys=True)}",
        f"3~8번 공식값 확보: {summary['official_value_fetched_category3_to8_count']}건",
        f"기존 AUTO 안전 재라우팅: {summary['unsafe_prior_auto_rerouted_count']}건",
        f"최종 Verdict 준비 완료: {summary['ready_for_verdict_count']}건",
        "신규 공급자/KOSIS 호출: 0건",
        f"정확도: {summary['accuracy_status']}",
    ]
    return "\n".join(lines) + "\n"


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook-rows-json", type=Path, required=True)
    parser.add_argument("--article-audit-csv", type=Path, required=True)
    parser.add_argument("--registry-results-csv", type=Path, required=True)
    parser.add_argument("--category12-results-json", type=Path, required=True)
    parser.add_argument("--r2-pilot-results-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-claim-count", type=int, default=1542)
    args = parser.parse_args()
    summary = build(
        workbook_rows_json=args.workbook_rows_json,
        article_audit_csv=args.article_audit_csv,
        registry_results_csv=args.registry_results_csv,
        category12_results_json=args.category12_results_json,
        r2_pilot_results_jsonl=args.r2_pilot_results_jsonl,
        output_dir=args.output_dir,
        expected_claim_count=args.expected_claim_count,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
