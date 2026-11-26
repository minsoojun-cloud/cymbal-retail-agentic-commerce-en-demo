#!/usr/bin/env python3
"""Render the project-independent seed files for the target project.

The seed files in `seed/` contain placeholders instead of project specific values:
  __SITE_BASE_URL__   -> $SITE_BASE_URL   (product `uri` and `images[].uri` of the 3,000-product catalog)
  __STORE_APP_URL__   -> $STORE_APP_URL
  __STAFF_URL__       -> $STAFF_URL
  __ENRICH_URL__      -> $ENRICH_URL
  __PROJECT_ID__      -> $PROJECT_ID      (e.g. dataset references inside enrichment batch metadata)
  __PROJECT_NUMBER__  -> $PROJECT_NUMBER
Legacy absolute Cloud Run URLs of any project (`https://<prefix>-site-<number>.<region>.run.app`) are rewritten too,
so a catalog exported from another installation can be dropped into seed/catalog unchanged.

Output goes to $RENDERED_DIR (default install/.state/rendered) — the files under seed/ are never modified.

Usage:
  python3 install/lib/render_seed.py                # catalog + BigQuery seed
  python3 install/lib/render_seed.py --catalog-only # catalog files only (used by config.env / deploy scripts)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEED = REPO / "seed"
CATALOG_FILES = ["products_retail.jsonl", "autocomplete_suggestions.jsonl", "recipes_en.json", "stores_en.json"]
LEGACY_SITE_RE = re.compile(r"https://[a-z][a-z0-9-]*-site-[0-9]{6,15}\.[a-z0-9-]+\.run\.app")


def env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise SystemExit(f"render_seed.py: environment variable {name} is required (source config.env first)")
    return val.rstrip("/")


def make_rewriter():
    repl = {
        "__SITE_BASE_URL__": env("SITE_BASE_URL"),
        "__STORE_APP_URL__": env("STORE_APP_URL"),
        "__STAFF_URL__": env("STAFF_URL"),
        "__ENRICH_URL__": env("ENRICH_URL"),
        "__PROJECT_ID__": env("PROJECT_ID"),
        "__PROJECT_NUMBER__": env("PROJECT_NUMBER"),
    }
    site = repl["__SITE_BASE_URL__"]

    def rewrite(text: str) -> str:
        for k, v in repl.items():
            if k in text:
                text = text.replace(k, v)
        return LEGACY_SITE_RE.sub(site, text)

    return rewrite


def render_catalog(rewrite, out_dir: Path) -> None:
    out = out_dir / "catalog"
    out.mkdir(parents=True, exist_ok=True)
    for name in CATALOG_FILES:
        src = SEED / "catalog" / name
        if not src.exists():
            print(f"  [skip] {src} (missing)", file=sys.stderr)
            continue
        text = rewrite(src.read_text(encoding="utf-8"))
        if name.endswith(".jsonl"):  # validate every row
            rows = 0
            for i, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    obj = json.loads(line)
                    if name == "products_retail.jsonl" and (not obj.get("id") or not obj.get("title")):
                        raise SystemExit(f"{src}:{i}: product without id/title")
                    rows += 1
        else:
            json.loads(text)
            rows = 1
        tmp = out / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out / name)
        print(f"  rendered catalog/{name} ({rows} rows)", file=sys.stderr)


def render_bigquery(rewrite, out_dir: Path) -> None:
    for ds_dir in sorted((SEED / "bigquery").glob("*")):
        if not ds_dir.is_dir():
            continue
        out = out_dir / "bigquery" / ds_dir.name
        out.mkdir(parents=True, exist_ok=True)
        for gz in sorted(ds_dir.glob("*.jsonl.gz")):
            table = gz.name[: -len(".jsonl.gz")]
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                text = rewrite(f.read())
            (out / f"{table}.jsonl").write_text(text, encoding="utf-8")
            schema = ds_dir / f"{table}.schema.json"
            if schema.exists():
                (out / f"{table}.schema.json").write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")
            rows = sum(1 for line in text.splitlines() if line.strip())
            print(f"  rendered bigquery/{ds_dir.name}/{table} ({rows} rows)", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--out", default=os.environ.get("RENDERED_DIR") or str(REPO / "install" / ".state" / "rendered"))
    a = ap.parse_args()
    out_dir = Path(a.out)
    rewrite = make_rewriter()
    print(f"render_seed: {SEED} -> {out_dir} (site: {env('SITE_BASE_URL')})", file=sys.stderr)
    render_catalog(rewrite, out_dir)
    if not a.catalog_only:
        render_bigquery(rewrite, out_dir)
    stamp = "|".join(env(k) for k in ("PROJECT_ID", "PROJECT_NUMBER", "REGION", "SERVICE_PREFIX", "SITE_BASE_URL",
                                      "STORE_APP_URL", "STAFF_URL", "ENRICH_URL"))
    (out_dir / ".stamp").write_text(stamp, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
