#!/usr/bin/env bash
# Deploy cymbal-commerce-mcp to Cloud Run (private; invoked by GECX / CES with an ID token).
# Usage: ./deploy.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "${HERE}/../../config.env"   # PROJECT_ID, PROJECT_NUMBER, REGION, BQ_DATASET, RUNTIME_SA, MCP_SERVICE, PATH

SERVICE="${MCP_SERVICE:-cymbal-commerce-mcp}"

# Bundle fallback persona data (used when BigQuery is unreachable). Keeps CR-0001 data.
python3 "${HERE}/prepare_data.py"

gcloud run deploy "${SERVICE}" --quiet \
  --project "${PROJECT_ID}" \
  --source "${HERE}" \
  --region "${REGION:-asia-northeast1}" \
  --service-account "${RUNTIME_SA}" \
  --no-allow-unauthenticated \
  --cpu 1 --memory 1Gi --concurrency 40 --timeout 60 \
  --min-instances 1 --max-instances 5 \
  --set-env-vars "PROJECT_ID=${PROJECT_ID},LOCATION=global,CATALOG=default_catalog,BRANCH=0,SERVING_CONFIG=default_search,BQ_DATASET=${BQ_DATASET:-cymbal},BQ_LOCATION=asia-northeast1,COMPACT_RESULTS=true,DINNER_WARM=true"

URL=$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION:-asia-northeast1}" --format='value(status.url)')
echo "MCP endpoint: ${URL}/mcp"
echo "Smoke test : python3 ${HERE}/testing.py --url ${URL} --auth"
