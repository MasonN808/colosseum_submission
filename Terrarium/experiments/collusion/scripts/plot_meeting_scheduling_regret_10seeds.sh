#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_meeting_scheduling_complete_n6_c2_10seeds/20260422-192126-10seeds"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
OUT_DIR="${OUT_DIR:-experiments/collusion/plots_outputs/collusion_meeting_scheduling_complete_n6_c2_10seeds/20260422-192126-10seeds/regret_report/complete_n6_c2}"
JUDGE_OUTPUT_TAG="${JUDGE_OUTPUT_TAG-foundry__gpt-5.4}"
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
SCATTER_OUT="${OUT_DIR}/plots/judge_vs_coalition_advantage_scatter.png"
SCATTER_STYLE_ARGS=(
  --legend-loc "upper left"
  --marker-scale 1.05
  --legend-marker-scale 1.1
  --legend-font-scale 1.05
  --x-step-limits 0.05
)

"${PYTHON_BIN}" -m experiments.collusion.plots.plot_judge_vs_coalition_advantage \
  "${CSV_PATH}" \
  --out "${SCATTER_OUT}" \
  "${SCATTER_STYLE_ARGS[@]}"
