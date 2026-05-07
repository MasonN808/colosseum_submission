#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_hospital_complete_n9_c4_10seeds/20260423-180614-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
MAX_CONCURRENT_JUDGES="${MAX_CONCURRENT_JUDGES:-16}"
JUDGE_PROVIDER="${JUDGE_PROVIDER:-foundry}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
JUDGE_OUTPUT_TAG="${JUDGE_OUTPUT_TAG:-gpt54}"
JUDGE_PROJECT_ENDPOINT_ENV_VAR="${JUDGE_PROJECT_ENDPOINT_ENV_VAR:-AI_FOUNDRY_PROJECT_ENDPOINT}"
JUDGE_API_KEY_ENV_VAR="${JUDGE_API_KEY_ENV_VAR:-AI_FOUNDRY_API_KEY}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

JUDGE_ARGS=(
  --judge-provider "${JUDGE_PROVIDER}"
  --judge-model "${JUDGE_MODEL}"
  --judge-output-tag "${JUDGE_OUTPUT_TAG}"
  --judge-project-endpoint-env-var "${JUDGE_PROJECT_ENDPOINT_ENV_VAR}"
  --judge-api-key-env-var "${JUDGE_API_KEY_ENV_VAR}"
)

"${PYTHON_BIN}" -m experiments.collusion.judge_blackboards \
  --root "${ROOT}" \
  --max-concurrent "${MAX_CONCURRENT_JUDGES}" \
  "${JUDGE_ARGS[@]}" \
  "$@"
