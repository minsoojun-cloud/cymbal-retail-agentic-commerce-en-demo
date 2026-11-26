#!/usr/bin/env bash
# 50_catalog_import.sh — 3,000-product English catalog
#   rendered catalog -> GCS -> BigQuery cymbal.products (+ top-level `channel`) -> Retail FULL import (from GCS)
#   -> attributes config (facets / filterable / retrievable) -> autocomplete -> Local Inventory for stores S01–S03
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config
CAT="${REPO_ROOT}/shared/catalog"

python3 "${INSTALL_DIR}/lib/render_seed.py" --catalog-only
[[ -s "${CATALOG_JSONL}" ]] || { log_err "rendered catalog ${CATALOG_JSONL} missing"; exit 1; }
N=$(wc -l < "${CATALOG_JSONL}")
python3 "${CAT}/validate_jsonl.py" "${CATALOG_JSONL}" >/dev/null 2>&1 || log_warn "validate_jsonl.py reported warnings"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "  [dry-run] upload ${N} products to gs://${GCS_BUCKET}/catalog/, bq load ${BQ_DATASET}.${BQ_TABLE}, Retail FULL import,"
  echo "            set_attributes_config.py, setup_search_features.py --autocomplete-only, push_local_inventory.py --force"
  exit 0
fi

GCS_LATEST="gs://${GCS_BUCKET}/catalog/products_retail_latest.jsonl"
log_info "[1/6] upload catalog (${N} products) to ${GCS_LATEST}"
gcloud storage cp "${CATALOG_JSONL}" "${GCS_LATEST}" --project "${PROJECT_ID}" --quiet

log_info "[2/6] BigQuery ${BQ_DATASET}.${BQ_TABLE} (Retail schema + channel column)"
TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT
python3 - "${CATALOG_JSONL}" "${TMP}/products_bq.jsonl" "${CAT}/bq_schema.json" "${TMP}/schema.json" <<'PY'
import json, sys
src, dst, sch_in, sch_out = sys.argv[1:5]
with open(dst, "w") as w:
    for line in open(src):
        p = json.loads(line)
        p["channel"] = next((a["value"]["text"][0] for a in p.get("attributes", []) if a["key"] == "channel"), "omni")
        w.write(json.dumps(p, ensure_ascii=False) + "\n")
s = json.load(open(sch_in))
if not any(f["name"] == "channel" for f in s):
    s.append({"name": "channel", "type": "STRING", "mode": "NULLABLE"})
json.dump(s, open(sch_out, "w"), indent=1)
PY
bq --project_id "${PROJECT_ID}" --location "${BQ_LOCATION}" --quiet load --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${PROJECT_ID}:${BQ_DATASET}.${BQ_TABLE}" "${TMP}/products_bq.jsonl" "${TMP}/schema.json"
log_ok "${BQ_DATASET}.${BQ_TABLE}: ${N} rows"

log_info "[3/6] Retail products:import (FULL, from GCS) — 2-5 minutes"
python3 "${CAT}/retail_import.py" --project "${PROJECT_ID}" --gcs-uri "${GCS_LATEST}"

log_info "[4/6] Retail attributes config (facets, filterable, retrievable)"
python3 "${CAT}/set_attributes_config.py" --project "${PROJECT_ID}"

log_info "[5/6] serving config facets + autocomplete (BigQuery ${BQ_DATASET}.autocomplete_suggestions)"
python3 "${CAT}/setup_search_features.py" --autocomplete-only || log_warn "autocomplete import failed — re-run: python3 shared/catalog/setup_search_features.py --autocomplete-only"

log_info "[6/6] Retail Local Inventory for stores S01–S03 (pickup-in-store, quantity, shelf) — ~5-8 minutes"
python3 "${REPO_ROOT}/store-navi/scripts/push_local_inventory.py" --force
log_ok "catalog ready: ${N} products, Local Inventory pushed"
