#!/usr/bin/env bash
# 90_verify.sh — end-to-end smoke test (tests/smoke_install.py) and URL summary
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

if [[ "${DRY_RUN:-0}" == "1" ]]; then echo "  [dry-run] python3 tests/smoke_install.py"; exit 0; fi
python3 "${REPO_ROOT}/tests/smoke_install.py"
cat <<EOM

${C_BOLD}Cymbal Retail demo is ready${C_RESET}
  Online store (PC / mobile) : ${SITE_BASE_URL}
  Store app (phone / tablet) : ${STORE_APP_URL}
  Staff app (mobile)         : ${STAFF_URL}
  Manager console (PC)       : ${STAFF_URL}/admin
  Enrichment Studio          : ${ENRICH_URL}
EOM
