#!/usr/bin/env bash
# 40_data.sh — Cloud Storage buckets, BigQuery datasets and the BigQuery seed
#   cymbal.{stores, store_layouts, store_inventory, store_inventory_events, shelf_slots, shelf_locations,
#           store_coupons, store_offers, customers, purchase_history} + cymbal_enrichment.* (63-SKU studio seed)
#   (cymbal.products / autocomplete_suggestions are built from the catalog in 50_catalog_import)
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

log_info "Cloud Storage buckets (${REGION})"
for b in "${GCS_BUCKET}" "${PHOTO_BUCKET}" "${IMAGE_BUCKET}"; do
  if gcloud storage buckets describe "gs://${b}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "  gs://${b} (exists)"
  else
    run_or_echo gcloud storage buckets create "gs://${b}" --project "${PROJECT_ID}" --location "${REGION}" --uniform-bucket-level-access
  fi
done
RETAIL_AGENT="service-${PROJECT_NUMBER}@gcp-sa-retail.iam.gserviceaccount.com"
run_or_echo gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" --project "${PROJECT_ID}" \
  --member "serviceAccount:${RETAIL_AGENT}" --role roles/storage.objectViewer >/dev/null 2>&1 \
  || log_warn "could not grant the Retail service agent read access on gs://${GCS_BUCKET}"

log_info "render seed for ${PROJECT_ID} (URLs -> ${SITE_BASE_URL})"
python3 "${INSTALL_DIR}/lib/render_seed.py"

log_info "BigQuery seed (${BQ_DATASET}, ${ENRICH_BQ_DATASET} in ${BQ_LOCATION})"
ARGS=(--project "${PROJECT_ID}" --location "${BQ_LOCATION}")
[[ "${DRY_RUN:-0}" == "1" ]] && ARGS+=(--dry-run)
python3 "${INSTALL_DIR}/lib/load_seed.py" "${ARGS[@]}"
log_ok "seed data loaded"
