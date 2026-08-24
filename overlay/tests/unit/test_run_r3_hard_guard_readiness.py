import csv
import json

from openpyxl import Workbook

from schemas.candidate import KosisCandidateSchema
from tools.run_r3_hard_guard_readiness import run


def _candidate():
    return KosisCandidateSchema(
        org_id="101",
        tbl_id="DT_PASS",
        tbl_name="월별 취업자 수",
        core_item_ids=["T1"],
        core_item_names=["취업자 수"],
        dimension_ids=[],
        dimension_names=[],
        dimension_members={},
        unit_names=["명"],
        frequency="월",
        start_period="2020.01",
        end_period="2026.12",
        metadata_status="OFFICIAL_METADATA_READY",
    )


def test_runner_excludes_sentence_column_and_writes_guard_artifact(tmp_path):
    ledger = tmp_path / "ledger.xlsx"
    candidates = tmp_path / "candidates.csv"
    output = tmp_path / "output"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "1542건 원장"
    sheet.append([
        "claim_id", "article_id", "article_date", "split", "claim_type_original",
        "gold_time_source", "sentence", "indicator", "value", "unit", "time",
        "frequency", "region", "population", "dimension", "comparison",
        "calculation", "condition", "source_hint", "original_parse_status",
        "missing_required_slots", "flexible_route", "route_reason", "next_action",
        "target_value_role_status", "runtime_r3_admission",
    ])
    sheet.append([
        "C1", "A1", "2025-01-01", "dev", "DIRECT_VALUE", "explicit",
        "SENTINEL_ARTICLE_TEXT_MUST_NOT_APPEAR", "취업자 수", "100", "명",
        "2025-01", "월", "전국", None, "{}", "{}", "DIRECT_VALUE", "{}",
        None, "AUTO_OK", "", "R2_SLOT_READY", "", "", "MISSING", "READY",
    ])
    workbook.save(ledger)
    with candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["claim_id", "split", "candidate_tbl_ids"],
        )
        writer.writeheader()
        writer.writerow({"claim_id": "C1", "split": "dev", "candidate_tbl_ids": "DT_PASS"})

    summary = run(
        ledger_xlsx=ledger,
        candidate_attachment_csv=candidates,
        output_dir=output,
        catalog_candidates=[_candidate()],
    )

    assert summary["structural_guard_survivor_claim_count"] == 1
    combined = (output / "hard_guard_readiness.csv").read_text(encoding="utf-8-sig")
    combined += (output / "manifest.json").read_text(encoding="utf-8")
    assert "SENTINEL_ARTICLE_TEXT_MUST_NOT_APPEAR" not in combined
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["input_contract"]["article_text_column"] == "EXCLUDED_NOT_READ"
