"""Create the enrichment dataset/tables and seed `source_products` with the Cymbal demo SKUs.

    PYTHONPATH=. python scripts/setup_bq.py                 # dataset + tables + curated demo SKUs (scripts/demo_skus.json)
    PYTHONPATH=. python scripts/setup_bq.py --per-group 10  # + N random SKUs per category group
    PYTHONPATH=. python scripts/setup_bq.py --migrate-only  # only create missing tables / columns (keeps data)
    PYTHONPATH=. python scripts/setup_bq.py --reset         # truncate enrichment result tables (keeps product_images)

Enrichment dataset: `${PROJECT_ID}.cymbal_enrichment` (env ENRICH_BQ_DATASET).
Source: `${PROJECT_ID}.cymbal.products` (Retail API schema, read-only here; env BQ_SOURCE_TABLE overrides). Some curated SKUs are marked `sparse` and are copied
as a "legacy master" (structured attributes removed, description rewritten in a messy legacy style) to show the value
of enrichment — `cymbal.products` itself is never modified by this script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")

from google.cloud import bigquery  # noqa: E402

from app.config import settings  # noqa: E402
from app.schema import category_group  # noqa: E402

DS = settings.dataset_ref
HERE = Path(__file__).resolve().parent

DDL = f"""
CREATE TABLE IF NOT EXISTS `{DS}.source_products` (
  sku STRING, title STRING, brand STRING, category STRING, description STRING, product_url STRING, image_url STRING,
  price FLOAT64, raw_attributes_json STRING, category_group STRING, attr_count INT64, updated_at TIMESTAMP,
  source STRING, doc_sources_json STRING);
CREATE TABLE IF NOT EXISTS `{DS}.enriched_attributes` (
  sku STRING, kind STRING, attr_key STRING, label STRING, raw_value STRING, value_json STRING, unit STRING,
  norm_note STRING, evidence STRING, source_field STRING, self_conf FLOAT64, evidence_match FLOAT64,
  token_conf FLOAT64, score FLOAT64, gate_status STRING, gate STRING, reason STRING, job_id STRING,
  model STRING, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.sku_extras` (
  sku STRING, job_id STRING, search_keywords ARRAY<STRING>, ai_summary STRING, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.review_log` (
  sku STRING, attr_key STRING, action STRING, before_value STRING, after_value STRING, comment STRING,
  reviewer STRING, reviewed_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.golden_records` (
  sku STRING, title STRING, category_group STRING, attributes_json STRING, feature_tags ARRAY<STRING>,
  search_keywords ARRAY<STRING>, ai_summary STRING, fill_rate_before FLOAT64, fill_rate_after FLOAT64,
  updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.batch_jobs` (
  job_id STRING, display_name STRING, model STRING, state STRING, sku_count INT64, request_count INT64,
  input_tokens INT64, output_tokens INT64, cost_jpy FLOAT64, cost_usd FLOAT64, src STRING, dest STRING,
  created_at TIMESTAMP, updated_at TIMESTAMP, ingested BOOL, ingested_skus INT64, error STRING);
CREATE TABLE IF NOT EXISTS `{DS}.gmc_conversational` (
  sku STRING, item_group_id STRING, item_group_title STRING,
  variant_option STRING, variant_options_json STRING,
  question_and_answer STRING, qa_json STRING,
  product_highlight STRING, highlights_json STRING,
  product_detail STRING, details_json STRING,
  related_product STRING, related_json STRING,
  popularity_rank FLOAT64, document_link STRING,
  model STRING, job_id STRING, updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.product_images` (
  image_id STRING, sku STRING, gcs_uri STRING, url STRING, kind STRING, status STRING, is_primary BOOL,
  preset STRING, preset_label STRING, season STRING, prompt STRING, extra_prompt STRING,
  base_image_uri STRING, base_kind STRING, source_url STRING, model STRING, width INT64, height INT64,
  aspect STRING, fidelity_score FLOAT64, fidelity_note STRING, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.doc_extractions` (
  job_id STRING, file_uri STRING, file_name STRING, document_type STRING, product_index INT64,
  applied_sku STRING, is_new BOOL, merge_mode STRING, changes INT64, extracted_json STRING, diff_json STRING,
  model STRING, created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS `{DS}.token_usage` (
  sku STRING, title STRING, model STRING, job_id STRING, source STRING,
  input_tokens INT64, output_tokens INT64, thinking_tokens INT64, calls INT64,
  stage_tokens_json STRING, source_chars INT64, created_at TIMESTAMP);
"""
MIGRATION = f"""
ALTER TABLE `{DS}.batch_jobs` ADD COLUMN IF NOT EXISTS error STRING;
ALTER TABLE `{DS}.batch_jobs` ADD COLUMN IF NOT EXISTS cost_usd FLOAT64;
ALTER TABLE `{DS}.source_products` ADD COLUMN IF NOT EXISTS doc_sources_json STRING;
"""
RESET_TABLES = ("enriched_attributes", "sku_extras", "review_log", "golden_records", "batch_jobs", "gmc_conversational",
                "doc_extractions")

# Retail attributes that are internal / pricing (not product facts) are not copied into the enrichment source
SKIP_KEYS = {"price_tax_excluded", "original_price_tax_excluded", "discount_rate", "top_category", "is_food", "is_seasonal",
             "jan_code", "title_ja", "base_id"}  # title_ja = Japanese reference only, never shown in the studio


def _fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def raw_attributes(row: dict) -> dict:
    out = {}
    for a in row.get("attributes") or []:
        k = a.get("key")
        if not k or k in SKIP_KEYS:
            continue
        v = a.get("value") or {}
        txt = [t for t in (v.get("text") or []) if t not in (None, "")]
        nums = [_fmt_num(n) for n in (v.get("numbers") or [])]
        val = " / ".join(txt) if txt else " / ".join(nums)
        if val:
            out[k] = val
    return out


# English "legacy master" style: upper-case run-on facts with ■ markers and terse storage codes.
_LEGACY_STORAGE = {
    "chilled": "KEEP REFRIGERATED",
    "refrigerated": "KEEP REFRIGERATED",
    "frozen": "KEEP FROZEN (-18C OR BELOW)",
    "ambient": "STORE AWAY FROM DIRECT SUNLIGHT/HEAT/HUMIDITY",
}
_LEGACY_KEYS = {"net content": "NET", "contents": "NET", "size/capacity": "SPEC", "size": "SPEC", "origin": "ORIGIN",
                "country of origin": "ORIGIN", "made in": "MADE IN", "material": "MATL", "color": "COL",
                "colour": "COL", "allergens": "ALLERGY", "specified allergens": "ALLERGY"}


def legacy_description(desc: str) -> str:
    """Rewrite the clean catalog copy in a messy 'legacy master' style (■ markers, upper-case run-on facts).

    The second line of a catalog description is a fact list like
    "Net content: 2 pcs / Origin: Hokkaido / Storage: Chilled / Allergens: egg".
    """
    lines = desc.split("\n")
    body = lines[0]
    facts = lines[1] if len(lines) > 1 else ""
    parts = []
    for item in [x.strip() for x in facts.split(" / ") if x.strip()]:
        k, sep, v = item.partition(":")
        k, v = k.strip(), v.strip()
        if not sep:
            parts.append(item.upper())
        elif k.lower() in ("storage", "storage method"):
            parts.append(_LEGACY_STORAGE.get(v.lower(), v.upper()))
        else:
            parts.append(f"■{_LEGACY_KEYS.get(k.lower(), k.upper())} {v.upper()}")
    legacy = "  ".join(parts)
    return f"{body}  {legacy}".strip() if legacy else body


def product_row(row: dict, sparse: bool, now: str) -> dict:
    raw = raw_attributes(row)
    desc = row.get("description") or ""
    source = "cymbal"
    if sparse:
        raw = {k: v for k, v in raw.items() if k in ("base_id",)}
        desc = legacy_description(desc)
        source = "legacy_master"
    cat = (row.get("categories") or [""])[0]
    raw_arr = [{"k": k, "v": v} for k, v in raw.items()]
    return {
        "sku": row["id"], "title": row.get("title") or "", "brand": (row.get("brands") or [""])[0] if row.get("brands") else "",
        "category": cat, "description": desc, "product_url": row.get("uri") or "",
        "image_url": ((row.get("images") or [{}])[0] or {}).get("uri") or "",
        "price": (row.get("priceInfo") or {}).get("price"), "raw_attributes_json": json.dumps(raw_arr, ensure_ascii=False),
        "category_group": category_group(cat), "attr_count": len(raw_arr), "updated_at": now, "source": source,
        "doc_sources_json": "[]",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-group", type=int, default=0, help="additionally sample N random SKUs per category group")
    ap.add_argument("--migrate-only", action="store_true")
    ap.add_argument("--reset", action="store_true", help="truncate enrichment result tables before seeding")
    ap.add_argument("--no-curated", action="store_true", help="skip the curated demo SKU list")
    args = ap.parse_args()
    bq = bigquery.Client(project=settings.project_id, location=settings.bq_location)
    ds = bigquery.Dataset(DS)
    ds.location = settings.bq_location
    ds.description = "Cymbal Retail Data Enrichment Studio (data-enrichment)"
    bq.create_dataset(ds, exists_ok=True)
    bq.query(DDL).result()
    bq.query(MIGRATION).result()
    print(f"tables ready in {DS} ({settings.bq_location})")
    if args.migrate_only:
        return
    if args.reset:
        for t in RESET_TABLES:
            bq.query(f"TRUNCATE TABLE `{DS}.{t}`").result()
        print("reset:", ", ".join(RESET_TABLES))

    curated = [] if args.no_curated else json.loads((HERE / "demo_skus.json").read_text())["skus"]
    sparse = {c["id"] for c in curated if c.get("sparse")}
    ids = [c["id"] for c in curated]
    rows = {}
    if ids:
        job = bq.query(f"SELECT TO_JSON_STRING(t) j FROM `{settings.bq_source_table}` t WHERE id IN UNNEST(@ids)",
                       job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("ids", "STRING", ids)]))
        for r in job.result():
            row = json.loads(r.j)
            rows[row["id"]] = row
        missing = [i for i in ids if i not in rows]
        if missing:
            print("WARNING: not found in source table:", missing)
    if args.per_group:
        q = f"""SELECT j FROM (
                  SELECT TO_JSON_STRING(t) j, (SELECT a.value.text[SAFE_OFFSET(0)] FROM UNNEST(attributes) a WHERE a.key='top_category') tc,
                         ROW_NUMBER() OVER (PARTITION BY (SELECT a.value.text[SAFE_OFFSET(0)] FROM UNNEST(attributes) a WHERE a.key='top_category')
                                            ORDER BY FARM_FINGERPRINT(id)) rn
                  FROM `{settings.bq_source_table}` t) WHERE rn <= {max(1, args.per_group // 3)}"""
        for r in bq.query(q).result():
            row = json.loads(r.j)
            rows.setdefault(row["id"], row)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    out = [product_row(r, r["id"] in sparse, now) for r in rows.values()]
    cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE",
                                 schema=bq.get_table(f"{DS}.source_products").schema)
    bq.load_table_from_json(out, f"{DS}.source_products", job_config=cfg).result()
    for r in bq.query(f"""SELECT category_group, COUNT(*) n, COUNTIF(source='legacy_master') sparse, ROUND(AVG(attr_count),1) a
                         FROM `{DS}.source_products` GROUP BY 1 ORDER BY 1""").result():
        print(dict(r))


if __name__ == "__main__":
    main()
