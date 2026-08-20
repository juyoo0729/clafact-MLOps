from datetime import date

from core.r1_article_pipeline import R1Candidate
from core.r2_claim_pipeline import run_r2_pipeline
from schemas.claim import ClaimSchema


class FakeExtractor:
    def extract(self, source_sentence: str) -> ClaimSchema:
        return ClaimSchema(
            claim_id="ignored",
            source_sentence=source_sentence,
            indicator="취업자 수",
            value=100000,
            target_value_role="CHANGE_VALUE",
            unit="명",
            time="2025년",
            frequency="년",
            comparison={"type": "YEAR_OVER_YEAR"},
            calculation="DIFFERENCE",
            parse_status="AUTO_OK",
        )


class UnsafeReasonExtractor:
    def extract(self, source_sentence: str) -> ClaimSchema:
        return ClaimSchema(
            claim_id="ignored",
            source_sentence=source_sentence,
            parse_status="HOLD",
            parse_reason="원시 제공자 설명은 운영 reason code가 아니다",
        )


def test_r2_routes_only_atomic_factual_value_traced_claims_to_r3() -> None:
    candidates = [
        R1Candidate(
            article_id="a1",
            published_at=date(2026, 8, 16),
            content_provenance="RSS_FULL_TEXT",
            claim_candidate_id="c1",
            sentence_index=1,
            source_sentence="2025년 취업자는 전년보다 10만명 증가했다.",
            sentence_hash="gold-hash-1",
        ),
        R1Candidate(
            article_id="a2",
            published_at=date(2026, 8, 16),
            content_provenance="RSS_FULL_TEXT",
            claim_candidate_id="c2",
            sentence_index=1,
            source_sentence="2025년 취업자는 10만명 증가할 것으로 전망했다.",
            sentence_hash="gold-hash-2",
        ),
    ]

    result = run_r2_pipeline(candidates, extractor=FakeExtractor())

    assert len(result.r3_ready) == 1
    assert result.r3_ready[0].claim.parse_status == "AUTO_OK"
    assert result.r3_ready[0].sentence_hash == "gold-hash-1"
    assert result.holds[0].reason_code == "FORECAST_CLAIM"
    assert result.holds[0].sentence_hash == "gold-hash-2"


def test_r2_redacts_provider_prose_from_operational_reason_codes() -> None:
    candidate = R1Candidate(
        article_id="a1",
        published_at=date(2026, 8, 16),
        content_provenance="RSS_FULL_TEXT",
        claim_candidate_id="c1",
        sentence_index=1,
        source_sentence="2025년 취업자는 전년보다 10만명 증가했다.",
    )

    result = run_r2_pipeline([candidate], extractor=UnsafeReasonExtractor())

    assert result.holds[0].reason_code == "R2_CLAIM_PARSE_NOT_AUTO_OK"
