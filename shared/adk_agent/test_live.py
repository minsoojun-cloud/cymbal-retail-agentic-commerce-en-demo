#!/usr/bin/env python3
"""Live end-to-end scenarios against cymbal-adk-agent (CONTRACT v3 SSE), simulating the page.

Usage:
  python3 test_live.py                       # URL from env ADK_AGENT_URL or DEPLOYMENT.env, ID token via gcloud
  python3 test_live.py --url http://localhost:8091
  python3 test_live.py --model gemini-3.8-flash --json /tmp/out.json

The script keeps a page simulator (cart / checkout / orders) that applies every streamed `action` exactly as
the storefront would, and sends it back as `page_state` on the next turn. It measures client-side
time-to-first-status / first-action / first-text / total per turn and checks the expected actions:
  (a) Recommend dinner for tonight   → show_products_on_page with sections (recommend_dinner_set), no shrimp
  (b) Add the set to my cart         → add_to_cart x N with the set quantities
  (c) Proceed to checkout            → start_checkout
  (d) Tomorrow afternoon please      → select_delivery_slot d1_pm
  (e) Place the order                → place_order with args.order_id CR-YYYYMMDD-NNNN
  (f) Recommend a frying pan         → show_products_on_page
  (g) Compare the top 3              → show_comparison
  (h) Which store has the first one in stock? → show_store_availability (after find_store_stock)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SHRIMP = ("shrimp", "prawn")
THINKING = "Thinking…"


def load_url() -> str:
  if os.environ.get("ADK_AGENT_URL"):
    return os.environ["ADK_AGENT_URL"]
  p = os.path.join(HERE, "DEPLOYMENT.env")
  if os.path.exists(p):
    for line in open(p, encoding="utf-8"):
      if line.startswith("ADK_AGENT_URL="):
        return line.split("=", 1)[1].strip().strip("'\"")
  return "http://localhost:8080"


def load_catalog() -> dict:
  cat = {}
  # full catalog first (has allergens), then the compact one baked into the image
  for p in (os.path.join(HERE, "..", "data", "products_retail.jsonl"), os.path.join(HERE, "data", "products_retail.jsonl")):
    if os.path.exists(p):
      for line in open(p, encoding="utf-8"):
        d = json.loads(line)
        cat[d["id"]] = {"title": d["title"], "price": int(d["priceInfo"]["price"]), "description": d.get("description", ""),
                        "allergens": next((a["value"].get("text") for a in d.get("attributes", []) if a["key"] == "allergens"), [])}
      break
  return cat


class Page:
  """Applies actions like online-store (cart.js / checkout.js) and produces page_state."""

  def __init__(self, catalog):
    self.cat = catalog
    self.cart: list[dict] = []
    self.checkout = {"state": "idle", "slot_id": None}
    self.orders: list[dict] = []
    self.view = {"view": "home", "query": "", "product_id": None}

  def apply(self, a: dict):
    n, args, res = a["name"], a.get("args") or {}, a.get("result") or {}
    if n == "add_to_cart" and res.get("ok"):
      pid, q = args["product_id"], int(args.get("quantity") or 1)
      line = next((l for l in self.cart if l["product_id"] == pid), None)
      if line:
        line["qty"] += q
      else:
        p = self.cat.get(pid, {})
        self.cart.append({"product_id": pid, "title": p.get("title", res.get("title", "")), "price": p.get("price", 0), "qty": q})
    elif n == "start_checkout" and res.get("ok"):
      self.checkout = {"state": "delivery", "slot_id": None}
    elif n == "select_delivery_slot" and res.get("ok"):
      self.checkout = {"state": "confirm", "slot_id": args["slot_id"]}
    elif n == "place_order" and res.get("ok"):
      self.orders.insert(0, {"order_id": args["order_id"], "status": "placed", "slot_label": res.get("slot_label")})
      self.cart = []
      self.checkout = {"state": "placed", "slot_id": self.checkout.get("slot_id")}
    elif n == "show_products_on_page":
      self.view = {"view": "set" if args.get("sections") else "search", "query": "", "product_id": None}

  def state(self) -> dict:
    return {"cart": self.cart, "checkout": self.checkout, "orders": self.orders, "page_context": self.view}


def id_token() -> str:
  return subprocess.check_output(["gcloud", "auth", "print-identity-token"], text=True).strip()


def run_turn(client, url, headers, body) -> dict:
  t0 = time.perf_counter()
  out = {"events": [], "text": "", "actions": [], "chips": [], "status": [], "t_first_status": None,
         "t_first_action": None, "t_first_text": None, "t_total": None, "error": None, "done": None}
  with client.stream("POST", url.rstrip("/") + "/api/chat/stream", json=body, headers=headers, timeout=120) as r:
    r.raise_for_status()
    for line in r.iter_lines():
      if not line.startswith("data:"):
        continue
      ev = json.loads(line[5:].strip())
      dt = round(time.perf_counter() - t0, 2)
      out["events"].append(ev)
      t = ev.get("type")
      if t == "status":
        out["status"].append(ev["text"])
        if out["t_first_status"] is None and ev["text"] != THINKING:
          out["t_first_status"] = dt
      elif t == "text":
        out["text"] += ev["text"]
        if out["t_first_text"] is None:
          out["t_first_text"] = dt
      elif t == "action":
        out["actions"].append(ev)
        if out["t_first_action"] is None:
          out["t_first_action"] = dt
      elif t == "chips":
        out["chips"] = ev["items"]
      elif t == "error":
        out["error"] = ev
      elif t == "done":
        out["done"] = ev
  out["t_total"] = round(time.perf_counter() - t0, 2)
  return out


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--url", default=None)
  ap.add_argument("--model", default=None)
  ap.add_argument("--json", default=None, help="write full transcript JSON here")
  ap.add_argument("--no-auth", action="store_true")
  a = ap.parse_args()
  url = a.url or load_url()
  local = url.startswith("http://localhost") or url.startswith("http://127.")
  headers = {} if (local or a.no_auth) else {"Authorization": f"Bearer {id_token()}"}
  cat = load_catalog()
  page = Page(cat)
  sid = f"live-{uuid.uuid4().hex[:8]}"
  results, failures = [], []
  ctx: dict = {}

  def check_a(r):
    sp = [x for x in r["actions"] if x["name"] == "show_products_on_page"]
    assert sp and sp[-1]["args"].get("sections"), "show_products_on_page with sections expected"
    args = sp[-1]["args"]
    ctx["set_ids"] = args["product_ids"]
    ctx["set_qty"] = args.get("quantities") or [1] * len(args["product_ids"])
    for pid in args["product_ids"]:
      p = cat.get(pid, {})
      hay = (p.get("title", "") + " " + " ".join(p.get("allergens") or [])).lower()
      assert not any(k in hay for k in SHRIMP), f"shrimp item in set: {p.get('title')}"
    text = re.sub(r"(containing|contains|contain|without|no|free of) (any )?shrimp", "", r["text"].lower())
    assert "prawn" not in text and "shrimp" not in text, "shrimp suggested in text"

  def check_b(r):
    adds = [x for x in r["actions"] if x["name"] == "add_to_cart"]
    got = {x["args"]["product_id"]: x["args"]["quantity"] for x in adds}
    want = dict(zip(ctx["set_ids"], ctx["set_qty"]))
    assert got == want, f"add_to_cart mismatch: got={got} want={want}"
    assert all(x["result"]["ok"] for x in adds)

  def check_c(r):
    sc = [x for x in r["actions"] if x["name"] == "start_checkout"]
    assert sc and sc[-1]["result"]["ok"] and len(sc[-1]["result"]["slots"]) == 5

  def check_d(r):
    s = [x for x in r["actions"] if x["name"] == "select_delivery_slot"]
    assert s and s[-1]["args"]["slot_id"] == "d1_pm" and s[-1]["result"]["next"] == "confirm"
    assert not any(x["name"] == "place_order" for x in r["actions"]), "must not place order before consent"

  def check_e(r):
    po = [x for x in r["actions"] if x["name"] == "place_order"]
    assert len(po) == 1, "exactly one place_order"
    oid = po[0]["args"].get("order_id", "")
    assert re.fullmatch(r"CR-\d{8}-\d{4}", oid), oid
    assert po[0]["result"]["order_id"] == oid and oid in r["text"], "order id must be in args/result/text"

  def check_f(r):
    sp = [x for x in r["actions"] if x["name"] == "show_products_on_page"]
    assert sp and len(sp[-1]["args"]["product_ids"]) >= 3
    assert len(sp[-1]["args"]["reasons"]) == len(sp[-1]["args"]["product_ids"])
    ctx["shown"] = sp[-1]["args"]["product_ids"]

  def check_g(r):
    sc = [x for x in r["actions"] if x["name"] == "show_comparison"]
    assert sc, "show_comparison expected"
    assert sc[-1]["args"]["product_ids"] == ctx["shown"][:3], f"top3 mismatch {sc[-1]['args']['product_ids']} vs {ctx['shown'][:3]}"
    assert all(len(row["values"]) == 3 for row in sc[-1]["args"]["rows"])

  def check_h(r):
    sa = [x for x in r["actions"] if x["name"] == "show_store_availability"]
    assert sa, "show_store_availability expected"
    assert sa[-1]["args"]["product_ids"] and isinstance(sa[-1]["args"].get("message"), str)
    assert sa[-1]["result"]["shown"] == len(sa[-1]["args"]["product_ids"])

  scenarios = [
      ("a", "Recommend dinner for tonight", check_a),
      ("b", "Add the set to my cart", check_b),
      ("c", "Proceed to checkout", check_c),
      ("d", "Tomorrow afternoon please", check_d),
      ("e", "Place the order", check_e),
      ("f", "Recommend a frying pan", check_f),
      ("g", "Compare the top 3", check_g),
      ("h", "Which store has the first one in stock?", check_h),
  ]
  with httpx.Client() as client:
    for key, msg, check in scenarios:
      body = {"session_id": sid, "customer_id": "CR-0001", "customer_name": "Sakura", "visitor_id": "v-7f3a9c21",
              "message": msg, "page_state": page.state()}
      if a.model:
        body["model"] = a.model
      try:
        r = run_turn(client, url, headers, body)
      except Exception as exc:
        r = {"error": str(exc), "actions": [], "text": "", "events": []}
      for act in r["actions"]:
        page.apply(act)
      ok, why = True, ""
      try:
        assert not r.get("error"), f"error event: {r.get('error')}"
        check(r)
      except AssertionError as e:
        ok, why = False, str(e)
        failures.append((key, why))
      results.append({"key": key, "message": msg, "ok": ok, "why": why, **{k: r.get(k) for k in (
          "t_first_status", "t_first_action", "t_first_text", "t_total", "text", "chips", "status")},
          "actions": [{"name": x["name"], "args": x["args"], "result": x["result"]} for x in r.get("actions", [])],
          "server_timings": (r.get("done") or {}).get("timings"), "model": (r.get("done") or {}).get("model")})
      print(f"\n=== ({key}) 👤 {msg}  [{'PASS' if ok else 'FAIL ' + why}]")
      print(f"  ⏱ status={r.get('t_first_status')}s action={r.get('t_first_action')}s first_text={r.get('t_first_text')}s total={r.get('t_total')}s")
      print(f"  status: {r.get('status')}")
      for x in r.get("actions", []):
        args = json.dumps(x["args"], ensure_ascii=False)
        print(f"  ⚡ {x['name']}({args[:220]}{'…' if len(args) > 220 else ''}) → {json.dumps(x['result'], ensure_ascii=False)[:200]}")
      print(f"  🤖 {r.get('text')}")
      print(f"  chips: {r.get('chips')}")
  ttft = [r["t_first_text"] for r in results if r.get("t_first_text")]
  tot = [r["t_total"] for r in results if r.get("t_total")]
  print(f"\nSUMMARY model={results[0].get('model')} pass={len(results) - len(failures)}/{len(results)} "
        f"avg_first_text={sum(ttft) / max(1, len(ttft)):.2f}s avg_total={sum(tot) / max(1, len(tot)):.2f}s")
  for k, w in failures:
    print(f"  FAIL ({k}): {w}")
  if a.json:
    with open(a.json, "w", encoding="utf-8") as f:
      json.dump({"url": url, "session_id": sid, "results": results}, f, ensure_ascii=False, indent=1)
  return 1 if failures else 0


if __name__ == "__main__":
  sys.exit(main())
