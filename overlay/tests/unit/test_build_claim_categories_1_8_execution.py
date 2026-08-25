import csv
import json

from tools.build_claim_categories_1_8_execution import build


SHEETS = {
    1: "01_문맥보완",
    2: "02_복수Claim분리",
    3: "03_최고최저기록",
    4: "04_순위",
    5: "05_비중구성비",
    6: "06_증감량",
    7: "07_증감률",
    8: "08_직접값",
}


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_builds_one_auditable_record_for_every_category(tmp_path) -> None:
    headers = [
        "Claim번호", "현재결과상태", "분류확신도", "기본검증방법", "현재판정",
        "공식근거URL",
    ]
    workbook_rows = {}
    for number, sheet in SHEETS.items():
        workbook_rows[sheet] = [
            [f"title {number}"], [], [], headers,
            [f"C{number}", "HOLD", "확정", f"method {number}", "UNDETERMINED", ""],
        ]
    workbook_json = tmp_path / "workbook.json"
    workbook_json.write_text(json.dumps(workbook_rows, ensure_ascii=False), encoding="utf-8")

    audit_rows = []
    registry_rows = []
    for number in range(1, 9):
        audit_rows.append({
            "Claim번호": f"C{number}", "통계표검색시도": "1", "항목정보조회시도": "1",
            "기간정보조회시도": "1", "KOSIS값조회시도": "YES" if number >= 3 else "NO",
            "KOSIS조회시각": "2026-08-25T00:00:00Z", "KOSIS조회경로": "",
            "KOSIS응답SHA256": "a" * 64,
        })
        registry_rows.append({
            "claim_id": f"C{number}", "coordinate_status": "REGISTERED_COORDINATE_READY",
            "reason_code": "", "org_id": "101", "table_id": "T1", "item_id": "I1",
            "object_codes": "{}", "period_type": "Y", "period": "2025",
            "official_value_status": "OFFICIAL_VALUE_FETCHED" if number >= 3 else "HOLD",
            "official_value": "10" if number >= 3 else "", "official_unit": "명",
            "value_response_sha256": "b" * 64, "verdict_status": "NOT_EVALUATED",
        })
    audit_csv = tmp_path / "audit.csv"
    registry_csv = tmp_path / "registry.csv"
    _write_csv(audit_csv, audit_rows)
    _write_csv(registry_csv, registry_rows)

    category12 = {
        "final_category1_results": [{"Claim번호": "C1", "최종실행상태": "SUCCESS", "성공실패사유": "OK"}],
        "final_category2_parent_results": [{"부모Claim번호": "C2", "최종실행상태": "SUCCESS", "성공실패사유": "OK"}],
        "final_category2_child_results": [
            {"부모Claim번호": "C2", "자식검증상태": "VALID", "부모최종실행상태": "SUCCESS"}
        ],
    }
    category12_json = tmp_path / "category12.json"
    category12_json.write_text(json.dumps(category12, ensure_ascii=False), encoding="utf-8")
    pilot = tmp_path / "pilot.jsonl"
    pilot.write_text("", encoding="utf-8")

    summary = build(
        workbook_rows_json=workbook_json,
        article_audit_csv=audit_csv,
        registry_results_csv=registry_csv,
        category12_results_json=category12_json,
        r2_pilot_results_jsonl=pilot,
        output_dir=tmp_path / "out",
        expected_claim_count=8,
    )

    assert summary["claim_count"] == 8
    assert summary["new_kosis_query_count"] == 0
    assert summary["category_summaries"]["6"]["status_counts"] == {"PARTIAL_SUCCESS": 1}
    assert summary["category_summaries"]["8"]["reason_counts"] == {
        "ARTICLE_ASOF_AND_TARGET_ROLE_UNCHECKED": 1
    }
    records = [json.loads(line) for line in (tmp_path / "out" / "execution_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len({row["claim_id"] for row in records}) == 8
    assert records[0]["execution_status"] == "R2_STAGE_SUCCESS"
    assert all(row["result_sha256"] for row in records)
