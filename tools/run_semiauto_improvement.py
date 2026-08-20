"""Run one fail-closed step of the CLAFACT semi-automatic improvement loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.semiauto_improvement import (  # noqa: E402
    ApprovalRecord,
    ImprovementProposal,
    approve_dev_experiment,
    build_failure_triage,
    build_improvement_proposal,
    evaluate_dev_candidate,
    write_immutable_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    triage = subparsers.add_parser("triage", help="classify aggregate failure reasons")
    triage.add_argument("--input", type=Path, required=True)
    triage.add_argument("--state-root", type=Path, required=True)

    propose = subparsers.add_parser("propose", help="select one dev Gold error slot")
    propose.add_argument("--error-summary", type=Path, required=True)
    propose.add_argument("--state-root", type=Path, required=True)
    propose.add_argument("--proposal-id")

    approve = subparsers.add_parser("approve", help="record explicit approval for one dev experiment")
    approve.add_argument("--proposal", type=Path, required=True)
    approve.add_argument("--scoreboard", type=Path, required=True)
    approve.add_argument("--experiment-id", required=True)
    approve.add_argument("--provider", required=True)
    approve.add_argument("--model", required=True)
    approve.add_argument("--prompt-version", required=True)
    approve.add_argument("--change-scope", choices=("prompt", "rule", "postprocess"), required=True)
    approve.add_argument("--change-reason-code", required=True)
    approve.add_argument("--baseline-source")

    evaluate = subparsers.add_parser("evaluate", help="compare one approved dev candidate with its baseline")
    evaluate.add_argument("--approval", type=Path, required=True)
    evaluate.add_argument("--candidate-report", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "triage":
            return _triage(args.input, args.state_root)
        if args.command == "propose":
            return _propose(args.error_summary, args.state_root, args.proposal_id)
        if args.command == "approve":
            return _approve(args)
        return _evaluate(args.approval, args.candidate_report)
    except (FileExistsError, ValueError, json.JSONDecodeError) as exc:
        reason = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{2,128}", reason):
            reason = "SEMIAUTO_INVALID_INPUT"
        _print_safe({"status": "HOLD", "reason_code": reason})
        return 2


def _triage(input_path: Path, state_root: Path) -> int:
    payload, source_sha256 = _load_json_with_sha(input_path)
    report = build_failure_triage(payload, source_sha256=source_sha256)
    output = state_root / "semiauto_improvements" / "triage" / f"triage-{source_sha256[:12]}.json"
    write_immutable_json(output, report)
    _print_safe(
        {
            "artifact": report.artifact,
            "status": report.status,
            "reason_counts": report.reason_counts,
            "automatic_training_allowed": report.automatic_training_allowed,
            "next_action_code": report.next_action_code,
        }
    )
    return 0


def _propose(error_summary_path: Path, state_root: Path, proposal_id: str | None) -> int:
    error_summary, source_sha256 = _load_json_with_sha(error_summary_path)
    identifier = proposal_id or f"r2-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{source_sha256[:8]}"
    proposal = build_improvement_proposal(
        error_summary,
        source_sha256=source_sha256,
        proposal_id=identifier,
    )
    output = state_root / "semiauto_improvements" / proposal.proposal_id / "proposal.json"
    write_immutable_json(output, proposal)
    _print_safe(
        {
            "artifact": proposal.artifact,
            "proposal_id": proposal.proposal_id,
            "status": proposal.status,
            "target_error_slot": proposal.target_error_slot,
            "observed_error_count": proposal.observed_error_count,
            "automatic_training_allowed": proposal.automatic_training_allowed,
        }
    )
    return 0


def _approve(args: argparse.Namespace) -> int:
    proposal_payload, proposal_sha256 = _load_json_with_sha(args.proposal)
    proposal = ImprovementProposal.model_validate(proposal_payload)
    scoreboard, scoreboard_sha256 = _load_json_with_sha(args.scoreboard)
    approval = approve_dev_experiment(
        proposal,
        scoreboard,
        proposal_sha256=proposal_sha256,
        scoreboard_sha256=scoreboard_sha256,
        experiment_id=args.experiment_id,
        provider=args.provider,
        model=args.model,
        prompt_version=args.prompt_version,
        change_scope=args.change_scope,
        change_reason_code=args.change_reason_code,
        baseline_source=args.baseline_source,
    )
    output = args.proposal.parent / "approval.json"
    write_immutable_json(output, approval)
    _print_safe(
        {
            "artifact": approval.artifact,
            "proposal_id": approval.proposal_id,
            "experiment_id": approval.experiment_id,
            "status": approval.status,
            "allowed_split": approval.allowed_split,
            "automatic_deployment_allowed": approval.automatic_deployment_allowed,
        }
    )
    return 0


def _evaluate(approval_path: Path, candidate_report_path: Path) -> int:
    approval_payload, approval_sha256 = _load_json_with_sha(approval_path)
    approval = ApprovalRecord.model_validate(approval_payload)
    candidate_report, candidate_sha256 = _load_json_with_sha(candidate_report_path)
    decision = evaluate_dev_candidate(
        approval,
        candidate_report,
        approval_sha256=approval_sha256,
        candidate_report_sha256=candidate_sha256,
    )
    output = approval_path.parent / "decision.json"
    write_immutable_json(output, decision)
    _print_safe(
        {
            "artifact": decision.artifact,
            "proposal_id": decision.proposal_id,
            "experiment_id": decision.experiment_id,
            "status": decision.status,
            "reason_codes": decision.reason_codes,
            "automatic_promotion_allowed": decision.automatic_promotion_allowed,
        }
    )
    return 0 if decision.status == "PROMOTION_REVIEW_REQUIRED" else 3


def _load_json_with_sha(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SEMIAUTO_JSON_OBJECT_REQUIRED")
    return payload, hashlib.sha256(raw).hexdigest()


def _print_safe(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
