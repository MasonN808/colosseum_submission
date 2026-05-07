#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_hospital_complete_n9_c4_10seeds/20260423-180614-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
CONFIG="${CONFIG:-experiments/collusion/configs/collusion_hospital_complete_n9_c4_regret_models_foundry_additions_6_models_10seeds.yaml}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

"${PYTHON_BIN}" -m experiments.collusion.resume \
  --root "${ROOT}" \
  --config "${CONFIG}" \
  --dry-run \
  "$@"
