from core.r1_article_pipeline import run_r1_pipeline


def test_r1_keeps_invalid_rows_as_hold_and_emits_traceable_candidates() -> None:
    result = run_r1_pipeline(
        [
            {
                "article_id": "a1",
                "published_at": "2026-08-16",
                "body": "취업자는 10만명 증가했다. 일반 문장이다.",
                "source_url": "https://example.test/a1",
                "content_provenance": "RSS_FULL_TEXT",
            },
            {
                "article_id": "a2",
                "published_at": "",
                "body": "물가는 2.1% 올랐다.",
                "content_provenance": "RSS_FULL_TEXT",
            },
        ]
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].article_id == "a1"
    assert result.candidates[0].source_sentence == "취업자는 10만명 증가했다."
    assert result.candidates[0].claim_candidate_id.startswith("candidate_")
    assert result.candidates[0].sentence_hash == "cdf709e9406451b15f29ac01d1c5633da42c31725d068a36ca37784fce08be41"
    assert result.holds[0].reason_code == "R1_PUBLISHED_AT_REQUIRED"


def test_r1_requires_https_source_url_for_approved_provenance() -> None:
    result = run_r1_pipeline(
        [
            {
                "article_id": "a1",
                "published_at": "2026-08-16",
                "body": "물가는 2.1% 올랐다.",
                "source_url": "http://example.test/a1",
                "content_provenance": "RSS_FULL_TEXT",
            }
        ]
    )

    assert result.candidates == []
    assert result.holds[0].reason_code == "R1_SOURCE_URL_NOT_HTTPS"
