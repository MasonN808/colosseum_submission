#!/usr/bin/env bash
set -euo pipefail

DEFAULT_ROOT="experiments/collusion/outputs/collusion_jira_complete_n6_c2_secret_channels/20260416-191730-secret-channel-sweep"
ROOT="${ROOT:-${DEFAULT_ROOT}}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  ROOT="$1"
  shift
fi
CONFIG="${CONFIG:-experiments/collusion/configs/collusion_jira_complete_n6_c2_secret_channel_counts.yaml}"

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
