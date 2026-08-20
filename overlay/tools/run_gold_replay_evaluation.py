"""Score one stored Gold replay run offline (R1 Gold30, R3/R4 Gold20).

Reads only saved replay artifacts and frozen Gold; never calls RSS, KOSIS, or
an LLM API.  The R3/R4 sections are CONDITIONAL results on frozen R2 Gold
12-slot inputs and are labelled as such — they are not end-to-end accuracy.
Gold expected routes/verdicts/tables are used for scoring only.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.mlops_gold_evaluation import (  # noqa: E402
    build_review_queue_status,
    evaluate_r1,
    evaluate_r3,
    evaluate_r4,
    write_evaluation_artifacts,
)
from tools.run_mlops_gold_evaluation import (  # noqa: E402
    _load_gold20_contract,
    _load_jsonl,
)


DEFAULT_GOLD20_FIXTURE = PROJECT / "tests" / "goldset" / "fixtures" / "pilot20.json"
DEFAULT_GOLD20_ROUTES = PROJECT / "tests" / "goldset" / "fixtures" / "pilot20_expected_routes.json"
DEFAULT_GOLD20_ROUTE_REPORT = PROJECT / "data" / "goldset_reports" / "pilot20_route_report.json"
DEFAULT_OUTPUT_ROOT = PROJECT / "data" / "gold_evaluations"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-run", required=True, type=Path, help="data/gold_replay_runs/<run_id> directory")
    parser.add_argument("--r1-gold", required=True, type=Path, help="R1 Gold30 CSV")
    parser.add_argument("--gold20-fixture", type=Path, default=DEFAULT_GOLD20_FIXTURE)
    parser.add_argument("--gold20-routes", type=Path, default=DEFAULT_GOLD20_ROUTES)
    parser.add_argument("--gold20-route-report", type=Path, default=DEFAULT_GOLD20_ROUTE_REPORT)
    parser.add_argument(
        "--operational-r1-candidates",
        type=Path,
        default=None,
        help="Optional stored operational r1_candidates.jsonl to include in review-queue status only.",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evaluation-id", help="Immutable output directory name; generated when omitted.")
    args = parser.parse_args()

    run_dir = args.replay_run.resolve()
    run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    run_id = str(run_manifest.get("run_id") or run_dir.name)
    _verify_outputs(run_dir, run_manifest)

    with args.r1_gold.open(encoding="utf-8-sig", newline="") as handle:
        r1_gold = list(csv.DictReader(handle))
    r1_candidates = _load_jsonl(run_dir / "r1" / "candidates.jsonl")
    r3_predictions = _load_jsonl(run_dir / "r3" / "ranked_candidates.jsonl")
    r4_predictions = _load_jsonl(run_dir / "r4" / "verdicts.jsonl")
    gold20 = _load_gold20_contract(
        fixture_path=args.gold20_fixture,
        routes_path=args.gold20_routes,
        route_report_path=args.gold20_route_report,
    )

    r1_result = evaluate_r1(gold_rows=r1_gold, candidate_rows=r1_candidates)
    r3_result = evaluate_r3(gold_rows=gold20, prediction_rows=r3_predictions)
    r4_result = evaluate_r4(gold_rows=gold20, prediction_rows=r4_predictions)
    gold_results = {"r1": r1_result, "r3": r3_result, "r4": r4_result}

    review_inputs = [{**row, "stage": "R1"} for row in r1_candidates]
    operational_note = None
    if args.operational_r1_candidates is not None:
        operational_rows = _load_jsonl(args.operational_r1_candidates)
        review_inputs += [{**row, "stage": "R1"} for row in operational_rows]
        operational_note = {
            "operational_r1_candidate_count": len(operational_rows),
            "policy": "Operational rows join review-queue status only; hash-less rows are kept as REQUIRES_SENTENCE_HASH_BACKFILL, never deleted.",
        }
    review_status = build_review_queue_status(gold_rows=r1_gold, candidate_rows=review_inputs)

    reason_counts: Counter[str] = Counter()
    for result in gold_results.values():
        for reason, count in (result.get("not_evaluable_reason_counts") or {}).items():
            reason_counts[reason] += count
    statuses = [result.get("status") for result in gold_results.values()]
    if all(status == "EVALUATED" for status in statuses):
        evaluation_status = "EVALUATED"
    elif any(status in {"EVALUATED", "PARTIAL"} for status in statuses):
        evaluation_status = "PARTIAL"
    else:
        evaluation_status = "NOT_EVALUABLE"

    evaluation_id = args.evaluation_id or (
        f"gold-replay-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{run_id[-8:]}"
    )
    summary = {
        "schema_version": "clafact_gold_replay_evaluation_v1",
        "evaluation_id": evaluation_id,
        "gold_replay_run_id": run_id,
        "evaluation_status": evaluation_status,
        "operational_metrics": {
            "metric_kind": "OPERATIONAL_REVIEW_QUEUE_COUNTS_NOT_ACCURACY",
            "review_input_count": 0 if operational_note is None else operational_note["operational_r1_candidate_count"],
        },
        "scope": {
            "conditional_vs_e2e": "CONDITIONAL_REPLAY_NOT_END_TO_END",
            "r1": "R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL",
            "r3_flags": ["R3_CONDITIONAL_ON_FROZEN_R2_GOLD", "NOT_END_TO_END"],
            "r2_input_source": "FROZEN_GOLD",
            "r4_value_policy": "SNAPSHOT_ONLY_NO_LATEST_API_SUBSTITUTION",
            "separation_note": (
                "These conditional replay scores measure R3/R4 behaviour given human-frozen R2 slots. "
                "End-to-end accuracy from raw articles is a different, unmeasured quantity."
            ),
        },
        "gold_evaluation_metrics": {
            stage: {key: value for key, value in result.items() if key != "row_results"}
            for stage, result in gold_results.items()
        },
        "not_evaluable_reason_counts": dict(sorted(reason_counts.items())),
        "review_queue_status_counts": dict(
            sorted(Counter(row["review_queue_action"] for row in review_status).items())
        ),
        "operational_review_inputs": operational_note,
        "offline_only": True,
        "safe_summary_policy": "No RSS text, article URL, API key, or secret value is copied.",
    }
    join_results = [
        row
        for result in gold_results.values()
        for row in result.get("row_results", [])
        if isinstance(row, dict)
    ]
    input_files: dict[str, Path] = {
        "replay_run_manifest": run_dir / "run_manifest.json",
        "r1_gold": args.r1_gold,
        "r1_candidates": run_dir / "r1" / "candidates.jsonl",
        "r3_ranked_candidates": run_dir / "r3" / "ranked_candidates.jsonl",
        "r4_verdicts": run_dir / "r4" / "verdicts.jsonl",
        "gold20_fixture": args.gold20_fixture,
        "gold20_expected_routes": args.gold20_routes,
        "gold20_route_report": args.gold20_route_report,
    }
    if args.operational_r1_candidates is not None:
        input_files["operational_r1_candidates"] = args.operational_r1_candidates
    output_dir = write_evaluation_artifacts(
        output_root=args.output_root,
        evaluation_id=evaluation_id,
        evaluation_summary=summary,
        join_results=join_results,
        review_queue_status=review_status,
        input_files=input_files,
    )
    print(output_dir)
    return 0


def _verify_outputs(run_dir: Path, run_manifest: dict[str, Any]) -> None:
    """Refuse to score replay outputs whose bytes drifted from the run manifest."""
    from hashlib import sha256

    expected = run_manifest.get("output_sha256")
    if not isinstance(expected, dict):
        raise ValueError("GOLD_REPLAY_RUN_OUTPUT_SHA256_MISSING")
    for relative, digest in expected.items():
        path = run_dir / relative
        if not path.is_file():
            raise FileNotFoundError(f"GOLD_REPLAY_OUTPUT_NOT_FOUND:{relative}")
        actual = sha256(path.read_bytes()).hexdigest()
        if actual != str(digest).lower():
            raise ValueError(f"GOLD_REPLAY_OUTPUT_SHA256_MISMATCH:{relative}")


if __name__ == "__main__":
    raise SystemExit(main())
