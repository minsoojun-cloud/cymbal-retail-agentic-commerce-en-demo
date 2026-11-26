#!/usr/bin/env python3
"""Load the rendered BigQuery seed (install/.state/rendered/bigquery/<dataset>/<table>.jsonl) into the target project.

Idempotent:
  * datasets are created when missing (location = $BQ_LOCATION);
  * tables with seed rows are loaded with WRITE_TRUNCATE (`bq load --replace`) and the exported schema;
  * tables whose seed is empty (runtime logs such as review_log / token_usage / store_inventory_events) are only
    created when missing, so runtime data of an existing installation is left untouched;
  * staff shelf registrations (`cymbal.shelf_locations` rows with source='staff') that already exist in the project are
    kept when their slots still exist, exactly like a re-run of the original pipeline.

Usage (normally called by install/steps/40_data.sh after `source config.env`):
  python3 install/lib/load_seed.py [--dataset cymbal] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def run(cmd: list[str], capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def bq(project: str, location: str, *args: str, capture: bool = False, check: bool = True):
    return run(["bq", f"--project_id={project}", f"--location={location}", "--quiet", *args], capture=capture, check=check)


def table_exists(project: str, location: str, dataset: str, table: str) -> bool:
    return bq(project, location, "show", "--format=none", f"{project}:{dataset}.{table}", capture=True, check=False).returncode == 0


def ensure_dataset(project: str, location: str, dataset: str, description: str, dry: bool) -> None:
    if bq(project, location, "show", "--format=none", f"{project}:{dataset}", capture=True, check=False).returncode == 0:
        print(f"  dataset {dataset}: exists")
        return
    print(f"  dataset {dataset}: creating in {location}")
    if not dry:
        bq(project, location, "mk", "--dataset", f"--description={description}", f"{project}:{dataset}")


def staff_rows_to_keep(project: str, location: str, dataset: str, slots_file: Path) -> list[dict]:
    if not table_exists(project, location, dataset, "shelf_locations"):
        return []
    r = bq(project, location, "query", "--use_legacy_sql=false", "--format=json", "--max_rows=100000",
           f"SELECT TO_JSON_STRING(t) j FROM `{project}.{dataset}.shelf_locations` t WHERE source = 'staff'",
           capture=True, check=False)
    if r.returncode != 0:
        print(f"  WARN: could not read existing staff shelf rows: {(r.stderr or '').strip()[:200]}")
        return []
    staff = [json.loads(x["j"]) for x in json.loads(r.stdout or "[]")]
    valid = set()
    if slots_file.exists():
        for line in slots_file.open(encoding="utf-8"):
            if line.strip():
                s = json.loads(line)
                valid.add((s.get("store_id"), s.get("slot_id")))
    kept = [row for row in staff if row.get("slot_ids") and all((row["store_id"], s) in valid for s in row["slot_ids"])]
    print(f"  shelf_locations: {len(staff)} existing staff registrations found, {len(kept)} kept")
    return kept


def load_table(project: str, location: str, dataset: str, table: str, data: Path, schema: Path,
               extra_rows: list[dict], dry: bool) -> str:
    rows = sum(1 for line in data.open(encoding="utf-8") if line.strip()) + len(extra_rows)
    ref = f"{project}:{dataset}.{table}"
    if rows == 0:
        if table_exists(project, location, dataset, table):
            return f"  {dataset}.{table}: empty seed, existing table kept"
        if not dry:
            bq(project, location, "mk", "--table", ref, str(schema))
        return f"  {dataset}.{table}: created (empty)"
    src = data
    if extra_rows:
        tmp = Path(tempfile.mkstemp(prefix=f"{table}_", suffix=".jsonl")[1])
        with tmp.open("w", encoding="utf-8") as w:
            w.write(data.read_text(encoding="utf-8").rstrip("\n") + "\n")
            for r in extra_rows:
                w.write(json.dumps(r, ensure_ascii=False) + "\n")
        src = tmp
    if not dry:
        bq(project, location, "load", "--replace", "--source_format=NEWLINE_DELIMITED_JSON", ref, str(src), str(schema),
           capture=True)
    return f"  {dataset}.{table}: {rows:,} rows loaded (replace)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID", ""))
    ap.add_argument("--location", default=os.environ.get("BQ_LOCATION") or os.environ.get("REGION", "asia-northeast1"))
    ap.add_argument("--rendered", default=os.environ.get("RENDERED_DIR") or str(REPO / "install" / ".state" / "rendered"))
    ap.add_argument("--dataset", action="append", help="only this seed dataset (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.project:
        print("ERROR: --project / PROJECT_ID is required", file=sys.stderr)
        return 1
    # seed dataset directory name -> target dataset name (configurable)
    targets = {"cymbal": os.environ.get("BQ_DATASET", "cymbal"),
               "cymbal_enrichment": os.environ.get("ENRICH_BQ_DATASET", "cymbal_enrichment")}
    descr = {"cymbal": "Cymbal Retail demo: catalog, stores, inventory, shelves, customers",
             "cymbal_enrichment": "Cymbal Retail Product Data Enrichment Studio"}
    base = Path(a.rendered) / "bigquery"
    if not base.is_dir():
        print(f"ERROR: {base} not found — run install/lib/render_seed.py first", file=sys.stderr)
        return 1
    jobs = []
    for ds_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if a.dataset and ds_dir.name not in a.dataset:
            continue
        ds = targets.get(ds_dir.name, ds_dir.name)
        ensure_dataset(a.project, a.location, ds, descr.get(ds_dir.name, "Cymbal Retail demo"), a.dry_run)
        for data in sorted(ds_dir.glob("*.jsonl")):
            table = data.name[: -len(".jsonl")]
            schema = ds_dir / f"{table}.schema.json"
            extra = []
            if ds_dir.name == "cymbal" and table == "shelf_locations":
                extra = staff_rows_to_keep(a.project, a.location, ds, ds_dir / "shelf_slots.jsonl")
            jobs.append((ds, table, data, schema, extra))
    failures = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(load_table, a.project, a.location, ds, t, d, s, e, a.dry_run): f"{ds}.{t}" for ds, t, d, s, e in jobs}
        for fut, name in futs.items():
            try:
                print(fut.result(), flush=True)
            except subprocess.CalledProcessError as exc:
                failures += 1
                print(f"  ERROR loading {name}: {(exc.stderr or exc.stdout or '').strip()[:500]}", flush=True)
    print(f"BigQuery seed: {len(jobs) - failures}/{len(jobs)} tables OK" + (" (dry run)" if a.dry_run else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
