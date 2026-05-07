#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_hospital_complete_n9_c4_10seeds/20260423-180614-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
CONFIG="${CONFIG:-experiments/collusion/configs/collusion_hospital_complete_n9_c4_regret_models_foundry_additions_6_models_10seeds.yaml}"
MAX_CONCURRENT_RUNS="${MAX_CONCURRENT_RUNS:-5}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

ERROR_RUN_DIR="${ROOT}/runs/claude-opus-4-6/complete_n9_c4/claude-opus-4-6__complete_n9_c4__complete__n9__c4__secret1__pvsimple__seed1__replica1"
STALE_JUDGE_FILE="${ROOT}/runs/claude-opus-4-6/judge_secret_blackboard__gpt54/complete_n9_c4/claude-opus-4-6__complete_n9_c4__complete__n9__c4__secret1__pvsimple__seed1__replica1.json"
if [[ -f "${ERROR_RUN_DIR}/agent_turns.json" ]] \
  && command -v rg >/dev/null 2>&1 \
  && rg -q "\\[ERROR\\]" "${ERROR_RUN_DIR}/agent_turns.json" \
  && [[ -f "${STALE_JUDGE_FILE}" ]]; then
  mv -n "${STALE_JUDGE_FILE}" "${STALE_JUDGE_FILE}.stale-before-10seeds"
fi

"${PYTHON_BIN}" -m experiments.collusion.resume \
  --root "${ROOT}" \
  --config "${CONFIG}" \
  --max-concurrent-runs "${MAX_CONCURRENT_RUNS}" \
  "$@"
