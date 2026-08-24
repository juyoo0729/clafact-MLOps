"""Write a privacy-safe frozen-Gold R2 flexible-routing ledger and summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.r2_flexible_gold_replay import replay_r2_flexible_gold


DEFAULT_GOLD = REPO / "data" / "model_benchmarks" / "r2_12slot_v2" / "gold.jsonl"
DEFAULT_GOLD_MANIFEST = DEFAULT_GOLD.with_name("manifest.json")


def run(
    *,
    gold_path: Path,
    output_dir: Path,
    gold_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError("R2_FLEXIBLE_GOLD_REPLAY_OUTPUT_EXISTS")
    gold_rows = _read_jsonl(gold_path)
    _verify_gold_contract(gold_path, gold_rows, gold_manifest_path)
    rows, summary = replay_r2_flexible_gold(gold_rows)
    output_dir.mkdir(parents=True, exist_ok=False)
    ledger_path = output_dir / "r2_12slot_flexible_ledger.jsonl"
    ledger_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "artifact": "r2_12slot_flexible_gold_replay_v1",
        "input": {
            "sha256": _sha256(gold_path),
            "record_count": len(gold_rows),
        },
        "outputs": {
            ledger_path.name: _sha256(ledger_path),
            summary_path.name: _sha256(summary_path),
        },
        "evaluation_boundary": summary["evaluation_boundary"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verify_gold_contract(
    gold_path: Path,
    rows: list[dict[str, Any]],
    manifest_path: Path | None,
) -> None:
    if manifest_path is None:
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if len(rows) != int(manifest["gold_count"]):
        raise ValueError("R2_GOLD_RECORD_COUNT_MISMATCH")
    if _sha256(gold_path) != str(manifest["gold_sha256"]):
        raise ValueError("R2_GOLD_SHA256_MISMATCH")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--gold-manifest", type=Path, default=DEFAULT_GOLD_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run(
        gold_path=args.gold,
        output_dir=args.output_dir,
        gold_manifest_path=args.gold_manifest,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
