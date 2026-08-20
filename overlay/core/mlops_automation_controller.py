"""Single-mode, no-retry controller for CLAFACT operations and Gold evaluation.

The controller composes existing Python tools without using a shell.  One
invocation selects exactly one mode, captures child output locally, and emits a
path-free external summary.  It contains no scheduler and never advances to a
second mode or retries a failed/HOLD/PARTIAL run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


SUPPORTED_MODES = (
    "operational_cycle",
    "post_run_gold_evaluation",
    "gold_replay_evaluation",
    "r2_dev_experiment",
)

CONDITIONAL_REPLAY_SCOPE = {
    "conditional_vs_e2e": "CONDITIONAL_REPLAY_NOT_END_TO_END",
    "r1": "R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL",
    "r3": "R3_CONDITIONAL_ON_FROZEN_R2_GOLD",
    "r2_input_source": "FROZEN_GOLD",
    "r4_value_policy": "SNAPSHOT_ONLY_NO_LATEST_API_SUBSTITUTION",
}


class AutomationConfigError(ValueError):
    """Raised when a controller configuration crosses an automation boundary."""


class AutomationRunExistsError(FileExistsError):
    """Raised before work starts when a controller run ID already exists."""


def validate_controller_config(config: Mapping[str, Any]) -> None:
    """Validate global, schedule-free controller configuration."""
    if config.get("schema_version") != "clafact_mlops_automation_controller_v1":
        raise AutomationConfigError("CONTROLLER_SCHEMA_VERSION_INVALID")
    scheduling = config.get("scheduling")
    if not isinstance(scheduling, Mapping) or scheduling.get("enabled") is not False:
        raise AutomationConfigError("SCHEDULING_NOT_ALLOWED")
    modes = config.get("modes")
    if not isinstance(modes, Mapping):
        raise AutomationConfigError("CONTROLLER_MODES_MISSING")
    unknown = set(modes).difference(SUPPORTED_MODES)
    if unknown:
        raise AutomationConfigError("CONTROLLER_MODE_UNKNOWN")
    output_root = config.get("controller_output_root")
    if not isinstance(output_root, str) or not output_root.strip():
        raise AutomationConfigError("CONTROLLER_OUTPUT_ROOT_MISSING")


def build_execution_plan(
    mode: str,
    config: Mapping[str, Any],
    *,
    project_root: str | Path,
    python_executable: str | Path = sys.executable,
) -> dict[str, Any]:
    """Return the exact child commands for one selected mode."""
    validate_controller_config(config)
    if mode not in SUPPORTED_MODES:
        raise AutomationConfigError("CONTROLLER_MODE_UNKNOWN")
    mode_config = config["modes"].get(mode)
    if not isinstance(mode_config, Mapping):
        raise AutomationConfigError(f"MODE_CONFIG_MISSING[{mode}]")
    if mode_config.get("enabled") is not True:
        raise AutomationConfigError(f"MODE_NOT_EXPLICITLY_ENABLED[{mode}]")

    root = Path(project_root).resolve()
    python = str(python_executable)
    if mode == "operational_cycle":
        commands, internal = _operational_plan(mode_config, root, python)
        network_allowed = True
        scope = {
            "result_kind": "OPERATIONAL_COVERAGE_NOT_ACCURACY",
            "fresh_news_accuracy": "PROHIBITED",
        }
    elif mode == "post_run_gold_evaluation":
        commands, internal = _post_run_plan(mode_config, root, python)
        network_allowed = False
        scope = {
            "evaluation_kind": "POST_RUN_OFFLINE_GOLD_EVALUATION",
            "fresh_news_accuracy": "PROHIBITED",
            "coordinate_policy": "HUMAN_READABLE_COORDINATE_NOT_MACHINE_EXACT",
        }
    elif mode == "gold_replay_evaluation":
        commands, internal = _gold_replay_plan(mode_config, root, python)
        network_allowed = False
        scope = dict(CONDITIONAL_REPLAY_SCOPE)
    else:
        commands, internal = _r2_dev_plan(mode_config, root, python)
        network_allowed = True
        scope = {
            "evaluation_kind": "R2_DEV_ONLY_NOT_FINAL_MODEL_PERFORMANCE",
            "split": "dev",
            "downstream_verdict_scoring": "PROHIBITED",
        }
    return {
        "mode": mode,
        "commands": commands,
        "network_calls_allowed": network_allowed,
        "retry_policy": "NONE",
        "scope": scope,
        "internal": internal,
    }


def build_controller_summary(
    *,
    mode: str,
    status: str,
    operational_metrics: Mapping[str, Any],
    gold_evaluation_metrics: Mapping[str, Any],
    evaluation_status: str,
    not_evaluable_reason_counts: Mapping[str, int],
    scope: Mapping[str, Any],
    artifact_hashes: Mapping[str, str],
    executed_tools: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a path-free controller record with separate metric namespaces."""
    if mode not in SUPPORTED_MODES:
        raise AutomationConfigError("CONTROLLER_MODE_UNKNOWN")
    if mode == "operational_cycle" and gold_evaluation_metrics:
        raise AutomationConfigError("FRESH_OPERATIONAL_RESULT_CANNOT_CONTAIN_GOLD_METRICS")
    return {
        "schema_version": "clafact_mlops_automation_controller_run_v1",
        "mode": mode,
        "status": str(status),
        "evaluation_status": str(evaluation_status),
        "operational_metrics": _json_safe_mapping(operational_metrics),
        "gold_evaluation_metrics": _json_safe_mapping(gold_evaluation_metrics),
        "not_evaluable_reason_counts": {
            str(key): int(value) for key, value in sorted(not_evaluable_reason_counts.items())
        },
        "scope": _json_safe_mapping(scope),
        "artifact_hashes": {str(key): str(value) for key, value in sorted(artifact_hashes.items())},
        "executed_tools": [
            {"tool": str(row.get("tool") or ""), "return_code": int(row.get("return_code") or 0)}
            for row in executed_tools
        ],
        "retry_policy": "NONE",
        "scheduling": "NOT_REGISTERED",
        "metric_boundary": "Operational coverage and Gold accuracy are separate namespaces.",
    }


def build_external_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Return the strict external-delivery whitelist, without local paths or raw data."""
    operational = summary.get("operational_metrics")
    gold = summary.get("gold_evaluation_metrics")
    return {
        "mode": str(summary.get("mode") or ""),
        "status": str(summary.get("status") or ""),
        "stage_counts": _stage_counts(operational, gold),
        "metric_values": _metric_values(gold),
        "reason_codes": _reason_code_list(summary.get("not_evaluable_reason_counts")),
        "artifact_hashes": _json_safe_mapping(summary.get("artifact_hashes")),
        "scope": _json_safe_mapping(summary.get("scope")),
    }


def execute_controller_mode(
    *,
    mode: str,
    config: Mapping[str, Any],
    controller_run_id: str,
    project_root: str | Path,
    python_executable: str | Path = sys.executable,
    runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Execute one plan once, preserve failure artifacts, and never retry."""
    root = Path(project_root).resolve()
    plan = build_execution_plan(
        mode,
        config,
        project_root=root,
        python_executable=python_executable,
    )
    safe_run_id = _safe_id(controller_run_id, "CONTROLLER_RUN_ID_INVALID")
    output_root = _path(config["controller_output_root"], root)
    run_dir = output_root / safe_run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise AutomationRunExistsError(f"CONTROLLER_RUN_ALREADY_EXISTS:{safe_run_id}") from error

    run_command = runner or _default_runner
    executed_tools: list[dict[str, Any]] = []
    completed_results: list[subprocess.CompletedProcess[str]] = []
    for command in plan["commands"]:
        completed = run_command(command, root)
        completed_results.append(completed)
        tool_name = Path(command[1]).name if len(command) > 1 else "UNKNOWN"
        executed_tools.append({"tool": tool_name, "return_code": completed.returncode})
        if completed.returncode != 0:
            summary = build_controller_summary(
                mode=mode,
                status="FAILED",
                operational_metrics={},
                gold_evaluation_metrics={},
                evaluation_status="NOT_EVALUABLE" if mode != "operational_cycle" else "NOT_RUN",
                not_evaluable_reason_counts={f"CHILD_COMMAND_FAILED_{tool_name.upper()}": 1},
                scope=plan["scope"],
                artifact_hashes={},
                executed_tools=executed_tools,
            )
            _write_controller_run(run_dir, summary)
            return run_dir, build_external_summary(summary)

    try:
        summary = _collect_success_summary(plan, completed_results, executed_tools)
    except Exception as error:  # fail closed without retaining raw exception text
        summary = build_controller_summary(
            mode=mode,
            status="FAILED",
            operational_metrics={},
            gold_evaluation_metrics={},
            evaluation_status="NOT_EVALUABLE" if mode != "operational_cycle" else "NOT_RUN",
            not_evaluable_reason_counts={f"CONTROLLER_ARTIFACT_READ_{type(error).__name__.upper()}": 1},
            scope=plan["scope"],
            artifact_hashes={},
            executed_tools=executed_tools,
        )
    _write_controller_run(run_dir, summary)
    return run_dir, build_external_summary(summary)


def load_controller_config(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AutomationConfigError("CONTROLLER_CONFIG_MUST_BE_OBJECT")
    validate_controller_config(value)
    return value


def new_controller_run_id(mode: str) -> str:
    if mode not in SUPPORTED_MODES:
        raise AutomationConfigError("CONTROLLER_MODE_UNKNOWN")
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{mode}"


def _operational_plan(config: Mapping[str, Any], root: Path, python: str) -> tuple[list[list[str]], dict[str, Any]]:
    _require_quality_gate_pass(config, root)
    if config.get("rss_approved") is not True:
        raise AutomationConfigError("OPERATIONAL_RSS_APPROVAL_REQUIRED")
    rss_config = _required_file(config, "approved_rss_config", root)
    state_root = _required_path(config, "state_root", root)
    max_records = int(config.get("max_records", 5))
    if not 1 <= max_records <= 5:
        raise AutomationConfigError("OPERATIONAL_MAX_RECORDS_MUST_BE_1_TO_5")
    through = str(config.get("through", "r4"))
    if through not in {"r1", "r2", "r3", "r4"}:
        raise AutomationConfigError("OPERATIONAL_THROUGH_INVALID")
    command = [
        python,
        str(root / "tools" / "run_clafact_operational_cycle.py"),
        "--rss-config",
        str(rss_config),
        "--state-root",
        str(state_root),
        "--through",
        through,
        "--max-records",
        str(max_records),
    ]
    optional_flags = {
        "use_configured_extractor": "--use-configured-extractor",
        "use_kosis_live_discovery": "--use-kosis-live-discovery",
        "use_kosis_api": "--use-kosis-api",
        "prefer_api": "--prefer-api",
    }
    for key, flag in optional_flags.items():
        if config.get(key) is True:
            command.append(flag)
    return [command], {"state_root": state_root}


def _post_run_plan(config: Mapping[str, Any], root: Path, python: str) -> tuple[list[list[str]], dict[str, Any]]:
    required = {
        key: _required_file(config, key, root)
        for key in (
            "run_manifest",
            "r1_gold",
            "r2_gold",
            "r2_predictions",
            "r3_predictions",
            "r4_predictions",
            "gold20_fixture",
            "gold20_routes",
            "gold20_route_report",
        )
    }
    split = str(config.get("r2_split", "dev"))
    if split not in {"train", "dev"}:
        raise AutomationConfigError("R2_LOCKED_TEST_SPLIT_FORBIDDEN")
    evaluation_id = _safe_id(config.get("evaluation_id"), "EVALUATION_ID_INVALID")
    evaluation_root = _required_path(config, "evaluation_root", root)
    output_dir = evaluation_root / evaluation_id
    if output_dir.exists():
        raise AutomationConfigError("EVALUATION_OUTPUT_ALREADY_EXISTS")
    command = [
        python,
        str(root / "tools" / "run_mlops_gold_evaluation.py"),
        "--run-manifest", str(required["run_manifest"]),
        "--r1-gold", str(required["r1_gold"]),
        "--r2-gold", str(required["r2_gold"]),
        "--r2-predictions", str(required["r2_predictions"]),
        "--r2-split", split,
        "--gold20-fixture", str(required["gold20_fixture"]),
        "--gold20-routes", str(required["gold20_routes"]),
        "--gold20-route-report", str(required["gold20_route_report"]),
        "--r3-predictions", str(required["r3_predictions"]),
        "--r4-predictions", str(required["r4_predictions"]),
        "--output-root", str(evaluation_root),
        "--evaluation-id", evaluation_id,
    ]
    return [command], {"evaluation_dir": output_dir}


def _gold_replay_plan(config: Mapping[str, Any], root: Path, python: str) -> tuple[list[list[str]], dict[str, Any]]:
    required = {
        key: _required_file(config, key, root)
        for key in (
            "r1_gold",
            "frozen_r2_claims",
            "concepts",
            "catalog",
            "period_snapshot",
            "gold20_fixture",
            "gold20_routes",
            "gold20_route_report",
        )
    }
    run_id = _safe_id(config.get("run_id"), "GOLD_REPLAY_RUN_ID_INVALID")
    replay_root = _required_path(config, "replay_run_root", root)
    replay_dir = replay_root / run_id
    if replay_dir.exists():
        raise AutomationConfigError("GOLD_REPLAY_RUN_ALREADY_EXISTS")
    evaluation_id = _safe_id(config.get("evaluation_id"), "EVALUATION_ID_INVALID")
    evaluation_root = _required_path(config, "evaluation_root", root)
    evaluation_dir = evaluation_root / evaluation_id
    if evaluation_dir.exists():
        raise AutomationConfigError("EVALUATION_OUTPUT_ALREADY_EXISTS")
    snapshot_dir = _required_path(config, "snapshot_dir", root)
    replay_command = [
        python,
        str(root / "tools" / "run_gold_replay.py"),
        "--run-id", run_id,
        "--r1-gold", str(required["r1_gold"]),
        "--frozen-r2-claims", str(required["frozen_r2_claims"]),
        "--concepts", str(required["concepts"]),
        "--catalog", str(required["catalog"]),
        "--period-snapshot", str(required["period_snapshot"]),
        "--snapshot-dir", str(snapshot_dir),
        "--run-root", str(replay_root),
    ]
    evaluation_command = [
        python,
        str(root / "tools" / "run_gold_replay_evaluation.py"),
        "--replay-run", str(replay_dir),
        "--r1-gold", str(required["r1_gold"]),
        "--gold20-fixture", str(required["gold20_fixture"]),
        "--gold20-routes", str(required["gold20_routes"]),
        "--gold20-route-report", str(required["gold20_route_report"]),
        "--output-root", str(evaluation_root),
        "--evaluation-id", evaluation_id,
    ]
    operational_candidates = config.get("operational_r1_candidates")
    if isinstance(operational_candidates, str) and operational_candidates.strip():
        path = _required_file(config, "operational_r1_candidates", root)
        evaluation_command.extend(["--operational-r1-candidates", str(path)])
    return [replay_command, evaluation_command], {
        "replay_dir": replay_dir,
        "evaluation_dir": evaluation_dir,
    }


def _r2_dev_plan(config: Mapping[str, Any], root: Path, python: str) -> tuple[list[list[str]], dict[str, Any]]:
    _require_quality_gate_pass(config, root)
    if config.get("approved_single_change") is not True:
        raise AutomationConfigError("R2_SINGLE_CHANGE_APPROVAL_REQUIRED")
    for key in ("expected_model", "prompt_version", "change_reason"):
        if not isinstance(config.get(key), str) or not str(config[key]).strip():
            raise AutomationConfigError(f"R2_{key.upper()}_REQUIRED")
    if str(config.get("split", "dev")) != "dev":
        raise AutomationConfigError("R2_LOCKED_TEST_SPLIT_FORBIDDEN")
    provider = str(config.get("provider") or "")
    if provider not in {"hcx", "openai"}:
        raise AutomationConfigError("R2_PROVIDER_INVALID")
    experiment_id = _safe_id(config.get("experiment_id"), "R2_EXPERIMENT_ID_INVALID")
    input_path = _required_file(config, "input", root)
    gold_path = _required_file(config, "gold", root)
    output_root = _required_path(config, "output_root", root)
    prediction_path = output_root / f"{experiment_id}.jsonl"
    report_path = output_root / f"{experiment_id}_report.json"
    tracking_root = output_root / "experiment_tracking" / experiment_id
    protected = (prediction_path, report_path, tracking_root)
    if any(path.exists() for path in protected):
        raise AutomationConfigError("R2_EXPERIMENT_OUTPUT_ALREADY_EXISTS")
    commands: list[list[str]] = []
    smoke_limit = int(config.get("smoke_limit", 0))
    if not 0 <= smoke_limit <= 10:
        raise AutomationConfigError("R2_SMOKE_LIMIT_MUST_BE_0_TO_10")
    if smoke_limit:
        smoke_path = output_root / f"{experiment_id}_smoke{smoke_limit}.jsonl"
        if smoke_path.exists():
            raise AutomationConfigError("R2_EXPERIMENT_OUTPUT_ALREADY_EXISTS")
        commands.append([
            python, str(root / "tools" / "run_r2_model_benchmark_v2.py"),
            "--provider", provider, "--split", "dev", "--input", str(input_path),
            "--limit", str(smoke_limit), "--output", str(smoke_path),
        ])
    commands.extend(
        [
            [
                python, str(root / "tools" / "run_r2_model_benchmark_v2.py"),
                "--provider", provider, "--split", "dev", "--input", str(input_path),
                "--output", str(prediction_path),
            ],
            [
                python, str(root / "tools" / "score_r2_model_benchmark_v2.py"),
                "--predictions", str(prediction_path), "--output", str(report_path),
                "--gold", str(gold_path), "--split", "dev",
            ],
            [
                python, str(root / "tools" / "build_r2_experiment_tracking.py"),
                "--report", str(report_path), "--predictions", str(prediction_path),
                "--gold", str(gold_path), "--output-root", str(tracking_root),
            ],
        ]
    )
    return commands, {
        "prediction_path": prediction_path,
        "report_path": report_path,
        "tracking_root": tracking_root,
        "provider": provider,
        "expected_model": str(config["expected_model"]),
        "prompt_version": str(config["prompt_version"]),
    }


def _collect_success_summary(
    plan: Mapping[str, Any],
    completed: Sequence[subprocess.CompletedProcess[str]],
    executed_tools: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    mode = str(plan["mode"])
    internal = plan["internal"]
    if mode == "operational_cycle":
        path = _last_output_path(completed[-1].stdout)
        payload = _json_object(path)
        operational = _safe_operational_metrics(payload)
        reasons = _operational_reason_counts(operational)
        return build_controller_summary(
            mode=mode,
            status=str(payload.get("cycle_status") or "COMPLETED"),
            operational_metrics=operational,
            gold_evaluation_metrics={},
            evaluation_status="NOT_RUN",
            not_evaluable_reason_counts=reasons,
            scope=plan["scope"],
            artifact_hashes={"operational_cycle": _file_sha256(path)},
            executed_tools=executed_tools,
        )
    if mode in {"post_run_gold_evaluation", "gold_replay_evaluation"}:
        evaluation_dir = Path(internal["evaluation_dir"])
        summary_path = evaluation_dir / "evaluation_summary.json"
        manifest_path = evaluation_dir / "sha256_manifest.json"
        payload = _json_object(summary_path)
        scope = payload.get("scope") if isinstance(payload.get("scope"), Mapping) else plan["scope"]
        if mode == "gold_replay_evaluation":
            _require_replay_scope(scope)
        return build_controller_summary(
            mode=mode,
            status=str(payload.get("evaluation_status") or "NOT_EVALUABLE"),
            operational_metrics=payload.get("operational_metrics") or {},
            gold_evaluation_metrics=payload.get("gold_evaluation_metrics") or {},
            evaluation_status=str(payload.get("evaluation_status") or "NOT_EVALUABLE"),
            not_evaluable_reason_counts=payload.get("not_evaluable_reason_counts") or {},
            scope=scope,
            artifact_hashes={
                "evaluation_summary": _file_sha256(summary_path),
                "evaluation_manifest": _file_sha256(manifest_path),
            },
            executed_tools=executed_tools,
        )

    report_path = Path(internal["report_path"])
    report = _json_object(report_path)
    _validate_r2_run_identity(report, internal)
    response_rate = report.get("response_rate")
    status = "PASS" if isinstance(response_rate, (int, float)) and response_rate >= 0.95 else "HOLD"
    metrics = {
        "r2": {
            "response_rate": {"value": response_rate},
            "parse_status_macro_f1": {
                "value": _nested(report, "parse_status_metrics", "macro_f1")
            },
            "slot_12_macro_accuracy": {
                "value": _nested(report, "all_slot_metrics", "macro_slot_accuracy")
            },
            "whole_claim_exact": {"value": report.get("whole_claim_exact_rate")},
        }
    }
    artifact_hashes = {"r2_report": _file_sha256(report_path)}
    scoreboard = Path(internal["tracking_root"]) / "scoreboard.json"
    if scoreboard.is_file():
        artifact_hashes["r2_scoreboard"] = _file_sha256(scoreboard)
    return build_controller_summary(
        mode=mode,
        status=status,
        operational_metrics={},
        gold_evaluation_metrics=metrics,
        evaluation_status="EVALUATED",
        not_evaluable_reason_counts=report.get("not_evaluable_reason_counts") or {},
        scope=plan["scope"],
        artifact_hashes=artifact_hashes,
        executed_tools=executed_tools,
    )


def _safe_operational_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    rss = payload.get("rss") if isinstance(payload.get("rss"), Mapping) else {}
    pipeline = payload.get("pipeline") if isinstance(payload.get("pipeline"), Mapping) else {}
    stages: dict[str, Any] = {}
    raw_stages = pipeline.get("stages") if isinstance(pipeline, Mapping) else []
    if isinstance(raw_stages, Sequence) and not isinstance(raw_stages, (str, bytes)):
        for row in raw_stages:
            if isinstance(row, Mapping) and isinstance(row.get("stage"), str):
                stages[str(row["stage"])] = {
                    "status": row.get("status"),
                    "counts": _integer_mapping(row.get("counts")),
                    "reason_counts": _integer_mapping(row.get("reason_counts")),
                }
    return {
        "metric_kind": "OPERATIONAL_COVERAGE_NOT_ACCURACY",
        "cycle_status": payload.get("cycle_status"),
        "rss_counts": {
            "new_articles": _integer(rss.get("NEW_ARTICLES")),
            "r1_ready": _integer(rss.get("R1_READY")),
        },
        "stages": stages,
    }


def _operational_reason_counts(operational: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter()
    stages = operational.get("stages")
    if isinstance(stages, Mapping):
        for stage in stages.values():
            if isinstance(stage, Mapping):
                counts.update(_integer_mapping(stage.get("reason_counts")))
    return dict(sorted(counts.items()))


def _require_replay_scope(scope: Mapping[str, Any]) -> None:
    if scope.get("conditional_vs_e2e") != CONDITIONAL_REPLAY_SCOPE["conditional_vs_e2e"]:
        raise AutomationConfigError("GOLD_REPLAY_CONDITIONAL_SCOPE_MISSING")
    if scope.get("r1") != CONDITIONAL_REPLAY_SCOPE["r1"]:
        raise AutomationConfigError("GOLD_REPLAY_R1_SCOPE_MISSING")
    r3_values = scope.get("r3_flags")
    if not isinstance(r3_values, Sequence) or CONDITIONAL_REPLAY_SCOPE["r3"] not in r3_values:
        raise AutomationConfigError("GOLD_REPLAY_R3_SCOPE_MISSING")
    if scope.get("r2_input_source") != CONDITIONAL_REPLAY_SCOPE["r2_input_source"]:
        raise AutomationConfigError("GOLD_REPLAY_R2_INPUT_SCOPE_MISSING")
    if scope.get("r4_value_policy") != CONDITIONAL_REPLAY_SCOPE["r4_value_policy"]:
        raise AutomationConfigError("GOLD_REPLAY_SNAPSHOT_SCOPE_MISSING")


def _validate_r2_run_identity(report: Mapping[str, Any], internal: Mapping[str, Any]) -> None:
    identity = report.get("run_identity")
    if not isinstance(identity, Mapping):
        raise AutomationConfigError("R2_RUN_IDENTITY_MISSING")
    providers = identity.get("providers")
    models = identity.get("models")
    prompt_versions = identity.get("prompt_versions")
    if not isinstance(providers, Sequence) or internal.get("provider") not in providers:
        raise AutomationConfigError("R2_PROVIDER_IDENTITY_MISMATCH")
    if not isinstance(models, Sequence) or internal.get("expected_model") not in models:
        raise AutomationConfigError("R2_EXPECTED_MODEL_MISMATCH")
    if not isinstance(prompt_versions, Sequence) or internal.get("prompt_version") not in prompt_versions:
        raise AutomationConfigError("R2_PROMPT_VERSION_MISMATCH")


def _write_controller_run(run_dir: Path, summary: Mapping[str, Any]) -> None:
    summary_path = run_dir / "controller_summary.json"
    external_path = run_dir / "external_summary.json"
    summary_path.write_text(_json_text(summary), encoding="utf-8")
    external_path.write_text(_json_text(build_external_summary(summary)), encoding="utf-8")
    manifest = {
        "artifact": "clafact_mlops_automation_controller_manifest_v1",
        "outputs": {
            summary_path.name: _file_sha256(summary_path),
            external_path.name: _file_sha256(external_path),
        },
        "path_policy": "No local absolute path is written to controller or external summaries.",
    }
    (run_dir / "sha256_manifest.json").write_text(_json_text(manifest), encoding="utf-8")


def _default_runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_quality_gate_pass(config: Mapping[str, Any], root: Path) -> None:
    report_path = _required_file(config, "quality_gate_report", root)
    report = _json_object(report_path)
    if report.get("status") != "PASS":
        raise AutomationConfigError("QUALITY_GATE_NOT_PASS")


def _required_file(config: Mapping[str, Any], key: str, root: Path) -> Path:
    path = _required_path(config, key, root)
    if not path.is_file():
        raise AutomationConfigError(f"REQUIRED_INPUT_FILE_NOT_FOUND[{key}]")
    return path


def _required_path(config: Mapping[str, Any], key: str, root: Path) -> Path:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AutomationConfigError(f"REQUIRED_PATH_MISSING[{key}]")
    return _path(value, root)


def _path(value: Any, root: Path) -> Path:
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _safe_id(value: Any, error_code: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", text):
        raise AutomationConfigError(error_code)
    return text


def _last_output_path(stdout: str) -> Path:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise AutomationConfigError("CHILD_OUTPUT_PATH_MISSING")
    path = Path(lines[-1])
    if not path.is_file():
        raise AutomationConfigError("CHILD_OUTPUT_ARTIFACT_NOT_FOUND")
    return path


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AutomationConfigError("JSON_OBJECT_REQUIRED")
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return json.loads(json.dumps(dict(value), ensure_ascii=False))


def _integer_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _integer(inner) for key, inner in value.items()}


def _integer(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _stage_counts(operational: Any, gold: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"operational": {}, "gold_join": {}}
    if isinstance(operational, Mapping):
        stages = operational.get("stages")
        if isinstance(stages, Mapping):
            for stage, row in stages.items():
                if isinstance(row, Mapping):
                    counts = row.get("counts") if isinstance(row.get("counts"), Mapping) else row
                    result["operational"][str(stage)] = _integer_mapping(counts)
    if isinstance(gold, Mapping):
        for stage, row in gold.items():
            if isinstance(row, Mapping) and isinstance(row.get("join"), Mapping):
                join = row["join"]
                result["gold_join"][str(stage)] = {
                    "gold_count": _integer(join.get("gold_count")),
                    "joined_count": _integer(join.get("joined_count")),
                }
    return result


def _metric_values(gold: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if not isinstance(gold, Mapping):
        return values
    for stage, result in gold.items():
        if not isinstance(result, Mapping):
            continue
        metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else result
        for name, metric in metrics.items():
            if isinstance(metric, Mapping) and "value" in metric:
                value = metric.get("value")
                if value is None or isinstance(value, (int, float, str, bool)):
                    values[f"{stage}.{name}"] = value
    return dict(sorted(values.items()))


def _reason_code_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    return [
        {"reason_code": str(reason), "count": _integer(count)}
        for reason, count in sorted(value.items())
    ]


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
