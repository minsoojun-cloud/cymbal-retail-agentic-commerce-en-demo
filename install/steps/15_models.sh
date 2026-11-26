#!/usr/bin/env bash
# 15_models.sh — check that the configured Gemini models answer on Vertex AI; record fallbacks if they do not
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  python3 "${INSTALL_DIR}/lib/probe_models.py" --project "${PROJECT_ID}" --location "${GEMINI_LOCATION}" --dry-run
  exit 0
fi
python3 "${INSTALL_DIR}/lib/probe_models.py" --project "${PROJECT_ID}" --location "${GEMINI_LOCATION}" \
  --out "${STATE_DIR}/models.env"
