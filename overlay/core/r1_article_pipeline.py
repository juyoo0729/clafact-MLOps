"""Deterministic R1 article preprocessing with row-level HOLD routing."""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import date
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from core.article_preprocessor import preprocess_article


_ALLOWED_PROVENANCE = {"RSS_FULL_TEXT", "ARTICLE_PAGE_CRAWL"}


class R1Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    published_at: date
    source_url: str | None = None
    content_provenance: str
    claim_candidate_id: str
    sentence_index: int
    source_sentence: str
    sentence_hash: str = ""


class R1Hold(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    reason_code: str


class R1PipelineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidates: list[R1Candidate]
    holds: list[R1Hold]
    input_count: int


def run_r1_pipeline(records: Sequence[Mapping[str, object]]) -> R1PipelineResult:
    """Process each article independently; one malformed row never aborts its siblings."""
    candidates: list[R1Candidate] = []
    holds: list[R1Hold] = []
    seen_article_ids: set[str] = set()

    for index, record in enumerate(records, start=1):
        article_id = _text(record.get("article_id")) or f"row-{index:06d}"
        if article_id in seen_article_ids:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_DUPLICATE_ARTICLE_ID"))
            continue
        seen_article_ids.add(article_id)
        published_raw = _text(record.get("published_at"))
        if not published_raw:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_PUBLISHED_AT_REQUIRED"))
            continue
        try:
            published_at = date.fromisoformat(published_raw[:10])
        except ValueError:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_PUBLISHED_AT_INVALID"))
            continue
        body = _text(record.get("body"))
        if not body:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_BODY_REQUIRED"))
            continue
        provenance = _text(record.get("content_provenance"))
        if provenance not in _ALLOWED_PROVENANCE:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_CONTENT_PROVENANCE_NOT_APPROVED"))
            continue
        source_url = _text(record.get("source_url"))
        parsed_url = urlsplit(source_url or "")
        if parsed_url.scheme != "https" or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_SOURCE_URL_NOT_HTTPS"))
            continue
        processed = preprocess_article(body)
        if not processed.claim_candidates:
            holds.append(R1Hold(article_id=article_id, reason_code="R1_NUMERIC_CANDIDATE_NOT_FOUND"))
            continue
        for sentence_index, sentence in enumerate(processed.claim_candidates, start=1):
            digest = hashlib.sha256(f"{article_id}\0{sentence_index}\0{sentence}".encode("utf-8")).hexdigest()[:16]
            candidates.append(
                R1Candidate(
                    article_id=article_id,
                    published_at=published_at,
                    source_url=source_url,
                    content_provenance=provenance,
                    claim_candidate_id=f"candidate_{digest}",
                    sentence_index=sentence_index,
                    source_sentence=sentence,
                    sentence_hash=normalized_sentence_hash(sentence),
                )
            )
    return R1PipelineResult(candidates=candidates, holds=holds, input_count=len(records))


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def normalized_sentence_hash(sentence: str) -> str:
    """Return the cross-artifact join key for a whitespace-insensitive sentence."""
    normalized = "".join(unicodedata.normalize("NFC", sentence).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
