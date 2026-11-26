#!/usr/bin/env bash
# 80_public.sh — make the 4 apps reachable without sign-in.
#   1) already public?                          -> nothing to do
#   2) grant roles/run.invoker to allUsers       -> works in projects without Domain Restricted Sharing
#   3) fallback: disable the Cloud Run invoker IAM check (--no-invoker-iam-check), the supported way to serve
#      public traffic when the org policy iam.allowedPolicyMemberDomains blocks allUsers bindings.
#   The two backends (MCP server, ADK agent) always stay private (IAM-protected, invoked by the runtime SA).
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

blocked=()
for svc in "${SITE_SERVICE}" "${STORE_APP_SERVICE}" "${STAFF_SERVICE}" "${ENRICH_SERVICE}"; do
  url="$(service_url "${svc}")"
  if [[ -z "${url}" ]]; then log_warn "${svc} is not deployed"; blocked+=("${svc}"); continue; fi
  code="$(http_status "${url}/api/health")"
  if [[ "${code}" == "200" ]]; then log_ok "${svc}: public (HTTP 200)"; continue; fi
  if [[ "${DRY_RUN:-0}" == "1" ]]; then echo "  [dry-run] ${svc}: HTTP ${code} -> would grant allUsers or set --no-invoker-iam-check"; continue; fi
  log_info "${svc}: HTTP ${code} — granting roles/run.invoker to allUsers"
  if gcloud run services add-iam-policy-binding "${svc}" --project "${PROJECT_ID}" --region "${REGION}" \
       --member allUsers --role roles/run.invoker --quiet >/dev/null 2>&1; then
    sleep 5; code="$(http_status "${url}/api/health")"
  else
    log_warn "${svc}: allUsers binding rejected (Domain Restricted Sharing org policy?) — using --no-invoker-iam-check"
    gcloud run services update "${svc}" --project "${PROJECT_ID}" --region "${REGION}" --no-invoker-iam-check --quiet >/dev/null 2>&1 \
      || log_warn "${svc}: --no-invoker-iam-check failed (needs run.services.setIamPolicy / org policy run.managed.requireInvokerIam off)"
    sleep 5; code="$(http_status "${url}/api/health")"
  fi
  if [[ "${code}" == "200" ]]; then log_ok "${svc}: public (HTTP 200)"; else log_warn "${svc}: still HTTP ${code}"; blocked+=("${svc}"); fi
done

if [[ ${#blocked[@]} -gt 0 ]]; then
  cat <<EOM

Some apps are not publicly reachable: ${blocked[*]}
  • Cloud Console > Cloud Run > <service> > Security > Authentication: "Allow public access", or
  • ask an org admin to allow it (iam.allowedPolicyMemberDomains / run.managed.requireInvokerIam), or
  • open the app privately through an authenticated local proxy:
      gcloud run services proxy ${SITE_SERVICE} --project ${PROJECT_ID} --region ${REGION} --port 8080
EOM
  exit 1
fi
