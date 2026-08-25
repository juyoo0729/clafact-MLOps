"""Run a claim batch through R3→R4 and record one execution-ledger row per claim.

Success is never recorded as a bare "성공": the row names the rule pattern that
succeeded and whether it is a general rule.  Failures name the exact stage and
cause, folded into one of six resolution bundles.  After the batch, the funnel
aggregation and the five artifacts (실행이력.csv, 실행원장.xlsx,
분류별_성능요약.csv, 개선전후_비교.csv, 최종보고서.txt) are generated
automatically.

Modes:
  --mode snapshot   auditable local snapshots only (no network) — this is the
                    only mode available without a KOSIS API key.
  --mode live       snapshots first, then read-only KOSIS table search,
                    ITM/PRD metadata, and parameter-value APIs. Requires the
                    KOSIS_API_KEY environment variable; the key is never
                    printed or copied anywhere.

Example (local machine, D1 basic batch):

    set KOSIS_API_KEY=...   # PowerShell: $env:KOSIS_API_KEY="..."
    python tools/run_execution_batch.py --mode live \
        --batch data/execution_ledger/batches/D1_metadata_failure_52_v1.json \
        --output-dir data/execution_ledger/runs/D1_live_v1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from core.data_loader import (  # noqa: E402
    load_kosis_catalog,
    load_period_availability_snapshot,
    load_standard_concepts,
)
from core.execution_ledger import (  # noqa: E402
    PIPELINE_STAGES,
    append_history,
    build_execution_record,
    generate_outputs,
)
from core.full_gold_r3_replay import _to_r2_ready_claim  # noqa: E402
from core.kosis_fetcher import OfficialValueFetcher  # noqa: E402
from core.kosis_live_catalog import KosisLiveCatalogSearch  # noqa: E402
from core.kosis_openapi_transport import get_meta  # noqa: E402
from core.r3_evidence_pipeline import run_r3_pipeline  # noqa: E402
from core.r4_verdict_pipeline import run_r4_pipeline  # noqa: E402

DEFAULT_GOLD = PROJECT / "data" / "model_benchmarks" / "r2_12slot_v2" / "gold.jsonl"
DEFAULT_CONCEPTS = PROJECT / "data" / "semantic_standard" / "seed_concepts.json"
DEFAULT_CATALOG = PROJECT / "data" / "kosis_catalog" / "catalog_350.json"
DEFAULT_PERIODS = PROJECT / "data" / "kosis_catalog" / "period_availability" / "period_availability_v1.json"
DEFAULT_SNAPSHOTS = PROJECT / "data" / "kosis_snapshots"

_KST = timezone(timedelta(hours=9))


class _CountingLiveSearch:
    def __init__(self, inner: KosisLiveCatalogSearch) -> None:
        self.inner = inner
        self.calls = 0

    def search(self, query: str, *, result_count: int = 20):
        self.calls += 1
        return self.inner.search(query, result_count=result_count)


class _CountingMetadataFetcher:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.inner(*args, **kwargs)


def build_r3_dependencies(
    *,
    mode: str,
    api_key: str | None,
) -> tuple[_CountingLiveSearch | None, str | None, _CountingMetadataFetcher | None]:
    """Return fail-closed R3 live dependencies for one execution mode.

    Snapshot mode never receives a network dependency. Live mode must wire both
    official table search and ITM/PRD metadata lookup; otherwise candidates with
    unavailable structure metadata can never recover before Hard Guard.
    """
    if mode == "snapshot":
        return None, None, None
    if mode != "live":
        raise ValueError(f"UNKNOWN_EXECUTION_MODE:{mode}")
    if not api_key:
        raise ValueError("KOSIS_API_KEY_REQUIRED")
    return (
        _CountingLiveSearch(KosisLiveCatalogSearch(api_key)),
        api_key,
        _CountingMetadataFetcher(get_meta),
    )


def r3_api_call_count(live_search, metadata_fetcher) -> int:
    return int(getattr(live_search, "calls", 0)) + int(
        getattr(metadata_fetcher, "calls", 0)
    )


def run_r3_claim(
    ready,
    *,
    concepts,
    catalog,
    period_availability,
    live_search,
    kosis_api_key,
    metadata_fetcher,
):
    """Run one Claim through R3 with the selected mode dependencies."""
    return run_r3_pipeline(
        [ready],
        concepts=concepts,
        catalog=catalog,
        period_availability=period_availability,
        live_search=live_search,
        kosis_api_key=kosis_api_key,
        metadata_fetcher=metadata_fetcher,
    )


def _stage_for_r3_reason(reason: str) -> str:
    upper = (reason or "").upper()
    if "SEMANTIC_STANDARD" in upper or "CONCEPT" in upper:
        return "CONCEPT"
    if "CATALOG" in upper or "METADATA" in upper:
        return "CATALOG_SEARCH"
    if "GUARD" in upper or "PERIOD_UNAVAILABLE" in upper:
        return "HARD_GUARD"
    if "SEMANTIC_MATCH" in upper or "MARGIN" in upper or "SCORE" in upper:
        return "SEMANTIC_MATCH"
    return "EVIDENCE_CELL"


def _stage_for_r4_reason(reason: str) -> str:
    upper = (reason or "").upper()
    if "AS_OF" in upper or "PUBLICATION" in upper or "RELEASE" in upper:
        return "PUBLICATION_CHECK"
    if "EVIDENCE_PLAN" in upper or "CELL" in upper:
        return "EVIDENCE_CELL"
    if "CALC" in upper:
        return "CALCULATION"
    return "OFFICIAL_VALUE_FETCH"


def _pass_until(failed_stage: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for stage in PIPELINE_STAGES:
        if stage == failed_stage:
            statuses[stage] = "FAIL"
            break
        statuses[stage] = "PASS"
    return statuses


def _pattern_signature(claim) -> str:
    coord = "SIMPLE" if not (claim.dimension or claim.region or claim.population) else "DETAILED"
    return f"{claim.frequency or 'NA'}_{claim.calculation}_{coord}_{claim.unit or 'NA'}"


def _code_version() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "UNKNOWN"


class _CountingFetcher:
    """Wrap the official fetcher so every claim's API call count is recorded."""

    def __init__(self, inner: OfficialValueFetcher) -> None:
        self._inner = inner
        self.calls = 0

    def fetch(self, cell, *, article_date=None):
        if self._inner._api_lookup is not None:  # noqa: SLF001 - counting only
            original = self._inner._api_lookup

            def counted(request_cell):
                self.calls += 1
                return original(request_cell)

            self._inner._api_lookup = counted
            try:
                return self._inner.fetch(cell, article_date=article_date)
            finally:
                self._inner._api_lookup = original
        return self._inner.fetch(cell, article_date=article_date)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, type=Path)
    parser.add_argument("--mode", choices=("snapshot", "live"), default="snapshot")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--concepts", type=Path, default=DEFAULT_CONCEPTS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--period-snapshot", type=Path, default=DEFAULT_PERIODS)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOTS)
    parser.add_argument("--data-version", default="r2_12slot_v2_frozen")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Refusing to overwrite a non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    api_lookup = None
    api_key = None
    if args.mode == "live":
        api_key = os.environ.get("KOSIS_API_KEY")
        if not api_key:
            parser.error("live 모드에는 KOSIS_API_KEY 환경변수가 필요합니다 (키는 출력·복사되지 않습니다)")
        from core.kosis_value_transport import get_parameter_data

        def api_lookup(cell):  # noqa: ANN001
            codes = list(cell.dimension_codes.values()) or (
                [cell.member_code] if cell.member_code else []
            )
            if not codes:
                raise RuntimeError("KOSIS_OBJECT_CODES_MISSING")
            return get_parameter_data(
                api_key, cell.org_id, cell.tbl_id, cell.itm_id,
                cell.prd_se, cell.prd_de, cell.prd_de, codes,
            )

    batch = json.loads(args.batch.read_text(encoding="utf-8-sig"))
    batch_claims = {c["claim_id"]: c for c in batch["claims"]}
    gold_rows = {}
    for line in args.gold.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row["claim_id"] in batch_claims:
                gold_rows[row["claim_id"]] = row
    missing = sorted(set(batch_claims) - set(gold_rows))
    if missing:
        raise SystemExit(f"BATCH_CLAIMS_MISSING_IN_GOLD:{len(missing)}:{missing[:3]}")

    concepts = load_standard_concepts(args.concepts)
    catalog = load_kosis_catalog(args.catalog)
    try:
        period_availability = load_period_availability_snapshot(
            args.period_snapshot,
            args.period_snapshot.with_name(args.period_snapshot.stem + ".manifest.json"),
        )
    except Exception:
        period_availability = None
    snapshot_paths = sorted(args.snapshot_dir.glob("*.json"))

    # pattern applicability: how many batch claims share each rule signature
    ready_by_id = {cid: _to_r2_ready_claim(gold_rows[cid]) for cid in batch_claims}
    signature_counts: dict[str, int] = {}
    for record in ready_by_id.values():
        sig = _pattern_signature(record.claim)
        signature_counts[sig] = signature_counts.get(sig, 0) + 1

    code_version = _code_version()
    records = []
    for claim_id, meta in batch_claims.items():
        ready = ready_by_id[claim_id]
        started = time.perf_counter()
        run_at = datetime.now(_KST).isoformat(timespec="seconds")
        counting = _CountingFetcher(OfficialValueFetcher(snapshot_paths, api_lookup))
        live_search, r3_api_key, metadata_fetcher = build_r3_dependencies(
            mode=args.mode,
            api_key=api_key,
        )
        common = {
            "claim_id": claim_id,
            "sentence": ready.claim.source_sentence,
            "run_at": run_at,
            "code_version": code_version,
            "data_version": args.data_version,
            "subtype": meta.get("subtype", ""),
            "prev_result": meta.get("prev_result", ""),
        }

        r3 = run_r3_claim(
            ready, concepts=concepts, catalog=catalog,
            period_availability=period_availability,
            live_search=live_search,
            kosis_api_key=r3_api_key,
            metadata_fetcher=metadata_fetcher,
        )
        if r3.holds:
            reason = r3.holds[0].reason_code
            stage = _stage_for_r3_reason(reason)
            records.append(build_execution_record(
                **common,
                stage_statuses=_pass_until(stage),
                failed_stage=stage, failure_cause=reason,
                final_verdict="UNDETERMINED", curr_result="HOLD",
                duration_ms=int((time.perf_counter() - started) * 1000),
                api_call_count=counting.calls + r3_api_call_count(
                    live_search, metadata_fetcher
                ),
            ))
            continue

        evidence = r3.r4_ready[0]
        r4 = run_r4_pipeline([evidence], fetcher=counting)
        verdict = r4.verdicts[0]
        provenance = r4.provenance[0] if r4.provenance else None
        cell = (verdict.evidence_cells or evidence.calculation_plan.required_cells or [None])[0]
        duration_ms = int((time.perf_counter() - started) * 1000)
        cell_fields = {
            "tbl_id": getattr(cell, "tbl_id", "") or evidence.candidate.tbl_id,
            "item_id": getattr(cell, "itm_id", ""),
            "period": getattr(cell, "prd_de", ""),
            "dimension_coords": json.dumps(getattr(cell, "dimension_codes", {}) or {},
                                           ensure_ascii=False, sort_keys=True),
            "official_source_url": (
                f"https://kosis.kr/statHtml/statHtml.do?orgId={getattr(cell, 'org_id', '')}"
                f"&tblId={getattr(cell, 'tbl_id', '')}" if cell is not None else ""
            ),
            "response_hash": provenance.response_hash if provenance else "",
            "publication_basis": (
                f"기사시점 as-of 필터 적용({ready.published_at}) · 출처={provenance.source}"
                if provenance else ""
            ),
            "article_value": str(verdict.claim_value if verdict.claim_value is not None
                                 else ready.claim.value),
            "official_value": ";".join(str(v) for v in verdict.evidence_values) or "",
            "calculation_result": verdict.verdict,
        }

        if verdict.verdict in ("MATCH", "MISMATCH"):
            signature = _pattern_signature(ready.claim)
            pattern = f"{evidence.evidence_profile or 'runtime_catalog'}:{signature}"
            records.append(build_execution_record(
                **common, **cell_fields,
                stage_statuses={s: "PASS" for s in PIPELINE_STAGES},
                applied_pattern=pattern,
                pattern_generality=("GENERAL_RULE" if signature_counts[signature] >= 2
                                    else "CLAIM_SPECIFIC"),
                failed_stage="VERDICT" if verdict.verdict == "MISMATCH" else None,
                failure_cause="VALUE_MISMATCH" if verdict.verdict == "MISMATCH" else None,
                final_verdict=verdict.verdict, curr_result=verdict.verdict,
                duration_ms=duration_ms,
                api_call_count=counting.calls + r3_api_call_count(
                    live_search, metadata_fetcher
                ),
            ))
        else:
            stage = _stage_for_r4_reason(verdict.reason_code)
            records.append(build_execution_record(
                **common, **cell_fields,
                stage_statuses=_pass_until(stage),
                failed_stage=stage, failure_cause=verdict.reason_code,
                final_verdict="UNDETERMINED", curr_result="HOLD",
                duration_ms=duration_ms,
                api_call_count=counting.calls + r3_api_call_count(
                    live_search, metadata_fetcher
                ),
            ))

    # pattern applicability keyed exactly like applied_pattern strings
    applicability = {}
    for record in records:
        name = record.get("applied_pattern")
        if name:
            applicability[name] = signature_counts.get(name.split(":", 1)[-1], "")

    history_path = args.output_dir / "실행이력.csv"
    append_history(history_path, records)
    written = generate_outputs(
        args.output_dir, history_path=history_path,
        pattern_applicability=applicability, batch_label=batch["batch_id"],
    )

    manifest = {
        "artifact": "execution_batch_run",
        "batch_id": batch["batch_id"],
        "mode": args.mode,
        "code_version": code_version,
        "data_version": args.data_version,
        "claim_count": len(records),
        "snapshot_count": len(snapshot_paths),
        "inputs_sha256": {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in (args.batch, args.gold, args.catalog)
        },
        "outputs_sha256": {
            p.name: sha256(p.read_bytes()).hexdigest()
            for p in [history_path, *written]
        },
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ok = sum(1 for r in records if r.get("final_verdict") in ("MATCH", "MISMATCH"))
    print(json.dumps({
        "batch": batch["batch_id"], "mode": args.mode, "claims": len(records),
        "판정성공": ok, "보류": len(records) - ok,
        "outputs": [p.name for p in [history_path, *written]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
