#!/usr/bin/env python3
"""Live verification of the English catalog in Retail (CONTRACT_EN.md, owner: data).

Checks: product count in branch 0, GetProduct localInventories (+ fulfillment pickup-in-store), search filter
pickupInStore: ANY("S01"), English queries return results, channel facet.
Usage: source config.env && python shared/catalog/verify_retail.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = os.environ["PROJECT_ID"]
NUM = os.environ["PROJECT_NUMBER"]
CAT = f"projects/{NUM}/locations/global/catalogs/default_catalog"
API = "https://retail.googleapis.com/v2alpha"
TOK = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def call(method, path, body=None, params=""):
    url = f"{API}/{path}" + (f"?{params}" if params else "")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json",
                                          "X-Goog-User-Project": PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"error": f"{e.code} {e.read().decode()[:300]}"}


def search(q, flt="", facets=None):
    body = {"visitorId": "verify-data", "query": q, "pageSize": 5, "languageCode": "en", "filter": flt}
    if facets:
        body["facetSpecs"] = [{"facetKey": {"key": f}} for f in facets]
    return call("POST", f"{CAT}/servingConfigs/default_search:search", body)


def main():
    ok = True
    # 1. count
    n, token = 0, ""
    while True:
        r = call("GET", f"{CAT}/branches/0/products", params=urllib.parse.urlencode(
            {"pageSize": 1000, "pageToken": token, "readMask": "id"}))
        n += len(r.get("products", []))
        token = r.get("nextPageToken", "")
        if not token:
            break
    exp = sum(1 for _ in open(os.environ.get("CATALOG_JSONL") or os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl")))
    print(f"[count] retail products={n} expected={exp}", "OK" if n == exp else "MISMATCH")
    ok &= n == exp
    # 2. local inventory on scenario products
    sc = json.load(open(os.path.join(ROOT, "store-navi", "data", "scenarios.json")))
    for pid in [sc["cold_tablet_out_at_S01"]["product_id"], sc["fever_set_S01"][0]]:
        p = call("GET", f"{CAT}/branches/0/products/{pid}")
        li = p.get("localInventories", [])
        ff = p.get("fulfillmentInfo", [])
        print(f"[local] {pid} {p.get('title')}: " + ", ".join(
            f"{x['placeId']} qty={x.get('attributes', {}).get('quantity', {}).get('numbers')}" for x in li),
              "| fulfillment:", [(f.get("type"), f.get("placeIds")) for f in ff])
        ok &= bool(li)
    # 3. pickupInStore filter + English queries
    for q in ["curry", "salmon", "cold medicine", "frying pan", "sushi", "dog food"]:
        r = search(q)
        r2 = search(q, 'pickupInStore: ANY("S01")')
        print(f"[search] {q!r}: total={r.get('totalSize', 0)} pickup S01={r2.get('totalSize', 0)} "
              f"{r.get('error') or r2.get('error') or ''} top={[x.get('id') for x in r.get('results', [])[:3]]}")
        ok &= r.get("totalSize", 0) > 0
    r = search("", 'pickupInStore: ANY("S01")')
    print(f"[filter] browse pickupInStore S01 total={r.get('totalSize', 0)} {r.get('error', '')}")
    ok &= r.get("totalSize", 0) > 0
    r = search("milk", facets=["attributes.channel"])
    print("[facet] channel:", [(v.get("value"), v.get("count")) for f in r.get("facets", []) for v in f.get("values", [])])
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
