#!/usr/bin/env bash
# 70_apps.sh — deploy the 4 public apps (in parallel unless --sequential):
#   online store (${SITE_SERVICE}), store app (${STORE_APP_SERVICE}), staff app (${STAFF_SERVICE}),
#   Enrichment Studio (${ENRICH_SERVICE}). Cross links use the deterministic URLs from config.env.
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

python3 "${INSTALL_DIR}/lib/render_seed.py" --catalog-only
MCP_LIVE="$(service_url "${MCP_SERVICE}")"
if [[ -n "${MCP_LIVE}" ]]; then export MCP_URL="${MCP_LIVE}"; fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "  [dry-run] online-store/deploy.sh   -> ${SITE_SERVICE}"
  echo "  [dry-run] store-navi/deploy.sh     -> ${STORE_APP_SERVICE} (customer) and ${STAFF_SERVICE} (staff)"
  echo "  [dry-run] data-enrichment/deploy.sh -> ${ENRICH_SERVICE}"
  SERVICE_NAME="${STORE_APP_SERVICE}" DRY_RUN=1 bash "${REPO_ROOT}/store-navi/deploy.sh" | tail -n 3
  exit 0
fi

LOG_DIR="${STATE_DIR}/logs"; mkdir -p "${LOG_DIR}"
deploy_one() {  # $1=label $2...=command
  local label="$1"; shift
  if "$@" >"${LOG_DIR}/${label}.log" 2>&1; then
    log_ok "${label} deployed"
  else
    log_err "${label} failed — see ${LOG_DIR}/${label}.log"; tail -n 20 "${LOG_DIR}/${label}.log" >&2; return 1
  fi
}
JOBS=(
  "site|bash ${REPO_ROOT}/online-store/deploy.sh"
  "store-app|env SERVICE_NAME=${STORE_APP_SERVICE} bash ${REPO_ROOT}/store-navi/deploy.sh"
  "store-staff|env SERVICE_NAME=${STAFF_SERVICE} bash ${REPO_ROOT}/store-navi/deploy.sh"
  "data-enrichment|bash ${REPO_ROOT}/data-enrichment/deploy.sh"
)
pids=(); fail=0
for j in "${JOBS[@]}"; do
  label="${j%%|*}"; cmd="${j#*|}"
  log_info "deploying ${label} (log: ${LOG_DIR}/${label}.log)"
  # shellcheck disable=SC2086
  if [[ "${SEQUENTIAL:-0}" == "1" ]]; then deploy_one "${label}" ${cmd} || fail=1
  else deploy_one "${label}" ${cmd} & pids+=($!); sleep 5; fi
done
for p in "${pids[@]}"; do wait "$p" || fail=1; done
[[ ${fail} == 0 ]] || exit 1
for s in "${SITE_SERVICE}" "${STORE_APP_SERVICE}" "${STAFF_SERVICE}" "${ENRICH_SERVICE}"; do
  printf '  %-26s %s\n' "${s}" "$(service_url "${s}")"
done
