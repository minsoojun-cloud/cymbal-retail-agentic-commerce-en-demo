#!/usr/bin/env python3
"""Load Cymbal Store Navi data into BigQuery `cymbal.*` and prepare GCS/IAM (idempotent).

  python3 setup_bq.py              # tables + customers/purchase_history append + bucket + IAM
  python3 setup_bq.py --skip-iam   # tables only
  python3 setup_bq.py --reset-shelf  # also drop staff shelf registrations (default keeps source='staff' rows)

Tables (create/replace): stores, store_layouts, store_inventory, store_coupons, store_offers.
shelf_locations: created if missing; seed rows (source='seed') replaced, staff rows kept.
store_inventory_events: created if missing (never truncated).
customers / purchase_history: rows for CR-0002 / CR-0003 deleted then re-appended using the EXISTING table
schema (no schema change; CR-0001 and other rows untouched).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "store-navi", "data")


def load_env() -> dict:
    env = {}
    for line in open(os.path.join(ROOT, "config.env"), encoding="utf-8"):
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            k, v = line[7:].split("=", 1)
            env[k] = v.strip().strip('"')
    return env


ENV = {**load_env(), **{k: v for k, v in os.environ.items() if k in ("PROJECT_ID", "BQ_DATASET", "BQ_LOCATION", "REGION")}}
PROJECT = ENV["PROJECT_ID"]
DATASET = ENV.get("BQ_DATASET", "cymbal")
LOC = ENV.get("BQ_LOCATION") or ENV.get("REGION") or "asia-northeast1"
RUNTIME_SA = os.environ.get("RUNTIME_SA") or f"cymbal-runtime@{PROJECT}.iam.gserviceaccount.com"
BUCKET = f"gs://{PROJECT}-store-navi"
NEW_CUSTOMERS = ["CR-0002", "CR-0003"]
os.environ["PATH"] = os.path.expanduser("~/google-cloud-sdk/bin") + ":" + os.environ.get("PATH", "")
os.environ.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
TMP = tempfile.mkdtemp(prefix="sn_bq_")

SCHEMAS = {
    "stores": [
        {"name": "store_id", "type": "STRING"}, {"name": "name", "type": "STRING"},
        {"name": "short_name", "type": "STRING"}, {"name": "lat", "type": "FLOAT64"}, {"name": "lng", "type": "FLOAT64"},
        {"name": "address", "type": "STRING"}, {"name": "phone", "type": "STRING"},
        {"name": "hours", "type": "RECORD", "fields": [{"name": "open", "type": "STRING"}, {"name": "close", "type": "STRING"}]},
        {"name": "pharmacy", "type": "RECORD", "fields": [{"name": "open", "type": "STRING"},
                                                          {"name": "close", "type": "STRING"},
                                                          {"name": "closed", "type": "STRING"}]},
        {"name": "services", "type": "STRING", "mode": "REPEATED"}, {"name": "radius_m", "type": "INT64"},
        {"name": "layout_id", "type": "STRING"}, {"name": "tagline", "type": "STRING"},
        {"name": "format", "type": "STRING"},
    ],
    "store_layouts": [{"name": "store_id", "type": "STRING"}, {"name": "version", "type": "INT64"},
                      {"name": "layout", "type": "STRING"}],
    "store_layout_changes": [
        {"name": "store_id", "type": "STRING"}, {"name": "version", "type": "INT64"},
        {"name": "base_version", "type": "INT64"}, {"name": "action", "type": "STRING"},
        {"name": "published_by", "type": "STRING"}, {"name": "published_at", "type": "TIMESTAMP"},
        {"name": "summary", "type": "STRING"},
    ],
    "store_inventory": [{"name": "store_id", "type": "STRING"}, {"name": "product_id", "type": "STRING"},
                        {"name": "quantity", "type": "INT64"}, {"name": "availability", "type": "STRING"},
                        {"name": "updated_at", "type": "TIMESTAMP"}],
    "shelf_locations": [{"name": "store_id", "type": "STRING"}, {"name": "product_id", "type": "STRING"},
                        {"name": "fixture_id", "type": "STRING"}, {"name": "slot_ids", "type": "STRING", "mode": "REPEATED"},
                        {"name": "tier", "type": "INT64"}, {"name": "facings", "type": "INT64"},
                        {"name": "gondola_id", "type": "STRING"}, {"name": "side", "type": "STRING"},
                        {"name": "position", "type": "INT64"},
                        {"name": "photo_uri", "type": "STRING"}, {"name": "source", "type": "STRING"},
                        {"name": "registered_by", "type": "STRING"}, {"name": "registered_at", "type": "TIMESTAMP"},
                        {"name": "note", "type": "STRING"}],
    "shelf_slots": [{"name": "store_id", "type": "STRING"}, {"name": "slot_id", "type": "STRING"},
                    {"name": "fixture_id", "type": "STRING"}, {"name": "tier", "type": "INT64"},
                    {"name": "index", "type": "INT64"}, {"name": "width_cm", "type": "INT64"},
                    {"name": "fixture_type", "type": "STRING"}, {"name": "x", "type": "FLOAT64"},
                    {"name": "y", "type": "FLOAT64"}],
    "store_inventory_events": [{"name": "store_id", "type": "STRING"}, {"name": "product_id", "type": "STRING"},
                               {"name": "delta", "type": "INT64"}, {"name": "quantity", "type": "INT64"},
                               {"name": "by", "type": "STRING"}, {"name": "at", "type": "TIMESTAMP"}],
    "store_coupons": [
        {"name": "coupon_id", "type": "STRING"}, {"name": "customer_id", "type": "STRING"},
        {"name": "store_id", "type": "STRING"}, {"name": "title", "type": "STRING"},
        {"name": "description", "type": "STRING"}, {"name": "discount_type", "type": "STRING"},
        {"name": "discount_value", "type": "FLOAT64"},
        {"name": "target", "type": "RECORD", "fields": [{"name": "categories", "type": "STRING", "mode": "REPEATED"},
                                                        {"name": "product_ids", "type": "STRING", "mode": "REPEATED"}]},
        {"name": "valid_until", "type": "DATE"}, {"name": "reason", "type": "STRING"}, {"name": "color", "type": "STRING"},
    ],
    "store_offers": [{"name": "store_id", "type": "STRING"}, {"name": "product_id", "type": "STRING"},
                     {"name": "sale_price", "type": "INT64"}, {"name": "original_price", "type": "INT64"},
                     {"name": "start_date", "type": "DATE"}, {"name": "end_date", "type": "DATE"},
                     {"name": "badge", "type": "STRING"}, {"name": "theme", "type": "STRING"},
                     {"name": "title", "type": "STRING"}],
}


def run(cmd: list[str], check=True, capture=False) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd[:8]) + (" ..." if len(cmd) > 8 else ""), flush=True)
    r = subprocess.run(cmd, text=True, capture_output=True)
    if capture or r.returncode:
        if r.returncode and check:
            print(r.stdout, r.stderr, file=sys.stderr)
            raise SystemExit(f"command failed: {' '.join(cmd)}")
    return r


def bq(*args, check=True, capture=False):
    return run(["bq", f"--project_id={PROJECT}", f"--location={LOC}", *args], check=check, capture=capture)


def query(sql: str):
    return bq("query", "--use_legacy_sql=false", "--format=prettyjson", sql, capture=True)


def table_exists(t: str) -> bool:
    return bq("show", f"{PROJECT}:{DATASET}.{t}", check=False, capture=True).returncode == 0


def write_ndjson(name: str, rows) -> str:
    p = os.path.join(TMP, f"{name}.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def schema_file(name: str, schema) -> str:
    p = os.path.join(TMP, f"{name}.schema.json")
    json.dump(schema, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    return p


def load(table: str, rows, replace=True, schema=None):
    path = write_ndjson(table, rows)
    sch = schema_file(table, schema or SCHEMAS[table])
    bq("load", "--replace" if replace else "--noreplace", "--source_format=NEWLINE_DELIMITED_JSON",
       f"{PROJECT}:{DATASET}.{table}", path, sch)


def read_jsonl(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def read_json(name):
    return json.load(open(os.path.join(DATA, name), encoding="utf-8"))


# ------------------------------------------------------------------------------------------------ tables
def load_tables(reset_shelf: bool):
    stores = read_json("stores.json")
    load("stores", stores)
    layouts = []
    for s in stores:
        lay = json.load(open(os.path.join(DATA, "layouts", f"{s['layout_id']}.json"), encoding="utf-8"))
        layouts.append({"store_id": s["store_id"], "version": lay["version"], "layout": json.dumps(lay, ensure_ascii=False)})
    load("store_layouts", layouts)
    load("store_inventory", read_jsonl("store_inventory.jsonl"))
    load("store_coupons", read_json("coupons.json"))
    load("store_offers", read_json("offers.json"))

    slots = read_jsonl("shelf_slots.jsonl")
    load("shelf_slots", slots)
    shelf = read_jsonl("shelf_locations.jsonl")
    kept = []
    if not reset_shelf and table_exists("shelf_locations"):
        # keep staff registrations only if all their slots still exist in the v2 layout
        valid = {(r["store_id"], r["slot_id"]) for r in slots}
        r = bq("query", "--use_legacy_sql=false", "--format=json", "--max_rows=100000",
               f"SELECT TO_JSON_STRING(t) j FROM `{PROJECT}.{DATASET}.shelf_locations` t WHERE source = 'staff'",
               capture=True)
        staff = [json.loads(x["j"]) for x in json.loads(r.stdout or "[]")]
        for row in staff:
            sids = row.get("slot_ids") or []
            if sids and all((row["store_id"], s) in valid for s in sids):
                kept.append({k: row.get(k) for k in (f["name"] for f in SCHEMAS["shelf_locations"])})
        print(f"  staff shelf rows: {len(staff)} found, {len(kept)} kept (slots valid), {len(staff) - len(kept)} dropped")
    load("shelf_locations", shelf + kept)
    if not table_exists("store_inventory_events"):
        bq("mk", "--table", f"{PROJECT}:{DATASET}.store_inventory_events",
           schema_file("store_inventory_events", SCHEMAS["store_inventory_events"]))


# ------------------------------------------------------------------------------------------------ customers
def existing_schema(table: str) -> list[dict]:
    r = bq("show", "--schema", "--format=json", f"{PROJECT}:{DATASET}.{table}", capture=True)
    return json.loads(r.stdout)


def conform(value, field):
    """Keep only keys present in the BQ schema (recursively); coerce simple types."""
    if value is None:
        return None
    if field.get("mode") == "REPEATED":
        if not isinstance(value, list):
            value = [value]
        return [conform(v, {**field, "mode": "NULLABLE"}) for v in value if v is not None]
    if field["type"] in ("RECORD", "STRUCT"):
        if not isinstance(value, dict):
            return None
        sub = {f["name"]: f for f in field["fields"]}
        return {k: conform(v, sub[k]) for k, v in value.items() if k in sub}
    if field["type"] in ("INTEGER", "INT64"):
        return int(value)
    if field["type"] in ("FLOAT", "FLOAT64"):
        return float(value)
    if field["type"] in ("BOOLEAN", "BOOL"):
        return bool(value)
    if field["type"] == "STRING" and not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return value


def ensure_base_customers():
    """New project: create cymbal.customers / purchase_history from seed/personas (CR-0001) if missing."""
    if table_exists("customers") and table_exists("purchase_history"):
        return
    print("  customers/purchase_history missing -> running seed/personas/load_to_bq.sh", flush=True)
    run(["bash", os.path.join(ROOT, "seed", "personas", "load_to_bq.sh")])


def append_customers():
    ensure_base_customers()
    personas = [p for p in read_json("store_personas.json") if p["customer_id"] in NEW_CUSTOMERS]
    schema = existing_schema("customers")
    fields = {f["name"]: f for f in schema}
    rows = []
    for p in personas:
        full = {**p,
                "favorite_categories": p.get("interest_categories", []),
                "visitor_id": f"v-{p['customer_id'].lower().replace('-', '')}",
                "cookie": {"name": "cr_customer", "value": p["customer_id"],
                           "example_header": f"Cookie: cr_customer={p['customer_id']}"},
                "marketing_consent": {"email": True, "push": True},
                "profile_json": json.dumps(p, ensure_ascii=False)}
        rows.append({k: conform(v, fields[k]) for k, v in full.items() if k in fields})
    ids = ",".join(f"'{c}'" for c in NEW_CUSTOMERS)
    query(f"DELETE FROM `{PROJECT}.{DATASET}.customers` WHERE customer_id IN ({ids})")
    load("customers", rows, replace=False, schema=schema)
    hist = read_jsonl("store_purchase_history.jsonl")
    assert all(r["customer_id"] in NEW_CUSTOMERS for r in hist)
    query(f"DELETE FROM `{PROJECT}.{DATASET}.purchase_history` WHERE customer_id IN ({ids})")
    load("purchase_history", hist, replace=False, schema=existing_schema("purchase_history"))


# ------------------------------------------------------------------------------------------------ GCS / IAM
def setup_gcs_iam() -> list[str]:
    granted = []
    if run(["gcloud", "storage", "buckets", "describe", BUCKET, f"--project={PROJECT}"], check=False,
           capture=True).returncode != 0:
        run(["gcloud", "storage", "buckets", "create", BUCKET, f"--project={PROJECT}", f"--location={LOC}",
             "--uniform-bucket-level-access", "--public-access-prevention"])
        granted.append(f"created {BUCKET} (asia-northeast1, uniform access, public access prevention)")
    run(["gcloud", "storage", "buckets", "add-iam-policy-binding", BUCKET, f"--member=serviceAccount:{RUNTIME_SA}",
         "--role=roles/storage.objectAdmin", f"--project={PROJECT}"], capture=True)
    granted.append(f"{RUNTIME_SA}: roles/storage.objectAdmin on {BUCKET}")

    # BigQuery dataEditor on dataset cymbal (dataset ACL: WRITER == dataEditor)
    r = bq("show", "--format=prettyjson", f"{PROJECT}:{DATASET}", capture=True)
    ds = json.loads(r.stdout)
    acc = ds.get("access", [])
    has = any(a.get("userByEmail") == RUNTIME_SA and a.get("role") in ("WRITER", "OWNER", "roles/bigquery.dataEditor",
                                                                        "roles/bigquery.dataOwner") for a in acc)
    proj_pol = json.loads(run(["gcloud", "projects", "get-iam-policy", PROJECT, "--format=json"], capture=True).stdout)
    proj_roles = {b["role"] for b in proj_pol.get("bindings", []) if f"serviceAccount:{RUNTIME_SA}" in b.get("members", [])}
    if not has and not proj_roles & {"roles/bigquery.dataEditor", "roles/bigquery.dataOwner", "roles/bigquery.admin",
                                     "roles/editor", "roles/owner"}:
        acc.append({"role": "WRITER", "userByEmail": RUNTIME_SA})
        p = os.path.join(TMP, "ds.json")
        json.dump({"access": acc}, open(p, "w"))
        bq("update", "--source", p, f"{PROJECT}:{DATASET}")
        granted.append(f"{RUNTIME_SA}: WRITER (dataEditor) on dataset {DATASET}")
    else:
        granted.append(f"{RUNTIME_SA}: BigQuery write already present ({'dataset ACL' if has else ', '.join(sorted(proj_roles))})")
    if not proj_roles & {"roles/retail.editor", "roles/retail.admin", "roles/editor", "roles/owner"}:
        run(["gcloud", "projects", "add-iam-policy-binding", PROJECT, f"--member=serviceAccount:{RUNTIME_SA}",
             "--role=roles/retail.editor", "--condition=None"], capture=True)
        granted.append(f"{RUNTIME_SA}: roles/retail.editor on project")
    else:
        granted.append(f"{RUNTIME_SA}: Retail write already present ({', '.join(sorted(proj_roles))})")
    return granted


def summary():
    tables = ["stores", "store_layouts", "store_inventory", "shelf_slots", "shelf_locations", "store_inventory_events", "store_coupons",
              "store_offers"]
    sql = " UNION ALL ".join(f"SELECT '{t}' t, COUNT(*) n FROM `{PROJECT}.{DATASET}.{t}`" for t in tables)
    sql += f" UNION ALL SELECT CONCAT('customers ', customer_id), COUNT(*) FROM `{PROJECT}.{DATASET}.customers` GROUP BY 1"
    sql += (f" UNION ALL SELECT CONCAT('purchase_history ', customer_id), COUNT(*) FROM "
            f"`{PROJECT}.{DATASET}.purchase_history` GROUP BY 1")
    r = query(sql)
    rows = json.loads(r.stdout)
    for row in sorted(rows, key=lambda x: x["t"]):
        print(f"  {row['t']:<34} {row['n']}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-iam", action="store_true")
    ap.add_argument("--reset-shelf", action="store_true")
    a = ap.parse_args()
    load_tables(a.reset_shelf)
    append_customers()
    granted = [] if a.skip_iam else setup_gcs_iam()
    print("== row counts")
    summary()
    if granted:
        print("== GCS/IAM")
        for g in granted:
            print("  " + g)


if __name__ == "__main__":
    main()
