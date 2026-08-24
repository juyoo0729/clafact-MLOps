"""Deterministic KOSIS candidate retrieval before metadata and Hard Guard.

This module improves candidate *attachment* coverage for unresolved R3 Claims.
It never selects a table, resolves an Evidence Cell, or produces a Verdict.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from difflib import SequenceMatcher


ELIGIBLE_R3_REASONS = frozenset({"CONCEPT_UNREGISTERED", "LOW_SEMANTIC_SCORE"})
ACCURACY_STATUS = "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD"
MANDATORY_NEXT_GATE = "OFFICIAL_METADATA -> HARD_GUARD -> EVIDENCE_CELL"


@dataclass(frozen=True)
class RankedKosisCandidate:
    org_id: str
    tbl_id: str
    tbl_name: str
    official_search_rank: int
    lexical_score: float
    retrieval_score: float
    retrieval_methods: tuple[str, ...] = (
        "KOSIS_OFFICIAL_SEARCH_RANK",
        "DETERMINISTIC_LEXICAL",
    )


@dataclass(frozen=True)
class CandidateShortlist:
    indicator: str
    status: str
    candidates: tuple[RankedKosisCandidate, ...]
    confidence_status: str
    next_retrieval_method: str
    selection_status: str = "HOLD_NO_TABLE_SELECTED"
    mandatory_next_gate: str = MANDATORY_NEXT_GATE


def shortlist_kosis_candidates(
    indicator: str,
    candidates: Iterable[Mapping[str, object]],
    *,
    top_k: int = 5,
    minimum_lexical_ready_score: float = 0.6,
) -> CandidateShortlist:
    """Fuse official search order and lexical similarity into an unresolved top-k.

    KOSIS integrated-search results already carry useful ordering.  A lexical
    score reorders obvious title matches while retaining the official rank as
    a deterministic prior.  The output is deliberately a shortlist only.
    """
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    normalized_indicator = _normalize_semantic_text(indicator)
    if not normalized_indicator:
        return CandidateShortlist(
            indicator=indicator,
            status="HOLD_INDICATOR_REQUIRED",
            candidates=(),
            confidence_status="NOT_APPLICABLE",
            next_retrieval_method="R2_INDICATOR_REVIEW",
        )

    deduplicated: dict[str, tuple[int, Mapping[str, object]]] = {}
    for official_rank, candidate in enumerate(candidates, start=1):
        table_id = _text(candidate.get("tbl_id"))
        table_name = _text(candidate.get("tbl_name"))
        org_id = _text(candidate.get("org_id"))
        if not table_id or not table_name or not org_id:
            continue
        deduplicated.setdefault(table_id, (official_rank, candidate))

    ranked: list[RankedKosisCandidate] = []
    for official_rank, candidate in deduplicated.values():
        table_name = _text(candidate.get("tbl_name"))
        lexical_score = _lexical_similarity(normalized_indicator, table_name)
        official_score = 1.0 / (1.0 + math.log2(official_rank))
        retrieval_score = 0.7 * lexical_score + 0.3 * official_score
        ranked.append(
            RankedKosisCandidate(
                org_id=_text(candidate.get("org_id")),
                tbl_id=_text(candidate.get("tbl_id")),
                tbl_name=table_name,
                official_search_rank=official_rank,
                lexical_score=round(lexical_score, 6),
                retrieval_score=round(retrieval_score, 6),
            )
        )
    ranked.sort(
        key=lambda item: (
            -item.retrieval_score,
            item.official_search_rank,
            item.tbl_id,
        )
    )
    shortlist = tuple(ranked[:top_k])
    confidence_status = (
        "LEXICAL_READY"
        if shortlist and shortlist[0].retrieval_score >= minimum_lexical_ready_score
        else "LOW_LEXICAL_CONFIDENCE" if shortlist else "NOT_APPLICABLE"
    )
    return CandidateShortlist(
        indicator=indicator,
        status=(
            "CANDIDATES_ATTACHED_FOR_METADATA"
            if shortlist
            else "HOLD_NO_OFFICIAL_CANDIDATE"
        ),
        candidates=shortlist,
        confidence_status=confidence_status,
        next_retrieval_method=(
            "OFFICIAL_METADATA_HYDRATION"
            if confidence_status == "LEXICAL_READY"
            else "EMBEDDING_TOP_K_REVIEW"
            if confidence_status == "LOW_LEXICAL_CONFIDENCE"
            else "KOSIS_QUERY_REVIEW"
        ),
    )


def evaluate_candidate_attachment(
    replay_rows: Iterable[Mapping[str, object]],
    candidates_by_indicator: Mapping[str, Iterable[Mapping[str, object]]],
    *,
    baseline_attached_claim_ids: Set[str] = frozenset(),
    split: str | None = "dev",
    top_k: int = 5,
    minimum_lexical_ready_score: float = 0.6,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    """Evaluate candidate attachment coverage without calling it R3 accuracy."""
    eligible: list[Mapping[str, object]] = []
    for row in replay_rows:
        if split is not None and _text(row.get("split")) != split:
            continue
        if _text(row.get("r3_reason_code")) not in ELIGIBLE_R3_REASONS:
            continue
        eligible.append(row)

    output: list[dict[str, str]] = []
    for row in eligible:
        indicator = _text(row.get("indicator"))
        shortlist = shortlist_kosis_candidates(
            indicator,
            candidates_by_indicator.get(indicator, ()),
            top_k=top_k,
            minimum_lexical_ready_score=minimum_lexical_ready_score,
        )
        output.append(
            {
                "claim_id": _text(row.get("claim_id")),
                "split": _text(row.get("split")),
                "r3_reason_code": _text(row.get("r3_reason_code")),
                "indicator": indicator,
                "unit": _text(row.get("unit")),
                "frequency": _text(row.get("frequency")),
                "calculation": _text(row.get("calculation")),
                "route_status": shortlist.status,
                "candidate_count": str(len(shortlist.candidates)),
                "candidate_tbl_ids": " | ".join(
                    candidate.tbl_id for candidate in shortlist.candidates
                ),
                "candidate_tbl_names": " | ".join(
                    candidate.tbl_name for candidate in shortlist.candidates
                ),
                "candidate_scores": " | ".join(
                    f"{candidate.retrieval_score:.6f}"
                    for candidate in shortlist.candidates
                ),
                "confidence_status": shortlist.confidence_status,
                "next_retrieval_method": shortlist.next_retrieval_method,
                "selection_status": shortlist.selection_status,
                "mandatory_next_gate": shortlist.mandatory_next_gate,
            }
        )

    attached_status = "CANDIDATES_ATTACHED_FOR_METADATA"
    attached_count = sum(row["route_status"] == attached_status for row in output)
    lexical_ready_count = sum(
        row["confidence_status"] == "LEXICAL_READY" for row in output
    )
    embedding_fallback_count = sum(
        row["confidence_status"] == "LOW_LEXICAL_CONFIDENCE" for row in output
    )
    eligible_ids = {row["claim_id"] for row in output}
    baseline_count = len(eligible_ids.intersection(baseline_attached_claim_ids))
    denominator = len(output)
    status_counts = Counter(row["route_status"] for row in output)
    return output, {
        "artifact": "r3_kosis_candidate_attachment_v1",
        "method": "official_search_rank_plus_deterministic_lexical_v1",
        "split": split or "all",
        "eligible_r3_reasons": sorted(ELIGIBLE_R3_REASONS),
        "eligible_claim_count": denominator,
        "baseline_attachment_count": baseline_count,
        "candidate_attachment_count": attached_count,
        "lexical_ready_count": lexical_ready_count,
        "embedding_fallback_count": embedding_fallback_count,
        "attachment_coverage_before": _ratio(baseline_count, denominator),
        "attachment_coverage_after": _ratio(attached_count, denominator),
        "lexical_ready_coverage": _ratio(lexical_ready_count, denominator),
        "attachment_coverage_delta": round(
            _ratio(attached_count, denominator) - _ratio(baseline_count, denominator),
            6,
        ),
        "route_status_counts": dict(sorted(status_counts.items())),
        "selection_status": "HOLD_NO_TABLE_SELECTED",
        "mandatory_next_gate": MANDATORY_NEXT_GATE,
        "accuracy_status": ACCURACY_STATUS,
        "accuracy_boundary": (
            "Candidate attachment coverage is operational retrieval coverage, not "
            "KOSIS table, Evidence Cell, value, calculation, or Verdict accuracy."
        ),
    }


def _lexical_similarity(normalized_indicator: str, table_name: str) -> float:
    normalized_name = _normalize_semantic_text(table_name)
    if not normalized_name:
        return 0.0
    if normalized_indicator == normalized_name:
        return 1.0
    containment = 1.0 if (
        normalized_indicator in normalized_name or normalized_name in normalized_indicator
    ) else 0.0
    sequence = SequenceMatcher(None, normalized_indicator, normalized_name).ratio()
    ngram = _dice(_character_ngrams(normalized_indicator), _character_ngrams(normalized_name))
    return max(containment, sequence, ngram)


def _normalize_semantic_text(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z가-힣]+", "", value).casefold()
    for suffix in ("증가율", "감소율", "상승률", "하락률", "등락률", "증감률"):
        normalized = normalized.replace(suffix, "변동률")
    return normalized


def _character_ngrams(value: str, size: int = 2) -> set[str]:
    if len(value) <= size:
        return {value} if value else set()
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _dice(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return 2.0 * len(left.intersection(right)) / (len(left) + len(right))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
