"""Run CLAFACT regression tests and write a count-only MLOps quality report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.settings import Settings, load_environment_file  # noqa: E402
from core.mlops_quality_gate import build_quality_gate_report  # noqa: E402


def _runner(command: tuple[str, ...], workspace: Path, timeout_seconds: int) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    # The detailed test output may contain local paths. It is used only to
    # extract test counts and is never written to the Hermes-facing report.
    return completed.returncode, completed.stdout + "\n" + completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=REPO)
    parser.add_argument("--require-secret", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--env-file", type=Path, default=REPO / ".env")
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 900:
        parser.error("--timeout-seconds must be between 1 and 900")

    load_environment_file(args.env_file, os.environ)
    # Settings applies the same repository/workspace fallback used by the
    # application, without returning or logging any secret values.
    settings = Settings()
    report = build_quality_gate_report(
        workspace=args.workspace,
        required_secret_names=tuple(args.require_secret),
        environment=os.environ,
        runner=_runner,
        timeout_seconds=args.timeout_seconds,
        claim_provider=settings.claim_provider,
    )
    gate_dir = args.state_root / "quality_gates"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = gate_dir / f"{gate_id}.json"
    payload = {
        **report.model_dump(mode="json"),
        "created_at": datetime.now(UTC).isoformat(),
        "execution_scope": "code regression only; no RSS, article, LLM, KOSIS value, calculation, or Verdict execution",
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.status, "reason_code": report.reason_code, "output": str(output.resolve())}, ensure_ascii=False))
    return 0 if report.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
