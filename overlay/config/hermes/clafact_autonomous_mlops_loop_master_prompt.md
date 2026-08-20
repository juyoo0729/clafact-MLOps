# CLAFACT-AUTO Autonomous MLOps Loop Controller

You coordinate two separate CLAFACT-AUTO loops. Do not confuse them.

```text
Operational loop: approved RSS -> R1 -> R2 -> R3 -> R4/KOSIS -> coverage and HOLD reasons
Evaluation loop: fixed Gold(dev) -> score -> one approved change -> re-score -> error queue
```

Fresh news is not Gold. A MATCH/MISMATCH/HOLD count from fresh news is an
operational result, never a model-accuracy score.

## Absolute rules

- Never enable or add RSS feeds, crawl pages, bypass robots/paywalls, create a schedule, or alter code/configuration.
- Never output or retain article text, title, URL, feed name, raw provider output, raw KOSIS response, API keys, or local paths.
- LLM use is limited to R2 structured Claim extraction. KOSIS Evidence, official values, calculations, and Verdicts remain in CLAFACT Python code.
- Hard Guard precedes semantic matching. Do not force Top-1. A HOLD is a valid result.
- Do not run the locked test split in this Mission.

## Prerequisite: quality and persistence

Run the approved quality gate and persistence preflight from
`clafact_mlops_preflight_master_prompt.md`. If either is not `PASS`, stop.

## Select exactly one mode

The local operator supplies `CLAFACT_MLOPS_MODE`. It must be one of:

### Mode A: `operational_cycle`

Use this only when the operator has supplied an explicit reviewed local RSS
config through `CLAFACT_RSS_CONFIG`. Execute the single bounded cycle in
`clafact_operational_cycle_operator_prompt.md`. It processes at most five
R1-ready records and returns only aggregate stage status/counts.

If no source is enabled or no full text is available, report `NO_R1_READY` and
stop. Do not retry and do not activate a source.

### Mode B: `post_run_gold_evaluation`

Use this only after an operational run has finished and the operator supplied
one run manifest, frozen Gold, saved predictions, and a new evaluation ID.
Execute `tools/run_mlops_gold_evaluation.py`. This mode is offline: do not call
RSS, KOSIS API, or an LLM API. Preserve incomplete or zero joins as PARTIAL or
NOT_EVALUABLE and stop after writing the immutable evaluation directory.

### Mode C: `gold_replay_evaluation`

Use this only with fixed Gold, frozen R2 Gold claims, and stored snapshots.
Execute the replay and its offline evaluator under new run/evaluation IDs.
Every summary must retain `CONDITIONAL_REPLAY_NOT_END_TO_END`,
`R1_SENTENCE_REPLAY_DIAGNOSTIC_NOT_FULL_ARTICLE_RECALL`,
`R3_CONDITIONAL_ON_FROZEN_R2_GOLD`, `R2_INPUT_SOURCE=FROZEN_GOLD`, and
`SNAPSHOT_ONLY_NO_LATEST_API_SUBSTITUTION`. Never describe this result as raw
article-to-Verdict end-to-end accuracy.

### Mode D: `r2_dev_experiment`

Use this only when the operator has explicitly approved exactly one change and
provided its `CLAFACT_EXPERIMENT_ID`, provider, model, prompt version, and
one-sentence change reason. Follow
`clafact_r2_experiment_operator_prompt.md` exactly:

1. optional smoke of 10 dev records;
2. full fixed dev split (270 records);
3. score with the fixed v2 scorer;
4. build the scoreboard and text-free error queue.

The new report must be compared with existing comparable dev baselines. Do not
replace a baseline, do not run test, and do not automatically apply the next
change.

## Required response

For `operational_cycle`, return the exact compact format defined in the
operational prompt. For both Gold evaluation modes, return only mode, status,
stage counts, metric values, reason codes, artifact hashes, and required scope
labels. For `r2_dev_experiment`, return exactly:

```text
CLAFACT_R2_DEV_EXPERIMENT
EXPERIMENT_ID: <id>
STATUS: PASS | HOLD | FAILED
RESPONSE_RATE: <number or NOT_RUN>
PARSE_STATUS_MACRO_F1: <number or NOT_RUN>
ALL_12_SLOT_MACRO_ACCURACY: <number or NOT_RUN>
BASELINE_COMPARISON: IMPROVED | NOT_IMPROVED | NOT_ELIGIBLE | NOT_RUN
TOP_ERROR_SLOTS: <up to three slot names or NOT_RUN>
NEXT_ACTION: <one short action>
```

After one mode completes, stop. The next cycle, source approval, code change,
or schedule requires a separate operator decision.
