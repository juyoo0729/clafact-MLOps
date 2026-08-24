from core.r2_flexible_gold_replay import replay_r2_flexible_gold


def _row(claim_id: str, status: str, **slot_updates: object) -> dict:
    slots = {
        "indicator": "취업자 수",
        "value": 10.0,
        "unit": "명",
        "time": "2025-01",
        "frequency": "월",
        "calculation": "DIRECT_VALUE",
    }
    slots.update(slot_updates)
    return {
        "claim_id": claim_id,
        "article_id": "A1",
        "article_date": "2025-02-01",
        "split": "train",
        "claim_type_original": "규모형",
        "gold_time_source": "absolute_in_sentence",
        "sentence": "취업자 수는 10명이다.",
        "expected_parse_status": status,
        "expected_claim_slots": slots,
    }


def test_replay_routes_time_frequency_only_hold_to_enrichment() -> None:
    rows, summary = replay_r2_flexible_gold([
        _row("C1", "HOLD", time=None, frequency=None),
    ])
    assert rows[0]["flexible_route"] == "ENRICHMENT_REQUIRED"
    assert rows[0]["missing_required_slots"] == ["time", "frequency"]
    assert summary["flexible_route_counts"] == {"ENRICHMENT_REQUIRED": 1}


def test_replay_preserves_core_hold_and_human_review() -> None:
    rows, _ = replay_r2_flexible_gold([
        _row("C1", "HOLD", value=None, unit=None),
        _row("C2", "HUMAN_REVIEW", time=None, frequency=None),
    ])
    assert rows[0]["flexible_route"] == "HOLD"
    assert rows[0]["route_reason"] == "R2_CORE_SLOT_MISSING"
    assert rows[1]["flexible_route"] == "HUMAN_REVIEW"


def test_replay_marks_slot_ready_but_keeps_runtime_role_gate() -> None:
    rows, _ = replay_r2_flexible_gold([_row("C1", "AUTO_OK")])
    assert rows[0]["flexible_route"] == "R2_SLOT_READY"
    assert rows[0]["runtime_r3_admission"] == "BLOCKED_UNTIL_TARGET_VALUE_ROLE"
