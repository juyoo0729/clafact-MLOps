"""Run R2 Claim structuring and write an immutable R3-admission manifest."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from config.settings import Settings  # noqa: E402
from core.claim_extractor_factory import create_claim_extractor  # noqa: E402
from core.mlops_run_store import MLOpsRunStore, manifest_sha256, sha256_file  # noqa: E402
from core.mlops_stage_io import read_jsonl_models, write_jsonl  # noqa: E402
from core.r1_article_pipeline import R1Candidate  # noqa: E402
from core.r2_claim_pipeline import run_r2_pipeline  # noqa: E402
from schemas.mlops_run import StageManifest  # noqa: E402


def main() -> int:
    started_at = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r1-manifest", type=Path, required=True)
    parser.add_argument("--r1-candidates", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--pipeline-run-id", required=True)
    parser.add_argument("--max-records", type=int, default=50)
    parser.add_argument("--use-configured-extractor", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_records <= 1_000:
        parser.error("--max-records must be between 1 and 1000")
    store = MLOpsRunStore(args.state_root)
    store.verify_upstream_artifact(
        args.pipeline_run_id,
        "r1",
        args.r1_manifest,
        args.r1_candidates,
        expected_name="r1_candidates.jsonl",
    )
    manifest_path = store.manifest_path(args.pipeline_run_id, "r2")
    if manifest_path.exists():
        store.verify_stage_outputs(store.load_manifest(args.pipeline_run_id, "r2"))
        print(manifest_path)
        return 0
    candidates = [R1Candidate.model_validate(item) for item in read_jsonl_models(args.r1_candidates, R1Candidate)]
    selected, pending = candidates[: args.max_records], candidates[args.max_records :]
    settings = Settings()
    extractor = create_claim_extractor(settings) if args.use_configured_extractor else None
    with store.stage_lock(args.pipeline_run_id, "r2"):
        result = run_r2_pipeline(selected, extractor=extractor)
        stage_dir = store.stage_dir(args.pipeline_run_id, "r2")
        ready_path = write_jsonl(stage_dir / "r3_ready_claims.jsonl", result.r3_ready)
        holds_path = write_jsonl(stage_dir / "r2_holds.jsonl", result.holds)
        enrichment_path = write_jsonl(
            stage_dir / "r2_enrichment_required.jsonl",
            result.enrichment_required,
        )
        pending_path = write_jsonl(stage_dir / "r2_pending.jsonl", pending)
        reason_counts = Counter(hold.reason_code for hold in result.holds)
        if result.enrichment_required:
            reason_counts["R2_SLOT_ENRICHMENT_REQUIRED"] += len(result.enrichment_required)
        if pending:
            reason_counts["R2_BATCH_LIMIT_REACHED"] += len(pending)
        manifest = StageManifest(
            pipeline_run_id=args.pipeline_run_id,
            stage="r2",
            status="HOLD" if result.holds or result.enrichment_required or pending else "SUCCESS",
            created_at=datetime.now(UTC),
            input_manifest_sha256=manifest_sha256(args.r1_manifest),
            input_artifact_sha256=sha256_file(args.r1_candidates),
            input_paths={
                "r1_manifest": str(args.r1_manifest.resolve()),
                "r1_candidates": str(args.r1_candidates.resolve()),
            },
            input_sha256={
                "r1_manifest": sha256_file(args.r1_manifest),
                "r1_candidates": sha256_file(args.r1_candidates),
            },
            output_sha256={
                ready_path.name: sha256_file(ready_path),
                holds_path.name: sha256_file(holds_path),
                enrichment_path.name: sha256_file(enrichment_path),
                pending_path.name: sha256_file(pending_path),
            },
            output_paths={
                ready_path.name: str(ready_path.resolve()),
                holds_path.name: str(holds_path.resolve()),
                enrichment_path.name: str(enrichment_path.resolve()),
                pending_path.name: str(pending_path.resolve()),
            },
            elapsed_seconds=perf_counter() - started_at,
            counts={
                "input": len(candidates),
                "processed": len(selected),
                "r3_ready": len(result.r3_ready),
                "holds": len(result.holds),
                "enrichment_required": len(result.enrichment_required),
                "pending": len(pending),
            },
            reason_counts=dict(sorted(reason_counts.items())),
            versions={
                "claim_schema_version": settings.claim_schema_version,
                "r2_pipeline_version": "1.1",
                "claim_provider": settings.claim_provider if extractor is not None else "none",
                "claim_model": settings.openai_model if settings.claim_provider == "openai" and extractor is not None else "not_applicable",
                "hcx_extraction_mode": settings.hcx_extraction_mode if settings.claim_provider == "hcx" and extractor is not None else "not_applicable",
                "extractor_class": type(extractor).__name__ if extractor is not None else "none",
            },
        )
        store.write_manifest(manifest)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
