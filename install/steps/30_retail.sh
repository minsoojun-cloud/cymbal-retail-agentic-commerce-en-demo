#!/usr/bin/env bash
# 30_retail.sh — AI Commerce Search (Retail API): accept data-use terms, enroll Search, service agent roles
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

API="https://retail.googleapis.com/v2alpha/projects/${PROJECT_ID}"
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "  [dry-run] POST ${API}/retailProject:acceptTerms"
  echo "  [dry-run] POST ${API}:enrollSolution {SOLUTION_TYPE_SEARCH}"
  echo "  [dry-run] create Retail service identity + grant BigQuery dataViewer/jobUser + bucket objectViewer"
  exit 0
fi
log_info "accept AI Commerce Search data-use terms"
retail_post "${API}/retailProject:acceptTerms" '{}' || log_warn "acceptTerms failed (accept them once in the Cloud Console: AI Commerce Search > Get started)"
log_info "enroll SOLUTION_TYPE_SEARCH"
retail_post "${API}:enrollSolution" '{"solution":"SOLUTION_TYPE_SEARCH"}' || log_warn "enrollSolution failed (it may already be enrolled)"

RETAIL_AGENT="service-${PROJECT_NUMBER}@gcp-sa-retail.iam.gserviceaccount.com"
gcloud beta services identity create --service retail.googleapis.com --project "${PROJECT_ID}" >/dev/null 2>&1 || true
grant_project_role "serviceAccount:${RETAIL_AGENT}" roles/bigquery.dataViewer
grant_project_role "serviceAccount:${RETAIL_AGENT}" roles/bigquery.jobUser
log_ok "AI Commerce Search ready (catalog projects/${PROJECT_NUMBER}/locations/global/catalogs/default_catalog)"
