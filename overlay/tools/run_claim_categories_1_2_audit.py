"""Execute Claim categories 1 and 2 with a six-W audit trail."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.claim_split_methods import run_split_method


CATEGORY1_TAB = "01_문맥보완"
CATEGORY2_TAB = "02_복수Claim분리"

CONTEXT_COLUMNS = (
    "기사번호", "Claim번호", "작성일", "제목", "URL", "원문", "앞문맥", "뒤문맥",
    "하위유형", "기사원문정확일치", "본문위치", "원래Claim기간", "원래Claim주기",
    "감지기간표현", "기간근거범위", "보완기준기간", "보완비교기간", "보완주기",
    "공식작성기관힌트", "문맥연결결과", "최종실행상태", "성공실패사유", "중단단계",
    "기존KOSIS상태", "기존KOSIS사유", "KOSIS재조회상태", "다음실행단계",
    "누가", "언제", "어디서", "무엇을", "어떻게", "왜",
)

PARENT_COLUMNS = (
    "기사번호", "부모Claim번호", "작성일", "제목", "URL", "원문", "앞문맥", "뒤문맥",
    "하위유형", "분리방법", "분리경로", "분리reason_code", "부모수치수", "생성자식수",
    "수치전수보존", "모든자식단일목표값", "최종실행상태", "성공실패사유", "중단단계",
    "기존KOSIS상태", "기존KOSIS사유", "KOSIS재조회상태", "다음실행단계",
    "누가", "언제", "어디서", "무엇을", "어떻게", "왜",
)

CHILD_COLUMNS = (
    "기사번호", "부모Claim번호", "부모최종실행상태", "자식Claim번호", "자식순번", "부모원문", "자식Claim",
    "목표수치", "target_value_role", "자식수치수", "수치원문존재", "자식검증상태",
    "성공실패사유", "12슬롯상태", "KOSIS재조회상태", "누가", "언제", "어디서",
    "무엇을", "어떻게", "왜",
)

EVENT_COLUMNS = (
    "이벤트종류", "기사번호", "Claim번호", "부모Claim번호", "실행시각UTC", "실행상태",
    "성공실패사유", "중단단계", "누가", "언제", "어디서", "무엇을", "어떻게", "왜",
    "입력요약", "출력요약", "KOSIS재조회상태",
)

OUTPUT_NAMES = (
    "CLAFACT_1번2번_595건_육하원칙_실행원장.xlsx",
    "category1_context_results.csv",
    "category2_parent_results.csv",
    "category2_child_results.csv",
    "execution_events.jsonl",
    "CLAFACT_1번2번_육하원칙_실행기록.txt",
    "summary.json",
    "CLAFACT_1번2번_595건_육하원칙_통합기록.json",
)

_QUANTITY_RE = re.compile(
    r"\d+(?:[.,]\d+)*(?:\s*(?:조|억|만|천)\s*\d*(?:[.,]\d+)*)*\s*"
    r"(?:%포인트|퍼센트포인트|%p|%|명|가구|원|건|개|대|배|달러|위|호|채|동|곳|척|톤|ha|㏊|헥타르)"
)
_AGENCIES = (
    "통계청", "한국은행", "관세청", "산업통상자원부", "기획재정부", "고용노동부",
    "국토교통부", "보건복지부", "금융감독원", "한국부동산원", "OECD", "KOSIS",
)


def run(
    *,
    claim_audit_csv: Path,
    instruction_txt: Path,
    output_dir: Path,
    split_runner: Callable[..., Mapping[str, Any]] = run_split_method,
    expected_category1_count: int | None = 256,
    expected_category2_count: int | None = 339,
    split_method: str = "rule_ensemble",
) -> dict[str, Any]:
    """Execute both categories without forcing unsafe KOSIS calls."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _read_csv(claim_audit_csv)
    category1 = [row for row in rows if _text(row.get("분류탭")) == CATEGORY1_TAB]
    category2 = [row for row in rows if _text(row.get("분류탭")) == CATEGORY2_TAB]
    _require_unique(category1 + category2, "Claim번호")
    if expected_category1_count is not None and len(category1) != expected_category1_count:
        raise ValueError(
            f"unexpected category 1 count: {len(category1)} != {expected_category1_count}"
        )
    if expected_category2_count is not None and len(category2) != expected_category2_count:
        raise ValueError(
            f"unexpected category 2 count: {len(category2)} != {expected_category2_count}"
        )

    execution_time = _now()
    context_results = [
        execute_context_claim(row, execution_time=execution_time) for row in category1
    ]
    parent_results: list[dict[str, Any]] = []
    child_results: list[dict[str, Any]] = []
    for row in category2:
        parent, children = execute_split_claim(
            row,
            execution_time=execution_time,
            split_method=split_method,
            split_runner=split_runner,
        )
        parent_results.append(parent)
        child_results.extend(children)

    events = _build_events(context_results, parent_results, child_results, execution_time)
    declared = _declared_counts(instruction_txt.read_text(encoding="utf-8"))
    summary = _build_summary(
        context_results=context_results,
        parent_results=parent_results,
        child_results=child_results,
        events=events,
        declared=declared,
        split_method=split_method,
    )

    _write_csv(output_dir / OUTPUT_NAMES[1], context_results, CONTEXT_COLUMNS)
    _write_csv(output_dir / OUTPUT_NAMES[2], parent_results, PARENT_COLUMNS)
    _write_csv(output_dir / OUTPUT_NAMES[3], child_results, CHILD_COLUMNS)
    _write_jsonl(output_dir / OUTPUT_NAMES[4], events)
    (output_dir / OUTPUT_NAMES[5]).write_text(
        _text_report(summary, events), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[6]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / OUTPUT_NAMES[7]).write_text(
        json.dumps(
            {
                "schema_version": "clafact_categories_1_2_consolidated_audit_v1",
                "execution_time_utc": execution_time,
                "scope": {
                    "category1": "문맥 보완 필요",
                    "category2": "복수 Claim 분리 필요",
                    "actual_parent_claim_count": len(context_results) + len(parent_results),
                },
                "summary": summary,
                "category1_context_results": context_results,
                "category2": {
                    "parent_results": parent_results,
                    "child_results": child_results,
                },
                "six_w_execution_events": events,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_workbook(
        output_dir / OUTPUT_NAMES[0],
        context_results=context_results,
        parent_results=parent_results,
        child_results=child_results,
        events=events,
        summary=summary,
        inputs=(claim_audit_csv, instruction_txt),
    )
    _write_manifest(output_dir, inputs=(claim_audit_csv, instruction_txt))
    return summary


def execute_context_claim(
    row: Mapping[str, Any], *, execution_time: str
) -> dict[str, Any]:
    article_date = _parse_date(row.get("작성일"))
    sentence = _text(row.get("원문"))
    before = _text(row.get("앞문맥"))
    after = _text(row.get("뒤문맥"))
    subtype = _text(row.get("하위유형"))
    exact = _text(row.get("기사원문정확일치")) == "YES"
    resolution = resolve_period(sentence, before, after, article_date)
    source_hints = [agency for agency in _AGENCIES if agency in f"{before} {sentence} {after}"]

    if not exact or article_date is None:
        status = "HOLD"
        reason = "ARTICLE_CONTEXT_OR_DATE_MISSING"
        stage = "ARTICLE_CONTEXT"
    elif subtype == "전면재해석형":
        status = "HOLD"
        reason = "FULL_STRUCTURED_REPARSE_REQUIRED"
        stage = "CLAIM_REPARSE"
    elif subtype == "슬롯보완형":
        status = "PARTIAL_SUCCESS"
        reason = "CONTEXT_ATTACHED_STRUCTURED_SLOT_REPARSE_REQUIRED"
        stage = "CLAIM_REPARSE"
    elif subtype == "비교기준복원형":
        if resolution["target_period"] and resolution["comparison_period"]:
            status, reason, stage = "SUCCESS", "TARGET_AND_COMPARISON_PERIOD_RESOLVED", "CONTEXT_COMPLETE"
        elif resolution["target_period"]:
            status, reason, stage = "PARTIAL_SUCCESS", "TARGET_PERIOD_ONLY_COMPARISON_UNRESOLVED", "COMPARISON_PERIOD"
        else:
            status, reason, stage = "HOLD", "COMPARISON_CONTEXT_UNRESOLVED", "COMPARISON_PERIOD"
    elif resolution["reason_code"] == "CONTEXT_ABSOLUTE_PERIOD_CANDIDATE_REQUIRES_SEMANTIC_LINK":
        status = "PARTIAL_SUCCESS"
        reason = resolution["reason_code"]
        stage = "PERIOD_SEMANTIC_LINK"
    elif resolution["target_period"]:
        status, reason, stage = "SUCCESS", resolution["reason_code"], "CONTEXT_COMPLETE"
    else:
        status, reason, stage = "HOLD", resolution["reason_code"], "TARGET_PERIOD"

    article_id = _text(row.get("기사번호"))
    claim_id = _text(row.get("Claim번호"))
    where = f"{_text(row.get('URL'))} / 본문 위치 {_text(row.get('기사내원문시작위치'))}"
    how = "ARTICLE_BODY_EXACT_JOIN + STRICT_CONTEXT_PERIOD_RESOLVER_V1"
    what = (
        f"문맥 보완({subtype}); 기준기간={resolution['target_period'] or '미확정'}; "
        f"비교기간={resolution['comparison_period'] or '미확정'}"
    )
    next_step = (
        "보완 Claim 12슬롯 재구조화 후 KOSIS 재조회"
        if status in {"SUCCESS", "PARTIAL_SUCCESS"}
        else "기사 문맥 구조화 출력 또는 사람 검토"
    )
    return {
        "기사번호": article_id,
        "Claim번호": claim_id,
        "작성일": _text(row.get("작성일")),
        "제목": _text(row.get("제목")),
        "URL": _text(row.get("URL")),
        "원문": sentence,
        "앞문맥": before,
        "뒤문맥": after,
        "하위유형": subtype,
        "기사원문정확일치": "YES" if exact else "NO",
        "본문위치": _text(row.get("기사내원문시작위치")),
        "원래Claim기간": _text(row.get("Claim기간")),
        "원래Claim주기": _text(row.get("Claim주기")),
        "감지기간표현": resolution["expression"],
        "기간근거범위": resolution["evidence_scope"],
        "보완기준기간": resolution["target_period"],
        "보완비교기간": resolution["comparison_period"],
        "보완주기": resolution["frequency"],
        "공식작성기관힌트": " | ".join(source_hints),
        "문맥연결결과": "SUCCESS" if exact and article_date else "HOLD",
        "최종실행상태": status,
        "성공실패사유": reason,
        "중단단계": stage,
        "기존KOSIS상태": _text(row.get("공식값상태")),
        "기존KOSIS사유": _text(row.get("최종성공실패사유")),
        "KOSIS재조회상태": "NOT_RUN_PRECONDITION_REPARSED_12SLOTS_REQUIRED",
        "다음실행단계": next_step,
        "누가": f"기사 {article_id} / Claim {claim_id}",
        "언제": f"기사 작성일 {_text(row.get('작성일'))} / 실행 {execution_time}",
        "어디서": where,
        "무엇을": what,
        "어떻게": how,
        "왜": reason,
    }


def execute_split_claim(
    row: Mapping[str, Any],
    *,
    execution_time: str,
    split_method: str,
    split_runner: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sentence = _text(row.get("원문"))
    result = dict(split_runner(split_method, sentence))
    raw_children = list(result.get("children") or [])
    parent_quantities = _quantities(sentence)
    children: list[dict[str, Any]] = []
    child_targets: list[str] = []
    for index, raw_child in enumerate(raw_children, start=1):
        text = _text(raw_child.get("text") if isinstance(raw_child, Mapping) else raw_child)
        quantities = _quantities(text)
        target = _text(raw_child.get("target_value_text")) if isinstance(raw_child, Mapping) else ""
        if not target and len(quantities) == 1:
            target = quantities[0]
        role = _text(raw_child.get("target_value_role")) if isinstance(raw_child, Mapping) else ""
        if not role and target:
            role = _infer_role(text)
        grounded = bool(target) and _normalize(target) in _normalize(sentence)
        child_status = "VALID" if len(quantities) == 1 and grounded else "HOLD"
        child_reason = (
            "SINGLE_TARGET_VALUE_SOURCE_GROUNDED"
            if child_status == "VALID"
            else "CHILD_TARGET_COUNT_OR_GROUNDING_FAILED"
        )
        if target:
            child_targets.append(_normalize(target))
        child_id = f"{_text(row.get('Claim번호'))}__S{index:02d}"
        children.append(
            {
                "기사번호": _text(row.get("기사번호")),
                "부모Claim번호": _text(row.get("Claim번호")),
                "자식Claim번호": child_id,
                "자식순번": index,
                "부모원문": sentence,
                "자식Claim": text,
                "목표수치": target,
                "target_value_role": role,
                "자식수치수": len(quantities),
                "수치원문존재": "YES" if grounded else "NO",
                "자식검증상태": child_status,
                "성공실패사유": child_reason,
                "12슬롯상태": "NOT_RUN_CHILD_12SLOTS_REQUIRED",
                "KOSIS재조회상태": "NOT_RUN_PRECONDITION_CHILD_12SLOTS_REQUIRED",
                "누가": f"기사 {_text(row.get('기사번호'))} / 자식 Claim {child_id}",
                "언제": f"기사 작성일 {_text(row.get('작성일'))} / 실행 {execution_time}",
                "어디서": f"{_text(row.get('URL'))} / 부모 Claim {_text(row.get('Claim번호'))}",
                "무엇을": f"부모 문장의 {target or '미확정 수치'}를 독립 자식 Claim으로 검증",
                "어떻게": f"{split_method} + 숫자 원문 존재 + 자식 단일 목표값 검사",
                "왜": child_reason,
            }
        )

    coverage = Counter(map(_normalize, parent_quantities)) == Counter(child_targets)
    all_single = bool(children) and all(child["자식검증상태"] == "VALID" for child in children)
    route = _text(result.get("route_status"))
    if route == "AUTO" and len(children) > 1 and coverage and all_single:
        status, reason, stage = "SUCCESS", "ATOMIC_SPLIT_VALIDATED", "SPLIT_COMPLETE"
    elif route == "AUTO" and len(children) <= 1:
        status, reason, stage = "HOLD", "CLASSIFIED_MULTI_BUT_RULE_FOUND_SINGLE", "CLAIM_SPLIT"
    elif route == "HUMAN_REVIEW":
        status, reason, stage = "HOLD", _text(result.get("reason_code")) or "HUMAN_REVIEW", "CLAIM_SPLIT"
    elif not coverage:
        status, reason, stage = "HOLD", "PARENT_CHILD_NUMBER_COVERAGE_MISMATCH", "SPLIT_VALIDATION"
    else:
        status, reason, stage = "HOLD", "CHILD_SINGLE_TARGET_VALIDATION_FAILED", "SPLIT_VALIDATION"

    for child in children:
        child["부모최종실행상태"] = status

    parent_id = _text(row.get("Claim번호"))
    how = f"{split_method} + 부모/자식 수치 전수보존 + 자식 단일 목표값 검사"
    parent = {
        "기사번호": _text(row.get("기사번호")),
        "부모Claim번호": parent_id,
        "작성일": _text(row.get("작성일")),
        "제목": _text(row.get("제목")),
        "URL": _text(row.get("URL")),
        "원문": sentence,
        "앞문맥": _text(row.get("앞문맥")),
        "뒤문맥": _text(row.get("뒤문맥")),
        "하위유형": _text(row.get("하위유형")),
        "분리방법": split_method,
        "분리경로": route,
        "분리reason_code": _text(result.get("reason_code")),
        "부모수치수": len(parent_quantities),
        "생성자식수": len(children),
        "수치전수보존": "YES" if coverage else "NO",
        "모든자식단일목표값": "YES" if all_single else "NO",
        "최종실행상태": status,
        "성공실패사유": reason,
        "중단단계": stage,
        "기존KOSIS상태": _text(row.get("공식값상태")),
        "기존KOSIS사유": _text(row.get("최종성공실패사유")),
        "KOSIS재조회상태": "NOT_RUN_PRECONDITION_CHILD_12SLOTS_REQUIRED",
        "다음실행단계": (
            "자식 Claim별 12슬롯 구조화 후 KOSIS 재조회"
            if status == "SUCCESS"
            else "LLM 구조화 분리 후보 생성 후 동일 검증 또는 사람 검토"
        ),
        "누가": f"기사 {_text(row.get('기사번호'))} / 부모 Claim {parent_id}",
        "언제": f"기사 작성일 {_text(row.get('작성일'))} / 실행 {execution_time}",
        "어디서": f"{_text(row.get('URL'))} / 본문 위치 {_text(row.get('기사내원문시작위치'))}",
        "무엇을": f"복수 수치 {len(parent_quantities)}개를 자식 Claim {len(children)}개로 분리",
        "어떻게": how,
        "왜": reason,
    }
    return parent, children


def resolve_period(
    sentence: str, before: str, after: str, article_date: date | None
) -> dict[str, str]:
    """Resolve only explicit or unique context-grounded periods."""
    empty = {
        "expression": "", "evidence_scope": "NONE", "target_period": "",
        "comparison_period": "", "frequency": "", "reason_code": "PERIOD_CONTEXT_UNRESOLVED",
    }
    if article_date is None:
        return {**empty, "reason_code": "ARTICLE_DATE_MISSING"}
    claim_resolution = _resolve_period_in_text(sentence, article_date)
    if claim_resolution:
        comparison = _comparison_period(
            f"{sentence} {before} {after}", claim_resolution["target_period"]
        )
        return {
            **claim_resolution,
            "evidence_scope": "CLAIM",
            "comparison_period": comparison,
            "reason_code": "RELATIVE_PERIOD_RESOLVED_FROM_CLAIM",
        }

    context_candidates = []
    for scope, text in (("CONTEXT_BEFORE", before), ("CONTEXT_AFTER", after)):
        resolved = _resolve_period_in_text(text, article_date)
        if resolved:
            context_candidates.append((scope, resolved))
    target_periods = {
        candidate["target_period"] for _, candidate in context_candidates
    }
    if len(target_periods) == 1:
        scope = "+".join(candidate_scope for candidate_scope, _ in context_candidates)
        resolved = context_candidates[0][1]
        comparison = _comparison_period(
            f"{sentence} {before} {after}", resolved["target_period"]
        )
        is_two_sided = len(context_candidates) == 2
        has_relative_expression = any(
            _is_relative_period_expression(candidate["expression"])
            for _, candidate in context_candidates
        )
        if is_two_sided:
            reason_code = "CONSISTENT_PERIOD_RESOLVED_FROM_BOTH_CONTEXT_SIDES"
        elif has_relative_expression:
            reason_code = "RELATIVE_PERIOD_RESOLVED_FROM_ARTICLE_CONTEXT"
        else:
            reason_code = "CONTEXT_ABSOLUTE_PERIOD_CANDIDATE_REQUIRES_SEMANTIC_LINK"
        return {
            **resolved,
            "evidence_scope": scope,
            "comparison_period": comparison,
            "reason_code": reason_code,
        }
    if len(target_periods) > 1:
        return {**empty, "reason_code": "MULTIPLE_CONTEXT_PERIODS_UNRESOLVED"}
    return empty


def _is_relative_period_expression(expression: str) -> bool:
    return bool(re.fullmatch(r"지난달|이달|이번\s*달|올해 들어|올해|작년|지난해", expression))


def _resolve_period_in_text(text: str, article_date: date) -> dict[str, str] | None:
    partial = re.search(
        r"(?:(?P<prefix>지난달|이달|이번\s*달)\s*)?"
        r"(?P<start>\d{1,2})\s*[~∼～-]\s*(?P<end>\d{1,2})일",
        text,
    )
    if partial:
        base = _previous_month(article_date) if partial.group("prefix") == "지난달" else article_date
        start, end = int(partial.group("start")), int(partial.group("end"))
        try:
            start_date = date(base.year, base.month, start)
            end_date = date(base.year, base.month, end)
        except ValueError:
            return None
        if end_date < start_date:
            return None
        return {
            "expression": partial.group(0),
            "target_period": f"{start_date.isoformat()}..{end_date.isoformat()}",
            "frequency": "부분기간",
        }
    if "지난달" in text:
        previous = _previous_month(article_date)
        return {
            "expression": "지난달", "target_period": f"{previous.year}-{previous.month:02d}",
            "frequency": "월",
        }
    this_month = re.search(r"이달|이번\s*달", text)
    if this_month:
        return {
            "expression": this_month.group(0),
            "target_period": f"{article_date.year}-{article_date.month:02d}",
            "frequency": "월",
        }
    if "올해 들어" in text:
        return {
            "expression": "올해 들어",
            "target_period": f"{article_date.year}-01-01..{article_date.isoformat()}",
            "frequency": "누계",
        }
    if "올해" in text:
        return {"expression": "올해", "target_period": str(article_date.year), "frequency": "년"}
    bare_last_year = re.search(r"작년|지난해", text)
    if bare_last_year:
        vicinity = text[bare_last_year.start():bare_last_year.end() + 12]
        if not re.search(r"같은|대비|보다|동기|동월", vicinity):
            return {
                "expression": bare_last_year.group(0),
                "target_period": str(article_date.year - 1),
                "frequency": "년",
            }
    explicit_month = re.search(r"(?P<year>20\d{2})년\s*(?P<month>\d{1,2})월", text)
    if explicit_month:
        following = text[explicit_month.end():explicit_month.end() + 6]
        month = int(explicit_month.group("month"))
        if 1 <= month <= 12 and not re.search(r"대비|보다", following):
            return {
                "expression": explicit_month.group(0),
                "target_period": f"{explicit_month.group('year')}-{month:02d}",
                "frequency": "월",
            }
    explicit_year = re.search(r"(?P<year>20\d{2})년", text)
    if explicit_year:
        following = text[explicit_year.end():explicit_year.end() + 6]
        if not re.search(r"대비|보다", following):
            return {
                "expression": explicit_year.group(0),
                "target_period": explicit_year.group("year"),
                "frequency": "년",
            }
    return None


def _comparison_period(sentence: str, target_period: str) -> str:
    if not target_period:
        return ""
    if re.search(r"전년\s*동월|작년\s*같은\s*달|1년\s*전\s*같은\s*기간|전년\s*같은\s*기간", sentence):
        return _shift_period_year(target_period, -1)
    if re.search(r"전월|전달", sentence):
        matched = re.fullmatch(r"(20\d{2})-(\d{2})", target_period)
        if matched:
            value = date(int(matched.group(1)), int(matched.group(2)), 1) - timedelta(days=1)
            return f"{value.year}-{value.month:02d}"
    if re.fullmatch(r"20\d{2}", target_period):
        explicit_years = set(re.findall(r"(20\d{2})년\s*(?:대비|보다)", sentence))
        if len(explicit_years) == 1:
            return next(iter(explicit_years))
    return ""


def _shift_period_year(period: str, years: int) -> str:
    parts = period.split("..")
    shifted = []
    for part in parts:
        matched = re.fullmatch(r"(20\d{2})(.*)", part)
        shifted.append(f"{int(matched.group(1)) + years}{matched.group(2)}" if matched else part)
    return "..".join(shifted)


def _previous_month(value: date) -> date:
    return date(value.year - 1, 12, 1) if value.month == 1 else date(value.year, value.month - 1, 1)


def _infer_role(child: str) -> str:
    if re.search(r"순위|\d+위", child):
        return "RANK_VALUE"
    if re.search(r"비율|비중|구성비|점유율", child):
        return "SHARE_VALUE"
    if re.search(r"증가|감소|늘|줄|상승|하락|차이|커졌|낮아졌|높아졌", child):
        return "CHANGE_VALUE"
    if re.search(r"과거|이전|직전|전년|지난해|작년", child):
        return "PRIOR_VALUE"
    return "CURRENT_VALUE"


def _quantities(text: str) -> list[str]:
    return [matched.group(0).strip() for matched in _QUANTITY_RE.finditer(text)]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _build_events(
    context_rows: Sequence[Mapping[str, Any]],
    parent_rows: Sequence[Mapping[str, Any]],
    child_rows: Sequence[Mapping[str, Any]],
    execution_time: str,
) -> list[dict[str, Any]]:
    events = []
    for kind, rows, claim_key, parent_key in (
        ("CONTEXT_PARENT", context_rows, "Claim번호", "Claim번호"),
        ("SPLIT_PARENT", parent_rows, "부모Claim번호", "부모Claim번호"),
        ("SPLIT_CHILD", child_rows, "자식Claim번호", "부모Claim번호"),
    ):
        for row in rows:
            status = _text(row.get("최종실행상태") or row.get("자식검증상태"))
            events.append({
                "이벤트종류": kind,
                "기사번호": _text(row.get("기사번호")),
                "Claim번호": _text(row.get(claim_key)),
                "부모Claim번호": _text(row.get(parent_key)),
                "실행시각UTC": execution_time,
                "실행상태": status,
                "성공실패사유": _text(row.get("성공실패사유")),
                "중단단계": _text(row.get("중단단계") or row.get("12슬롯상태")),
                "누가": _text(row.get("누가")),
                "언제": _text(row.get("언제")),
                "어디서": _text(row.get("어디서")),
                "무엇을": _text(row.get("무엇을")),
                "어떻게": _text(row.get("어떻게")),
                "왜": _text(row.get("왜")),
                "입력요약": _text(row.get("원문") or row.get("부모원문")),
                "출력요약": _text(row.get("보완기준기간") or row.get("자식Claim") or row.get("생성자식수")),
                "KOSIS재조회상태": _text(row.get("KOSIS재조회상태")),
            })
    return events


def _build_summary(
    *,
    context_results: Sequence[Mapping[str, Any]],
    parent_results: Sequence[Mapping[str, Any]],
    child_results: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    declared: Mapping[str, int],
    split_method: str,
) -> dict[str, Any]:
    context_status = Counter(_text(row.get("최종실행상태")) for row in context_results)
    split_status = Counter(_text(row.get("최종실행상태")) for row in parent_results)
    child_status = Counter(_text(row.get("자식검증상태")) for row in child_results)
    reasons = Counter(_text(row.get("성공실패사유")) for row in [*context_results, *parent_results])
    return {
        "artifact": "clafact_categories_1_2_sixw_execution_v1",
        "instruction_declared_category1_count": declared.get("category1", 0),
        "instruction_declared_category2_count": declared.get("category2", 0),
        "actual_category1_count": len(context_results),
        "actual_category2_count": len(parent_results),
        "declared_actual_category1_delta": len(context_results) - declared.get("category1", 0),
        "context_status_counts": dict(sorted(context_status.items())),
        "context_exact_join_count": sum(row["문맥연결결과"] == "SUCCESS" for row in context_results),
        "split_method": split_method,
        "split_parent_status_counts": dict(sorted(split_status.items())),
        "split_child_count": len(child_results),
        "split_child_status_counts": dict(sorted(child_status.items())),
        "split_safe_child_count": sum(
            row.get("부모최종실행상태") == "SUCCESS"
            and row.get("자식검증상태") == "VALID"
            for row in child_results
        ),
        "event_count": len(events),
        "kosis_requery_count": 0,
        "kosis_requery_status": "NOT_RUN_PRECONDITION_REPARSED_CHILD_12SLOTS_REQUIRED",
        "parent_reason_counts": dict(sorted(reasons.items())),
        "split_accuracy_status": "NOT_EVALUABLE_NO_PARENT_TO_CHILD_GOLD_FOR_339",
        "secret_handling": "NO_SECRET_READ_OR_RECORDED",
    }


def _declared_counts(text: str) -> dict[str, int]:
    output = {}
    one = re.search(r"1\.\s*문맥\s*보완\s*필요\s*[—-]\s*(\d+)건", text)
    two = re.search(r"2\.\s*복수\s*Claim\s*분리\s*필요\s*[—-]\s*(\d+)건", text, re.IGNORECASE)
    if one:
        output["category1"] = int(one.group(1))
    if two:
        output["category2"] = int(two.group(1))
    return output


def _write_workbook(
    path: Path,
    *,
    context_results: Sequence[Mapping[str, Any]],
    parent_results: Sequence[Mapping[str, Any]],
    child_results: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    inputs: Sequence[Path],
) -> None:
    workbook = Workbook()
    overview = workbook.active
    overview.title = "요약"
    context_sheet = workbook.create_sheet("1_문맥보완")
    parent_sheet = workbook.create_sheet("2_부모Claim")
    child_sheet = workbook.create_sheet("2_자식Claim")
    event_sheet = workbook.create_sheet("육하원칙 이벤트")
    reason_sheet = workbook.create_sheet("성공실패 사유")
    info_sheet = workbook.create_sheet("실행정보")
    _append_table(context_sheet, CONTEXT_COLUMNS, context_results)
    _append_table(parent_sheet, PARENT_COLUMNS, parent_results)
    _append_table(child_sheet, CHILD_COLUMNS, child_results)
    _append_table(event_sheet, EVENT_COLUMNS, events)

    overview.append(["1번·2번 Claim 육하원칙 실행 원장"])
    overview.append(["지표", "값", "해석"])
    c_status = _column_letter(CONTEXT_COLUMNS, "최종실행상태")
    p_status = _column_letter(PARENT_COLUMNS, "최종실행상태")
    child_status = _column_letter(CHILD_COLUMNS, "자식검증상태")
    child_parent_status = _column_letter(CHILD_COLUMNS, "부모최종실행상태")
    formulas = (
        ("텍스트 선언 1번", summary["instruction_declared_category1_count"], "설명 TXT의 기준"),
        ("최신 실제 1번", f"=COUNTA('1_문맥보완'!B2:B{len(context_results)+1})", "최신 분류 원장"),
        ("1번 성공", f'=COUNTIF(\'1_문맥보완\'!{c_status}2:{c_status}{len(context_results)+1},"SUCCESS")', "기간까지 확정"),
        ("1번 부분성공", f'=COUNTIF(\'1_문맥보완\'!{c_status}2:{c_status}{len(context_results)+1},"PARTIAL_SUCCESS")', "문맥 연결 후 재구조화 필요"),
        ("1번 HOLD", f'=COUNTIF(\'1_문맥보완\'!{c_status}2:{c_status}{len(context_results)+1},"HOLD")', "근거 부족"),
        ("텍스트 선언 2번", summary["instruction_declared_category2_count"], "설명 TXT의 기준"),
        ("최신 실제 2번", f"=COUNTA('2_부모Claim'!B2:B{len(parent_results)+1})", "최신 분류 원장"),
        ("2번 안전 분리 성공", f'=COUNTIF(\'2_부모Claim\'!{p_status}2:{p_status}{len(parent_results)+1},"SUCCESS")', "숫자 보존·단일 목표값 통과"),
        ("2번 HOLD", f'=COUNTIF(\'2_부모Claim\'!{p_status}2:{p_status}{len(parent_results)+1},"HOLD")', "분리 또는 검증 중단"),
        ("생성 자식 Claim", f"=COUNTA('2_자식Claim'!C2:C{len(child_results)+1})", "단일/HOLD 후보 포함"),
        ("유효 자식 Claim", f'=COUNTIF(\'2_자식Claim\'!{child_status}2:{child_status}{len(child_results)+1},"VALID")', "원문 수치·단일 목표값 통과"),
        (
            "안전 자식 Claim",
            f'=COUNTIFS(\'2_자식Claim\'!{child_parent_status}2:{child_parent_status}{len(child_results)+1},"SUCCESS",\'2_자식Claim\'!{child_status}2:{child_status}{len(child_results)+1},"VALID")',
            "부모 분리까지 성공한 자식만 집계",
        ),
        (
            "KOSIS 재조회",
            f'=COUNTIF(\'육하원칙 이벤트\'!Q2:Q{len(events)+1},"REQUERY_SUCCESS")',
            "자식 12슬롯 전제조건 미충족으로 미호출",
        ),
    )
    for row in formulas:
        overview.append(row)
    overview.merge_cells("A1:C1")

    parent_reasons = sorted(
        set(row["성공실패사유"] for row in [*context_results, *parent_results])
    )
    reason_sheet.append(["성공실패사유", "1번 건수", "2번 건수", "합계"])
    c_reason = _column_letter(CONTEXT_COLUMNS, "성공실패사유")
    p_reason = _column_letter(PARENT_COLUMNS, "성공실패사유")
    for index, reason in enumerate(parent_reasons, start=2):
        reason_sheet.cell(index, 1, reason)
        reason_sheet.cell(index, 2, f'=COUNTIF(\'1_문맥보완\'!{c_reason}2:{c_reason}{len(context_results)+1},A{index})')
        reason_sheet.cell(index, 3, f'=COUNTIF(\'2_부모Claim\'!{p_reason}2:{p_reason}{len(parent_results)+1},A{index})')
        reason_sheet.cell(index, 4, f"=SUM(B{index}:C{index})")

    info_sheet.append(["항목", "내용"])
    info = (
        ("생성시각(UTC)", _now()),
        ("1번 성공 기준", "기사 원문·작성일 연결 후 엄격 기간 변환 성공"),
        ("2번 성공 기준", "2개 이상 자식 + 부모 수치 전수보존 + 각 자식 단일 목표값"),
        ("KOSIS 미호출 이유", "보완/자식 Claim의 12슬롯이 아직 생성되지 않아 전제조건 미충족"),
        ("정확도 경계", summary["split_accuracy_status"]),
        ("비밀정보", "API 키를 읽거나 기록하지 않음"),
    )
    for row in info:
        info_sheet.append(row)
    for source in inputs:
        info_sheet.append((f"입력:{source.name}", json.dumps(_file_record(source), ensure_ascii=False)))

    for sheet in workbook.worksheets:
        _style_sheet(sheet)
    _style_header_row(overview, 2)
    overview["A1"].font = Font(name="Arial", size=14, bold=True, color="FFFFFF")
    overview["A1"].alignment = Alignment(horizontal="center")
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.save(path)


def _append_table(sheet: Any, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    sheet.append(list(columns))
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions


def _style_sheet(sheet: Any) -> None:
    _style_header_row(sheet, 1)
    sheet.sheet_view.showGridLines = False
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial", size=9)
            cell.alignment = Alignment(vertical="top", wrap_text=bool(
                any(token in _text(sheet.cell(1, cell.column).value) for token in ("원문", "문맥", "육하", "무엇", "어떻게", "왜"))
            ))
    for index, cell in enumerate(sheet[1], start=1):
        name = _text(cell.value)
        width = 15
        if any(token in name for token in ("원문", "문맥", "사유", "누가", "언제", "어디서", "무엇", "어떻게", "왜")):
            width = 34
        if name in {"제목", "URL"}:
            width = 48
        sheet.column_dimensions[get_column_letter(index)].width = width


def _style_header_row(sheet: Any, row_number: int) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[row_number]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _text_report(
    summary: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> str:
    lines = [
        "CLAFACT 1번·2번 육하원칙 실행 기록",
        "=" * 60,
        f"텍스트 선언: 1번 {summary['instruction_declared_category1_count']}건 / 2번 {summary['instruction_declared_category2_count']}건",
        f"최신 실제: 1번 {summary['actual_category1_count']}건 / 2번 {summary['actual_category2_count']}건",
        f"1번 차이: {summary['declared_actual_category1_delta']}건 (텍스트 설명이 최신 원장보다 큼)",
        "",
        f"1번 결과: {json.dumps(summary['context_status_counts'], ensure_ascii=False)}",
        f"2번 부모 결과: {json.dumps(summary['split_parent_status_counts'], ensure_ascii=False)}",
        f"2번 생성 자식: {summary['split_child_count']}건",
        f"2번 자식 검증: {json.dumps(summary['split_child_status_counts'], ensure_ascii=False)}",
        f"2번 안전 자식: {summary['split_safe_child_count']}건 (부모 분리 성공 + 자식 VALID)",
        "",
        "KOSIS 재조회: 0건",
        "이유: 보완 Claim과 자식 Claim의 12슬롯이 아직 없으므로 잘못된 공식 좌표 호출을 차단함.",
        f"분리 정확도: {summary['split_accuracy_status']}",
        "",
        f"육하원칙 상세 이벤트: {len(events)}건",
        "",
        "[육하원칙 상세 실행 기록]",
        "=" * 60,
    ]
    for index, event in enumerate(events, start=1):
        lines.extend([
            "",
            f"[{index:04d}] {event['이벤트종류']} / {event['실행상태']}",
            f"기사·Claim: {event['기사번호']} / {event['Claim번호']}",
            f"누가: {event['누가']}",
            f"언제: {event['언제']}",
            f"어디서: {event['어디서']}",
            f"무엇을: {event['무엇을']}",
            f"어떻게: {event['어떻게']}",
            f"왜: {event['왜']}",
            f"중단단계: {event['중단단계'] or '없음'}",
            f"KOSIS 재조회: {event['KOSIS재조회상태']}",
            f"입력: {event['입력요약']}",
            f"출력: {event['출력요약']}",
        ])
    return "\n".join(lines) + "\n"


def refresh_output_manifest(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["outputs"] = {name: _file_record(output_dir / name) for name in OUTPUT_NAMES}
    manifest["manifest_refreshed_at"] = _now()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _write_manifest(output_dir: Path, *, inputs: Sequence[Path]) -> None:
    payload = {
        "schema_version": "clafact_categories_1_2_sixw_execution_v1",
        "created_at": _now(),
        "inputs": {str(path): _file_record(path) for path in inputs},
        "outputs": {name: _file_record(output_dir / name) for name in OUTPUT_NAMES},
        "secrets": {"api_key": "NOT_READ_NOT_RECORDED"},
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _require_unique(rows: Sequence[Mapping[str, Any]], column: str) -> None:
    values = [_text(row.get(column)) for row in rows]
    if any(not value for value in values) or len(values) != len(set(values)):
        raise ValueError(f"blank or duplicate {column}")


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_text(value)[:10])
    except ValueError:
        return None


def _column_letter(columns: Sequence[str], name: str) -> str:
    return get_column_letter(columns.index(name) + 1)


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path), "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-audit-csv", type=Path)
    parser.add_argument("--instruction-txt", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-category1-count", type=int, default=256)
    parser.add_argument("--expected-category2-count", type=int, default=339)
    parser.add_argument("--split-method", default="rule_ensemble")
    parser.add_argument("--refresh-manifest-only", action="store_true")
    args = parser.parse_args()
    if args.refresh_manifest_only:
        result = refresh_output_manifest(args.output_dir)
    else:
        if args.claim_audit_csv is None or args.instruction_txt is None:
            parser.error("--claim-audit-csv and --instruction-txt are required")
        result = run(
            claim_audit_csv=args.claim_audit_csv,
            instruction_txt=args.instruction_txt,
            output_dir=args.output_dir,
            expected_category1_count=args.expected_category1_count,
            expected_category2_count=args.expected_category2_count,
            split_method=args.split_method,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
