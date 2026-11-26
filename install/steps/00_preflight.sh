#!/usr/bin/env bash
# 00_preflight.sh — CLIs, authentication, project access, billing, configuration sanity
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

fail=0
for cli in gcloud bq python3 curl; do
  if command -v "${cli}" >/dev/null 2>&1; then log_ok "${cli}: $(command -v "${cli}")"; else log_err "${cli} not found in PATH"; fail=1; fi
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { log_err "python3 >= 3.9 is required"; fail=1; }
[[ ${fail} == 0 ]] || exit 1

ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)"
[[ -n "${ACCOUNT}" ]] || { log_err "no active gcloud account — run: gcloud auth login"; exit 1; }
log_ok "gcloud account: ${ACCOUNT}"
gcloud auth print-access-token >/dev/null 2>&1 || { log_err "cannot mint an access token — run: gcloud auth login"; exit 1; }

STATE="$(gcloud projects describe "${PROJECT_ID}" --format='value(lifecycleState)' 2>/dev/null || true)"
[[ "${STATE}" == "ACTIVE" ]] || { log_err "project ${PROJECT_ID} is not accessible or not ACTIVE (state='${STATE}')"; exit 1; }
log_ok "project ${PROJECT_ID} (#${PROJECT_NUMBER}) is ACTIVE"

BILLING="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null || true)"
case "${BILLING}" in
  True|true) log_ok "billing is enabled" ;;
  False|false) log_err "billing is NOT enabled on ${PROJECT_ID}"; exit 1 ;;
  *) log_warn "could not read the billing status (needs billing.resourceAssociations.list) — continuing" ;;
esac

[[ "${SERVICE_PREFIX}" =~ ^[a-z][a-z0-9-]{0,20}$ ]] || { log_err "SERVICE_PREFIX must be lowercase letters/digits/dashes (max 21 chars)"; exit 1; }
for f in seed/catalog/products_retail.jsonl seed/bigquery/cymbal/stores.jsonl.gz online-store/static/img/products; do
  [[ -e "${REPO_ROOT}/${f}" ]] || { log_err "missing ${f} — is the repository complete (git lfs not needed)?"; exit 1; }
done
log_ok "seed files present ($(wc -l < "${REPO_ROOT}/seed/catalog/products_retail.jsonl") products, $(find "${REPO_ROOT}/online-store/static/img/products" -type f | wc -l) product images)"
print_config_summary
