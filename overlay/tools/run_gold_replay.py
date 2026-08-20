"""Run the Gold replay (R1 Gold30 sentence diagnostic + conditional R3/R4 Gold20).

Artifacts are written only under ``data/gold_replay_runs/<run_id>/`` with an
exist-check, so no operational MLOps run or previous evaluation artifact is
ever overwritten.  The R3/R4 legs consume frozen R2 Gold 12-slot claims and
are therefore CONDITIONAL replays, not end-to-end results.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.data_loader import (  # noqa: E402
    load_kosis_catalog,
    load_period_availability_snapshot,
    load_standard_concepts,
)
from core.gold_replay import (  # noqa: E402
    GoldReplaySentenceHashMissingError,
    R2_INPUT_SOURCE_FROZEN_GOLD,
    build_frozen_r2_gold_claims,
    replay_r1_gold30,
    replay_r3_gold20,
    replay_r4_gold20,
)


DEFAULT_RUN_ROOT = PROJECT / "data" / "gold_replay_runs"
DEFAULT_FROZEN_CLAIMS = PROJECT / "tests" / "goldset" / "fixtures" / "pilot20_r2_frozen_gold_claims_v1.json"
DEFAULT_CONCEPTS = PROJECT / "data" / "semantic_standard" / "seed_concepts.json"
DEFAULT_CATALOG = PROJECT / "data" / "kosis_catalog" / "catalog_350.json"
DEFAULT_PERIODS = PROJECT / "data" / "kosis_catalog" / "period_availability" / "period_availability_v1.json"
DEFAULT_SNAPSHOT_DIR = PROJECT / "data" / "kosis_snapshots"


class GoldReplayRunExistsError(FileExistsError):
    """Raised when a replay run would overwrite an existing run directory."""


def write_gold_replay_run(
    *,
    run_root: Path,
    run_id: str,
    r1_result: dict[str, Any],
    r3_result: dict[str, Any],
    r4_result: dict[str, Any],
    input_files: dict[str, Path],
    gold_sources: dict[str, Any],
) -> Path:
    """Write one immutable Gold replay run directory with SHA-256 manifests."""
    safe_id = run_id.strip()
    if not safe_id or Path(safe_id).name != safe_id:
        raise ValueError("GOLD_REPLAY_RUN_ID_MUST_BE_A_SAFE_DIRECTORY_NAME")
    for candidate in r1_result.get("candidates", []):
        value = str(candidate.get("sentence_hash") or "")
        if len(value) != 64:
            raise GoldReplaySentenceHashMissingError(
                f"R1_REPLAY_OUTPUT_SENTENCE_HASH_MISSING:{candidate.get('claim_candidate_id')}"
            )
    run_dir = Path(run_root) / safe_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise GoldReplayRunExistsError(f"GOLD_REPLAY_RUN_ALREADY_EXISTS:{safe_id}") from error

    stage_outputs: dict[str, dict[str, Any]] = {}
    stage_outputs["r1"] = _write_stage(
        run_dir / "r1",
        stage="r1",
        run_id=safe_id,
        files={"candidates.jsonl": r1_result.get("candidates", [])},
        status="COMPLETED",
        counts={
            "input": r1_result.get("input_count", 0),
            "candidates": len(r1_result.get("candidates", [])),
            "holds": len(r1_result.get("holds", [])),
        },
        hold_reason_counts=r1_result.get("hold_reason_counts", {}),
        scope={
            "replay_kind": r1_result.get("replay_kind"),
            **(r1_result.get("scope") or {}),
        },
    )
    stage_outputs["r3"] = _write_stage(
        run_dir / "r3",
        stage="r3",
        run_id=safe_id,
        files={
            "ranked_candidates.jsonl": r3_result.get("records", []),
            "holds.jsonl": r3_result.get("holds", []),
        },
        status="COMPLETED",
        counts={
            "input": r3_result.get("input_count", 0),
            "records": len(r3_result.get("records", [])),
            "auto": sum(1 for row in r3_result.get("records", []) if row.get("route_status") == "AUTO"),
            "holds": len(r3_result.get("holds", [])),
        },
        hold_reason_counts=r3_result.get("hold_reason_counts")
        or dict(sorted(Counter(str(h.get("reason_code")) for h in r3_result.get("holds", [])).items())),
        scope={
            "scope_flags": r3_result.get("scope_flags", []),
            "r2_input_source": r3_result.get("r2_input_source"),
        },
    )
    stage_outputs["r4"] = _write_stage(
        run_dir / "r4",
        stage="r4",
        run_id=safe_id,
        files={
            "verdicts.jsonl": r4_result.get("verdict_rows", []),
            "provenance.jsonl": r4_result.get("provenance_rows", []),
        },
        status="COMPLETED",
        counts={
            "input": r4_result.get("input_count", 0),
            "verdicts": len(r4_result.get("verdict_rows", [])),
            "auto": sum(
                1 for row in r4_result.get("verdict_rows", []) if row.get("route_status") == "AUTO"
            ),
            "holds": sum(
                1 for row in r4_result.get("verdict_rows", []) if row.get("route_status") == "HOLD"
            ),
        },
        hold_reason_counts=r4_result.get("hold_reason_counts")
        or dict(
            sorted(
                Counter(
                    str(row.get("reason_code"))
                    for row in r4_result.get("verdict_rows", [])
                    if row.get("route_status") == "HOLD"
                ).items()
            )
        ),
        scope={"snapshot_only": bool(r4_result.get("snapshot_only"))},
    )

    input_hashes = {
        name: {"file_name": Path(path).name, "sha256": _file_sha256(Path(path))}
        for name, path in sorted(input_files.items())
    }
    output_hashes = {
        f"{stage}/{file_name}": digest
        for stage, stage_output in stage_outputs.items()
        for file_name, digest in stage_output["output_sha256"].items()
    }
    run_manifest = {
        "schema_version": "clafact_gold_replay_run_v1",
        "run_id": safe_id,
        "status": "COMPLETED",
        "gold_sources": {**gold_sources, "input_files": input_hashes},
        "input_sha256": {name: value["sha256"] for name, value in input_hashes.items()},
        "output_sha256": output_hashes,
        "stages": [
            {"stage": stage, **{k: v for k, v in stage_output.items() if k != "output_sha256"}}
            for stage, stage_output in stage_outputs.items()
        ],
        "scope": {
            "conditional_vs_e2e": "CONDITIONAL_REPLAY_NOT_END_TO_END",
            "r1": "R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL",
            "r2_input_source": R2_INPUT_SOURCE_FROZEN_GOLD,
            "r4_value_policy": "SNAPSHOT_ONLY_NO_LATEST_API_SUBSTITUTION",
        },
        "isolation": "Writes only under data/gold_replay_runs/<run_id>; never touches operational MLOps runs or prior evaluations.",
    }
    (run_dir / "run_manifest.json").write_text(_json_text(run_manifest), encoding="utf-8")
    return run_dir


def _write_stage(
    stage_dir: Path,
    *,
    stage: str,
    run_id: str,
    files: dict[str, list[dict[str, Any]]],
    status: str,
    counts: dict[str, int],
    hold_reason_counts: dict[str, int],
    scope: dict[str, Any],
) -> dict[str, Any]:
    stage_dir.mkdir(parents=True, exist_ok=False)
    output_sha256: dict[str, str] = {}
    for file_name, rows in files.items():
        path = stage_dir / file_name
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
        output_sha256[file_name] = _file_sha256(path)
    manifest = {
        "stage": stage,
        "run_id": run_id,
        "status": status,
        "counts": counts,
        "hold_reason_counts": dict(sorted(hold_reason_counts.items())),
        "scope": scope,
        "output_sha256": output_sha256,
    }
    (stage_dir / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="New immutable run directory name")
    parser.add_argument("--r1-gold", required=True, type=Path, help="R1 Gold30 CSV (row_id, sentence, sentence_hash, article_date, is_claim_human)")
    parser.add_argument("--frozen-r2-claims", type=Path, default=DEFAULT_FROZEN_CLAIMS)
    parser.add_argument("--concepts", type=Path, default=DEFAULT_CONCEPTS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--period-snapshot", type=Path, default=DEFAULT_PERIODS)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    args = parser.parse_args()

    with args.r1_gold.open(encoding="utf-8-sig", newline="") as handle:
        r1_gold_rows = list(csv.DictReader(handle))
    fixture_rows = json.loads(args.frozen_r2_claims.read_text(encoding="utf-8-sig"))
    if not isinstance(fixture_rows, list):
        raise ValueError("FROZEN_R2_GOLD_FIXTURE_MUST_BE_A_LIST")

    r1_result = replay_r1_gold30(r1_gold_rows)

    claims = build_frozen_r2_gold_claims(fixture_rows)
    concepts = load_standard_concepts(args.concepts)
    catalog = load_kosis_catalog(args.catalog)
    period_manifest = args.period_snapshot.with_name(args.period_snapshot.stem + ".manifest.json")
    try:
        period_availability = load_period_availability_snapshot(args.period_snapshot, period_manifest)
    except Exception:
        period_availability = None
    r3_result = replay_r3_gold20(
        claims,
        concepts=concepts,
        catalog=catalog,
        period_availability=period_availability,
    )

    snapshot_paths = sorted(args.snapshot_dir.glob("*.json"))
    r4_result = replay_r4_gold20(
        ready=r3_result["ready"],
        carried_holds=r3_result["holds"],
        snapshot_paths=snapshot_paths,
    )

    run_dir = write_gold_replay_run(
        run_root=args.run_root,
        run_id=args.run_id,
        r1_result=r1_result,
        r3_result=r3_result,
        r4_result=r4_result,
        input_files={
            "r1_gold": args.r1_gold,
            "frozen_r2_claims": args.frozen_r2_claims,
            "concepts": args.concepts,
            "catalog": args.catalog,
        },
        gold_sources={
            "r1_gold_file": args.r1_gold.name,
            "frozen_r2_claims_file": args.frozen_r2_claims.name,
            "snapshot_count": len(snapshot_paths),
        },
    )
    summary = {
        "run_dir": str(run_dir),
        "r1": {"input": r1_result["input_count"], "candidates": len(r1_result["candidates"]), "holds": len(r1_result["holds"])},
        "r3": {"input": r3_result["input_count"], "auto": sum(1 for r in r3_result["records"] if r["route_status"] == "AUTO"), "holds": len(r3_result["holds"])},
        "r4": {
            "verdicts": len(r4_result["verdict_rows"]),
            "auto": sum(1 for r in r4_result["verdict_rows"] if r.get("route_status") == "AUTO"),
            "hold": sum(1 for r in r4_result["verdict_rows"] if r.get("route_status") == "HOLD"),
            "verdict_counts": dict(sorted(Counter(r.get("verdict") for r in r4_result["verdict_rows"]).items())),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_text(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
