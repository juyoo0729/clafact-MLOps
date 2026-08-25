import csv
import json

from tools.run_claim_split_multimethod_benchmark import run


def test_multimethod_benchmark_records_union_and_agreement(tmp_path) -> None:
    source = tmp_path / "parents.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("기사번호", "부모Claim번호", "원문"))
        writer.writeheader()
        writer.writerow({"기사번호": "A1", "부모Claim번호": "A1_1", "원문": "9개 품목이 60%를 차지한다."})

    def fake_runner(method, sentence):
        assert sentence == "9개 품목이 60%를 차지한다."
        return {
            "method": method,
            "children": [
                {"text": "품목은 9개이다.", "target_value_text": "9개", "target_value_role": "CURRENT_VALUE"},
                {"text": "품목은 60%를 차지한다.", "target_value_text": "60%", "target_value_role": "SHARE_VALUE"},
            ],
            "route_status": "AUTO",
            "reason_code": "TEST",
        }

    out = tmp_path / "out"
    summary = run(
        parent_csv=source,
        output_dir=out,
        expected_parent_count=1,
        methods=("m1", "m2"),
        method_runner=fake_runner,
    )
    assert summary["validated_union_success_count"] == 1
    assert summary["same_children_agreement_success_count"] == 1
    assert summary["scoreboard"]["m1"]["status_SUCCESS"] == 1
    record = json.loads((out / "per_claim_method_results.jsonl").read_text(encoding="utf-8"))
    assert record["agreement_groups"] == [["m1", "m2"]]
    assert (out / "manifest.json").exists()
