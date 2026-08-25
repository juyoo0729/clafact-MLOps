"""Build an exact same-Claim replay batch from one execution history CSV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.execution_failure_cohort import (  # noqa: E402
    build_failure_cohort,
    write_failure_cohort,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", required=True, type=Path)
    parser.add_argument("--source-batch", required=True, type=Path)
    parser.add_argument("--failure-cause", required=True)
    parser.add_argument("--expected-history-count", required=True, type=int)
    parser.add_argument("--expected-cohort-count", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cohort = build_failure_cohort(
        history_path=args.history,
        source_batch_path=args.source_batch,
        failure_cause=args.failure_cause,
        expected_history_count=args.expected_history_count,
        expected_cohort_count=args.expected_cohort_count,
    )
    path = write_failure_cohort(args.output, cohort)
    print(f"COHORT_FROZEN:{len(cohort['claims'])}:{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
