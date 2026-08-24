"""Replay deterministic KOSIS candidate attachment on a frozen R3 split."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from core.r3_kosis_candidate_retrieval import evaluate_candidate_attachment


OUTPUT_COLUMNS = (
    "claim_id",
    "split",
    "r3_reason_code",
    "indicator",
    "unit",
    "frequency",
    "calculation",
    "route_status",
    "candidate_count",
    "candidate_tbl_ids",
    "candidate_tbl_names",
    "candidate_scores",
    "confidence_status",
    "next_retrieval_method",
    "selection_status",
    "mandatory_next_gate",
)


def run(
    *,
    replay_csv: Path,
    candidate_jsonl: Path,
    output_dir: Path,
    baseline_route_csv: Path | None = None,
    split: str | None = "dev",
    top_k: int = 5,
    minimum_lexical_ready_score: float = 0.6,
) -> dict[str, Any]:
    """Write a new immutable, count-safe B-stage evaluation artifact."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    replay_rows = _read_csv(replay_csv)
    candidates_by_indicator = _read_candidates(candidate_jsonl)
    baseline_ids = _baseline_attached_ids(baseline_route_csv)
    rows, summary = evaluate_candidate_attachment(
        replay_rows,
        candidates_by_indicator,
        baseline_attached_claim_ids=baseline_ids,
        split=split,
        top_k=top_k,
        minimum_lexical_ready_score=minimum_lexical_ready_score,
    )

    candidate_output = output_dir / "candidate_attachment.csv"
    summary_output = output_dir / "summary.json"
    _write_csv(candidate_output, rows)
    _write_json(summary_output, summary)
    manifest = {
        "artifact": "r3_kosis_candidate_attachment_v1",
        "inputs": {
            "replay_csv": _file_record(replay_csv),
            "candidate_jsonl": _file_record(candidate_jsonl),
            "baseline_route_csv": (
                _file_record(baseline_route_csv) if baseline_route_csv else None
            ),
        },
        "parameters": {
            "split": split or "all",
            "top_k": top_k,
            "minimum_lexical_ready_score": minimum_lexical_ready_score,
        },
        "outputs": {
            "candidate_attachment_csv": _file_record(candidate_output),
            "summary_json": _file_record(summary_output),
        },
        "evaluation_boundary": summary["accuracy_status"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_candidates(path: Path) -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            indicator = str(payload.get("indicator", "")).strip()
            candidates = payload.get("candidates", [])
            if not indicator or not isinstance(candidates, list):
                raise ValueError(f"invalid candidate record at line {line_number}")
            output[indicator] = [
                candidate for candidate in candidates if isinstance(candidate, dict)
            ]
    return output


def _baseline_attached_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    return {
        row.get("claim_id", "").strip()
        for row in _read_csv(path)
        if row.get("automatic_route_status") == "HOLD_CANDIDATES_ATTACHED"
        and row.get("claim_id", "").strip()
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-csv", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, required=True)
    parser.add_argument("--baseline-route-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--minimum-lexical-ready-score", type=float, default=0.6)
    args = parser.parse_args()
    summary = run(
        replay_csv=args.replay_csv,
        candidate_jsonl=args.candidate_jsonl,
        baseline_route_csv=args.baseline_route_csv,
        output_dir=args.output_dir,
        split=None if args.split.casefold() == "all" else args.split,
        top_k=args.top_k,
        minimum_lexical_ready_score=args.minimum_lexical_ready_score,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
