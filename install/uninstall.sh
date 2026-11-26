#!/usr/bin/env bash
# ==============================================================================
# install/uninstall.sh — remove what install.sh created (Cloud Run services, data, buckets, service account)
# ==============================================================================
# By default only the 6 Cloud Run services are deleted. Add flags to delete data too:
#   --data        delete BigQuery datasets (${BQ_DATASET}, ${ENRICH_BQ_DATASET}) and AI Commerce Search products (branch 0)
#   --buckets     delete the 3 Cloud Storage buckets (catalog, shelf photos, enrichment images)
#   --sa          delete the runtime service account
#   --all         all of the above
#   --project / --region / --prefix / --config as in install.sh;  -y to skip the confirmation
# APIs, Retail enrollment and org-level settings are never changed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/common.sh"

DEL_DATA=0; DEL_BUCKETS=0; DEL_SA=0; ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) export PROJECT_ID="${2:?}"; shift 2 ;;
    --region)  export REGION="${2:?}"; shift 2 ;;
    --prefix)  export SERVICE_PREFIX="${2:?}"; shift 2 ;;
    --config)  CYMBAL_CONFIG="$(cd "$(dirname "${2:?}")" && pwd)/$(basename "$2")"; export CYMBAL_CONFIG; shift 2 ;;
    --data)    DEL_DATA=1; shift ;;
    --buckets) DEL_BUCKETS=1; shift ;;
    --sa)      DEL_SA=1; shift ;;
    --all)     DEL_DATA=1; DEL_BUCKETS=1; DEL_SA=1; shift ;;
    -y|--yes)  ASSUME_YES=1; shift ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) log_err "unknown option: $1"; exit 2 ;;
  esac
done
export CYMBAL_SKIP_RENDER=1
load_config

SERVICES=("${SITE_SERVICE}" "${STORE_APP_SERVICE}" "${STAFF_SERVICE}" "${ENRICH_SERVICE}" "${ADK_SERVICE}" "${MCP_SERVICE}")
echo "Project ${PROJECT_ID} / ${REGION}. Will delete:"
printf '  Cloud Run service: %s\n' "${SERVICES[@]}"
[[ ${DEL_DATA} == 1 ]] && echo "  BigQuery datasets: ${BQ_DATASET}, ${ENRICH_BQ_DATASET}; AI Commerce Search products in default_catalog branch 0"
[[ ${DEL_BUCKETS} == 1 ]] && echo "  Buckets: gs://${GCS_BUCKET} gs://${PHOTO_BUCKET} gs://${IMAGE_BUCKET}"
[[ ${DEL_SA} == 1 ]] && echo "  Service account: ${RUNTIME_SA}"
if [[ ${ASSUME_YES} != 1 ]]; then
  read -r -p "Type the project id to confirm: " ans
  [[ "${ans}" == "${PROJECT_ID}" ]] || { echo "aborted"; exit 1; }
fi

for s in "${SERVICES[@]}"; do
  if gcloud run services describe "${s}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
    gcloud run services delete "${s}" --project "${PROJECT_ID}" --region "${REGION}" --quiet && log_ok "deleted ${s}"
  else
    log_info "${s}: not found"
  fi
done

if [[ ${DEL_DATA} == 1 ]]; then
  for ds in "${BQ_DATASET}" "${ENRICH_BQ_DATASET}"; do
    bq --project_id "${PROJECT_ID}" rm -r -f -d "${PROJECT_ID}:${ds}" && log_ok "deleted dataset ${ds}" || log_warn "dataset ${ds}: not deleted"
  done
  log_info "purging AI Commerce Search products (branch 0)"
  curl -sS -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" -d '{"filter":"*","force":true}' \
    "https://retail.googleapis.com/v2/projects/${PROJECT_ID}/locations/global/catalogs/default_catalog/branches/0/products:purge" \
    | head -c 300; echo
fi
if [[ ${DEL_BUCKETS} == 1 ]]; then
  for b in "${GCS_BUCKET}" "${PHOTO_BUCKET}" "${IMAGE_BUCKET}"; do
    gcloud storage rm -r "gs://${b}" --project "${PROJECT_ID}" --quiet >/dev/null 2>&1 && log_ok "deleted gs://${b}" || log_warn "gs://${b}: not deleted"
  done
fi
if [[ ${DEL_SA} == 1 ]]; then
  gcloud iam service-accounts delete "${RUNTIME_SA}" --project "${PROJECT_ID}" --quiet && log_ok "deleted ${RUNTIME_SA}" || true
fi
rm -f "${STATE_DIR}"/*.done
log_ok "uninstall finished"
