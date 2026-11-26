#!/usr/bin/env bash
# ==============================================================================
# install/install.sh — one-shot installer for the Cymbal Retail Agentic Commerce demo (English edition)
# ==============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

ALL_STEPS=(00_preflight 10_apis 15_models 20_iam 30_retail 40_data 50_catalog_import 60_backend 70_apps 80_public 90_verify)

step_title() {
  case "$1" in
    00_preflight)      echo "Preflight checks (CLIs, auth, project, billing, config)" ;;
    10_apis)           echo "Enable Google Cloud APIs" ;;
    15_models)         echo "Verify Gemini models on Vertex AI (with fallbacks)" ;;
    20_iam)            echo "Runtime service account, IAM roles, build permissions" ;;
    30_retail)         echo "AI Commerce Search: terms, enrollment, service agent" ;;
    40_data)           echo "Buckets, BigQuery datasets and seed data (stores, shelves, customers, enrichment)" ;;
    50_catalog_import) echo "Catalog: BigQuery products, AI Commerce Search FULL import, attributes, autocomplete, Local Inventory" ;;
    60_backend)        echo "Deploy private backends (Commerce MCP server, ADK agent)" ;;
    70_apps)           echo "Deploy the 4 apps (online store, store app, staff app, Enrichment Studio)" ;;
    80_public)         echo "Public access for the 4 apps (handles Domain-Restricted-Sharing orgs)" ;;
    90_verify)         echo "Smoke test + URL summary" ;;
    *)                 echo "$1" ;;
  esac
}

usage() {
  cat <<'EOF'
Usage: ./install/install.sh [options]

Installs the complete Cymbal Retail Agentic Commerce demo (2 private backends + 4 public apps + 3,000-product
catalog, BigQuery store/shelf/customer data, AI Commerce Search Local Inventory) into a Google Cloud project.

Options:
  --project <PROJECT_ID>   target project (overrides install/config.env)
  --region <REGION>        Cloud Run / BigQuery / GCS region (default asia-northeast1)
  --prefix <PREFIX>        Cloud Run service-name prefix (default cymbal)
  --config <FILE>          configuration file (default install/config.env)
  --steps <s1,s2,...>      run only these steps (e.g. 70_apps,90_verify)
  --from <STEP>            run from this step to the end (e.g. 50_catalog_import)
  --resume                 skip steps already completed (install/.state/<step>.done)
  --sequential             deploy the 4 apps one after another (default: in parallel)
  --dry-run                resolve configuration and print the plan / commands without changing anything
  -y, --yes                do not ask for confirmation
  -h, --help               show this help

Steps:
  00_preflight  10_apis  15_models  20_iam  30_retail  40_data  50_catalog_import  60_backend  70_apps  80_public  90_verify
EOF
}

SELECTED_STEPS=""; FROM_STEP=""; RESUME=0; ASSUME_YES=0
export DRY_RUN="${DRY_RUN:-0}" SEQUENTIAL="${SEQUENTIAL:-0}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)    export PROJECT_ID="${2:?}"; shift 2 ;;
    --region)     export REGION="${2:?}"; shift 2 ;;
    --prefix)     export SERVICE_PREFIX="${2:?}"; shift 2 ;;
    --config)     CYMBAL_CONFIG="$(cd "$(dirname "${2:?}")" && pwd)/$(basename "$2")"; export CYMBAL_CONFIG; shift 2 ;;
    --steps)      SELECTED_STEPS="${2:?}"; shift 2 ;;
    --from)       FROM_STEP="${2:?}"; shift 2 ;;
    --resume)     RESUME=1; shift ;;
    --sequential) SEQUENTIAL=1; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -y|--yes)     ASSUME_YES=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) log_err "unknown option: $1"; usage; exit 2 ;;
  esac
done

load_config

# ---- resolve the step plan ----
PLAN=()
if [[ -n "${SELECTED_STEPS}" ]]; then
  IFS=',' read -r -a PLAN <<<"${SELECTED_STEPS}"
elif [[ -n "${FROM_STEP}" ]]; then
  started=0
  for s in "${ALL_STEPS[@]}"; do [[ "$s" == "${FROM_STEP}" ]] && started=1; [[ $started == 1 ]] && PLAN+=("$s"); done
  [[ ${#PLAN[@]} -gt 0 ]] || { log_err "unknown step: ${FROM_STEP}"; exit 2; }
else
  PLAN=("${ALL_STEPS[@]}")
fi
for s in "${PLAN[@]}"; do
  [[ -f "${SCRIPT_DIR}/steps/${s}.sh" ]] || { log_err "unknown step: ${s}"; exit 2; }
done

printf '%s\n' "${C_BOLD}Cymbal Retail Agentic Commerce demo — installer${C_RESET}"
print_config_summary
echo "  Steps             : ${PLAN[*]}"
[[ "${DRY_RUN}" == "1" ]] && echo "  Mode              : DRY RUN (no changes)"
if [[ "${ASSUME_YES}" != "1" && "${DRY_RUN}" != "1" && -t 0 ]]; then
  read -r -p "Proceed? [y/N] " ans
  [[ "${ans}" =~ ^[Yy] ]] || { echo "aborted"; exit 1; }
fi

START_TS=$(date +%s)
for s in "${PLAN[@]}"; do
  if [[ "${RESUME}" == "1" && -f "${STATE_DIR}/${s}.done" ]]; then
    log_info "skip ${s} (already done — remove ${STATE_DIR}/${s}.done to re-run)"
    continue
  fi
  log_step "${s} — $(step_title "$s")"
  t0=$(date +%s)
  if bash "${SCRIPT_DIR}/steps/${s}.sh"; then
    [[ "${DRY_RUN}" == "1" ]] || date -u +%FT%TZ > "${STATE_DIR}/${s}.done"
    log_ok "${s} finished in $(( $(date +%s) - t0 ))s"
  else
    rc=$?
    log_err "${s} failed (exit ${rc}). Fix the problem and re-run with: ./install/install.sh --from ${s}"
    exit "${rc}"
  fi
done
log_ok "All steps finished in $(( ($(date +%s) - START_TS) / 60 )) min."
