"""Gold replay layer: stored, joinable R1/R3/R4 predictions from frozen Gold inputs.

Boundaries enforced here:

- R1 replay is a synthetic one-sentence diagnostic and is always labelled
  ``R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL`` so it can never be
  mistaken for full-article recall.  UNCERTAIN Gold labels are never coerced.
- R3 replay consumes frozen R2 Gold 12-slot claims and is therefore flagged
  ``R3_CONDITIONAL_ON_FROZEN_R2_GOLD`` / ``NOT_END_TO_END`` with
  ``R2_INPUT_SOURCE=FROZEN_GOLD``.  Gold expected answers (table IDs, routes,
  verdicts, coordinates) are rejected from every inference payload.
- R4 replay reads official values from local snapshots only; a missing
  article-time snapshot stays HOLD and is never replaced by a live API value.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from core.data_loader import SemanticStandardRecord
from core.kosis_fetcher import OfficialValueFetcher
from core.r1_article_pipeline import run_r1_pipeline
from core.r2_claim_pipeline import R2ReadyClaim
from core.r3_evidence_pipeline import R3PipelineResult, R3ReadyEvidence, run_r3_pipeline
from core.r3_runtime_catalog import resolve_runtime_catalog_candidates
from core.r4_verdict_pipeline import run_r4_pipeline
from core.semantic_matcher import semantic_match
from core.semantic_normalizer import normalize_concept
from schemas.candidate import KosisCandidateSchema
from schemas.claim import ClaimSchema
from schemas.period_availability import PeriodAvailabilitySnapshot


R1_REPLAY_KIND = "R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL"
R3_SCOPE_FLAGS = ("R3_CONDITIONAL_ON_FROZEN_R2_GOLD", "NOT_END_TO_END")
R2_INPUT_SOURCE_FROZEN_GOLD = "FROZEN_GOLD"

GOLD_EXPECTED_FIELDS_FORBIDDEN = (
    "gold_table_id",
    "gold_table_ids",
    "gold_coordinate",
    "gold_coordinates",
    "gold_evidence_value",
    "gold_verdict_raw",
    "gold_verdict_standard",
    "expected_route",
    "expected_verdict",
    "expected_tbl_id",
    "expected_cell",
    "expected_evidence",
    "expected_parse_status",
    "expected_claim_slots",
)


class GoldExpectedFieldLeakError(ValueError):
    """A Gold expected/answer field reached an inference payload."""


class GoldReplaySentenceHashMissingError(ValueError):
    """A new replay output was produced without a sentence hash."""


def replay_r1_gold30(gold_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Replay each Gold sentence as a synthetic one-sentence article through R1.

    This is a candidate-emission diagnostic only.  It never measures recall on
    full article bodies, and the returned payload says so explicitly.
    """
    records = []
    row_by_article_id: dict[str, Mapping[str, Any]] = {}
    for row in gold_rows:
        row_id = str(row.get("row_id") or "").strip()
        article_id = f"R1G-{row_id}" if row_id else f"R1G-row{len(row_by_article_id) + 1:04d}"
        row_by_article_id[article_id] = row
        records.append(
            {
                "article_id": article_id,
                "published_at": str(row.get("article_date") or "").strip(),
                "source_url": f"https://gold-replay.local/{row_id or article_id}",
                "content_provenance": "ARTICLE_PAGE_CRAWL",
                "body": str(row.get("sentence") or ""),
            }
        )
    result = run_r1_pipeline(records)
    candidates: list[dict[str, Any]] = []
    for candidate in result.candidates:
        payload = candidate.model_dump(mode="json")
        if not _is_sha256(payload.get("sentence_hash")):
            raise GoldReplaySentenceHashMissingError(
                f"R1_REPLAY_CANDIDATE_SENTENCE_HASH_MISSING:{payload.get('claim_candidate_id')}"
            )
        gold_row = row_by_article_id.get(candidate.article_id)
        payload["gold_row_id"] = str(gold_row.get("row_id")) if gold_row else None
        payload["replay_kind"] = R1_REPLAY_KIND
        candidates.append(payload)
    holds = [hold.model_dump(mode="json") for hold in result.holds]
    return {
        "replay_kind": R1_REPLAY_KIND,
        "scope": {
            "synthetic_sentence_replay": True,
            "full_article_recall": False,
            "note": (
                "Each Gold sentence is replayed as a one-sentence synthetic article. "
                "This diagnoses numeric-candidate emission only and must be reported "
                "separately from real full-article R1 precision/recall."
            ),
        },
        "input_count": result.input_count,
        "candidates": candidates,
        "holds": holds,
        "hold_reason_counts": dict(sorted(Counter(h["reason_code"] for h in holds).items())),
    }


def build_frozen_r2_gold_claims(fixture_rows: Sequence[Mapping[str, Any]]) -> list[R2ReadyClaim]:
    """Build R3 input claims from the frozen R2 Gold 12-slot fixture.

    Raises :class:`GoldExpectedFieldLeakError` if any Gold expected/answer
    field is present anywhere in the payload — expected values are for
    scoring only and must never steer inference.
    """
    claims: list[R2ReadyClaim] = []
    for row in fixture_rows:
        _reject_expected_fields(row, where=str(row.get("claim_id") or "fixture_row"))
        claim_payload = row.get("claim")
        if not isinstance(claim_payload, Mapping):
            raise ValueError(f"FROZEN_R2_GOLD_CLAIM_PAYLOAD_MISSING:{row.get('claim_id')}")
        claim_id = str(row.get("claim_id") or "").strip()
        if not claim_id:
            raise ValueError("FROZEN_R2_GOLD_CLAIM_ID_MISSING")
        claim = ClaimSchema.model_validate({"claim_id": claim_id, **dict(claim_payload), "claim_id": claim_id})
        claims.append(
            R2ReadyClaim(
                article_id=str(row.get("article_id") or claim_id),
                published_at=str(row.get("published_at") or ""),
                claim_candidate_id=claim_id,
                sentence_hash=str(row.get("sentence_hash") or ""),
                split_parent_id=claim_id,
                split_index=1,
                split_count=1,
                claim=claim,
            )
        )
    return claims


def replay_r3_gold20(
    ready_claims: Sequence[R2ReadyClaim],
    *,
    concepts: Sequence[SemanticStandardRecord],
    catalog: Sequence[KosisCandidateSchema],
    period_availability: PeriodAvailabilitySnapshot | None = None,
) -> dict[str, Any]:
    """Run offline R3 on frozen-Gold claims, recording per-claim ranked candidates.

    The authoritative route/HOLD comes from the production ``run_r3_pipeline``
    (offline configuration: no live search, no metadata fetcher, no API key).
    Ranked candidate lists are recorded with the same offline building blocks
    so the ranking evidence is inspectable per claim.
    """
    result: R3PipelineResult = run_r3_pipeline(
        ready_claims,
        concepts=concepts,
        catalog=catalog,
        period_availability=period_availability,
        live_search=None,
        kosis_api_key=None,
        metadata_fetcher=None,
    )
    ready_by_id = {record.claim_candidate_id: record for record in result.r4_ready}
    hold_by_id = {hold.claim_candidate_id: hold for hold in result.holds}

    records: list[dict[str, Any]] = []
    for claim_record in ready_claims:
        claim_id = claim_record.claim_candidate_id
        ranked = _ranked_candidates_for(claim_record, concepts, catalog, period_availability)
        ready = ready_by_id.get(claim_id)
        hold = hold_by_id.get(claim_id)
        if ready is not None:
            route_status = "AUTO"
            reason_code = None
            selected_tbl_id = ready.candidate.tbl_id
            selected_cells = [cell.model_dump(mode="json") for cell in ready.calculation_plan.required_cells]
            if not ranked["ranked_candidate_tbl_ids"]:
                ranked = {
                    "ranked_candidate_tbl_ids": [ready.candidate.tbl_id],
                    "ranked_candidates": [
                        {
                            "tbl_id": ready.candidate.tbl_id,
                            "ranking_reason": f"registered_profile_direct_selection:{ready.evidence_profile}",
                        }
                    ],
                    "ranking_source": "REGISTERED_PROFILE",
                }
        else:
            route_status = "HOLD"
            reason_code = hold.reason_code if hold is not None else "R3_RESULT_MISSING"
            selected_tbl_id = None
            selected_cells = []
        records.append(
            {
                "claim_id": claim_id,
                "r2_input_source": R2_INPUT_SOURCE_FROZEN_GOLD,
                "route_status": route_status,
                "reason_code": reason_code,
                "ranked_candidate_tbl_ids": ranked["ranked_candidate_tbl_ids"] or None,
                "ranked_candidates": ranked["ranked_candidates"] or None,
                "ranking_source": ranked["ranking_source"],
                "selected_tbl_id": selected_tbl_id,
                "selected_evidence_cells": selected_cells,
            }
        )
    holds = [
        {"claim_id": hold.claim_candidate_id, "reason_code": hold.reason_code}
        for hold in result.holds
    ]
    return {
        "scope_flags": list(R3_SCOPE_FLAGS),
        "r2_input_source": R2_INPUT_SOURCE_FROZEN_GOLD,
        "input_count": result.input_count,
        "records": records,
        "ready": list(result.r4_ready),
        "holds": holds,
        "hold_reason_counts": dict(sorted(Counter(h["reason_code"] for h in holds).items())),
    }


def replay_r4_gold20(
    *,
    ready: Sequence[R3ReadyEvidence],
    carried_holds: Sequence[Mapping[str, Any]],
    snapshot_paths: Sequence[Path],
) -> dict[str, Any]:
    """Run deterministic R4 with a snapshot-only fetcher (no API substitution)."""
    fetcher = OfficialValueFetcher(list(snapshot_paths))  # api_lookup=None: snapshots only
    result = run_r4_pipeline(list(ready), fetcher=fetcher)
    verdict_rows: list[dict[str, Any]] = []
    for verdict in result.verdicts:
        payload = verdict.model_dump(mode="json")
        payload["r2_input_source"] = R2_INPUT_SOURCE_FROZEN_GOLD
        payload["snapshot_only"] = True
        verdict_rows.append(payload)
    for hold in carried_holds:
        verdict_rows.append(
            {
                "claim_id": str(hold.get("claim_id")),
                "route_status": "HOLD",
                "verdict": "UNDETERMINED",
                "reason_code": str(hold.get("reason_code") or "R3_HOLD_CARRIED_FORWARD"),
                "explanation": "Held before R4; no official value was fetched.",
                "evidence_cells": [],
                "evidence_values": [],
                "calculated_value": None,
                "r2_input_source": R2_INPUT_SOURCE_FROZEN_GOLD,
                "snapshot_only": True,
            }
        )
    provenance_rows = [row.model_dump(mode="json") for row in result.provenance]
    if any(row.get("source") == "API" for row in provenance_rows):
        raise ValueError("R4_REPLAY_MUST_NOT_USE_API_VALUES")
    return {
        "snapshot_only": True,
        "input_count": result.input_count + len(carried_holds),
        "verdict_rows": verdict_rows,
        "provenance_rows": provenance_rows,
        "hold_reason_counts": dict(
            sorted(
                Counter(
                    row["reason_code"] for row in verdict_rows if row.get("route_status") == "HOLD"
                ).items()
            )
        ),
    }


def _ranked_candidates_for(
    claim_record: R2ReadyClaim,
    concepts: Sequence[SemanticStandardRecord],
    catalog: Sequence[KosisCandidateSchema],
    period_availability: PeriodAvailabilitySnapshot | None,
) -> dict[str, Any]:
    """Record the offline lexical+semantic ranking evidence for one claim.

    Uses the same offline building blocks as ``run_r3_pipeline`` (runtime
    catalog resolution then Hard-Guarded semantic matching) so the recorded
    ranking is the evidence behind the authoritative route decision.
    """
    empty = {"ranked_candidate_tbl_ids": [], "ranked_candidates": [], "ranking_source": "NONE"}
    claim = claim_record.claim
    if claim.parse_status != "AUTO_OK":
        return empty
    try:
        concept = normalize_concept(claim, concepts)
    except Exception:
        return empty
    if concept.status != "MATCHED":
        return empty
    try:
        candidates, candidate_availability = resolve_runtime_catalog_candidates(
            claim,
            concept,
            catalog,
            period_availability=period_availability,
            live_search=None,
            kosis_api_key=None,
            metadata_fetcher=None,
        )
    except Exception:
        return empty
    if not candidates:
        return empty
    try:
        matches = semantic_match(claim, candidates, period_availability=candidate_availability)
    except Exception:
        return empty
    ranked = [
        {
            "tbl_id": match.candidate_tbl_id,
            "semantic_score": match.semantic_score,
            "top1_top2_margin": match.top1_top2_margin,
            "route_status": match.route_status,
            "reason_code": match.reason_code,
        }
        for match in matches
    ]
    return {
        "ranked_candidate_tbl_ids": [row["tbl_id"] for row in ranked],
        "ranked_candidates": ranked,
        "ranking_source": "OFFLINE_LEXICAL_SEMANTIC_MATCH",
    }


def _reject_expected_fields(value: Any, *, where: str) -> None:
    if isinstance(value, Mapping):
        for key, inner in value.items():
            if str(key) in GOLD_EXPECTED_FIELDS_FORBIDDEN:
                raise GoldExpectedFieldLeakError(f"GOLD_EXPECTED_FIELD_IN_INFERENCE_PAYLOAD:{key}@{where}")
            _reject_expected_fields(inner, where=where)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _reject_expected_fields(inner, where=where)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())
