"""R2 atomic Claim structuring and fail-closed R3 admission."""

from __future__ import annotations

import re
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

from core.claim_parser import StructuredClaimExtractor, parse_claim
from core.claim_source_classifier import classify_claim_source
from core.claim_splitter import analyze_claim_split
from core.r2_slot_routing import (
    ENRICHABLE_AUTO_SLOTS,
    missing_auto_required_slots,
    revalidate_auto_readiness,
)
from core.claim_time_resolver import resolve_relative_time
from core.claim_value_trace import validate_claim_value_trace
from core.r1_article_pipeline import R1Candidate
from schemas.claim import ClaimSchema


_SAFE_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


def _reason_code_or_default(value: str | None, default: str) -> str:
    """Prevent provider prose or source text from entering operational reasons."""
    return value if isinstance(value, str) and _SAFE_REASON_CODE.fullmatch(value) else default


class R2ReadyClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    published_at: str
    claim_candidate_id: str
    sentence_hash: str = ""
    split_parent_id: str
    split_index: int
    split_count: int
    claim: ClaimSchema


class R2Hold(BaseModel):
    model_config = ConfigDict(frozen=True)

    article_id: str
    claim_candidate_id: str
    sentence_hash: str = ""
    reason_code: str
    route_status: str
    atomic_claim: str | None = None
    claim: ClaimSchema | None = None
    missing_slots: tuple[str, ...] = ()
    next_action: str = "REVIEW_REQUIRED"


class R2EnrichmentClaim(BaseModel):
    """A structured Claim that lacks only a recoverable context slot."""

    model_config = ConfigDict(frozen=True)

    article_id: str
    published_at: str
    claim_candidate_id: str
    sentence_hash: str = ""
    split_parent_id: str
    split_index: int
    split_count: int
    atomic_claim: str
    route_status: str = "ENRICHMENT_REQUIRED"
    reason_code: str = "R2_SLOT_ENRICHMENT_REQUIRED"
    missing_slots: tuple[str, ...]
    next_action: str = "RECOVER_TIME_FREQUENCY_FROM_ARTICLE_CONTEXT"
    claim: ClaimSchema


class R2PipelineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    r3_ready: list[R2ReadyClaim]
    holds: list[R2Hold]
    enrichment_required: list[R2EnrichmentClaim] = Field(default_factory=list)
    input_count: int


def run_r2_pipeline(
    candidates: Sequence[R1Candidate],
    *,
    extractor: StructuredClaimExtractor | None = None,
) -> R2PipelineResult:
    ready: list[R2ReadyClaim] = []
    holds: list[R2Hold] = []
    enrichment_required: list[R2EnrichmentClaim] = []

    for candidate in candidates:
        source_class = classify_claim_source(candidate.source_sentence)
        if source_class.route_status != "READY":
            holds.append(
                R2Hold(
                    article_id=candidate.article_id,
                    claim_candidate_id=candidate.claim_candidate_id,
                    sentence_hash=candidate.sentence_hash,
                    reason_code=_reason_code_or_default(source_class.reason_code, "R2_SOURCE_SCOPE_HOLD"),
                    route_status="HOLD",
                    next_action="REVIEW_SOURCE_SCOPE",
                )
            )
            continue
        split = analyze_claim_split(candidate.source_sentence)
        if split.route_status == "HUMAN_REVIEW" or not split.atomic_claims:
            holds.append(
                R2Hold(
                    article_id=candidate.article_id,
                    claim_candidate_id=candidate.claim_candidate_id,
                    sentence_hash=candidate.sentence_hash,
                    reason_code=_reason_code_or_default(split.reason_code, "R2_CLAIM_SPLIT_REVIEW_REQUIRED"),
                    route_status="HUMAN_REVIEW",
                    atomic_claim=candidate.source_sentence,
                    next_action="REVIEW_OR_RECONSTRUCT_ATOMIC_CLAIMS",
                )
            )
            continue
        split_count = len(split.atomic_claims)
        for split_index, atomic_claim in enumerate(split.atomic_claims, start=1):
            try:
                claim = parse_claim(atomic_claim, extractor)
                claim = resolve_relative_time(claim, candidate.published_at)
                claim = revalidate_auto_readiness(claim)
            except Exception:
                holds.append(
                    R2Hold(
                        article_id=candidate.article_id,
                        claim_candidate_id=candidate.claim_candidate_id,
                        sentence_hash=candidate.sentence_hash,
                        reason_code="R2_STRUCTURED_EXTRACTOR_FAILED",
                        route_status="HOLD",
                        atomic_claim=atomic_claim,
                        next_action="RETRY_OR_REVIEW_STRUCTURED_EXTRACTION",
                    )
                )
                continue
            if claim.parse_status != "AUTO_OK":
                missing_slots = missing_auto_required_slots(claim)
                is_enrichable = (
                    claim.parse_status == "HOLD"
                    and (claim.parse_reason or "").startswith("MISSING_REQUIRED_SLOTS:")
                    and bool(missing_slots)
                    and set(missing_slots).issubset(ENRICHABLE_AUTO_SLOTS)
                )
                if is_enrichable:
                    enrichment_required.append(
                        R2EnrichmentClaim(
                            article_id=candidate.article_id,
                            published_at=candidate.published_at.isoformat(),
                            claim_candidate_id=candidate.claim_candidate_id,
                            sentence_hash=candidate.sentence_hash,
                            split_parent_id=candidate.claim_candidate_id,
                            split_index=split_index,
                            split_count=split_count,
                            atomic_claim=atomic_claim,
                            missing_slots=missing_slots,
                            claim=claim,
                        )
                    )
                    continue
                holds.append(
                    R2Hold(
                        article_id=candidate.article_id,
                        claim_candidate_id=candidate.claim_candidate_id,
                        sentence_hash=candidate.sentence_hash,
                        reason_code=_reason_code_or_default(claim.parse_reason, "R2_CLAIM_PARSE_NOT_AUTO_OK"),
                        route_status=claim.parse_status,
                        atomic_claim=atomic_claim,
                        claim=claim,
                        missing_slots=missing_slots,
                        next_action=(
                            "REEXTRACT_OR_REVIEW_REQUIRED_SLOTS"
                            if missing_slots
                            else "REVIEW_SEMANTIC_CONFLICT"
                        ),
                    )
                )
                continue
            trace = validate_claim_value_trace(claim)
            if not trace.passed:
                holds.append(
                    R2Hold(
                        article_id=candidate.article_id,
                        claim_candidate_id=candidate.claim_candidate_id,
                        sentence_hash=candidate.sentence_hash,
                        reason_code=_reason_code_or_default(trace.reason_code, "R2_VALUE_TRACE_FAILED"),
                        route_status="HOLD",
                        atomic_claim=atomic_claim,
                        claim=claim,
                        next_action="REVIEW_ARTICLE_VALUE_TRACE",
                    )
                )
                continue
            ready.append(
                R2ReadyClaim(
                    article_id=candidate.article_id,
                    published_at=candidate.published_at.isoformat(),
                    claim_candidate_id=candidate.claim_candidate_id,
                    sentence_hash=candidate.sentence_hash,
                    split_parent_id=candidate.claim_candidate_id,
                    split_index=split_index,
                    split_count=split_count,
                    claim=claim,
                )
            )
    return R2PipelineResult(
        r3_ready=ready,
        holds=holds,
        enrichment_required=enrichment_required,
        input_count=len(candidates),
    )
