#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_regret_complete_n6_c2_10seeds/20260416-191730-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
DEFAULT_OUT_DIR="experiments/collusion/plots_outputs/collusion_regret_complete_n6_c2_10seeds/20260416-191730-10seeds/regret_report/complete_n6_c2"
DEFAULT_JUDGE_OUTPUT_TAG="foundry__gpt-5.4"
JUDGE_OUTPUT_TAG="${JUDGE_OUTPUT_TAG:-${DEFAULT_JUDGE_OUTPUT_TAG}}"
if [[ -z "${OUT_DIR:-}" ]]; then
  if [[ "${JUDGE_OUTPUT_TAG}" == "${DEFAULT_JUDGE_OUTPUT_TAG}" ]]; then
    OUT_DIR="${DEFAULT_OUT_DIR}"
  else
    OUT_DIR="${DEFAULT_OUT_DIR}__${JUDGE_OUTPUT_TAG}"
  fi
fi
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
else
  PYTHON_BIN="${PYTHON_BIN:-python}"
fi

REPORT_ARGS=(
  --root "${ROOT}"
  --sweep-name complete_n6_c2
  --topology complete
  --num-agents 6
  --colluder-count 2
  --out-dir "${OUT_DIR}"
  --compute-optimal
)

if [[ -n "${JUDGE_OUTPUT_TAG}" ]]; then
  REPORT_ARGS+=(--judge-output-tag "${JUDGE_OUTPUT_TAG}")
fi

"${PYTHON_BIN}" -m experiments.collusion.plots.generate_regret_report \
  "${REPORT_ARGS[@]}" \
  "$@"

CSV_PATH="${OUT_DIR}/plots/regret_report__normalized_regret__coalition_gap__judge__data.csv"
JUDGE_SCATTER_OUT="${OUT_DIR}/plots/judge_vs_coalition_advantage_scatter.png"
REGRET_SCATTER_OUT="${OUT_DIR}/plots/regret_vs_coalition_advantage_scatter.png"
SCATTER_TITLE_ARGS=()
if [[ -n "${PLOT_HEADER:-}" ]]; then
  SCATTER_TITLE_ARGS=(--title "${PLOT_HEADER}")
fi
SCATTER_STYLE_ARGS=(
  --legend-loc "upper left"
  --marker-scale 1.05
  --legend-marker-scale 1.1
  --legend-font-scale 1.05
  --x-step-limits 0.05
)

"${PYTHON_BIN}" -m experiments.collusion.plots.plot_judge_vs_coalition_advantage \
  "${CSV_PATH}" \
  --out "${JUDGE_SCATTER_OUT}" \
  --y-metric judge \
  --y-step-limits 0.5 \
  "${SCATTER_TITLE_ARGS[@]}" \
  "${SCATTER_STYLE_ARGS[@]}"

"${PYTHON_BIN}" -m experiments.collusion.plots.plot_judge_vs_coalition_advantage \
  "${CSV_PATH}" \
  --out "${REGRET_SCATTER_OUT}" \
  --y-metric regret \
  "${SCATTER_TITLE_ARGS[@]}" \
  "${SCATTER_STYLE_ARGS[@]}"
