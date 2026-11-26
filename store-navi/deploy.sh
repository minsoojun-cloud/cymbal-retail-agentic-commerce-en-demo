#!/usr/bin/env bash
# Deploy Cymbal Store Navi (CONTRACT v8) to Cloud Run (asia-northeast1). One image, two services:
#   SERVICE_NAME=cymbal-store-app   ./deploy.sh    # customer app  (APP_MODE=customer, env STAFF_URL)
#   SERVICE_NAME=cymbal-store-staff ./deploy.sh    # staff app     (APP_MODE=staff,    env CUSTOMER_URL)
# Optional env: AGENT_MODEL (default gemini-3.5-flash), AGENT_MODE (live|mock, default live), STAFF_URL, CUSTOMER_URL,
#   ONLINE_STORE_URL (default ${SITE_BASE_URL} from config.env), MCP_SERVICE (default cymbal-commerce-mcp), DRY_RUN=1 (stage only).
# Any other SERVICE_NAME is refused (existing services must not be touched by this script).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
source "${ROOT}/config.env"   # PROJECT_ID, PROJECT_NUMBER, REGION, RUNTIME_SA, PATH

SERVICE="${SERVICE_NAME:-}"
case "${SERVICE}" in
  "${STORE_APP_SERVICE:-cymbal-store-app}") APP_MODE="customer" ;;
  "${STAFF_SERVICE:-cymbal-store-staff}")   APP_MODE="staff" ;;
  *) echo "refusing: SERVICE_NAME must be ${STORE_APP_SERVICE:-cymbal-store-app} or ${STAFF_SERVICE:-cymbal-store-staff} (got '${SERVICE}')" >&2; exit 1 ;;
esac
if [[ -n "${APP_MODE_OVERRIDE:-}" && "${APP_MODE_OVERRIDE}" != "${APP_MODE}" ]]; then
  echo "refusing: ${SERVICE} must run APP_MODE=${APP_MODE}" >&2; exit 1
fi

URL_SUFFIX="${PROJECT_NUMBER}.${REGION}.run.app"
STAFF_URL="${STAFF_URL:-https://${STAFF_SERVICE:-cymbal-store-staff}-${URL_SUFFIX}}"
CUSTOMER_URL="${CUSTOMER_URL:-${STORE_APP_URL:-https://${STORE_APP_SERVICE:-cymbal-store-app}-${URL_SUFFIX}}}"
ONLINE_STORE_URL="${ONLINE_STORE_URL:-${SITE_BASE_URL:-https://${SITE_SERVICE:-cymbal-site}-${URL_SUFFIX}}}"
AGENT_MODEL="${AGENT_MODEL:-gemini-3.5-flash}"
AGENT_MODE="${AGENT_MODE:-live}"
MCP_SERVICE="${MCP_SERVICE:-cymbal-commerce-mcp}"
PHOTO_BUCKET="${PHOTO_BUCKET:-${PROJECT_ID}-store-navi}"

DATA_JSONL="${CATALOG_JSONL}"
SUGG_JSONL="${AUTOCOMPLETE_JSONL}"
IMG_DIR="${ROOT}/online-store/static/img/products"
SITE_APP="${ROOT}/online-store/app"
[[ -s "${DATA_JSONL}" ]] || { echo "missing ${DATA_JSONL}" >&2; exit 1; }
[[ -s "${HERE}/data/stores.json" ]] || { echo "missing ${HERE}/data/stores.json (store data not ready)" >&2; exit 1; }
for f in catalog.py search.py ai.py; do [[ -s "${SITE_APP}/${f}" ]] || { echo "missing ${SITE_APP}/${f}" >&2; exit 1; }; done

MCP_URL="${MCP_URL:-}"
if [[ -z "${MCP_URL}" && "${DRY_RUN:-0}" != "1" ]]; then
  MCP_URL="$(gcloud run services describe "${MCP_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)' 2>/dev/null || true)"
  MCP_URL="${MCP_URL:-https://${MCP_SERVICE}-${URL_SUFFIX}}"   # deterministic URL if MCP not deployed yet
fi
echo "SERVICE=${SERVICE} APP_MODE=${APP_MODE} MCP_URL=${MCP_URL:-<unset>} AGENT_MODEL=${AGENT_MODEL} AGENT_MODE=${AGENT_MODE}"

# ---- stage (app + site_lib copies + static + product images + data) ----
STAGE="$(mktemp -d -t cymbal-store-navi-XXXXXX)"
ENV_FILE="$(mktemp -t cymbal-store-navi-env-XXXXXX.yaml)"
trap 'rm -rf "${STAGE}" "${ENV_FILE}"' EXIT
cp -r "${HERE}/app" "${HERE}/static" "${HERE}/Dockerfile" "${HERE}/requirements.txt" "${STAGE}/"
if [[ "${APP_MODE}" == "staff" && ! -s "${STAGE}/app/staff.py" ]]; then
  echo "refusing: APP_MODE=staff but app/staff.py is missing" >&2; exit 1
fi
mkdir -p "${STAGE}/site_lib" "${STAGE}/data" "${STAGE}/product_images"
cp "${SITE_APP}/catalog.py" "${SITE_APP}/search.py" "${SITE_APP}/ai.py" "${STAGE}/site_lib/"   # unchanged copies
printf '"""Copies of online-store/app/{catalog,search,ai}.py (staged by store-navi/deploy.sh; do not edit)."""\n' > "${STAGE}/site_lib/__init__.py"
cp -r "${HERE}/data/." "${STAGE}/data/"
cp "${DATA_JSONL}" "${STAGE}/data/products_retail.jsonl"
[[ -s "${SUGG_JSONL}" ]] && cp "${SUGG_JSONL}" "${STAGE}/data/autocomplete_suggestions.jsonl"
cp -r "${IMG_DIR}/." "${STAGE}/product_images/"
find "${STAGE}" -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "${STAGE}/data/products_extra.jsonl"   # dev fixtures never ship
# online purchase history (CR-0001 "Last bought" / forgot-something) unless store data ships its own copy
[[ -s "${STAGE}/data/purchase_history.jsonl" ]] || cp "${ROOT}/seed/personas/purchase_history.jsonl" "${STAGE}/data/purchase_history.jsonl"
printf '__pycache__\n*.pyc\n' > "${STAGE}/.gcloudignore"
echo "staged: $(wc -l < "${STAGE}/data/products_retail.jsonl") products, $(ls "${STAGE}/product_images" | wc -l) images," \
     "$(wc -l < "${STAGE}/data/autocomplete_suggestions.jsonl" 2>/dev/null || echo 0) suggestions, store data: $(ls "${STAGE}/data" | tr '\n' ' ')"

export ENRICH_URL="${ENRICH_URL:-}" RETAIL_LOCAL_INVENTORY="${RETAIL_LOCAL_INVENTORY:-1}"
export APP_MODE STAFF_URL CUSTOMER_URL ONLINE_STORE_URL AGENT_MODEL AGENT_MODE MCP_URL PHOTO_BUCKET
python3 - "${ENV_FILE}" <<'PY'
import json, os, sys
env = {
  "APP_MODE": os.environ["APP_MODE"],
  "DATA_MODE": "bq",
  "PROJECT_ID": os.environ["PROJECT_ID"],
  "PROJECT_NUMBER": os.environ["PROJECT_NUMBER"],
  "BQ_DATASET": os.environ.get("BQ_DATASET", "cymbal"),
  "CATALOG": "default_catalog",
  "BRANCH": os.environ.get("RETAIL_BRANCH", "0"),
  "SERVING_CONFIG": os.environ.get("SERVING_CONFIG", "default_search"),
  "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
  "GEMINI_LOCATION": os.environ.get("GEMINI_LOCATION", "global"),
  "ONLINE_STORE_URL": os.environ["ONLINE_STORE_URL"],
  "MCP_URL": os.environ.get("MCP_URL", ""),
  "AGENT_MODEL": os.environ["AGENT_MODEL"],
  "AGENT_MODE": os.environ["AGENT_MODE"],
  "AGENT_LOCATION": "global",
  "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
  "GOOGLE_CLOUD_PROJECT": os.environ["PROJECT_ID"],
  "GOOGLE_CLOUD_LOCATION": "global",
  "PHOTO_BUCKET": os.environ["PHOTO_BUCKET"],
  "STORE_DATA_REFRESH_SEC": os.environ.get("STORE_DATA_REFRESH_SEC", "20"),
}
if os.environ.get("ENRICH_URL"):
    env["ENRICH_URL"] = os.environ["ENRICH_URL"]
if env["APP_MODE"] == "customer":
    env["STAFF_URL"] = os.environ["STAFF_URL"]
else:
    env["CUSTOMER_URL"] = os.environ["CUSTOMER_URL"]
    env["RETAIL_LOCAL_INVENTORY"] = os.environ.get("RETAIL_LOCAL_INVENTORY", "1")
    if os.environ.get("STAFF_PREWARM"):
        env["STAFF_PREWARM"] = os.environ["STAFF_PREWARM"]
with open(sys.argv[1], "w") as f:
    for k, v in env.items():
        f.write(f"{k}: {json.dumps(str(v), ensure_ascii=False)}\n")
print("env:", {k: (v[:60] + "…" if len(v) > 60 else v) for k, v in env.items()})
PY

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "DRY_RUN=1: staged at ${STAGE} (not deployed)"; ls -la "${STAGE}"; trap - EXIT; exit 0
fi

# ---- IAM: the embedded ADK agent calls the private MCP server with the runtime SA ID token ----
if ! gcloud run services describe "${MCP_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" >/dev/null 2>&1; then
  echo "WARN: ${MCP_SERVICE} not deployed yet; skipping invoker grant (runtime SA has project-level run.invoker)"
elif ! gcloud run services get-iam-policy "${MCP_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format=json \
     | grep -q "serviceAccount:${RUNTIME_SA}"; then
  echo "granting roles/run.invoker on ${MCP_SERVICE} to ${RUNTIME_SA}"
  gcloud run services add-iam-policy-binding "${MCP_SERVICE}" --quiet --project "${PROJECT_ID}" --region "${REGION}" \
    --member "serviceAccount:${RUNTIME_SA}" --role roles/run.invoker >/dev/null
else
  echo "ok: ${RUNTIME_SA} already has an IAM binding on ${MCP_SERVICE}"
fi

# Sessions of the embedded agent live in memory → session affinity, few instances.
set +e
gcloud run deploy "${SERVICE}" --quiet \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source "${STAGE}" \
  --service-account "${RUNTIME_SA}" \
  --no-invoker-iam-check \
  --cpu 2 --memory 2Gi --cpu-boost \
  --min-instances 1 --max-instances 3 --session-affinity \
  --concurrency 40 --timeout 300 \
  --env-vars-file "${ENV_FILE}"
rc=$?
set -e
if [[ ${rc} -ne 0 ]]; then
  echo "deploy failed (rc=${rc})." >&2
  exit ${rc}
fi
URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
echo "${SERVICE}: ${URL}"
echo "health: curl -s ${URL}/api/health"
