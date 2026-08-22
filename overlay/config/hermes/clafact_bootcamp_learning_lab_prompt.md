# CLAFACT Bootcamp Offline Learning Lab

Run one educational review of stored CLAFACT artifacts. Do not operate the news
service and do not change code automatically.

## Objective

Turn the latest verified failure evidence into one teachable learning question
for section A, B, or C:

- A: R1-R2 Claim detection and 12-slot structuring
- B: R3 KOSIS table retrieval after Hard Guard
- C: R4 Evidence coordinates, deterministic calculation, Verdict, and HOLD

The output must help a learner explain baseline -> bottleneck -> hypothesis ->
test -> measured result -> reflection. A high operational count is not an
accuracy result.

## Inputs

- Project: `/opt/data/clafact-auto`
- Persistent state: `/opt/data/clafact_state`
- Python: `/opt/data/clafact_state/venvs/clafact-auto/bin/python`
- Quality gate: `/opt/data/clafact-auto/tools/run_flyhermes_quality_gate.sh`
- R2 stored-audit root: `/opt/data/clafact_state/r2_internal_audits`
- Learning output root: `/opt/data/clafact_state/bootcamp_learning_runs`
- Operational config guard:
  `/opt/data/clafact_state/config/mlops_automation.operational_20260820.json`

## Procedure

1. Confirm `scheduling.enabled=false` and
   `modes.operational_cycle.rss_approved=false`. If either differs, stop with
   `RSS_COLLECTION_PAUSE_GUARD_FAILED`. Do not inspect or alter the RSS list.
2. Run the quality gate exactly once. If it is not PASS, stop and report only
   test counts and reason codes.
3. Read stored manifests, Gold evaluation summaries, R2 audit reports,
   scoreboards, and text-free error summaries only. Do not call RSS, KOSIS, an
   LLM, or any provider API.
4. Compute an input fingerprint from the selected stored artifacts and relevant
   evaluation code. If the latest successful learning run has the same
   fingerprint, report `LEARNING_INPUT_UNCHANGED` and create no duplicate run.
5. Select one section and one dominant evaluable failure type. Prefer the
   largest error count, but reject it when its Gold join or metric contract is
   not valid. Record rejected candidates with reason codes.
6. Create a new immutable
   `/opt/data/clafact_state/bootcamp_learning_runs/<learning_run_id>/` only when
   inputs changed. Never overwrite an existing ID.
7. Write:
   - `learning_summary.json`: section, learning objective, evidence artifact
     IDs, baseline metrics, status, and next single hypothesis;
   - `experiment_card.md`: one-sentence hypothesis, proposed failing test,
     allowed one-element change, regression risk, and acceptance rule;
   - `sha256_manifest.json`: selected input and new output hashes;
   - `safe_summary.txt`: count-only learner handoff without source text, IDs,
     URLs, secrets, or raw responses.
8. Do not claim that a recommendation was implemented. Use
   `EXPERIMENT_NOT_RUN` until a person approves a code change and a separate
   experiment produces new predictions.
9. Do not modify Git, Gold, predictions, thresholds, prompts, code, schedules,
   RSS settings, or provider configuration.

## Required report

```text
CLAFACT_BOOTCAMP_LEARNING_LAB
STATUS: PASS | HOLD | FAILED | LEARNING_INPUT_UNCHANGED
SECTION: A | B | C | NOT_SELECTED
LEARNING_OBJECTIVE: <one sentence or NOT_SELECTED>
BASELINE_EVIDENCE: <metric names and count-only values or NOT_EVALUABLE>
TOP_FAILURE: <safe reason/slot and count or NOT_SELECTED>
GOLD_JOIN_STATUS: <status and coverage only>
PROPOSED_FAILING_TEST: <one concise test behavior or NOT_SELECTED>
NEXT_SINGLE_HYPOTHESIS: <one concise hypothesis or NOT_SELECTED>
EXPERIMENT_STATUS: EXPERIMENT_NOT_RUN
ARTIFACT_PATH: <new immutable path or NOT_CREATED>
SHA256_STATUS: VERIFIED | NOT_CREATED | FAILED
```

Stop after this report. A learner must approve and perform the next experiment.
