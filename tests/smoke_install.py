#!/usr/bin/env python3
"""Post-install smoke test for the Cymbal Retail demo (standard library only).

Reads the service URLs from the environment (source config.env) or from --site/--store-app/--staff/--enrich, and checks:
  * all 4 public apps answer HTTP 200 on / and /api/health
  * online store: search "curry" returns products; /api/products/<id>/stores answers from Retail Local Inventory
  * store app: catalog of 3,000 products on the Retail engine; availability at S01 comes from retail_local_inventory
  * staff app: staff router enabled, manager console /admin reachable
  * Enrichment Studio: 63 seeded SKUs
Use --id-token when the apps are not public (sends `gcloud auth print-identity-token`).

  source config.env && python3 tests/smoke_install.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RESULTS: list[tuple[bool, str]] = []
HEADERS: dict[str, str] = {}


def get(url: str, timeout: int = 60, retries: int = 3) -> tuple[int, bytes]:
    last = (0, b"")
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "cymbal-smoke/1.0", **HEADERS})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, e.read())
            if e.code < 500:
                return last
        except Exception as e:  # noqa: BLE001
            last = (0, str(e).encode())
        time.sleep(3 * (i + 1))
    return last


def get_json(url: str):
    code, body = get(url)
    try:
        return code, json.loads(body or b"{}")
    except json.JSONDecodeError:
        return code, {}


def check(ok: bool, msg: str) -> bool:
    RESULTS.append((ok, msg))
    print(f"  {'PASS' if ok else 'FAIL'}  {msg}", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=os.environ.get("SITE_BASE_URL", ""))
    ap.add_argument("--store-app", default=os.environ.get("STORE_APP_URL", ""))
    ap.add_argument("--staff", default=os.environ.get("STAFF_URL", ""))
    ap.add_argument("--enrich", default=os.environ.get("ENRICH_URL", ""))
    ap.add_argument("--query", default="curry")
    ap.add_argument("--store", default="S01")
    ap.add_argument("--id-token", action="store_true")
    a = ap.parse_args()
    if not all([a.site, a.store_app, a.staff, a.enrich]):
        print("set SITE_BASE_URL / STORE_APP_URL / STAFF_URL / ENRICH_URL (source config.env) or pass the URLs", file=sys.stderr)
        return 2
    if a.id_token:
        tok = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, check=True).stdout.strip()
        HEADERS["Authorization"] = f"Bearer {tok}"

    print("[1] public apps")
    for name, base in (("online store", a.site), ("store app", a.store_app), ("staff app", a.staff), ("Enrichment Studio", a.enrich)):
        code, _ = get(base.rstrip("/") + "/")
        check(code == 200, f"{name:18s} GET /            -> {code}  ({base})")
        code, _ = get(base.rstrip("/") + "/api/health")
        check(code == 200, f"{name:18s} GET /api/health  -> {code}")

    print("[2] online store")
    q = urllib.parse.quote(a.query)
    code, d = get_json(f"{a.site}/api/search?q={q}")
    results = d.get("results") or []
    check(code == 200 and len(results) > 0, f'search "{a.query}" -> {len(results)} results'
          + (f' (top: {results[0].get("title")})' if results else ""))
    pid = next((r["id"] for r in results if r.get("channel", "omni") != "online_only"), results[0]["id"] if results else "")
    if pid:
        code, d = get_json(f"{a.site}/api/products/{pid}/stores")
        stores = d.get("stores") or []
        check(code == 200 and len(stores) > 0, f"/api/products/{pid}/stores -> {len(stores)} stores")
        check(d.get("source") == "retail_local_inventory", f"store availability source = {d.get('source')!r} (want 'retail_local_inventory')")

    print("[3] store app")
    code, h = get_json(f"{a.store_app}/api/health")
    check(h.get("engine") == "retail" and int(h.get("catalog") or 0) >= 3000,
          f"health: engine={h.get('engine')} catalog={h.get('catalog')} data_mode={h.get('data_mode')}")
    code, d = get_json(f"{a.store_app}/api/search?q={q}&store_id={a.store}")
    sres = d.get("results") or []
    check(code == 200 and len(sres) > 0, f'search "{a.query}" at {a.store} -> {len(sres)} results')
    if pid:
        code, d = get_json(f"{a.store_app}/api/products/{pid}/availability?store_id={a.store}")
        cur = d.get("current") or {}
        check(code == 200 and d.get("source") == "retail_local_inventory",
              f"availability {pid}@{a.store}: source={d.get('source')!r} status={cur.get('status')} qty={cur.get('quantity')}")

    print("[4] staff app + manager console")
    code, h = get_json(f"{a.staff}/api/health")
    check(bool(h.get("staff_router")), f"staff health: app_mode={h.get('app_mode')} staff_router={h.get('staff_router')}")
    code, _ = get(f"{a.staff}/admin")
    check(code == 200, f"manager console GET /admin -> {code}")

    print("[5] Enrichment Studio")
    code, h = get_json(f"{a.enrich}/api/health")
    check(int(h.get("products") or 0) >= 63, f"health: products={h.get('products')} enriched={h.get('enriched')} gmc={h.get('gmc')}")

    failed = [m for ok, m in RESULTS if not ok]
    print(f"\nsmoke test: {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
