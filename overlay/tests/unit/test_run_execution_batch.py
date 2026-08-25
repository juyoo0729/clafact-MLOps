from core.kosis_live_catalog import KosisLiveCatalogSearch
from core.kosis_openapi_transport import get_meta
import tools.run_execution_batch as execution_batch
from tools.run_execution_batch import build_r3_dependencies, run_r3_claim


def test_live_mode_wires_official_search_and_structure_metadata() -> None:
    live_search, api_key, metadata_fetcher = build_r3_dependencies(
        mode="live",
        api_key="test-key",
    )

    assert isinstance(live_search.inner, KosisLiveCatalogSearch)
    assert api_key == "test-key"
    assert metadata_fetcher.inner is get_meta
    assert execution_batch.r3_api_call_count(live_search, metadata_fetcher) == 0


def test_snapshot_mode_keeps_r3_offline() -> None:
    live_search, api_key, metadata_fetcher = build_r3_dependencies(
        mode="snapshot",
        api_key=None,
    )

    assert live_search is None
    assert api_key is None
    assert metadata_fetcher is None


def test_claim_runner_forwards_live_dependencies_to_r3(monkeypatch) -> None:
    captured = {}
    expected = object()

    def fake_run_r3_pipeline(claims, **kwargs):
        captured["claims"] = claims
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(execution_batch, "run_r3_pipeline", fake_run_r3_pipeline)
    ready = object()
    live_search = object()
    metadata_fetcher = object()

    result = run_r3_claim(
        ready,
        concepts="concepts",
        catalog="catalog",
        period_availability="periods",
        live_search=live_search,
        kosis_api_key="test-key",
        metadata_fetcher=metadata_fetcher,
    )

    assert result is expected
    assert captured == {
        "claims": [ready],
        "concepts": "concepts",
        "catalog": "catalog",
        "period_availability": "periods",
        "live_search": live_search,
        "kosis_api_key": "test-key",
        "metadata_fetcher": metadata_fetcher,
    }


def test_r3_api_call_count_includes_search_and_metadata_attempts() -> None:
    class Search:
        def search(self, query, *, result_count=20):
            return [query, result_count]

    def metadata(*args, **kwargs):
        return [args, kwargs]

    search = execution_batch._CountingLiveSearch(Search())
    fetcher = execution_batch._CountingMetadataFetcher(metadata)

    search.search("고용")
    fetcher("key", "101", "DT", meta_type="ITM")

    assert execution_batch.r3_api_call_count(search, fetcher) == 2
