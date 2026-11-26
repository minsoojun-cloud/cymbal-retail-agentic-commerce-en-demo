#!/usr/bin/env bash
# Deploy the Cymbal Retail storefront (EN unified demo, CONTRACT_EN) to Cloud Run.
#   ./deploy.sh                      # service ${SITE_SERVICE} (cymbal-site): AGENT_BACKEND=adk, VOICE_ENABLED=1,
#                                    #   ENRICH_URL / STORE_APP_URL from ../config.env, ADK_AGENT_URL from
#                                    #   ../shared/adk_agent/DEPLOYMENT.env (written by shared/adk_agent/deploy.sh)
#   ADK_AGENT_URL= ./deploy.sh       # force the local mock concierge
#   DELIVERY_SPEED_SEC=4 ./deploy.sh # faster delivery simulation
# Parameters (environment, all optional): SERVICE_NAME, AGENT_BACKEND (adk|mock), ENRICH_URL, STORE_APP_URL,
#   FLYER_URL, VOICE_ENABLED (1|0), ADK_AGENT_URL.
# The build context is staged in a temp dir: app/ + static/ (incl. product images) + data/products_retail.jsonl
# + data/personas/ (seed/personas: personas.json, purchase_history.jsonl -> /api/recommendations).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
# caller overrides are captured BEFORE config.env (which exports the defaults)
_SERVICE_IN="${SERVICE_NAME:-}"
_ENRICH_IN="${ENRICH_URL-__unset__}"
_STORE_APP_IN="${STORE_APP_URL-__unset__}"
_VOICE_IN="${VOICE_ENABLED-__unset__}"
ADK_AGENT_URL_OVERRIDE="${ADK_AGENT_URL-__unset__}"
# shellcheck disable=SC1091
source "${ROOT}/config.env"
SERVICE="${_SERVICE_IN:-${SITE_SERVICE:-cymbal-site}}"
[[ "${_ENRICH_IN}" != "__unset__" ]] && ENRICH_URL="${_ENRICH_IN}"
[[ "${_STORE_APP_IN}" != "__unset__" ]] && STORE_APP_URL="${_STORE_APP_IN}"
VOICE_ENABLED="1"; [[ "${_VOICE_IN}" != "__unset__" ]] && VOICE_ENABLED="${_VOICE_IN}"
[[ "${VOICE_ENABLED}" == "0" ]] && VOICE_ENABLED=""
FLYER_URL="${FLYER_URL:-}"
if [[ -f "${ROOT}/shared/adk_agent/DEPLOYMENT.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/shared/adk_agent/DEPLOYMENT.env"
  set +a
fi
[[ "${ADK_AGENT_URL_OVERRIDE}" != "__unset__" ]] && ADK_AGENT_URL="${ADK_AGENT_URL_OVERRIDE}"
export ADK_AGENT_URL="${ADK_AGENT_URL:-}" DELIVERY_SPEED_SEC="${DELIVERY_SPEED_SEC:-8}" AGENT_BACKEND="${AGENT_BACKEND:-adk}"
export ENRICH_URL FLYER_URL VOICE_ENABLED STORE_APP_URL
case "${AGENT_BACKEND}" in
  adk|mock) ;;
  *) echo "AGENT_BACKEND must be adk|mock (got '${AGENT_BACKEND}')" >&2; exit 1 ;;
esac
echo "SERVICE=${SERVICE} AGENT_BACKEND=${AGENT_BACKEND} STORE_APP_URL=${STORE_APP_URL:-<unset>}" \
     "ADK_AGENT_URL=${ADK_AGENT_URL:-<unset>} ENRICH_URL=${ENRICH_URL:-<unset>} VOICE_ENABLED=${VOICE_ENABLED:-<unset>}"

DATA_JSONL="${CATALOG_JSONL}"
[[ -s "${DATA_JSONL}" ]] || { echo "missing ${DATA_JSONL}" >&2; exit 1; }

STAGE="$(mktemp -d -t cymbal-site-XXXXXX)"
ENV_FILE="$(mktemp -t cymbal-site-env-XXXXXX.yaml)"
trap 'rm -rf "${STAGE}" "${ENV_FILE}"' EXIT
cp -r "${HERE}/app" "${HERE}/static" "${HERE}/Dockerfile" "${HERE}/requirements.txt" "${STAGE}/"
find "${STAGE}" -name '__pycache__' -prune -exec rm -rf {} +
mkdir -p "${STAGE}/data"
cp "${DATA_JSONL}" "${STAGE}/data/products_retail.jsonl"
[[ -s "${AUTOCOMPLETE_JSONL}" ]] && cp "${AUTOCOMPLETE_JSONL}" "${STAGE}/data/autocomplete_suggestions.jsonl"
mkdir -p "${STAGE}/data/personas"
cp "${ROOT}/seed/personas/personas.json" "${ROOT}/seed/personas/purchase_history.jsonl" "${STAGE}/data/personas/"
# local fallback for store availability (BigQuery is the master)
for f in "${ROOT}/seed/catalog/stores_en.json" "${ROOT}/store-navi/data/store_inventory.jsonl" "${ROOT}/store-navi/data/shelf_locations.jsonl"; do
  if [[ -s "${f}" ]]; then cp "${f}" "${STAGE}/data/"; else echo "WARN: missing store fallback ${f}" >&2; fi
done
echo "staged: $(wc -l < "${STAGE}/data/products_retail.jsonl") products, $(ls "${STAGE}/static/img/products" | wc -l) image files"
printf '__pycache__\n*.pyc\n' > "${STAGE}/.gcloudignore"

python3 - "${ENV_FILE}" <<'PY'
import json, os, sys
env = {
  "PROJECT_ID": os.environ["PROJECT_ID"],
  "PROJECT_NUMBER": os.environ["PROJECT_NUMBER"],
  "BQ_DATASET": os.environ.get("BQ_DATASET", "cymbal"),
  "BQ_LOCATION": os.environ.get("BQ_LOCATION", "asia-northeast1"),
  "CATALOG": "default_catalog",
  "BRANCH": os.environ.get("RETAIL_BRANCH", "0"),
  "SERVING_CONFIG": os.environ.get("SERVING_CONFIG", "default_search"),
  "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
  "GEMINI_LOCATION": os.environ.get("GEMINI_LOCATION", "global"),
  "ADK_AGENT_URL": os.environ.get("ADK_AGENT_URL", ""),
  "AGENT_BACKEND": os.environ.get("AGENT_BACKEND", "adk"),
  "DELIVERY_SPEED_SEC": os.environ.get("DELIVERY_SPEED_SEC", "8"),
}
if os.environ.get("STORE_APP_URL"):
    env["STORE_APP_URL"] = os.environ["STORE_APP_URL"]
if os.environ.get("ENRICH_URL"):
    env["ENRICH_URL"] = os.environ["ENRICH_URL"]
if os.environ.get("FLYER_URL"):
    env["FLYER_URL"] = os.environ["FLYER_URL"]
if os.environ.get("VOICE_ENABLED"):
    env["VOICE_ENABLED"] = "1"  # Speech-to-Text v2 + Cloud TTS as the runtime SA
with open(sys.argv[1], "w") as f:
    for k, v in env.items():
        f.write(f"{k}: {json.dumps(str(v), ensure_ascii=False)}\n")
print("env:", {k: (v[:60] + "…" if len(v) > 60 else v) for k, v in env.items()})
PY
gcloud run deploy "${SERVICE}" --quiet \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source "${STAGE}" \
  --service-account "${RUNTIME_SA}" \
  --no-invoker-iam-check \
  --cpu 1 --memory 1Gi --min-instances 1 --max-instances 5 --concurrency 40 --timeout 300 \
  --env-vars-file "${ENV_FILE}"

gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)'
