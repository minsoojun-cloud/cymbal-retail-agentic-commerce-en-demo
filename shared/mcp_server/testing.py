#!/usr/bin/env python3
"""Smoke test for cymbal-commerce-mcp: initialize → tools/list → tools/call (JSON-RPC over /mcp).

Usage:
  python3 testing.py --url http://localhost:8080
  python3 testing.py --url https://cymbal-commerce-mcp-xxxx.a.run.app --auth   # ID token via gcloud
No third-party deps (urllib only).
"""
import argparse
import json
import subprocess
import sys
import urllib.request


def id_token() -> str:
    return subprocess.check_output(["gcloud", "auth", "print-identity-token"], text=True).strip()


def rpc(url, method, params=None, rid=1, token=None, timeout=30):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url.rstrip("/") + "/mcp", data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
    if raw.startswith("event:") or raw.startswith("data:"):  # SSE fallback
        raw = next(l[5:].strip() for l in raw.splitlines() if l.startswith("data:"))
    return json.loads(raw)


def call_tool(url, name, args, token, rid):
    res = rpc(url, "tools/call", {"name": name, "arguments": args}, rid, token, timeout=60)
    if "error" in res:
        return res["error"]
    text = res["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except ValueError:
        return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--auth", action="store_true", help="send `gcloud auth print-identity-token` as Bearer")
    ap.add_argument("--query", default="frying pan")
    ap.add_argument("--customer", default="CR-0001")
    ap.add_argument("--month", default="10", help='1-12 or "YYYY-MM"')
    a = ap.parse_args()
    token = id_token() if a.auth else None

    init = rpc(a.url, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "cr-testing", "version": "1.0"}}, 1, token)
    print("initialize:", init.get("result", {}).get("serverInfo"))

    tools = rpc(a.url, "tools/list", {}, 2, token)["result"]["tools"]
    print("tools/list:", [t["name"] for t in tools])

    s = call_tool(a.url, "search_products", {"query": a.query, "page_size": 5}, token, 3)
    if isinstance(s, dict) and "productDetails" in s:
        print(f"search_products('{a.query}'): totalSize={s.get('totalSize')} returned={len(s['productDetails'])}")
        for p in s["productDetails"][:5]:
            print(f"  - {p['productId']} {p['title']} ¥{p['price']} size={p.get('size')} sale={p.get('onSale', False)} labels={p.get('labels')}")
        if s.get("warning") or s.get("error"):
            print("  warning/error:", s.get("warning") or s.get("error"))
        pid = s["productDetails"][0]["productId"] if s["productDetails"] else None
    else:
        print("search_products:", s)
        pid = None

    big = call_tool(a.url, "search_products", {"query": a.query, "page_size": 500}, token, 4)
    if isinstance(big, dict):
        print(f"search_products(page_size=500) → returned={len(big.get('productDetails', []))} (must be ≤100)")

    if pid:
        d = call_tool(a.url, "get_product_details", {"product_id": pid}, token, 5)
        print("get_product_details:", {k: d.get(k) for k in ("productId", "title", "price", "availability")} if isinstance(d, dict) else d)
        if isinstance(d, dict):
            print("  attributes:", d.get("attributes"))

        ids = [p["productId"] for p in s["productDetails"][:3]] if isinstance(s, dict) else []
        if len(ids) >= 2:
            c = call_tool(a.url, "compare_products", {"product_ids": ids}, token, 8)
            print("compare_products:", json.dumps(c, ensure_ascii=False)[:800])

    prof = call_tool(a.url, "get_customer_profile", {"customer_id": a.customer}, token, 6)
    if isinstance(prof, dict):
        print("get_customer_profile:", prof.get("name"), prof.get("age"), prof.get("allergies"), "source=", prof.get("_source"))

    month = int(a.month) if a.month.isdigit() else a.month
    args = {"customer_id": a.customer, "month": month, "limit": 10}
    h = call_tool(a.url, "get_purchase_history", args, token, 7)
    if isinstance(h, dict):
        print(f"get_purchase_history(month={month!r}): orders={h.get('orderCount')} rows={h.get('totalRows')} source={h.get('_source')}")
        for t in (h.get("topProducts") or [])[:8]:
            print(f"  * {t['product_title']} x{t['quantity']} ({t['times']} times)")

    if pid:
        inv = call_tool(a.url, "check_store_inventory", {"product_ids": [pid]}, token, 9)
        if isinstance(inv, dict):
            for it in inv.get("items", []):
                print(f"check_store_inventory({it['product_id']}): channel={it['channel']} online={it['online_available']}"
                      f" source={inv.get('_source')}")
                for st in it.get("stores", []):
                    print(f"  - {st['store_id']} {st['name']} qty={st['quantity']} {st['availability']} open={st['open_now']}"
                          f" {st.get('shelf_label', '')}")
        else:
            print("check_store_inventory:", inv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
