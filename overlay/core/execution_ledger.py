"""Per-claim execution ledger: one row per run, success or failure alike.

Every run of the verification pipeline over a claim appends exactly one row to
an append-only history CSV.  Success rows must name the rule pattern that
succeeded (never a bare "성공"); failure rows must name the exact failed stage
and cause, which is folded into one of six resolution bundles.  A batch is
aggregated automatically into a funnel, per-stage/per-cause failure counts,
pattern generality stats, and a before/after comparison, and the whole set is
rendered as: 실행이력.csv (raw), 실행원장.xlsx (human view),
분류별_성능요약.csv, 개선전후_비교.csv, 최종보고서.txt.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

PIPELINE_STAGES: tuple[str, ...] = (
    "R2_INPUT",
    "CONCEPT",
    "CATALOG_SEARCH",
    "HARD_GUARD",
    "SEMANTIC_MATCH",
    "EVIDENCE_CELL",
    "OFFICIAL_VALUE_FETCH",
    "PUBLICATION_CHECK",
    "CALCULATION",
    "VERDICT",
)

# The six resolution bundles the operator groups failures into.
FAILURE_BUNDLES: tuple[str, ...] = (
    "표_미발견",          # could not find an official table at all
    "항목_미발견",        # table found, item/classification not found
    "좌표_불일치",        # period/region/dimension coordinates do not fit
    "공식값_조회실패",    # official value lookup itself failed
    "공표일_미확인",      # publication basis could not be confirmed
    "실제_불일치",        # article value genuinely disagrees with official value
    "기타",
)

_STAGE_TO_BUNDLE: dict[str, str] = {
    "R2_INPUT": "기타",
    "CONCEPT": "표_미발견",
    "CATALOG_SEARCH": "표_미발견",
    "HARD_GUARD": "좌표_불일치",
    "SEMANTIC_MATCH": "좌표_불일치",
    "EVIDENCE_CELL": "항목_미발견",
    "OFFICIAL_VALUE_FETCH": "공식값_조회실패",
    "PUBLICATION_CHECK": "공표일_미확인",
    "CALCULATION": "공식값_조회실패",
    "VERDICT": "실제_불일치",
}

_CAUSE_OVERRIDES: dict[str, str] = {
    "ITEM_CODE_NOT_FOUND": "항목_미발견",
    "KOSIS_METADATA_UNAVAILABLE": "항목_미발견",
    "PERIOD_UNAVAILABLE": "좌표_불일치",
    "RELEASE_DATE_UNCONFIRMED": "공표일_미확인",
    "VALUE_MISMATCH": "실제_불일치",
}

EXECUTION_HISTORY_COLUMNS: tuple[str, ...] = (
    "run_seq", "run_at", "claim_id", "sentence", "code_version", "data_version",
    "subtype", "applied_pattern", "pattern_generality",
    *(f"stage_{stage}" for stage in PIPELINE_STAGES),
    "failed_stage", "failure_cause", "failure_bundle",
    "tbl_id", "item_id", "period", "dimension_coords",
    "official_value", "article_value", "official_source_url",
    "publication_basis", "response_hash",
    "calculation_result", "final_verdict",
    "duration_ms", "api_call_count", "prev_result", "curr_result",
)

_SUCCESS_VERDICTS = {"TRUE", "FALSE", "MATCH", "MISMATCH"}


def failure_bundle_for(failed_stage: str | None, failure_cause: str | None) -> str | None:
    if not failed_stage and not failure_cause:
        return None
    if failure_cause and failure_cause in _CAUSE_OVERRIDES:
        return _CAUSE_OVERRIDES[failure_cause]
    if failed_stage:
        return _STAGE_TO_BUNDLE.get(failed_stage, "기타")
    return "기타"


def build_execution_record(
    *,
    claim_id: str,
    sentence: str,
    stage_statuses: Mapping[str, str],
    run_at: str = "",
    code_version: str = "",
    data_version: str = "",
    subtype: str = "",
    applied_pattern: str | None = None,
    pattern_generality: str | None = None,
    failed_stage: str | None = None,
    failure_cause: str | None = None,
    tbl_id: str | None = None,
    item_id: str | None = None,
    period: str | None = None,
    dimension_coords: str | None = None,
    official_value: str | None = None,
    article_value: str | None = None,
    official_source_url: str | None = None,
    publication_basis: str | None = None,
    response_hash: str | None = None,
    calculation_result: str | None = None,
    final_verdict: str | None = None,
    duration_ms: int | None = None,
    api_call_count: int = 0,
    prev_result: str | None = None,
    curr_result: str | None = None,
) -> dict[str, Any]:
    if not claim_id:
        raise ValueError("CLAIM_ID_REQUIRED")
    if not stage_statuses:
        raise ValueError("STAGE_STATUSES_REQUIRED")
    unknown = set(stage_statuses) - set(PIPELINE_STAGES)
    if unknown:
        raise ValueError(f"UNKNOWN_STAGE:{sorted(unknown)}")

    succeeded = final_verdict in _SUCCESS_VERDICTS
    failed_stages = [s for s, st in stage_statuses.items() if st == "FAIL"]
    if succeeded and not applied_pattern:
        raise ValueError("APPLIED_PATTERN_REQUIRED: 성공은 어떤 규칙으로 성공했는지 반드시 기록")
    if succeeded and pattern_generality not in ("GENERAL_RULE", "CLAIM_SPECIFIC"):
        raise ValueError("PATTERN_GENERALITY_REQUIRED: 일반 규칙인지 Claim 전용인지 명시")
    if failed_stages and not failed_stage:
        raise ValueError("FAILED_STAGE_REQUIRED: 실패는 정확한 단계와 원인을 반드시 기록")
    if failed_stage and not failure_cause:
        raise ValueError("FAILURE_CAUSE_REQUIRED")

    record: dict[str, Any] = {
        "run_at": run_at, "claim_id": claim_id, "sentence": sentence,
        "code_version": code_version, "data_version": data_version,
        "subtype": subtype, "applied_pattern": applied_pattern or "",
        "pattern_generality": pattern_generality or "",
        "failed_stage": failed_stage or "", "failure_cause": failure_cause or "",
        "failure_bundle": failure_bundle_for(failed_stage, failure_cause) or "",
        "tbl_id": tbl_id or "", "item_id": item_id or "", "period": period or "",
        "dimension_coords": dimension_coords or "",
        "official_value": official_value or "", "article_value": article_value or "",
        "official_source_url": official_source_url or "",
        "publication_basis": publication_basis or "",
        "response_hash": response_hash or "",
        "calculation_result": calculation_result or "",
        "final_verdict": final_verdict or "",
        "duration_ms": duration_ms if duration_ms is not None else "",
        "api_call_count": api_call_count,
        "prev_result": prev_result or "", "curr_result": curr_result or "",
    }
    for stage in PIPELINE_STAGES:
        record[f"stage_{stage}"] = stage_statuses.get(stage, "NOT_RUN")
    return record


def append_history(path: str | Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Append records to the history CSV (append-only; run_seq stays monotonic)."""
    path = Path(path)
    existing = load_history(path) if path.exists() else []
    next_seq = (int(existing[-1]["run_seq"]) + 1) if existing else 1
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(EXECUTION_HISTORY_COLUMNS))
        if write_header:
            writer.writeheader()
        count = 0
        for record in records:
            row = {col: record.get(col, "") for col in EXECUTION_HISTORY_COLUMNS}
            row["run_seq"] = next_seq
            writer.writerow(row)
            next_seq += 1
            count += 1
    return count


def load_history(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def aggregate_batch(
    records: Sequence[Mapping[str, Any]],
    *,
    pattern_applicability: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    pattern_applicability = dict(pattern_applicability or {})
    total = len(records)

    def _passed(record: Mapping[str, Any], stage: str) -> bool:
        return record.get(f"stage_{stage}") == "PASS"

    funnel = {
        "전체_실행": total,
        "공식_API_도달": sum(1 for r in records if int(r.get("api_call_count") or 0) > 0
                          or _passed(r, "OFFICIAL_VALUE_FETCH")),
        "공식_좌표_확정": sum(1 for r in records if _passed(r, "EVIDENCE_CELL")),
        "공식값_조회_성공": sum(1 for r in records if _passed(r, "OFFICIAL_VALUE_FETCH")),
        "최종판정_성공": sum(1 for r in records if r.get("final_verdict") in _SUCCESS_VERDICTS),
        "보류": sum(1 for r in records if r.get("final_verdict") not in _SUCCESS_VERDICTS),
    }
    stage_failures = Counter(r["failed_stage"] for r in records if r.get("failed_stage"))
    cause_failures = Counter(r["failure_cause"] for r in records if r.get("failure_cause"))
    bundle_failures = Counter(r["failure_bundle"] for r in records if r.get("failure_bundle"))

    patterns: dict[str, dict[str, Any]] = {}
    for r in records:
        name = r.get("applied_pattern") or ""
        if not name:
            continue
        entry = patterns.setdefault(name, {
            "적용": 0, "성공": 0, "실패": 0,
            "일반성": r.get("pattern_generality") or "",
            "같은_규칙_적용가능_건수": pattern_applicability.get(name, ""),
            "성공_지표": set(), "성공_단위": set(), "성공_기간": set(),
        })
        entry["적용"] += 1
        if r.get("final_verdict") in _SUCCESS_VERDICTS:
            entry["성공"] += 1
            if r.get("subtype"):
                entry["성공_지표"].add(str(r.get("subtype")))
            if r.get("official_value"):
                entry["성공_단위"].add(str(r.get("article_value") or ""))
            if r.get("period"):
                entry["성공_기간"].add(str(r.get("period")))
        else:
            entry["실패"] += 1
    for entry in patterns.values():
        applicable = entry["같은_규칙_적용가능_건수"]
        if isinstance(applicable, int) and applicable > 0 and entry["적용"]:
            entry["유형_전체_예상_적용률"] = round(
                entry["성공"] / entry["적용"] * applicable / applicable, 4
            )
            entry["유형_전체_예상_성공건수"] = round(entry["성공"] / entry["적용"] * applicable, 1)
        periods = sorted(entry.pop("성공_기간"))
        entry["성공_기간_범위"] = (periods[0] if len(periods) == 1
                               else f"{periods[0]}~{periods[-1]}" if periods else "")
        entry["성공_지표"] = sorted(entry["성공_지표"])
        entry["성공_단위"] = sorted(entry["성공_단위"])

    improvement = compare_before_after(records)
    return {
        "funnel": funnel,
        "실패_단계별": dict(stage_failures.most_common()),
        "실패_원인별": dict(cause_failures.most_common()),
        "실패_묶음별": dict(bundle_failures.most_common()),
        "패턴별": patterns,
        "개선_전후": {k: v for k, v in improvement.items() if not k.endswith("_목록")},
        "일반_규칙_적용가능_건수": sum(
            v for v in pattern_applicability.values() if isinstance(v, int)
        ),
    }


def compare_before_after(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved, kept, regressed, unknown = [], [], [], []
    ok = _SUCCESS_VERDICTS
    for r in records:
        prev, curr = r.get("prev_result") or "", r.get("curr_result") or ""
        item = (r.get("claim_id", ""), prev, curr)
        if not prev or not curr:
            unknown.append(item)
        elif prev not in ok and curr in ok:
            improved.append(item)
        elif prev in ok and curr not in ok:
            regressed.append(item)
        else:
            kept.append(item)
    return {
        "개선": len(improved), "유지": len(kept), "퇴행": len(regressed),
        "비교불가": len(unknown),
        "개선_목록": improved, "퇴행_목록": regressed,
    }


def generate_outputs(
    output_dir: str | Path,
    *,
    history_path: str | Path,
    pattern_applicability: Mapping[str, int] | None = None,
    batch_label: str = "",
) -> list[Path]:
    """Generate the four derived artifacts from the raw history CSV."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_history(history_path)
    summary = aggregate_batch(records, pattern_applicability=pattern_applicability)
    written: list[Path] = []

    # 1) human-facing Excel ledger
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "실행원장"
    hdr_font = Font(name="Malgun Gothic", bold=True, color="FFFFFF", size=10)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    body = Font(name="Malgun Gothic", size=10)
    for j, col in enumerate(EXECUTION_HISTORY_COLUMNS, start=1):
        cell = ws.cell(row=1, column=j, value=col)
        cell.font, cell.fill = hdr_font, hdr_fill
    for i, r in enumerate(records, start=2):
        for j, col in enumerate(EXECUTION_HISTORY_COLUMNS, start=1):
            ws.cell(row=i, column=j, value=r.get(col, "")).font = body
    ws.freeze_panes = "D2"
    summary_ws = wb.create_sheet("자동집계")
    row_i = 1
    for section, payload in (
        ("퍼널", summary["funnel"]),
        ("실패_단계별", summary["실패_단계별"]),
        ("실패_원인별", summary["실패_원인별"]),
        ("실패_묶음별", summary["실패_묶음별"]),
        ("개선_전후", summary["개선_전후"]),
    ):
        cell = summary_ws.cell(row=row_i, column=1, value=section)
        cell.font = hdr_font
        cell.fill = hdr_fill
        row_i += 1
        for key, value in payload.items():
            summary_ws.cell(row=row_i, column=1, value=str(key)).font = body
            summary_ws.cell(row=row_i, column=2, value=value).font = body
            row_i += 1
        row_i += 1
    ledger_path = output_dir / "실행원장.xlsx"
    wb.save(ledger_path)
    written.append(ledger_path)

    # 2) per-pattern performance summary CSV
    perf_path = output_dir / "분류별_성능요약.csv"
    with perf_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["패턴", "일반성", "적용", "성공", "실패",
                         "같은_규칙_적용가능_건수", "유형_전체_예상_성공건수",
                         "성공_지표", "성공_단위", "성공_기간_범위"])
        for name, entry in summary["패턴별"].items():
            writer.writerow([
                name, entry.get("일반성", ""), entry["적용"], entry["성공"], entry["실패"],
                entry.get("같은_규칙_적용가능_건수", ""),
                entry.get("유형_전체_예상_성공건수", ""),
                ";".join(entry.get("성공_지표", [])),
                ";".join(entry.get("성공_단위", [])),
                entry.get("성공_기간_범위", ""),
            ])
    written.append(perf_path)

    # 3) before/after comparison CSV
    diff = compare_before_after(records)
    diff_path = output_dir / "개선전후_비교.csv"
    with diff_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["구분", "claim_id", "개선_전", "개선_후"])
        for label, items in (("개선", diff["개선_목록"]), ("퇴행", diff["퇴행_목록"])):
            for claim_id, prev, curr in items:
                writer.writerow([label, claim_id, prev, curr])
        writer.writerow([])
        writer.writerow(["요약", "", "", ""])
        for key in ("개선", "유지", "퇴행", "비교불가"):
            writer.writerow([key, diff[key], "", ""])
    written.append(diff_path)

    # 4) final TXT report
    lines = [
        f"CLAFACT 실행 배치 보고서 — {batch_label}",
        "=" * 60,
        "",
        "[묶음 단위 자동 집계]",
    ]
    for key, value in summary["funnel"].items():
        lines.append(f"  {key}: {value}")
    lines += ["", "[실패 단계별]"]
    lines += [f"  {k}: {v}" for k, v in summary["실패_단계별"].items()] or ["  (없음)"]
    lines += ["", "[실패 원인별]"]
    lines += [f"  {k}: {v}" for k, v in summary["실패_원인별"].items()] or ["  (없음)"]
    lines += ["", "[실패 해결 묶음별]"]
    lines += [f"  {k}: {v}" for k, v in summary["실패_묶음별"].items()] or ["  (없음)"]
    lines += ["", "[패턴별 성공 — 어떤 규칙으로 성공했는가]"]
    if summary["패턴별"]:
        for name, entry in summary["패턴별"].items():
            applicable = entry.get("같은_규칙_적용가능_건수", "")
            expected = entry.get("유형_전체_예상_성공건수", "")
            lines.append(
                f"  {name} ({entry.get('일반성','')}): 적용 {entry['적용']}건 → "
                f"성공 {entry['성공']}·실패 {entry['실패']}"
                + (f" / 같은 규칙 적용 가능 {applicable}건, 예상 성공 {expected}건"
                   if applicable != "" else "")
            )
    else:
        lines.append("  (성공 패턴 없음)")
    lines += ["", "[개선 전후]"]
    lines += [f"  {k}: {v}" for k, v in summary["개선_전후"].items()]
    lines += ["", "경계: 이 보고서는 실행 기록 집계다. 보류는 실패가 아니라 안전 정지이며,",
              "성공률은 이 배치 표본에 대한 값으로 유형 전체 성능을 보증하지 않는다."]
    report_path = output_dir / "최종보고서.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.append(report_path)
    return written
