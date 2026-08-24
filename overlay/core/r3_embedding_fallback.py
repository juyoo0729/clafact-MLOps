"""Optional sentence-embedding reranker for low-confidence KOSIS shortlists.

The encoder is injected so the deterministic pipeline does not acquire a hard
runtime dependency or make a provider call.  This stage only reorders already
official KOSIS table identities and never selects a table.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass


Vector = Sequence[float]
EmbeddingEncoder = Callable[[Sequence[str]], Sequence[Vector]]
MANDATORY_NEXT_GATE = "OFFICIAL_METADATA -> HARD_GUARD -> EVIDENCE_CELL"


@dataclass(frozen=True)
class EmbeddingRankedCandidate:
    tbl_id: str
    tbl_name: str
    embedding_score: float
    original_rank: int


@dataclass(frozen=True)
class EmbeddingFallbackResult:
    status: str
    candidates: tuple[EmbeddingRankedCandidate, ...]
    model_id: str
    model_revision: str
    selection_status: str = "HOLD_NO_TABLE_SELECTED"
    mandatory_next_gate: str = MANDATORY_NEXT_GATE


def rerank_embedding_fallback(
    *,
    indicator: str,
    unit: str,
    frequency: str,
    calculation: str,
    confidence_status: str,
    candidates: Iterable[Mapping[str, object]],
    encoder: EmbeddingEncoder,
    model_id: str,
    model_revision: str,
    top_k: int = 5,
) -> EmbeddingFallbackResult:
    """Rerank only low-lexical-confidence candidates by cosine similarity."""
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if confidence_status != "LOW_LEXICAL_CONFIDENCE":
        return EmbeddingFallbackResult(
            status="NOT_RUN_LEXICAL_READY",
            candidates=(),
            model_id=model_id,
            model_revision=model_revision,
        )

    materialized: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for original_rank, candidate in enumerate(candidates, start=1):
        table_id = _text(candidate.get("tbl_id"))
        table_name = _text(candidate.get("tbl_name"))
        if not table_id or not table_name or table_id in seen:
            continue
        seen.add(table_id)
        materialized.append((original_rank, table_id, table_name))
    if not materialized:
        return EmbeddingFallbackResult(
            status="HOLD_NO_CANDIDATE_FOR_EMBEDDING",
            candidates=(),
            model_id=model_id,
            model_revision=model_revision,
        )

    query = _query_text(indicator, unit, frequency, calculation)
    candidate_texts = [f"KOSIS 통계표: {table_name}" for _, _, table_name in materialized]
    vectors = [list(vector) for vector in encoder([query, *candidate_texts])]
    if len(vectors) != len(materialized) + 1:
        raise ValueError("encoder returned an unexpected vector count")
    query_vector = vectors[0]
    _validate_vectors(vectors)

    ranked = [
        EmbeddingRankedCandidate(
            tbl_id=table_id,
            tbl_name=table_name,
            embedding_score=round(_cosine(query_vector, vectors[index]), 6),
            original_rank=original_rank,
        )
        for index, (original_rank, table_id, table_name) in enumerate(
            materialized,
            start=1,
        )
    ]
    ranked.sort(
        key=lambda candidate: (
            -candidate.embedding_score,
            candidate.original_rank,
            candidate.tbl_id,
        )
    )
    return EmbeddingFallbackResult(
        status="EMBEDDING_SHORTLIST_ATTACHED",
        candidates=tuple(ranked[:top_k]),
        model_id=model_id,
        model_revision=model_revision,
    )


def _query_text(indicator: str, unit: str, frequency: str, calculation: str) -> str:
    calculation_label = {
        "DIRECT_VALUE": "공식값 직접 조회",
        "GROWTH_RATE": "두 시점 증감률 계산",
        "DIFFERENCE": "두 값 차이 계산",
        "RATIO": "비율 계산",
    }.get(calculation, calculation)
    return (
        f"검증할 통계지표: {indicator}; 단위: {unit}; 주기: {frequency}; "
        f"계산방법: {calculation_label}"
    )


def _validate_vectors(vectors: Sequence[Sequence[float]]) -> None:
    if not vectors or not vectors[0]:
        raise ValueError("encoder returned an empty vector")
    dimension = len(vectors[0])
    for vector in vectors:
        if len(vector) != dimension or not all(math.isfinite(float(value)) for value in vector):
            raise ValueError("encoder returned invalid vectors")


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
