#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_regret_complete_n6_c2_10seeds/20260416-191730-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
CONFIG="${CONFIG:-experiments/collusion/configs/collusion_jira_complete_n6_c2_regret_models_foundry_additions_10seeds.yaml}"
MAX_CONCURRENT_RUNS="${MAX_CONCURRENT_RUNS:-5}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

"${PYTHON_BIN}" -m experiments.collusion.resume \
  --root "${ROOT}" \
  --config "${CONFIG}" \
  --max-concurrent-runs "${MAX_CONCURRENT_RUNS}" \
  "$@"
