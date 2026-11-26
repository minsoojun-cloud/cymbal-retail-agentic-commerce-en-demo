#!/usr/bin/env bash
# shellcheck shell=bash
# ==============================================================================
# install/lib/common.sh — shared helpers for install.sh, uninstall.sh and install/steps/*.sh
# ==============================================================================
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${INSTALL_DIR}/.." && pwd)"
STATE_DIR="${INSTALL_DIR}/.state"
export INSTALL_DIR REPO_ROOT STATE_DIR
mkdir -p "${STATE_DIR}"

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_YELLOW=$'\033[1;33m'; C_RED=$'\033[1;31m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""
fi
log_info()  { printf '%s[INFO]%s  %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
log_ok()    { printf '%s[ OK ]%s  %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
log_warn()  { printf '%s[WARN]%s  %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
log_err()   { printf '%s[FAIL]%s  %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }
log_step()  { printf '\n%s━━━ %s ━━━%s\n' "${C_BOLD}" "$*" "${C_RESET}"; }

# load_config: resolve install/config.env (or $CYMBAL_CONFIG) + derived values from the repo-root config.env.
load_config() {
  export CYMBAL_CONFIG="${CYMBAL_CONFIG:-${INSTALL_DIR}/config.env}"
  if [[ ! -f "${CYMBAL_CONFIG}" && -z "${PROJECT_ID:-}" ]]; then
    log_err "No configuration found. Run: cp install/config.example.env install/config.env && edit PROJECT_ID"
    exit 1
  fi
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/config.env"
  if [[ -z "${PROJECT_NUMBER:-}" ]]; then
    log_err "Could not resolve the project number of '${PROJECT_ID}' (does the project exist and can you access it?)"
    exit 1
  fi
  export ADK_AGENT_URL="${ADK_AGENT_URL:-https://${ADK_SERVICE}-${RUN_SUFFIX}}"
  export CUSTOMER_URL="${CUSTOMER_URL:-${STORE_APP_URL}}"
  export STORE_URL="${STORE_URL:-${SITE_BASE_URL}}"
}

# run_or_echo: execute a command, or only print it when DRY_RUN=1.
run_or_echo() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    printf '  [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

# retry <attempts> <sleep-seconds> <command...>
retry() {
  local n="$1" s="$2"; shift 2
  local i
  for ((i = 1; i <= n; i++)); do
    if "$@"; then return 0; fi
    [[ $i -lt $n ]] && sleep "$s"
  done
  return 1
}

# grant_project_role <member> <role> — idempotent, retried (IAM propagation)
grant_project_role() {
  local member="$1" role="$2"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then printf '  [dry-run] grant %s -> %s\n' "$role" "$member"; return 0; fi
  if retry 4 8 gcloud projects add-iam-policy-binding "${PROJECT_ID}" --member "${member}" --role "${role}" \
       --condition=None --quiet >/dev/null 2>&1; then
    printf '  + %s -> %s\n' "${role}" "${member}"
  else
    log_warn "could not grant ${role} to ${member}"
  fi
}

service_url() {  # actual URL of a deployed Cloud Run service (empty if not deployed)
  gcloud run services describe "$1" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)' 2>/dev/null || true
}

http_status() {  # anonymous HTTP status of a URL
  curl -s -o /dev/null -m 30 -w '%{http_code}' "$1" 2>/dev/null || echo 000
}

retail_post() {  # $1=url $2=json body; accepts 200 or ALREADY_EXISTS-style answers; retries for API propagation
  local url="$1" body="$2" out code attempt
  for attempt in 1 2 3 4 5 6; do
    out=$(curl -sS -w '\n%{http_code}' -X POST \
      -H "Authorization: Bearer $(gcloud auth print-access-token)" \
      -H "X-Goog-User-Project: ${PROJECT_ID}" -H "Content-Type: application/json" \
      -d "${body}" "${url}")
    code=$(tail -n1 <<<"${out}")
    if [[ "${code}" == 200 ]] || grep -qiE 'ALREADY_EXISTS|already enrolled|already accepted|already been accepted' <<<"${out}"; then
      printf '  OK (%s): %s\n' "${code}" "$(head -n -1 <<<"${out}" | tr -d '\n' | head -c 200)"
      return 0
    fi
    printf '  attempt %s HTTP %s: %s\n' "${attempt}" "${code}" "$(head -n -1 <<<"${out}" | tr -d '\n' | head -c 200)"
    sleep 10
  done
  return 1
}

print_config_summary() {
  cat <<EOF
  Project           : ${PROJECT_ID} (#${PROJECT_NUMBER})
  Region            : ${REGION}
  Service prefix    : ${SERVICE_PREFIX}
  Runtime SA        : ${RUNTIME_SA}
  BigQuery datasets : ${BQ_DATASET}, ${ENRICH_BQ_DATASET} (${BQ_LOCATION})
  Buckets           : ${GCS_BUCKET}, ${PHOTO_BUCKET}, ${IMAGE_BUCKET}
  Models            : site/store=${GEMINI_MODEL}  agent=${AGENT_MODEL}  enrichment=${ENRICH_GEMINI_MODEL}  image=${IMAGE_MODEL}
  Online store      : ${SITE_BASE_URL}
  Store app         : ${STORE_APP_URL}
  Staff app         : ${STAFF_URL}
  Enrichment Studio : ${ENRICH_URL}
EOF
}
