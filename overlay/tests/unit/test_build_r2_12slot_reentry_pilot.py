import json

from tools.build_r2_12slot_reentry_pilot import build


def _row(claim_id, source_type, role=None):
    return {
        "claim_id": claim_id,
        "article_id": claim_id.split("_")[0],
        "parent_claim_id": "",
        "source_type": source_type,
        "source_sentence": f"{claim_id} 수치는 9개이다.",
        "published_at": "2025-01-10",
        "context_before": "",
        "context_after": "",
        "verbatim_target_value": "9개" if role else "",
        "period_evidence": "",
        "known_slots": {"target_value_role": role, "time": None, "frequency": None},
    }


def test_builds_balanced_deterministic_pilot_with_role_coverage(tmp_path) -> None:
    queue = tmp_path / "queue.jsonl"
    rows = [_row(f"C1_{index}", "CATEGORY1_CONTEXT_COMPLETED_PARENT") for index in range(12)]
    roles = [
        "CURRENT_VALUE", "CHANGE_VALUE", "PRIOR_VALUE", "SHARE_VALUE", "THRESHOLD_VALUE",
        "RANK_VALUE", "RATIO_VALUE", "RANGE_VALUE", "CURRENT_VALUE", "CHANGE_VALUE", "CURRENT_VALUE",
    ]
    rows.extend(_row(f"C2_{index}", "CATEGORY2_VALIDATED_ATOMIC_CHILD", role) for index, role in enumerate(roles))
    queue.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    summary = build(queue_jsonl=queue, output_dir=tmp_path / "out", per_source_count=10)

    assert summary["record_count"] == 20
    assert summary["source_type_counts"] == {
        "CATEGORY1_CONTEXT_COMPLETED_PARENT": 10,
        "CATEGORY2_VALIDATED_ATOMIC_CHILD": 10,
    }
    assert summary["category2_role_counts"]["RANGE_VALUE"] == 1
    selected = [json.loads(line) for line in (tmp_path / "out" / "pilot_input.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({row["claim_id"] for row in selected}) == 20
    assert all(row["pilot_selection_sha256"] for row in selected)
