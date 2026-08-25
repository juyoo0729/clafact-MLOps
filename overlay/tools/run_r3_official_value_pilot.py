"""Run a bounded official KOSIS metadata and current-value pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.kosis_openapi_transport import get_meta
from core.kosis_value_transport import get_parameter_data
from core.r3_official_value_pilot import (
    ACCURACY_STATUS,
    PilotTarget,
    VERDICT_STATUS,
    evaluate_official_value_rows,
    prepare_official_coordinate,
    prepare_registered_control_coordinate,
    select_single_guard_controls,
    select_pilot_targets,
)
from tools.run_r3_hard_guard_readiness import read_claim_slots_without_article_text


RESULT_COLUMNS = (
    "claim_id",
    "split",
    "catalog_scope",
    "org_id",
    "table_id",
    "indicator",
    "candidate_claim_count",
    "metadata_status",
    "coordinate_status",
    "official_value_status",
    "reason_code",
    "item_id",
    "period_type",
    "period",
    "dimension_code_count",
    "response_row_count",
    "official_value",
    "official_unit",
    "metadata_response_sha256",
    "value_response_sha256",
    "retrieved_at",
    "verdict_status",
)


def run(
    *,
    ledger_xlsx: Path,
    candidate_attachment_csv: Path,
    candidate_identity_jsonl: Path,
    catalog_json: Path,
    output_dir: Path,
    api_key: str,
    metadata_fetcher: Callable[..., Any] = get_meta,
    value_fetcher: Callable[..., Any] = get_parameter_data,
    guard_readiness_csv: Path | None = None,
    registered_coordinates_json: Path | None = None,
    member_codes_json: Path | None = None,
    split: str = "dev",
    limit: int = 20,
    control_limit: int = 0,
    registered_control_limit: int = 0,
    sheet_name: str = "1542건 원장",
) -> dict[str, Any]:
    """Collect structure and one exact current official value per ready table."""
    if not api_key:
        raise RuntimeError("KOSIS_API_KEY_NOT_CONFIGURED")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    claim_slots = read_claim_slots_without_article_text(
        ledger_xlsx, sheet_name=sheet_name
    )
    candidate_rows = _read_csv(candidate_attachment_csv)
    identities = _read_candidate_identities(candidate_identity_jsonl)
    catalog_records = _catalog_records(catalog_json)
    catalog_ids = set(catalog_records)
    effective_control_limit = min(max(control_limit, 0), limit)
    expansion_limit = limit - effective_control_limit
    expansion_targets = (
        select_pilot_targets(
            candidate_rows,
            catalog_table_ids=catalog_ids,
            identity_by_table=identities,
            claim_slots_by_id=claim_slots,
            split=split,
            limit=expansion_limit,
        )
        if expansion_limit > 0
        else []
    )
    control_targets = (
        select_single_guard_controls(
            _read_csv(guard_readiness_csv),
            candidate_rows,
            identity_by_table=identities,
            claim_slots_by_id=claim_slots,
            split=split,
            limit=effective_control_limit,
        )
        if guard_readiness_csv is not None
        else []
    )
    registered_profiles = (
        _read_json_array(registered_coordinates_json)
        if registered_coordinates_json is not None
        else []
    )
    member_code_rows = (
        _read_json_array(member_codes_json)
        if member_codes_json is not None
        else []
    )
    registered_profile_by_table = {
        str(row.get("tbl_id", "")).strip(): row
        for row in registered_profiles
        if str(row.get("tbl_id", "")).strip()
    }
    registered_targets: list[PilotTarget] = []
    if registered_control_limit > 0:
        for table_id in sorted(registered_profile_by_table):
            catalog = catalog_records.get(table_id)
            if catalog is None:
                continue
            aliases = registered_profile_by_table[table_id].get("indicator_aliases", [])
            indicator = (
                str(aliases[0]).strip()
                if isinstance(aliases, list) and aliases
                else ""
            )
            registered_targets.append(
                PilotTarget(
                    claim_id=f"REGISTERED::{table_id}",
                    split="control",
                    indicator=indicator,
                    table_id=table_id,
                    table_name=str(
                        catalog.get("TBL_NM_META")
                        or catalog.get("TBL_NM_INPUT")
                        or ""
                    ).strip(),
                    org_id=str(catalog.get("ORG_ID", "")).strip(),
                    catalog_scope="REGISTERED_EVIDENCE_CONTROL",
                    candidate_claim_count=0,
                )
            )
            if len(registered_targets) >= registered_control_limit:
                break
    targets = expansion_targets + control_targets + registered_targets

    results: list[dict[str, object]] = []
    metadata_snapshots: list[dict[str, object]] = []
    value_snapshots: list[dict[str, object]] = []
    api_call_count = 0
    for target in targets:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        metadata_responses: dict[str, object] = {}
        metadata_errors: dict[str, str] = {}
        for meta_type in ("ITM", "PRD"):
            api_call_count += 1
            try:
                response = metadata_fetcher(
                    api_key,
                    target.org_id,
                    target.table_id,
                    meta_type=meta_type,
                    retries=1,
                    timeout_seconds=10,
                )
                metadata_responses[meta_type] = response
            except Exception as error:  # transport boundary records safe code only
                metadata_responses[meta_type] = None
                metadata_errors[meta_type] = _safe_error(error)
            metadata_snapshots.append(
                {
                    "request": {
                        "org_id": target.org_id,
                        "table_id": target.table_id,
                        "meta_type": meta_type,
                    },
                    "retrieved_at": retrieved_at,
                    "status": (
                        "SUCCESS"
                        if metadata_responses[meta_type] is not None
                        else "HOLD"
                    ),
                    "error_code": metadata_errors.get(meta_type, ""),
                    "response_sha256": _payload_hash(
                        metadata_responses[meta_type]
                    ),
                    "response": metadata_responses[meta_type],
                }
            )

        metadata_hash = _payload_hash(metadata_responses)
        slots = claim_slots.get(target.claim_id, {})
        if metadata_errors:
            result = _base_result(target, retrieved_at)
            result.update(
                {
                    "metadata_status": "HOLD_METADATA_FETCH_FAILED",
                    "coordinate_status": "HOLD_COORDINATE_UNRESOLVED",
                    "official_value_status": "HOLD",
                    "reason_code": " | ".join(
                        f"{key}:{metadata_errors[key]}"
                        for key in sorted(metadata_errors)
                    ),
                    "metadata_response_sha256": metadata_hash,
                }
            )
            results.append(result)
            continue

        item_rows = metadata_responses.get("ITM")
        period_rows = metadata_responses.get("PRD")
        if target.catalog_scope == "REGISTERED_EVIDENCE_CONTROL":
            coordinate = prepare_registered_control_coordinate(
                target,
                registered_profile_by_table[target.table_id],
                member_code_rows,
                item_rows if isinstance(item_rows, list) else [],
                period_rows if isinstance(period_rows, list) else [],
            )
        else:
            coordinate = prepare_official_coordinate(
                target,
                slots,
                item_rows if isinstance(item_rows, list) else [],
                period_rows if isinstance(period_rows, list) else [],
            )
        result = _base_result(target, retrieved_at)
        result.update(
            {
                "metadata_status": "OFFICIAL_METADATA_FETCHED",
                "coordinate_status": coordinate.status,
                "reason_code": coordinate.reason_code,
                "item_id": coordinate.item_id,
                "period_type": coordinate.period_type,
                "period": coordinate.period,
                "dimension_code_count": len(coordinate.object_codes),
                "metadata_response_sha256": metadata_hash,
            }
        )
        if coordinate.status != "COORDINATE_READY_FOR_VALUE_FETCH":
            results.append(result)
            continue

        api_call_count += 1
        try:
            value_rows = value_fetcher(
                api_key,
                target.org_id,
                target.table_id,
                coordinate.item_id,
                coordinate.period_type,
                coordinate.period,
                coordinate.period,
                list(coordinate.object_codes),
                retries=1,
            )
            value_error = ""
        except Exception as error:  # transport boundary records safe code only
            value_rows = None
            value_error = _safe_error(error)
        value_hash = _payload_hash(value_rows)
        value_snapshots.append(
            {
                "request": {
                    "org_id": target.org_id,
                    "table_id": target.table_id,
                    "item_id": coordinate.item_id,
                    "period_type": coordinate.period_type,
                    "start_period": coordinate.period,
                    "end_period": coordinate.period,
                    "object_codes": list(coordinate.object_codes),
                },
                "retrieved_at": retrieved_at,
                "status": "SUCCESS" if value_rows is not None else "HOLD",
                "error_code": value_error,
                "response_sha256": value_hash,
                "response": value_rows,
            }
        )
        if value_rows is None:
            value_result = {
                "official_value_status": "HOLD",
                "reason_code": value_error or "KOSIS_VALUE_FETCH_FAILED",
                "official_value": "",
                "official_unit": "",
                "response_row_count": 0,
                "verdict_status": VERDICT_STATUS,
            }
        else:
            value_result = evaluate_official_value_rows(
                coordinate,
                slots,
                value_rows if isinstance(value_rows, list) else [],
            )
        result.update(value_result)
        result["value_response_sha256"] = value_hash
        results.append(result)

    results_path = output_dir / "pilot_results.csv"
    metadata_path = output_dir / "metadata_snapshots.jsonl"
    values_path = output_dir / "value_snapshots.jsonl"
    summary_path = output_dir / "summary.json"
    _write_csv(results_path, results)
    _write_jsonl(metadata_path, metadata_snapshots)
    _write_jsonl(values_path, value_snapshots)
    reason_counts = Counter(
        str(row.get("reason_code", ""))
        for row in results
        if row.get("reason_code")
    )
    summary = {
        "artifact": "r3_official_value_pilot_v1",
        "split": split,
        "selection_limit": limit,
        "expansion_table_count": sum(
            target.catalog_scope == "EXPANSION_OUTSIDE_CATALOG"
            for target in targets
        ),
        "control_table_count": sum(
            target.catalog_scope == "CONTROL_SINGLE_GUARD_SURVIVOR"
            for target in targets
        ),
        "registered_control_count": sum(
            target.catalog_scope == "REGISTERED_EVIDENCE_CONTROL"
            for target in targets
        ),
        "selected_table_count": len(targets),
        "metadata_fetched_count": sum(
            row["metadata_status"] == "OFFICIAL_METADATA_FETCHED"
            for row in results
        ),
        "coordinate_ready_count": sum(
            row["coordinate_status"] == "COORDINATE_READY_FOR_VALUE_FETCH"
            for row in results
        ),
        "official_value_fetched_count": sum(
            row["official_value_status"] == "OFFICIAL_VALUE_FETCHED"
            for row in results
        ),
        "hold_reason_counts": dict(sorted(reason_counts.items())),
        "api_call_count": api_call_count,
        "source_context_status": "NOT_READ_ARTICLE_TEXT",
        "official_value_scope": "CURRENT_VALUE_SNAPSHOT_ARTICLE_ASOF_UNCHECKED",
        "table_selection_status": "HOLD_NO_TABLE_SELECTED_FROM_VALUE_AGREEMENT",
        "verdict_status": VERDICT_STATUS,
        "accuracy_status": ACCURACY_STATUS,
    }
    _write_json(summary_path, summary)
    manifest = {
        "artifact": "r3_official_value_pilot_v1",
        "inputs": {
            "ledger_xlsx": _file_record(ledger_xlsx),
            "candidate_attachment_csv": _file_record(candidate_attachment_csv),
            "candidate_identity_jsonl": _file_record(candidate_identity_jsonl),
            "catalog_json": _file_record(catalog_json),
            "guard_readiness_csv": (
                _file_record(guard_readiness_csv)
                if guard_readiness_csv is not None
                else None
            ),
            "registered_coordinates_json": (
                _file_record(registered_coordinates_json)
                if registered_coordinates_json is not None
                else None
            ),
            "member_codes_json": (
                _file_record(member_codes_json)
                if member_codes_json is not None
                else None
            ),
        },
        "parameters": {
            "split": split,
            "limit": limit,
            "control_limit": effective_control_limit,
            "registered_control_limit": registered_control_limit,
        },
        "secrets": {"kosis_api_key": "PRESENT_NOT_RECORDED"},
        "privacy": {"article_text_column": "EXCLUDED_NOT_READ"},
        "outputs": {
            "pilot_results_csv": _file_record(results_path),
            "metadata_snapshots_jsonl": _file_record(metadata_path),
            "value_snapshots_jsonl": _file_record(values_path),
            "summary_json": _file_record(summary_path),
        },
        "evaluation_boundary": ACCURACY_STATUS,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def _base_result(target, retrieved_at: str) -> dict[str, object]:
    return {
        "claim_id": target.claim_id,
        "split": target.split,
        "catalog_scope": target.catalog_scope,
        "org_id": target.org_id,
        "table_id": target.table_id,
        "indicator": target.indicator,
        "candidate_claim_count": target.candidate_claim_count,
        "metadata_status": "HOLD",
        "coordinate_status": "HOLD_COORDINATE_UNRESOLVED",
        "official_value_status": "HOLD",
        "reason_code": "",
        "item_id": "",
        "period_type": "",
        "period": "",
        "dimension_code_count": 0,
        "response_row_count": 0,
        "official_value": "",
        "official_unit": "",
        "metadata_response_sha256": "",
        "value_response_sha256": "",
        "retrieved_at": retrieved_at,
        "verdict_status": VERDICT_STATUS,
    }


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_candidate_identities(path: Path) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            for candidate in payload.get("candidates", []):
                if not isinstance(candidate, Mapping):
                    continue
                table_id = str(candidate.get("tbl_id", "")).strip()
                org_id = str(candidate.get("org_id", "")).strip()
                if table_id and org_id:
                    output.setdefault(
                        table_id,
                        {
                            "org_id": org_id,
                            "tbl_name": str(candidate.get("tbl_name", "")).strip(),
                        },
                    )
    return output


def _catalog_table_ids(path: Path) -> set[str]:
    return set(_catalog_records(path))


def _catalog_records(path: Path) -> dict[str, dict[str, object]]:
    return {
        str(row.get("TBL_ID", "")).strip(): row
        for row in _read_json_array(path)
        if str(row.get("TBL_ID", "")).strip()
    }


def _read_json_array(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"expected a JSON array of objects: {path}")
    return payload


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _payload_hash(payload: object) -> str:
    if payload is None:
        return ""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _safe_error(error: Exception) -> str:
    text = str(error).strip()
    if text.startswith("KOSIS_") or text.startswith("OFFICIAL_"):
        return text
    return type(error).__name__.upper()


def main() -> int:
    from config.settings import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-xlsx", type=Path, required=True)
    parser.add_argument("--candidate-attachment-csv", type=Path, required=True)
    parser.add_argument("--candidate-identity-jsonl", type=Path, required=True)
    parser.add_argument("--catalog-json", type=Path, required=True)
    parser.add_argument("--guard-readiness-csv", type=Path)
    parser.add_argument("--registered-coordinates-json", type=Path)
    parser.add_argument("--member-codes-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--control-limit", type=int, default=0)
    parser.add_argument("--registered-control-limit", type=int, default=0)
    parser.add_argument("--allow-live-kosis", action="store_true")
    args = parser.parse_args()
    if not args.allow_live_kosis:
        raise RuntimeError("LIVE_KOSIS_APPROVAL_FLAG_REQUIRED")
    settings = Settings()
    summary = run(
        ledger_xlsx=args.ledger_xlsx,
        candidate_attachment_csv=args.candidate_attachment_csv,
        candidate_identity_jsonl=args.candidate_identity_jsonl,
        catalog_json=args.catalog_json,
        guard_readiness_csv=args.guard_readiness_csv,
        registered_coordinates_json=args.registered_coordinates_json,
        member_codes_json=args.member_codes_json,
        output_dir=args.output_dir,
        api_key=settings.kosis_api_key or "",
        split=args.split,
        limit=args.limit,
        control_limit=args.control_limit,
        registered_control_limit=args.registered_control_limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
