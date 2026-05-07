#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_regret_complete_n6_c2_10seeds/20260416-191730-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi

MAX_CONCURRENT_JUDGES="${MAX_CONCURRENT_JUDGES:-16}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

PLOTS_ROOT="${PLOTS_ROOT:-experiments/collusion/plots_outputs/collusion_regret_complete_n6_c2_10seeds/20260416-191730-10seeds/regret_report}"
PLOT_SCRIPT="${PLOT_SCRIPT:-experiments/collusion/scripts/plot_jira_regret_10seeds.sh}"
DRY_RUN=0
for arg in "$@"; do
  if [[ "${arg}" == "--dry-run" ]]; then
    DRY_RUN=1
  fi
done

run_foundry_judge_and_plot() {
  local tag="$1"
  local model="$2"
  local endpoint_env="$3"
  local key_env="$4"
  local auth_mode="$5"
  shift 5

  local judge_args=(
    --root "${ROOT}"
    --max-concurrent "${MAX_CONCURRENT_JUDGES}"
    --judge-provider foundry
    --judge-model "${model}"
    --judge-output-tag "${tag}"
    --judge-project-endpoint-env-var "${endpoint_env}"
    --judge-api-key-env-var "${key_env}"
  )
  if [[ -n "${auth_mode}" ]]; then
    judge_args+=(--judge-auth-mode "${auth_mode}")
  fi

  "${PYTHON_BIN}" -m experiments.collusion.judge_blackboards \
    "${judge_args[@]}" \
    "$@"

  if [[ "${DRY_RUN}" == "1" ]]; then
    return
  fi

  local out_dir="${PLOTS_ROOT}/complete_n6_c2__${tag}"
  JUDGE_OUTPUT_TAG="${tag}" \
    OUT_DIR="${out_dir}" \
    PYTHON_BIN="${PYTHON_BIN}" \
    MPLCONFIGDIR="${MPLCONFIGDIR}" \
    "${PLOT_SCRIPT}" "${ROOT}"
}

run_foundry_judge_and_plot \
  "${GPT54_NANO_JUDGE_OUTPUT_TAG:-foundry__gpt-5.4-nano}" \
  "${GPT54_NANO_JUDGE_MODEL:-gpt-5.4-nano}" \
  "${GPT54_NANO_JUDGE_PROJECT_ENDPOINT_ENV_VAR:-AI_FOUNDRY_PROJECT_ENDPOINT}" \
  "${GPT54_NANO_JUDGE_API_KEY_ENV_VAR:-AI_FOUNDRY_API_KEY}" \
  "${GPT54_NANO_JUDGE_AUTH_MODE:-api_key}" \
  "$@"

run_foundry_judge_and_plot \
  "${OPUS46_JUDGE_OUTPUT_TAG:-foundry__claude-opus-4-6}" \
  "${OPUS46_JUDGE_MODEL:-claude-opus-4-6}" \
  "${OPUS46_JUDGE_PROJECT_ENDPOINT_ENV_VAR:-AI_FOUNDRY_RBR_EAST_US_2_PROJECT_ENDPOINT}" \
  "${OPUS46_JUDGE_API_KEY_ENV_VAR:-AI_FOUNDRY_RBR_EAST_US_2_API_KEY}" \
  "${OPUS46_JUDGE_AUTH_MODE:-api_key}" \
  "$@"
