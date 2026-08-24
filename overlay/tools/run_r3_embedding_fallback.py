"""Rerank only low-lexical-confidence KOSIS candidates with a local model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from core.r3_embedding_fallback import EmbeddingEncoder, rerank_embedding_fallback


DEFAULT_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
OUTPUT_COLUMNS = (
    "claim_id",
    "split",
    "indicator",
    "unit",
    "frequency",
    "calculation",
    "embedding_status",
    "lexical_candidate_tbl_ids",
    "embedding_candidate_tbl_ids",
    "embedding_candidate_tbl_names",
    "embedding_scores",
    "embedding_top1_changed",
    "prior_proposal_available",
    "lexical_top1_prior_proposal_overlap",
    "embedding_top1_prior_proposal_overlap",
    "lexical_top3_prior_proposal_overlap",
    "embedding_top3_prior_proposal_overlap",
    "selection_status",
    "mandatory_next_gate",
)


def run(
    *,
    candidate_attachment_csv: Path,
    output_dir: Path,
    encoder: EmbeddingEncoder,
    model_id: str,
    model_revision: str,
    baseline_route_csv: Path | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Write an immutable embedding fallback artifact without selecting tables."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    source_rows = _read_csv(candidate_attachment_csv)
    proposal_by_claim = _proposal_ids_by_claim(baseline_route_csv)
    rows: list[dict[str, str]] = []
    for source in source_rows:
        if source.get("confidence_status") != "LOW_LEXICAL_CONFIDENCE":
            continue
        lexical_ids = _split(source.get("candidate_tbl_ids", ""))
        lexical_names = _split(source.get("candidate_tbl_names", ""))
        candidates = [
            {
                "tbl_id": table_id,
                "tbl_name": lexical_names[index] if index < len(lexical_names) else "",
            }
            for index, table_id in enumerate(lexical_ids)
        ]
        result = rerank_embedding_fallback(
            indicator=source.get("indicator", ""),
            unit=source.get("unit", ""),
            frequency=source.get("frequency", ""),
            calculation=source.get("calculation", ""),
            confidence_status=source.get("confidence_status", ""),
            candidates=candidates,
            encoder=encoder,
            model_id=model_id,
            model_revision=model_revision,
            top_k=top_k,
        )
        embedding_ids = [candidate.tbl_id for candidate in result.candidates]
        prior_ids = proposal_by_claim.get(source.get("claim_id", ""), [])
        rows.append(
            {
                "claim_id": source.get("claim_id", ""),
                "split": source.get("split", ""),
                "indicator": source.get("indicator", ""),
                "unit": source.get("unit", ""),
                "frequency": source.get("frequency", ""),
                "calculation": source.get("calculation", ""),
                "embedding_status": result.status,
                "lexical_candidate_tbl_ids": " | ".join(lexical_ids),
                "embedding_candidate_tbl_ids": " | ".join(embedding_ids),
                "embedding_candidate_tbl_names": " | ".join(
                    candidate.tbl_name for candidate in result.candidates
                ),
                "embedding_scores": " | ".join(
                    f"{candidate.embedding_score:.6f}"
                    for candidate in result.candidates
                ),
                "embedding_top1_changed": _yes_no(
                    bool(lexical_ids and embedding_ids and lexical_ids[0] != embedding_ids[0])
                ),
                "prior_proposal_available": _yes_no(bool(prior_ids)),
                "lexical_top1_prior_proposal_overlap": _yes_no(
                    _overlap(lexical_ids[:1], prior_ids)
                ),
                "embedding_top1_prior_proposal_overlap": _yes_no(
                    _overlap(embedding_ids[:1], prior_ids)
                ),
                "lexical_top3_prior_proposal_overlap": _yes_no(
                    _overlap(lexical_ids[:3], prior_ids)
                ),
                "embedding_top3_prior_proposal_overlap": _yes_no(
                    _overlap(embedding_ids[:3], prior_ids)
                ),
                "selection_status": result.selection_status,
                "mandatory_next_gate": result.mandatory_next_gate,
            }
        )

    summary = _summarize(rows, model_id=model_id, model_revision=model_revision)
    result_csv = output_dir / "embedding_fallback.csv"
    summary_json = output_dir / "summary.json"
    _write_csv(result_csv, rows)
    _write_json(summary_json, summary)
    manifest = {
        "artifact": "r3_embedding_fallback_v1",
        "inputs": {
            "candidate_attachment_csv": _file_record(candidate_attachment_csv),
            "baseline_route_csv": (
                _file_record(baseline_route_csv) if baseline_route_csv else None
            ),
        },
        "model": {"id": model_id, "revision": model_revision},
        "parameters": {"top_k": top_k},
        "outputs": {
            "embedding_fallback_csv": _file_record(result_csv),
            "summary_json": _file_record(summary_json),
        },
        "evaluation_boundary": summary["accuracy_status"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def _summarize(
    rows: list[dict[str, str]], *, model_id: str, model_revision: str
) -> dict[str, Any]:
    proposal_rows = [row for row in rows if row["prior_proposal_available"] == "YES"]
    lexical_top1 = sum(
        row["lexical_top1_prior_proposal_overlap"] == "YES" for row in proposal_rows
    )
    embedding_top1 = sum(
        row["embedding_top1_prior_proposal_overlap"] == "YES" for row in proposal_rows
    )
    return {
        "artifact": "r3_embedding_fallback_v1",
        "model_id": model_id,
        "model_revision": model_revision,
        "low_lexical_claim_count": len(rows),
        "embedding_processed_count": sum(
            row["embedding_status"] == "EMBEDDING_SHORTLIST_ATTACHED" for row in rows
        ),
        "embedding_top1_changed_count": sum(
            row["embedding_top1_changed"] == "YES" for row in rows
        ),
        "prior_proposal_comparison_count": len(proposal_rows),
        "lexical_top1_prior_proposal_overlap_count": lexical_top1,
        "embedding_top1_prior_proposal_overlap_count": embedding_top1,
        "lexical_top1_prior_proposal_overlap_rate": _ratio(
            lexical_top1, len(proposal_rows)
        ),
        "embedding_top1_prior_proposal_overlap_rate": _ratio(
            embedding_top1, len(proposal_rows)
        ),
        "lexical_top3_prior_proposal_overlap_count": sum(
            row["lexical_top3_prior_proposal_overlap"] == "YES"
            for row in proposal_rows
        ),
        "embedding_top3_prior_proposal_overlap_count": sum(
            row["embedding_top3_prior_proposal_overlap"] == "YES"
            for row in proposal_rows
        ),
        "experiment_result": (
            "IMPROVED_PROXY_ONLY"
            if embedding_top1 > lexical_top1
            else "NOT_IMPROVED_PROXY"
        ),
        "locked_full_replay_decision": (
            "NOT_RUN_UNTIL_DEV_PROXY_IMPROVES_AND_R3_TABLE_GOLD_EXISTS"
        ),
        "selection_status": "HOLD_NO_TABLE_SELECTED",
        "accuracy_status": "NOT_EVALUABLE_NO_INDEPENDENT_R3_TABLE_GOLD",
        "proxy_boundary": (
            "Agreement with an earlier LLM proposal is a consistency proxy, not "
            "KOSIS table accuracy or Gold."
        ),
    }


def _load_sentence_transformer(model_id: str, revision: str) -> EmbeddingEncoder:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError(
            "sentence-transformers is required only for this optional runner"
        ) from error
    model = SentenceTransformer(model_id, revision=revision)

    def encode(texts):
        return model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    return encode


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _proposal_ids_by_claim(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    return {
        row.get("claim_id", ""): _split(row.get("candidate_tbl_ids", ""))
        for row in _read_csv(path)
        if row.get("automatic_route_status") == "HOLD_CANDIDATES_ATTACHED"
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _file_record(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


def _overlap(left: list[str], right: list[str]) -> bool:
    return bool(set(left).intersection(right))


def _yes_no(value: bool) -> str:
    return "YES" if value else "NO"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-attachment-csv", type=Path, required=True)
    parser.add_argument("--baseline-route-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    summary = run(
        candidate_attachment_csv=args.candidate_attachment_csv,
        baseline_route_csv=args.baseline_route_csv,
        output_dir=args.output_dir,
        encoder=_load_sentence_transformer(args.model_id, args.model_revision),
        model_id=args.model_id,
        model_revision=args.model_revision,
        top_k=args.top_k,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
