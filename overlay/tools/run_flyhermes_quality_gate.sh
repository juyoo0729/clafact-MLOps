#!/bin/sh
set -eu

REPO=/opt/data/clafact-auto
STATE_ROOT=/opt/data/clafact_state
ENV_FILE=/opt/data/.hermes/.env
PYTHON_BIN=/opt/data/clafact_state/venvs/clafact-auto/bin/python

if [ ! -f "$ENV_FILE" ]; then
  echo "HOLD_SECRET_FILE_NOT_FOUND"
  exit 2
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "HOLD_CLAFACT_PYTHON_NOT_FOUND"
  exit 2
fi
if [ ! -d "$REPO" ]; then
  echo "HOLD_WORKSPACE_NOT_FOUND"
  exit 2
fi

cd "$REPO"
exec "$PYTHON_BIN" tools/run_mlops_quality_gate.py \
  --state-root "$STATE_ROOT" \
  --workspace "$REPO" \
  --env-file "$ENV_FILE" \
  --require-secret OPENAI_API_KEY \
  --require-secret KOSIS_API_KEY \
  --timeout-seconds 600 \
  "$@"
