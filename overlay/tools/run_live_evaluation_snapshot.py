"""Write an on-demand snapshot from stored CLAFACT operational and Gold artifacts."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.live_evaluation_snapshot import write_live_evaluation_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--snapshot-id")
    args = parser.parse_args()

    snapshot_id = args.snapshot_id or f"live-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    output_dir = write_live_evaluation_snapshot(
        state_root=args.state_root,
        output_root=args.output_root,
        snapshot_id=snapshot_id,
    )
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

