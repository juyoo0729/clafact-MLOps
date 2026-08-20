"""Run exactly one CLAFACT automation mode from an explicit local config.

This controller never creates a schedule and never retries.  Child stdout and
stderr remain local and are not forwarded; the printed JSON is the strict,
path-free external summary only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.mlops_automation_controller import (  # noqa: E402
    SUPPORTED_MODES,
    AutomationConfigError,
    AutomationRunExistsError,
    build_execution_plan,
    execute_controller_mode,
    load_controller_config,
    new_controller_run_id,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=SUPPORTED_MODES)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--controller-run-id")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the selected mode and print only its safe execution boundary.",
    )
    args = parser.parse_args()

    try:
        config = load_controller_config(args.config)
        plan = build_execution_plan(
            args.mode,
            config,
            project_root=PROJECT,
            python_executable=sys.executable,
        )
        if args.validate_only:
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "status": "READY",
                        "command_count": len(plan["commands"]),
                        "network_calls_allowed": plan["network_calls_allowed"],
                        "retry_policy": plan["retry_policy"],
                        "scheduling": "NOT_REGISTERED",
                        "scope": plan["scope"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        controller_run_id = args.controller_run_id or new_controller_run_id(args.mode)
        _, external_summary = execute_controller_mode(
            mode=args.mode,
            config=config,
            controller_run_id=controller_run_id,
            project_root=PROJECT,
            python_executable=sys.executable,
        )
        print(json.dumps(external_summary, ensure_ascii=False, sort_keys=True))
        return 1 if external_summary["status"] == "FAILED" else 0
    except (AutomationConfigError, AutomationRunExistsError, FileNotFoundError, json.JSONDecodeError) as error:
        reason_code = str(error).split(":", 1)[0]
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "status": "FAILED",
                    "stage_counts": {},
                    "metric_values": {},
                    "reason_codes": [{"reason_code": reason_code, "count": 1}],
                    "artifact_hashes": {},
                    "scope": {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    except Exception as error:  # fail closed without printing raw child/provider/path details
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "status": "FAILED",
                    "stage_counts": {},
                    "metric_values": {},
                    "reason_codes": [
                        {"reason_code": f"CONTROLLER_{type(error).__name__.upper()}", "count": 1}
                    ],
                    "artifact_hashes": {},
                    "scope": {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
