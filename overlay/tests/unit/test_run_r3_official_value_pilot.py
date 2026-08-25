import csv
import json

from openpyxl import Workbook

from tools.run_r3_official_value_pilot import run


def test_runner_writes_local_snapshots_without_article_text_or_api_key(tmp_path):
    ledger = tmp_path / "ledger.xlsx"
    candidates = tmp_path / "candidates.csv"
    identities = tmp_path / "identities.jsonl"
    catalog = tmp_path / "catalog.json"
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
        "SENTINEL_ARTICLE_TEXT_MUST_NOT_APPEAR", "취업자 수", "28000000", "명",
        "2025년 1월", "월", "전국", None, "{}", "{}", "DIRECT_VALUE", "{}",
        None, "AUTO_OK", "", "R2_SLOT_READY", "", "", "MISSING", "READY",
    ])
    workbook.save(ledger)
    with candidates.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["claim_id", "split", "indicator", "candidate_tbl_ids"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "claim_id": "C1",
                "split": "dev",
                "indicator": "취업자 수",
                "candidate_tbl_ids": "DT_NEW",
            }
        )
    identities.write_text(
        json.dumps(
            {
                "indicator": "취업자 수",
                "candidates": [
                    {"org_id": "101", "tbl_id": "DT_NEW", "tbl_name": "취업자 수"}
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    catalog.write_text("[]\n", encoding="utf-8")

    def metadata_fetcher(api_key, org_id, table_id, *, meta_type, **_kwargs):
        assert api_key == "SECRET_MUST_NOT_BE_RECORDED"
        if meta_type == "ITM":
            return [
                {
                    "ORG_ID": org_id,
                    "TBL_ID": table_id,
                    "OBJ_ID": "ITEM",
                    "OBJ_NM": "항목",
                    "ITM_ID": "T1",
                    "ITM_NM": "취업자 수",
                    "UNIT_NM": "명",
                },
                {
                    "ORG_ID": org_id,
                    "TBL_ID": table_id,
                    "OBJ_ID": "A",
                    "OBJ_NM": "지역별",
                    "OBJ_ID_SN": "1",
                    "ITM_ID": "A0",
                    "ITM_NM": "전국",
                },
            ]
        return [{"PRD_SE": "M", "STRT_PRD_DE": "202001", "END_PRD_DE": "202612"}]

    def value_fetcher(
        api_key,
        org_id,
        table_id,
        item_id,
        period_type,
        start_period,
        end_period,
        object_codes,
        **_kwargs,
    ):
        assert api_key == "SECRET_MUST_NOT_BE_RECORDED"
        return [
            {
                "ORG_ID": org_id,
                "TBL_ID": table_id,
                "ITM_ID": item_id,
                "PRD_SE": period_type,
                "PRD_DE": start_period,
                "C1": object_codes[0],
                "C1_NM": "전국",
                "UNIT_NM": "명",
                "DT": "28000000",
            }
        ]

    summary = run(
        ledger_xlsx=ledger,
        candidate_attachment_csv=candidates,
        candidate_identity_jsonl=identities,
        catalog_json=catalog,
        output_dir=output,
        api_key="SECRET_MUST_NOT_BE_RECORDED",
        metadata_fetcher=metadata_fetcher,
        value_fetcher=value_fetcher,
        limit=20,
    )

    assert summary["selected_table_count"] == 1
    assert summary["official_value_fetched_count"] == 1
    combined = "\n".join(
        path.read_text(encoding="utf-8-sig")
        for path in output.iterdir()
        if path.suffix in {".csv", ".json", ".jsonl"}
    )
    assert "SENTINEL_ARTICLE_TEXT_MUST_NOT_APPEAR" not in combined
    assert "SECRET_MUST_NOT_BE_RECORDED" not in combined
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["secrets"]["kosis_api_key"] == "PRESENT_NOT_RECORDED"
