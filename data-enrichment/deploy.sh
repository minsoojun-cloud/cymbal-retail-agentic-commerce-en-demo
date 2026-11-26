#!/bin/bash
# Deploy the Cymbal Retail Product Data Enrichment Studio to Cloud Run (cymbal-data-enrichment).
#   ./deploy.sh            # uses ../config.env (PROJECT_ID, REGION, RUNTIME_SA) + defaults below
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ../config.env ]; then
  # shellcheck disable=SC1091
  source ../config.env
fi
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${PROJECT_ID:?Set PROJECT_ID (config.env or .env)}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${ENRICH_SERVICE:-cymbal-data-enrichment}"
RUNTIME_SA="${RUNTIME_SA:-cymbal-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
ENRICH_BQ_DATASET="${ENRICH_BQ_DATASET:-cymbal_enrichment}"
BQ_SOURCE_TABLE="${BQ_SOURCE_TABLE:-${PROJECT_ID}.cymbal.products}"
ENRICH_GEMINI_MODEL="${ENRICH_GEMINI_MODEL:-gemini-3.8-flash}"
GEMINI_FALLBACK_MODEL="${GEMINI_FALLBACK_MODEL:-gemini-3.5-flash}"
IMAGE_BUCKET="${IMAGE_BUCKET:-products_images_${PROJECT_ID}}"
IMAGE_MODEL="${IMAGE_MODEL:-gemini-3.1-flash-image}"
IMAGE_MODEL_PRO="${IMAGE_MODEL_PRO:-gemini-3-pro-image}"
IMAGE_MODEL_FALLBACK="${IMAGE_MODEL_FALLBACK:-gemini-2.5-flash-image}"
PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-${ENRICH_URL:-https://${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app}}"
STORE_URL="${STORE_URL:-${SITE_BASE_URL:-https://cymbal-site-${PROJECT_NUMBER}.${REGION}.run.app}}"

echo "🚀 Deploying ${SERVICE_NAME} to ${PROJECT_ID}/${REGION} (model: ${ENRICH_GEMINI_MODEL}, dataset: ${ENRICH_BQ_DATASET})"
# max-instances=1: the demo keeps an in-memory cache (persisted to BigQuery), so a single instance keeps state consistent.
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${RUNTIME_SA}" \
  --no-invoker-iam-check \
  --min-instances 0 --max-instances 1 \
  --memory 2Gi --cpu 2 --timeout 900 \
  --set-env-vars "^@^PROJECT_ID=${PROJECT_ID}@ENRICH_BQ_DATASET=${ENRICH_BQ_DATASET}@BQ_LOCATION=${REGION}@BQ_SOURCE_TABLE=${BQ_SOURCE_TABLE}@ENRICH_GEMINI_MODEL=${ENRICH_GEMINI_MODEL}@GEMINI_FALLBACK_MODEL=${GEMINI_FALLBACK_MODEL}@VERTEX_LOCATION=global@ENRICH_BATCH_MODEL=${ENRICH_GEMINI_MODEL}@BATCH_LOCATION=global@IMAGE_BUCKET=${IMAGE_BUCKET}@IMAGE_MODEL=${IMAGE_MODEL}@IMAGE_MODEL_PRO=${IMAGE_MODEL_PRO}@IMAGE_MODEL_FALLBACK=${IMAGE_MODEL_FALLBACK}@PUBLIC_BASE_URL=${PUBLIC_BASE_URL}@STORE_URL=${STORE_URL}" \
  || { echo "deploy failed"; exit 1; }
echo "✅ Done: $(gcloud run services describe "${SERVICE_NAME}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
