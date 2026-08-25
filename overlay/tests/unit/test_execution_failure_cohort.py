import csv
import json

import pytest

from core.execution_failure_cohort import build_failure_cohort


def _write_history(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["claim_id", "failed_stage", "failure_cause"],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_builds_exact_same_claim_failure_cohort(tmp_path) -> None:
    history = tmp_path / "history.csv"
    source_batch = tmp_path / "batch.json"
    _write_history(
        history,
        [
            {"claim_id": "C1", "failed_stage": "CATALOG_SEARCH", "failure_cause": "KOSIS_METADATA_UNAVAILABLE"},
            {"claim_id": "C2", "failed_stage": "HARD_GUARD", "failure_cause": "NO_HARD_GUARD_CANDIDATE"},
            {"claim_id": "C3", "failed_stage": "CATALOG_SEARCH", "failure_cause": "KOSIS_METADATA_UNAVAILABLE"},
        ],
    )
    source_batch.write_text(
        json.dumps(
            {
                "batch_id": "D1",
                "claims": [
                    {"claim_id": "C1", "subtype": "direct"},
                    {"claim_id": "C2", "subtype": "direct"},
                    {"claim_id": "C3", "subtype": "direct"},
                ],
            }
        ),
        encoding="utf-8",
    )

    cohort = build_failure_cohort(
        history_path=history,
        source_batch_path=source_batch,
        failure_cause="KOSIS_METADATA_UNAVAILABLE",
        expected_history_count=3,
        expected_cohort_count=2,
    )

    assert cohort["batch_id"] == "D1__KOSIS_METADATA_UNAVAILABLE__2"
    assert [row["claim_id"] for row in cohort["claims"]] == ["C1", "C3"]
    assert cohort["source_history_sha256"]
    assert cohort["source_batch_sha256"]


def test_rejects_history_count_drift(tmp_path) -> None:
    history = tmp_path / "history.csv"
    source_batch = tmp_path / "batch.json"
    _write_history(
        history,
        [{"claim_id": "C1", "failed_stage": "CATALOG_SEARCH", "failure_cause": "KOSIS_METADATA_UNAVAILABLE"}],
    )
    source_batch.write_text(
        json.dumps({"batch_id": "D1", "claims": [{"claim_id": "C1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="HISTORY_COUNT_MISMATCH"):
        build_failure_cohort(
            history_path=history,
            source_batch_path=source_batch,
            failure_cause="KOSIS_METADATA_UNAVAILABLE",
            expected_history_count=146,
            expected_cohort_count=52,
        )


def test_rejects_duplicate_claim_ids(tmp_path) -> None:
    history = tmp_path / "history.csv"
    source_batch = tmp_path / "batch.json"
    _write_history(
        history,
        [
            {"claim_id": "C1", "failed_stage": "CATALOG_SEARCH", "failure_cause": "KOSIS_METADATA_UNAVAILABLE"},
            {"claim_id": "C1", "failed_stage": "CATALOG_SEARCH", "failure_cause": "KOSIS_METADATA_UNAVAILABLE"},
        ],
    )
    source_batch.write_text(
        json.dumps({"batch_id": "D1", "claims": [{"claim_id": "C1"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DUPLICATE_HISTORY_CLAIM_IDS"):
        build_failure_cohort(
            history_path=history,
            source_batch_path=source_batch,
            failure_cause="KOSIS_METADATA_UNAVAILABLE",
            expected_history_count=2,
            expected_cohort_count=2,
        )
