#!/usr/bin/env python3
"""Push store inventory to Retail API LocalInventory (CONTRACT v8) — throttled, retried, resumable.

For each product carried by any store: products.addLocalInventories with one LocalInventory per store:
  placeId = store_id, attributes: shelf_id (fixture id, e.g. "A01" / "RX"), slot_ids (comma-joined text, e.g.
  "H44-504,H44-505" — Retail allows 1 value per local attribute), tier (number), in_stock ("true"/"false"),
  quantity (number = availableQuantity), availability ("IN_STOCK"/"OUT_OF_STOCK"); fulfillmentTypes ["pickup-in-store"]
  when in stock. (Retail v2 LocalInventory has no native availability/quantity field, so they are attributes.)
Must run AFTER any Retail FULL import (re-run after re-imports; use --force to ignore the state file).

  python3 push_local_inventory.py [--workers 12] [--limit N] [--force] [--verify] [--probe]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "store-navi", "data")
STATE_DEFAULT = os.path.join(ROOT, "install", ".state", "local_inventory_state.jsonl")
PROJECT = os.environ.get("PROJECT_ID", "")
if not PROJECT and os.path.exists(os.path.join(ROOT, "config.env")):
    for line in open(os.path.join(ROOT, "config.env"), encoding="utf-8"):
        if line.startswith("export PROJECT_ID="):
            PROJECT = line.split("=", 1)[1].strip().strip('"')
BRANCH = f"projects/{PROJECT}/locations/global/catalogs/default_catalog/branches/default_branch"
API = "https://retail.googleapis.com/v2"
SLOT_LIST = os.environ.get("SLOT_IDS_AS_LIST", "0") == "1"  # Retail allows only 1 value per local-inventory attribute -> comma-joined
os.environ["PATH"] = os.path.expanduser("~/google-cloud-sdk/bin") + ":" + os.environ.get("PATH", "")


class Token:
    def __init__(self):
        self.lock = threading.Lock()
        self.tok, self.at = None, 0.0

    def get(self) -> str:
        with self.lock:
            if not self.tok or time.time() - self.at > 1800:
                self.tok = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
                self.at = time.time()
            return self.tok


TOKEN = Token()


def call(method: str, url: str, body=None, retries=6):
    data = json.dumps(body).encode() if body is not None else None
    delay = 1.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {TOKEN.get()}", "Content-Type": "application/json",
            "x-goog-user-project": PROJECT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg[:400]}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise RuntimeError(str(e)) from None


def build_payloads() -> dict[str, list[dict]]:
    inv = [json.loads(x) for x in open(os.path.join(DATA, "store_inventory.jsonl"), encoding="utf-8")]
    latest = {}
    for x in open(os.path.join(DATA, "shelf_locations.jsonl"), encoding="utf-8"):
        r = json.loads(x)
        k = (r["store_id"], r["product_id"])
        if k not in latest or r["registered_at"] >= latest[k]["registered_at"]:
            latest[k] = r
    per_product: dict[str, list[dict]] = defaultdict(list)
    for r in inv:
        sh = latest.get((r["store_id"], r["product_id"]))
        in_stock = r["quantity"] > 0
        li = {"placeId": r["store_id"], "attributes": {
            "in_stock": {"text": ["true" if in_stock else "false"]},
            "quantity": {"numbers": [float(r["quantity"])]},
            "availability": {"text": [r["availability"]]},
        }}
        if sh:
            li["attributes"]["shelf_id"] = {"text": [sh.get("fixture_id") or sh["gondola_id"]]}
            li["attributes"]["tier"] = {"numbers": [float(sh["tier"])]}
            if sh.get("slot_ids"):
                li["attributes"]["slot_ids"] = ({"text": list(sh["slot_ids"])} if SLOT_LIST
                                                else {"text": [",".join(sh["slot_ids"])]})
        if in_stock:
            li["fulfillmentTypes"] = ["pickup-in-store"]
        per_product[r["product_id"]].append(li)
    for v in per_product.values():
        v.sort(key=lambda li: li["placeId"])
    return per_product


def sig(lis) -> str:
    return hashlib.sha1(json.dumps(lis, sort_keys=True).encode()).hexdigest()[:16]


STORES = ["S01", "S02", "S03"]


def push(args):
    adds = build_payloads()
    catalog_ids = [json.loads(x)["id"] for x in open(os.environ.get("CATALOG_JSONL") or os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl"), encoding="utf-8")]
    payloads = {}
    for pid in catalog_ids:
        lis = adds.get(pid, [])
        have = {li["placeId"] for li in lis}
        payloads[pid] = {"add": lis, "remove": [s for s in STORES if s not in have]}
    done = {}
    if os.path.exists(args.state) and not args.force:
        for x in open(args.state, encoding="utf-8"):
            r = json.loads(x)
            done[r["id"]] = r["sig"]
    todo = [(pid, lis) for pid, lis in sorted(payloads.items()) if done.get(pid) != sig(lis)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"catalog products: {len(payloads)} (with local inventory: {len(adds)}); to push: {len(todo)} "
          f"(state: {args.state})", flush=True)
    os.makedirs(os.path.dirname(args.state), exist_ok=True)
    lock = threading.Lock()
    stats = {"ok": 0, "err": 0}
    errors = []
    min_interval = 1.0 / args.qps if args.qps else 0
    last = [0.0]

    def throttle():
        if not min_interval:
            return
        with lock:
            now = time.time()
            wait = last[0] + min_interval - now
            last[0] = max(now, last[0] + min_interval)
        if wait > 0:
            time.sleep(wait)

    def work(item):
        pid, pl = item
        url = f"{API}/{BRANCH}/products/{urllib.parse.quote(pid)}"
        if pl["remove"]:  # stale places (product no longer carried there)
            throttle()
            call("POST", f"{url}:removeLocalInventories", {"placeIds": pl["remove"], "allowMissing": True})
        if pl["add"]:
            throttle()
            call("POST", f"{url}:addLocalInventories", {"localInventories": pl["add"], "allowMissing": True})
        return pid, sig(pl)

    t0 = time.time()
    with open(args.state, "a", encoding="utf-8") as st, cf.ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(work, it): it[0] for it in todo}
        for i, f in enumerate(cf.as_completed(futs), 1):
            try:
                pid, s = f.result()
                with lock:
                    st.write(json.dumps({"id": pid, "sig": s}) + "\n")
                    st.flush()
                stats["ok"] += 1
            except Exception as e:  # noqa: BLE001
                stats["err"] += 1
                errors.append((futs[f], str(e)))
            if i % 500 == 0:
                print(f"  {i}/{len(todo)} ok={stats['ok']} err={stats['err']} {time.time() - t0:.0f}s", flush=True)
    print(f"done ok={stats['ok']} err={stats['err']} in {time.time() - t0:.0f}s")
    for pid, e in errors[:10]:
        print("  ERR", pid, e)
    return stats


def verify(ids):
    for pid in ids:
        p = call("GET", f"{API}/{BRANCH}/products/{pid}")
        lis = p.get("localInventories", [])
        print(pid, p.get("title"), json.dumps([{**li} for li in lis], ensure_ascii=False)[:600])
        ff = p.get("fulfillmentInfo")
        if ff:
            print("   fulfillmentInfo:", ff)


def probe(store="S01", query="cold medicine"):
    url = f"{API}/{BRANCH.replace('/branches/default_branch', '')}/servingConfigs/default_search:search"
    filters = [
        f'inventory({store}, attributes.in_stock): ANY("true")',
        f'inventory("{store}", attributes.in_stock): ANY("true")',
        f'inventory({store}, attributes.shelf_id): ANY("A-01")',
        f'pickupInStore: ANY("{store}")',
        f'inventory({store}, attributes.quantity) > 0',
    ]
    out = []
    for flt in filters:
        body = {"placement": url, "visitorId": "probe-local-inventory", "query": query, "pageSize": 5, "filter": flt}
        try:
            r = call("POST", url, body, retries=2)
            n = r.get("totalSize", 0)
            out.append((flt, "OK", n, [x.get("id") for x in r.get("results", [])][:5]))
        except Exception as e:  # noqa: BLE001
            out.append((flt, "ERROR", 0, str(e)[:300]))
    base = call("POST", url, {"placement": url, "visitorId": "probe-local-inventory", "query": query, "pageSize": 5})
    print("baseline totalSize:", base.get("totalSize"))
    for o in out:
        print(json.dumps(o, ensure_ascii=False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--qps", type=float, default=15.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--verify", nargs="*", default=None)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    if not a.no_push:
        push(a)
    if a.verify is not None:
        sc = json.load(open(os.path.join(DATA, "scenarios.json"), encoding="utf-8"))
        ids = a.verify or [sc["cold_tablet_out_at_S01"]["product_id"], sc["fever_set_S01"][0],
                           sc["beauty_out_at_S03"]["product_id"]]
        verify(ids)
    if a.probe:
        probe()


if __name__ == "__main__":
    main()
