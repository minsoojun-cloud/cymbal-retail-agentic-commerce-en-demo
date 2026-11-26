"""Fake ADK agent service for local E2E of the LIVE chat path (SSE contract), no Gemini needed.

  DATA_FILE=online-store/testdata/products_en.jsonl uvicorn tests.fake_adk_agent:app --port 8091   (from the repo root)
  ADK_AGENT_URL=http://127.0.0.1:8091 uvicorn app.main:app --port 8092                               (from online-store)

POST /api/chat/stream {session_id, customer_id, customer_name, visitor_id, message, page_state}
-> text/event-stream: status / text (deltas) / action / chips / done, computed from page_state like the real agent.
English only (CONTRACT_EN v14). Includes the omnichannel page action `show_store_availability`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import random
import re
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DATA_FILE") or ROOT / "online-store" / "testdata" / "products_en.jsonl")
CATALOG: dict[str, dict] = {}
for line in DATA.read_text(encoding="utf-8").splitlines():
    try:
        p = json.loads(line)
    except json.JSONDecodeError:
        continue
    attrs = {a["key"]: (a.get("value") or {}).get("text") or [] for a in p.get("attributes") or [] if isinstance(a, dict)}
    CATALOG[p["id"]] = {"id": p["id"], "title": p["title"], "price": int(p["priceInfo"]["price"]),
                        "cat": (p.get("categories") or [""])[0], "channel": (attrs.get("channel") or ["omni"])[0],
                        "allergens": attrs.get("allergens") or []}

SLOTS = {"d1_am": "Tomorrow 9:00-12:00", "d1_pm": "Tomorrow 14:00-17:00", "d1_eve": "Tomorrow 18:00-21:00",
         "d2_am": "Day after tomorrow 9:00-12:00", "d2_pm": "Day after tomorrow 14:00-17:00"}
SESS: dict[str, dict] = {}
app = FastAPI(title="fake cymbal-adk-agent")
REQUESTS: list[dict] = []


def find(word: str, cat: str = "", channel_ok=("omni", "online_only")) -> dict | None:
    w = word.lower()
    for p in CATALOG.values():
        if (w in p["title"].lower() and cat.lower() in p["cat"].lower() and p["channel"] in channel_ok
                and "shrimp" not in p["allergens"] and "food service" not in p["title"].lower()):
            return p
    return None


def slot_of(msg: str) -> str:
    """slot label ("Tomorrow 14:00-17:00") or natural language (voice: "tomorrow afternoon", "day after tomorrow morning")"""
    for k, v in SLOTS.items():
        if v.lower() in msg.lower():
            return k
    m = msg.lower()
    day = "d2" if "day after tomorrow" in m else "d1" if "tomorrow" in m else ""
    part = ("pm" if ("afternoon" in m or "14" in m) else "eve" if ("evening" in m or "night" in m or "18" in m)
            else "am" if ("morning" in m or re.search(r"\b9\b", m)) else "")
    if not day or not part or (day == "d2" and part == "eve"):
        return ""
    return f"{day}_{part}"


def ev(**k) -> bytes:
    return ("data: " + json.dumps(k, ensure_ascii=False) + "\n\n").encode()


async def say(text: str):
    for i in range(0, len(text), 12):
        yield ev(type="text", text=text[i:i + 12], author="cymbal_concierge")
        await asyncio.sleep(0.02)


def totals(cart: list[dict]) -> tuple[int, int]:
    sub = sum(int(l["price"]) * int(l["qty"]) for l in cart)
    return sub, (0 if sub >= 3000 or sub == 0 else 330)


async def turn(body: dict):
    msg = body.get("message", "")
    m = msg.lower()
    ps = body.get("page_state") or {}
    cart = ps.get("cart") or []
    sess = SESS.setdefault(body.get("session_id", ""), {})
    first = (body.get("customer_name") or "").split(" ")[0] or "you"
    yield ev(type="status", text="Thinking…")
    await asyncio.sleep(0.15)
    if "dinner" in m or "menu" in m:
        yield ev(type="status", text="Checking your purchase history and family preferences…")
        roles = [("Main", find("salmon") or find("yellowtail"), ["Frequently bought", "Family favorite"], 2),
                 ("Side", find("tofu") or find("spinach"), ["Quick"], 1),
                 ("Soup", find("miso"), ["Family favorite"], 1),
                 ("Staple", find("rice"), ["Frequently bought"], 1),
                 ("Dessert & fruit", find("persimmon") or find("apple"), ["In season"], 1)]
        secs = [{"label": r, "product_ids": [p["id"]], "reasons": [f"Picked as the {r.lower()} (test)"], "tags": [t],
                 "quantities": [q]} for r, p, t, q in roles if p]
        sess["set"] = [(s["product_ids"][0], s["quantities"][0]) for s in secs]
        args = {"theme": f"Tonight's dinner set for the {body.get('customer_name', '').split(' ')[-1] or 'family'} family (test)",
                "message": "Based on what you often buy, family favorites and what's in season",
                "product_ids": [s["product_ids"][0] for s in secs], "reasons": [s["reasons"][0] for s in secs],
                "sections": secs}
        yield ev(type="action", name="show_products_on_page", args=args, result={"shown": len(secs)})
        async for c in say(f"How about a **salmon chan-chan yaki dinner** tonight, {first}?\n- Main: salmon meal kit\n"
                           f"- With side, soup, rice and fruit ({len(secs)} items, no shrimp)"):
            yield c
        yield ev(type="chips", items=["Add the set to my cart", "Show me another set", "Which store has the potato salad?"])
    elif "set" in m and ("cart" in m or "add" in m):
        for pid, q in sess.get("set", []):
            yield ev(type="action", name="add_to_cart", args={"product_id": pid, "quantity": q}, result={"ok": True})
        async for c in say("I added the set to your cart."):
            yield c
        yield ev(type="chips", items=["Proceed to checkout"])
    elif "store" in m or "pick up" in m or "pickup" in m or "in stock" in m:
        p = None
        for w in re.findall(r"[a-z]+", m):
            if len(w) > 3 and w not in ("store", "stores", "which", "where", "pick", "have", "stock", "salad", "want", "today", "with", "that", "there", "this", "nearby", "near"):
                p = find(w, channel_ok=("omni", "store_only"))
                if p:
                    break
        p = p or find("potato salad", channel_ok=("store_only",)) or next(iter(CATALOG.values()))
        yield ev(type="status", text="Checking store inventory…")
        note = " It's sold in stores only, so it can't be delivered." if p["channel"] == "store_only" else ""
        yield ev(type="action", name="show_store_availability",
                 args={"product_ids": [p["id"]], "message": f"Store availability for {p['title']}.{note}"},
                 result={"shown": 1})
        async for c in say(f"Here's where you can pick up **{p['title']}** today.{note}"):
            yield c
        yield ev(type="chips", items=["Recommend dinner for tonight"])
    elif "checkout" in m:
        if not cart:
            async for c in say("Your cart is empty."):
                yield c
        else:
            yield ev(type="action", name="start_checkout", args={},
                     result={"ok": True, "slots": [{"slot_id": k, "label": v} for k, v in SLOTS.items()]})
            async for c in say("Please choose a delivery time."):
                yield c
    elif slot_of(msg) and "place" not in m:
        sid = slot_of(msg)
        sub, ship = totals(cart)
        yield ev(type="action", name="select_delivery_slot", args={"slot_id": sid},
                 result={"ok": True, "total_tax_included": sub + ship})
        async for c in say(f"Delivery set for {SLOTS[sid]}. Please review your order."):
            yield c
    elif "place" in m or "confirm" in m:
        oid = "CR-" + dt.date.today().strftime("%Y%m%d") + "-" + str(random.randint(1000, 9999))
        sub, ship = totals(cart)
        yield ev(type="status", text="Placing your order…")
        yield ev(type="action", name="place_order", args={"order_id": oid},
                 result={"ok": True, "order_id": oid, "total_tax_included": sub + ship})
        async for c in say(f"Your order is placed (**{oid}**)."):
            yield c
        yield ev(type="chips", items=["What's my order status?"])
    else:
        async for c in say(f"Hi {first}! How can I help you today?"):
            yield c
    yield ev(type="done", session_id=body.get("session_id", ""))


@app.post("/api/chat/stream")
async def chat_stream(request: Request, body: dict = Body(default={})):
    REQUESTS.append({"auth": bool(request.headers.get("authorization")), **body})
    return StreamingResponse(turn(body), media_type="text/event-stream")


@app.get("/debug/requests")
async def debug_requests():
    return REQUESTS[-20:]
