# CLAFACT FlyHermes deployment overlay

This repository carries the bounded, reviewable overlay used to move the
CLAFACT MLOps Gold-evaluation controller to FlyHermes.

## Safety boundary

- One controller mode per invocation.
- No scheduler and no automatic retry.
- `post_run_gold_evaluation` is offline: it reads stored manifests,
  predictions, and Gold only.
- Operational counts and Gold accuracy remain separate namespaces.
- R1 Gold CSV, API keys, RSS article text, and article URLs are not stored in
  this public repository.

## Layout

Files below `overlay/` are copied over the existing FlyHermes checkout while
preserving the same relative paths. Existing files are backed up under the
persistent FlyHermes state directory before deployment.

`deployment_sha256.json` records the SHA-256 of every overlay file so the
server deployment can be verified without exposing source data.
