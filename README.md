# CLAFACT Bootcamp Learning Lab

This repository carries the bounded, reviewable FlyHermes overlay used to
learn how the CLAFACT pipeline fails, test one improvement at a time, and
preserve evidence that another learner can reproduce.

The primary deliverable is **not a production news service**. It is a clear
problem-solving record:

```text
baseline -> one bottleneck -> hypothesis -> failing test -> one change
         -> fixed-data evaluation -> error analysis -> reflection
```

The end-to-end RSS/KOSIS flow remains available only as a bounded demonstration
and integration check. Uptime, article volume, and AUTO/HOLD counts do not prove
model quality.

## Bootcamp learning outcomes

- Explain the R1-R4 pipeline by input, output, failure reason, and owner.
- Separate operational coverage from Gold-based model accuracy.
- Improve one A/B/C section with TDD and a fixed evaluation contract.
- Preserve immutable predictions, manifests, metrics, and SHA-256 evidence.
- Treat `HOLD` and `NOT_EVALUABLE` as responsible decisions, not failures to hide.
- Present the initial result, failed attempts, measured improvement, and next limit.

The detailed learning contract is in
`overlay/docs/reference/19_BOOTCAMP_EDUCATIONAL_DIRECTION.md`.

The first bounded R3 candidate-retrieval experiment is documented in
`overlay/docs/reference/21_R3_KOSIS_CANDIDATE_RETRIEVAL.md`. It improves
official-candidate attachment coverage while keeping table selection,
Hard Guard, Evidence Cell, and accuracy claims separate.

The follow-up structural Hard Guard replay is documented in
`overlay/docs/reference/22_R3_HARD_GUARD_READINESS.md`. It keeps the existing
Guard unchanged, separates catalog coverage from slot/metadata conflicts, and
does not treat a surviving candidate as a selected or correct KOSIS table.
The bounded official-value pilot is documented in
`overlay/docs/reference/23_R3_OFFICIAL_VALUE_PILOT.md`. It verifies current
KOSIS metadata and values only after an Evidence Cell is resolved, records
unresolved candidates as HOLD, and does not infer table or Verdict accuracy.
The full-ledger registry replay is documented in
`overlay/docs/reference/24_R3_REGISTRY_FULL_VALUE_REPLAY.md`. It joins all
1,542 Claim IDs, reinforces a provisional composite-signature registry, and
deduplicates exact official-cell requests while preserving unresolved HOLDs.
It supports explicit `--offline-cache-only` reproducibility and
`--allow-live-kosis` cache-miss lookup modes.
The D1 structure-metadata replay is documented in
`overlay/docs/reference/25_D1_METADATA_FAILURE_REPLAY.md`. It freezes the exact
failure cohort from one execution history and wires live KOSIS table search
plus ITM/PRD metadata into R3 without weakening Hard Guard.
The full-article execution audit is documented in
`overlay/docs/reference/26_FULL_ARTICLE_EXECUTION_AUDIT.md`. It joins all 499
source articles to 1,542 Claims and records every stored KOSIS metadata/value
attempt, response hash, success, HOLD stage, and reason without recording keys.
The category 1/2 six-W execution is documented in
`overlay/docs/reference/27_CATEGORIES_1_2_SIXW_EXECUTION.md`. It executes the
latest 256 context-completion and 339 multi-Claim rows, validates conservative
period resolution and atomic splitting, and records success/HOLD evidence as
992 who/when/where/what/how/why events before any new KOSIS call.
The stronger second-pass replay is documented in
`overlay/docs/reference/28_CATEGORIES_1_2_STRONG_LLM_REPLAY.md`. It applies an
LLM only after deterministic HOLD, then accepts a proposal only when article
evidence, period normalization, target-number grounding, and parent-child
coverage pass deterministic checks. Stage success remains separate from Gold
accuracy and from KOSIS official-value completion.
The multi-method cascade is documented in
`overlay/docs/reference/29_CATEGORIES_1_2_MULTIMETHOD_CASCADE.md`. It compares
six offline split rules, preserves guarded OpenAI successes, applies HCX only
to the remaining HOLD cohort, and records both improved and not-improved
attempts. It leaves KOSIS lookup blocked until the accepted Claims are rebuilt
as 12-slot records with an explicit target value role. The bounded
`tools.build_r2_multimethod_reentry_queue` command materializes that next-stage
queue without claiming that extraction or KOSIS lookup already succeeded.
The fixed 20-row 12-slot re-entry pilot is documented in
`overlay/docs/reference/30_R2_12SLOT_REENTRY_PILOT.md`. It passes article date
and adjacent context to HCX, then independently checks required slots, source
value grounding, period consistency, and category-2 target role/value/unit.
Only 4 of 20 rows passed every deterministic Gate in the final pilot; KOSIS
remained uncalled and accuracy remained not evaluable without linked Gold.
A Korean team handoff is available at
`output/pdf/CLAFACT_Hard_Guard_KOSIS_연결_방법_20260824.pdf`.
The same explanation is available as editable Markdown at
`output/md/CLAFACT_Hard_Guard_KOSIS_연결_방법_20260824.md`.

## Safety boundary

- One controller mode per invocation.
- No scheduler and no automatic retry.
- Networked operational children may receive named keys from an explicit local
  `environment_file`; secret values are never copied to controller artifacts.
- `post_run_gold_evaluation` is offline: it reads stored manifests,
  predictions, and Gold only.
- A failed operational child still preserves its verified count-only cycle
  artifact. Missing R3/R4 predictions become explicit `NOT_EVALUABLE` stages.
- Operational counts and Gold accuracy remain separate namespaces.
- R1 Gold CSV, API keys, RSS article text, and article URLs are not stored in
  this public repository.

## Repository role

Files below `overlay/` are copied over the existing FlyHermes checkout while
preserving the same relative paths. Existing files are backed up under the
persistent FlyHermes state directory before deployment.

`deployment_sha256.json` records the SHA-256 and executable-mode expectation
of every overlay file so the server deployment can be verified without
exposing source data.

After a saved pipeline run, `tools/run_linked_post_run_evaluation.py` creates a
new immutable derived config and runs exactly one offline Gold evaluation. It
discovers existing R3/R4 artifacts but never fabricates missing predictions,
calls an API, retries an operational run, or overwrites an evaluation ID.

FlyHermes is used as a reproducible remote lab runner. It is not treated as an
autonomous service operator or an automatic training system. The default daily
learning check reads stored artifacts only and proposes the next bounded
experiment; people approve code, Gold, provider calls, and experiment changes.
