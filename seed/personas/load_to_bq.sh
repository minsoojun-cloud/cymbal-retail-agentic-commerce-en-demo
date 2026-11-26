#!/usr/bin/env bash
# Load persona + fake purchase history into BigQuery (${PROJECT_ID}:cymbal, asia-northeast1).
# Usage: ./load_to_bq.sh   (the installer loads the same data via install/lib/load_seed.py; this is a dev shortcut)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "${HERE}/../../config.env"   # PROJECT_ID, BQ_DATASET, PATH
DS="${PROJECT_ID}:${BQ_DATASET:-cymbal}"
LOC="asia-northeast1"

[ -f "${HERE}/purchase_history.jsonl" ] || { echo "purchase_history.jsonl missing"; exit 1; }

bq --project_id="${PROJECT_ID}" --location="${LOC}" show "${DS}" >/dev/null 2>&1 || bq --project_id="${PROJECT_ID}" --location="${LOC}" mk --dataset "${DS}"

# customers: personas.json (JSON array) -> NDJSON. Every row also carries `profile_json` (the full persona as a JSON
# string) so the MCP server gets the exact nested structure (family_preferences etc.) even if autodetect flattens or
# drops something. If the autodetected nested load fails, fall back to a slim fixed schema.
python3 - "${HERE}/personas.json" "${TMPDIR:-/tmp}/cr_customers.jsonl" "${TMPDIR:-/tmp}/cr_customers_slim.jsonl" <<'PY'
import json, sys
personas = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], "w", encoding="utf-8") as full, open(sys.argv[3], "w", encoding="utf-8") as slim:
    for p in personas:
        pj = json.dumps(p, ensure_ascii=False)
        full.write(json.dumps({**p, "profile_json": pj}, ensure_ascii=False) + "\n")
        slim.write(json.dumps({"customer_id": p["customer_id"], "name": p.get("name", ""),
                               "household_size": p.get("household_size"), "profile_json": pj}, ensure_ascii=False) + "\n")
PY
if ! bq --project_id="${PROJECT_ID}" --location="${LOC}" load --replace --autodetect --source_format=NEWLINE_DELIMITED_JSON \
  "${DS}.customers" "${TMPDIR:-/tmp}/cr_customers.jsonl"; then
  echo "autodetect load failed; loading slim schema (customer_id, name, household_size, profile_json)" >&2
  bq --project_id="${PROJECT_ID}" --location="${LOC}" load --replace --source_format=NEWLINE_DELIMITED_JSON \
    "${DS}.customers" "${TMPDIR:-/tmp}/cr_customers_slim.jsonl" \
    customer_id:STRING,name:STRING,household_size:INT64,profile_json:STRING
fi

bq --project_id="${PROJECT_ID}" --location="${LOC}" load --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${DS}.purchase_history" "${HERE}/purchase_history.jsonl" \
  order_id:STRING,customer_id:STRING,order_date:DATE,product_id:STRING,product_title:STRING,category:STRING,quantity:INT64,unit_price_tax_included:INT64,amount:INT64

# MCP server fallback copies (used when BigQuery is unavailable)
mkdir -p "${HERE}/../../shared/mcp_server/data"
cp "${HERE}/personas.json" "${HERE}/purchase_history.jsonl" "${HERE}/../../shared/mcp_server/data/"

bq --project_id="${PROJECT_ID}" --location="${LOC}" query --use_legacy_sql=false --format=pretty \
  "SELECT FORMAT_DATE('%Y-%m', order_date) ym, COUNT(DISTINCT order_id) orders, COUNT(*) rows_, SUM(amount) amount
   FROM \`${PROJECT_ID}.${BQ_DATASET:-cymbal}.purchase_history\` GROUP BY ym ORDER BY ym"
