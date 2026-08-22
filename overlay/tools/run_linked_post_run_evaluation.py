"""Prepare and run exactly one offline Gold evaluation for a saved pipeline run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.mlops_automation_controller import (  # noqa: E402
    AutomationConfigError,
    AutomationRunExistsError,
    execute_controller_mode,
    load_controller_config,
    prepare_linked_post_run_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--run-manifest", required=True, type=Path)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--config-output", required=True, type=Path)
    parser.add_argument("--controller-run-id", required=True)
    args = parser.parse_args()
    try:
        config_path = prepare_linked_post_run_config(
            template_path=args.template,
            run_manifest_path=args.run_manifest,
            evaluation_id=args.evaluation_id,
            output_path=args.config_output,
        )
        config = load_controller_config(config_path)
        _, external = execute_controller_mode(
            mode="post_run_gold_evaluation",
            config=config,
            controller_run_id=args.controller_run_id,
            project_root=PROJECT,
            python_executable=sys.executable,
        )
        print(json.dumps(external, ensure_ascii=False, sort_keys=True))
        return 1 if str(external.get("status") or "").endswith("FAILED") else 0
    except (AutomationConfigError, AutomationRunExistsError, FileExistsError, FileNotFoundError) as error:
        reason = str(error).split(":", 1)[0]
        print(
            json.dumps(
                {
                    "mode": "post_run_gold_evaluation",
                    "status": "FAILED",
                    "stage_counts": {},
                    "metric_values": {},
                    "reason_codes": [{"reason_code": reason, "count": 1}],
                    "artifact_hashes": {},
                    "scope": {},
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    except Exception as error:
        print(
            json.dumps(
                {
                    "mode": "post_run_gold_evaluation",
                    "status": "FAILED",
                    "stage_counts": {},
                    "metric_values": {},
                    "reason_codes": [
                        {
                            "reason_code": f"POST_RUN_AUTOMATION_{type(error).__name__.upper()}",
                            "count": 1,
                        }
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
