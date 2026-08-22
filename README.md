# CLAFACT FlyHermes deployment overlay

This repository carries the bounded, reviewable overlay used to move the
CLAFACT MLOps Gold-evaluation controller to FlyHermes.

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

## Layout

Files below `overlay/` are copied over the existing FlyHermes checkout while
preserving the same relative paths. Existing files are backed up under the
persistent FlyHermes state directory before deployment.

`deployment_sha256.json` records the SHA-256 of every overlay file so the
server deployment can be verified without exposing source data.

After a saved pipeline run, `tools/run_linked_post_run_evaluation.py` creates a
new immutable derived config and runs exactly one offline Gold evaluation. It
discovers existing R3/R4 artifacts but never fabricates missing predictions,
calls an API, retries an operational run, or overwrites an evaluation ID.
