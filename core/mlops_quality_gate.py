"""Redacted pre-run quality gate for a CLAFACT MLOps Shadow run."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict


_PYTEST_PASSED = re.compile(r"(?P<count>\d+)\s+passed")
_PYTEST_FAILED = re.compile(r"(?P<count>\d+)\s+failed")
_PROVIDER_SECRET_NAMES = {
    "hcx": "HCX_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class QualityGateReport(BaseModel):
    """Count-only report safe to hand to Hermes after a local test run."""

    model_config = ConfigDict(frozen=True)

    artifact: str = "clafact_mlops_quality_gate_v1"
    status: str
    reason_code: str | None = None
    python_version: str
    checks: dict[str, str]
    pytest_passed_count: int | None = None
    pytest_failed_count: int | None = None


Runner = Callable[[Sequence[str], Path, int], tuple[int, str]]


def build_quality_gate_report(
    *,
    workspace: Path,
    required_secret_names: Sequence[str],
    environment: Mapping[str, str],
    runner: Runner,
    timeout_seconds: int = 180,
    python_version: tuple[int, int, int] | None = None,
    claim_provider: str | None = None,
) -> QualityGateReport:
    """Run the test suite and return only stable health counts and reason codes."""
    version = python_version or sys.version_info[:3]
    checks = {
        "python_runtime": "PASS" if version >= (3, 12, 0) else "HOLD_RUNTIME",
        "workspace": "PASS" if workspace.is_dir() else "HOLD_WORKSPACE",
        "secrets": "PASS" if all(environment.get(name) for name in required_secret_names) else "HOLD_SECRET_STORE",
    }
    if checks["python_runtime"] != "PASS":
        return _held("PYTHON_3_12_REQUIRED", version, checks)
    if checks["workspace"] != "PASS":
        return _held("WORKSPACE_NOT_FOUND", version, checks)
    if checks["secrets"] != "PASS":
        return _held("REQUIRED_SECRET_MISSING", version, checks)

    if claim_provider is not None:
        provider = claim_provider.strip().casefold()
        secret_name = _PROVIDER_SECRET_NAMES.get(provider)
        if secret_name is None:
            checks["claim_provider"] = "HOLD_UNSUPPORTED"
            return _held("CLAIM_PROVIDER_UNSUPPORTED", version, checks)
        checks["claim_provider"] = "PASS"
        if not environment.get(secret_name):
            checks["claim_provider_secret"] = "HOLD_SECRET_STORE"
            return _held("CLAIM_PROVIDER_SECRET_MISSING", version, checks)
        checks["claim_provider_secret"] = "PASS"

    try:
        exit_code, output = runner((sys.executable, "-m", "pytest", "-q"), workspace, timeout_seconds)
    except TimeoutError:
        checks["pytest"] = "HOLD_TIMEOUT"
        return _held("PYTEST_TIMEOUT", version, checks)
    passed = _count(_PYTEST_PASSED, output)
    failed = _count(_PYTEST_FAILED, output) or 0
    if exit_code != 0:
        checks["pytest"] = "HOLD_FAILED"
        return QualityGateReport(
            status="HOLD",
            reason_code="PYTEST_FAILED",
            python_version=_version_text(version),
            checks=checks,
            pytest_passed_count=passed,
            pytest_failed_count=failed,
        )
    if passed is None or passed <= 0:
        checks["pytest"] = "HOLD_UNPARSEABLE"
        return _held("PYTEST_SUMMARY_UNPARSEABLE", version, checks)
    checks["pytest"] = "PASS"
    return QualityGateReport(
        status="PASS",
        python_version=_version_text(version),
        checks=checks,
        pytest_passed_count=passed,
        pytest_failed_count=failed,
    )


def _held(reason_code: str, version: tuple[int, int, int], checks: dict[str, str]) -> QualityGateReport:
    return QualityGateReport(
        status="HOLD",
        reason_code=reason_code,
        python_version=_version_text(version),
        checks=checks,
    )


def _count(pattern: re.Pattern[str], output: str) -> int | None:
    match = pattern.search(output)
    return int(match.group("count")) if match else None


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(value) for value in version)
