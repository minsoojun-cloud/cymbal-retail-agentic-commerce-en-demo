"""Store concierge tools (ADK function tools) + server-side builder for the `show_store_items` client action.

MCP tools (cymbal-commerce-mcp via ID token; fallback: AICS / local catalog directly):
  search_products, get_product_details
Local tools: check_availability
Client actions (recorded per turn → streamed as SSE `action` / `chips`): show_store_items, suggest_replies
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional, TypedDict

from google.adk.tools import ToolContext

from .. import config, deps, inventory, layout, offers, stores
from . import mcp_client, safety

log = logging.getLogger("store-navi.agent.tools")

MAX_SEARCHES_PER_TURN = 4
IN_STOCK = ("in_stock", "low_stock")
STATUS_EN = {"in_stock": "In stock", "low_stock": "Low stock", "out_of_stock": "Out of stock", "not_carried": "Not carried"}
STATUS_JA = STATUS_EN  # backward-compatible alias


class StoreItem(TypedDict, total=False):
    """One recommended product: product_id (from search results), role (e.g. Pain reliever / Cooling sheet), reason (~8 words)."""
    product_id: str
    role: str
    reason: str


class TurnState:
    """Per-session state for the store concierge (seeded every turn by runner.py)."""

    def __init__(self) -> None:
        self.store_id = config.DEFAULT_STORE
        self.customer_id = config.DEFAULT_CUSTOMER
        self.flags: list[dict] = []
        self.abuse: list[str] = []
        self.age: float | None = None
        self.actions: list[dict] = []
        self.chips: list[str] = []
        self.search_count = 0
        self.last_items: list[dict] = []
        self.page_state: dict = {}
        self.trace: list[dict] = []

    def begin(self, store_id: str, customer_id: str, message: str, page_state: dict | None) -> None:
        self.store_id = store_id or self.store_id
        self.customer_id = customer_id or self.customer_id
        new_flags = safety.detect_red_flags(message)
        self.flags = new_flags
        self.abuse = safety.detect_abuse(message)
        age = safety.child_age(message)
        if age is not None:
            self.age = age
        self.actions = []
        self.chips = []
        self.search_count = 0
        self.page_state = page_state or {}
        self.trace = []

    def note(self, tool: str, detail: str, source: str) -> None:
        """Agent trace ("How I answered"): one row per tool call with the data source it used."""
        self.trace.append({"tool": tool, "label": TRACE_LABELS.get(tool, tool), "detail": detail[:140],
                           "source": source, "source_label": SOURCE_LABELS.get(source, source)})

    def drain(self) -> list[dict]:
        out, self.actions = self.actions, []
        return out


_STATES: dict[str, TurnState] = {}
_lock = threading.Lock()


def turn_state(session_id: str) -> TurnState:
    with _lock:
        st = _STATES.get(session_id)
        if st is None:
            if len(_STATES) > 2000:
                _STATES.clear()
            st = _STATES[session_id] = TurnState()
        return st


def _st(ctx) -> TurnState:
    return turn_state(ctx.session.id)


def mcp_enabled() -> bool:
    if (os.environ.get("MCP_URL") or "").strip().lower() in ("off", "none", "0"):
        return False
    return bool(mcp_client.MCP_URL) and (bool(os.environ.get("MCP_URL")) or config.data_mode() == "bq")


# ---------------------------------------------------------------------------------------------- helpers
def _shelf_text(sh: dict | None) -> str | None:
    """"A01 Cold medicine · A01-203 (shelf 2, 3rd from left)" for the model."""
    if not sh:
        return None
    if sh.get("counter"):
        return "Pharmacy counter (ask at the register)"
    lab = f"{sh.get('fixture_id') or sh.get('gondola_id')} {sh.get('fixture_label') or ''}".strip()
    return f"{lab} · {sh['slot_label']}" if sh.get("slot_label") else (sh.get("label") or lab)


def _compact(p: dict, store_id: str) -> dict:
    inv = inventory.inventory_for(store_id, p["id"])
    out = {"product_id": p["id"], "title": p.get("title"), "price": p.get("price"),
           "category": p.get("category_path") or p.get("category"),
           "stock_here": STATUS_EN[inv["status"] if inv else "not_carried"],
           "qty_here": inv["quantity"] if inv else 0}
    if p.get("drug_class"):
        out.update(drug_class=p["drug_class"], symptoms=p.get("symptoms") or [], age_min=p.get("age_min"),
                   abuse_control=bool(p.get("abuse_control")))
    if p.get("on_sale"):
        out["on_sale"] = True
    ch = p.get("channel") or "omni"
    if ch != "omni":
        out["channel"] = ch
    return out


def _local_search(query: str, page_size: int, store_id: str) -> list[dict]:
    try:
        res = deps.get_engine().search(query, "", {}, [], "", "", "", "", 1, page_size, "store-navi-agent", query)
        ids = [r["id"] for r in res.get("results") or []]
    except Exception as e:  # noqa: BLE001
        log.warning("engine search failed: %s", e)
        ids = [n["id"] for _, n in deps.get_catalog().text_search(query)[:page_size]]
    if not ids or query.strip().lower() in deps.SYNONYMS:
        ids = list(dict.fromkeys(deps.symptom_search(query, page_size) + ids))[:page_size]
    return [p for p in deps.hydrate(ids)]


# ---------------------------------------------------------------------------------------------- tools
async def search_products(query: str, tool_context: ToolContext, page_size: int = 12) -> dict[str, Any]:
    """Searches the Cymbal Retail catalog (groceries, OTC medicine & health care, household, beauty, beverages …).

    Args:
      query: short English keywords (1–3 words), e.g. "pain reliever", "cooling gel sheet kids", "oral rehydration",
        "eye drops tired eyes", "curry roux".
      page_size: number of results (5–20, default 12).

    Returns:
      {products:[{product_id,title,price,category,stock_here (stock at this store),qty_here,channel,drug_class,symptoms,
       age_min,abuse_control}], source}. At most 4 searches per message.
    """
    st = _st(tool_context)
    st.search_count += 1
    if st.search_count > MAX_SEARCHES_PER_TURN:
        return {"error": "SEARCH_LIMIT", "message": "Search limit for this message reached. Answer with what you found."}
    n = max(5, min(int(page_size or 12), 20))
    products: list[dict] = []
    source = "mcp"
    if mcp_enabled():
        res = await mcp_client.call_tool("search_products", {"query": query, "page_size": n})
        if isinstance(res, dict) and isinstance(res.get("productDetails"), list) and res["productDetails"]:
            ids = [str(p.get("productId") or p.get("id")) for p in res["productDetails"]]
            local = {p["id"]: p for p in deps.hydrate(ids)}
            for raw in res["productDetails"]:
                pid = str(raw.get("productId") or raw.get("id"))
                if pid in local:
                    products.append(local[pid])
                else:
                    products.append({"id": pid, "title": raw.get("title"), "price": raw.get("price"),
                                     "category_path": raw.get("topCategory") or ""})
        else:
            log.info("MCP search fallback (%s)", str(res)[:160])
    if not products:
        source = "aics" if deps.get_engine().mode == "retail" else "local"
        import asyncio

        products = await asyncio.to_thread(_local_search, query, n, st.store_id)
    items = [_compact(p, st.store_id) for p in products[:n]]
    # in-stock at this store first (relevance order kept within each group)
    items.sort(key=lambda x: x["stock_here"] not in ("In stock", "Low stock"))
    st.note("search_products", f'"{query}" → {len(items)} products', source)
    return {"products": items, "source": source, "store": (stores.get_store(st.store_id) or {}).get("short_name")}


async def get_product_details(product_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Gets product details (description, drug_class, approved uses `symptoms`, age_min, size, price).

    Args:
      product_id: a product_id from search_products results.
    """
    st = _st(tool_context)
    p = deps.to_product(product_id)
    out: dict[str, Any] = {}
    if mcp_enabled():
        res = await mcp_client.call_tool("get_product_details", {"product_id": product_id})
        if isinstance(res, dict) and not res.get("error"):
            out.update({k: v for k, v in res.items() if k not in ("images", "image", "uri")})
    if p:
        out.update({"product_id": p["id"], "title": p.get("title"), "price": p.get("price"),
                    "description": (p.get("description") or "")[:400], "size": p.get("size"),
                    "drug_class": p.get("drug_class") or None, "symptoms": p.get("symptoms") or [],
                    "age_min": p.get("age_min"), "abuse_control": bool(p.get("abuse_control")),
                    "stock_here": _compact(p, st.store_id)["stock_here"]})
    st.note("get_product_details", str((p or {}).get("title") or product_id), "mcp" if mcp_enabled() else "catalog")
    return out or {"error": "NOT_FOUND"}


async def check_availability(product_ids: list[str], tool_context: ToolContext) -> dict[str, Any]:
    """Checks stock in the order "this store → nearby stores → online" (with shelf location).

    Args:
      product_ids: product_ids to check (max 8).
    """
    st = _st(tool_context)
    out = []
    src = "bigquery" if config.data_mode() == "bq" else "local"
    for pid in [str(x) for x in (product_ids or []) if x][:8]:
        p = deps.to_product(pid)
        if not p:
            continue
        av = inventory.get_availability(pid, st.store_id, p, live=True)
        cur = av["current"] or {}
        src = av.get("source") or src
        out.append({"product_id": pid, "title": p.get("title"), "channel": av.get("channel"),
                    "here": {"store": cur.get("short_name"), "status": STATUS_EN.get(cur.get("status"), ""),
                             "qty": cur.get("quantity"), "shelf": _shelf_text(cur.get("shelf"))},
                    "nearby": [{"store": n["short_name"], "status": STATUS_EN.get(n["status"], ""),
                                "distance_km": round(n["distance_m"] / 1000, 1), "open_now": n["open_now"]}
                               for n in av["nearby"]],
                    "online": {"available": av["online"]["available"], "price": av["online"]["price"]}})
    st.note("check_availability", f"{len(out)} products · this store → nearby → online", src)
    return {"items": out}


def build_store_items(store_id: str, customer_id: str, theme: str, advice: str, items: list[dict],
                      flags: list[dict], age: float | None = None, red_flag: bool = False,
                      abuse: bool = False) -> tuple[dict, dict]:
    """Server-side enrichment for the show_store_items action. Returns (action_args, summary_for_model)."""
    resolved = []
    for it in items or []:
        if not isinstance(it, dict):
            it = it.model_dump() if hasattr(it, "model_dump") else dict(it)
        pid = str((it or {}).get("product_id") or "")
        p = deps.to_product(pid) if pid else None
        if not p:
            continue
        if any(r["product"]["id"] == pid for r in resolved):
            continue
        resolved.append({"product_id": pid, "role": str(it.get("role") or ""), "reason": str(it.get("reason") or ""),
                         "product": p})
    flags = list(flags or [])
    if red_flag and not flags:
        flags = [{"code": "model", "level": "consult", "label": "may need to see a doctor"}]
    kept, notes = safety.filter_items(resolved, flags, age, abuse=abuse)
    route_stops = []
    live = inventory.prefetch_local_inventory([it["product_id"] for it in kept])
    for it in kept:
        av = inventory.get_availability(it["product_id"], store_id, it["product"], live)
        it["availability"] = av
        it["notes"] = safety.compliance_notes(it["product"])
        it["limits"] = safety.item_limits(it["product"])
        cur = av.get("current") or {}
        it["in_store"] = cur.get("status") in IN_STOCK and bool(cur.get("shelf"))
        if it["in_store"]:
            sh = cur["shelf"]
            route_stops.append({"product_id": it["product_id"], "fixture_id": sh.get("fixture_id") or sh["gondola_id"],
                                "slot_ids": sh.get("slot_ids") or [], "primary_slot": sh.get("primary_slot"),
                                "side": sh.get("side"), "tier": sh.get("tier"), "position": sh.get("position")})
    route = layout.plan_route(store_id, route_stops) if route_stops else None
    order = {s["product_id"]: s["no"] for s in (route or {}).get("stops") or []}
    for it in kept:
        it["no"] = order.get(it["product_id"])
    kept.sort(key=lambda it: (it["no"] is None, it["no"] or 0))
    store = stores.get_store(store_id) or {}
    persona = offers.get_persona(customer_id)
    args = {"theme": theme or "Recommended products", "advice": advice or "", "red_flag": bool(red_flag or safety.is_blocking(flags)),
            "items": [{k: it[k] for k in ("no", "product_id", "role", "reason", "product", "availability", "notes",
                                           "limits", "in_store")} for it in kept],
            "route": route, "disclaimer": safety.DISCLAIMER, "store_id": store_id,
            "store_name": store.get("short_name") or store.get("name"),
            "customer_name": persona.get("name"), "removed_by_safety": len(resolved) - len(kept)}
    summary = {"shown": len(kept), "removed_by_safety": len(resolved) - len(kept),
               "items": [{"no": it["no"], "title": it["product"].get("title"),
                          "here": STATUS_EN.get((it["availability"].get("current") or {}).get("status"), ""),
                          "shelf": _shelf_text((it["availability"].get("current") or {}).get("shelf")),
                          "nearby_in_stock": [n["short_name"] for n in it["availability"]["nearby"] if n["status"] in IN_STOCK],
                          "online": it["availability"]["online"]["available"]} for it in kept],
               "route_length_m": (route or {}).get("length_m"),
               "safety_notes": notes[:5]}
    return args, summary


async def show_store_items(theme: str, advice: str, items: list[StoreItem], tool_context: ToolContext,
                           red_flag: bool = False) -> dict[str, Any]:
    """Shows the recommended set as cards with in-store shelf locations, the walking order (#1 #2 #3) and the route.

    Args:
      theme: heading, e.g. "Self-care for early cold symptoms".
      advice: general self-care advice shown above the cards (2–3 lines, no diagnosis).
      items: [{product_id, role, reason}] (2–5). role e.g. "Pain reliever", "Cooling sheet", "Hydration"; reason ~8 words.
        At most one medicine per symptom group; the rest non-medicine support items.
      red_flag: true when the shopper should see a doctor (medicines are then hidden).

    Returns:
      {shown, items:[{no (walking order; null when not in stock here), title, here, shelf, nearby_in_stock, online}],
       route_length_m, removed_by_safety}
    """
    st = _st(tool_context)
    args, summary = build_store_items(st.store_id, st.customer_id, theme, advice, items, st.flags, st.age, red_flag,
                                      abuse=bool(st.abuse))
    st.actions.append({"type": "action", "name": "show_store_items", "args": args, "result": {"ok": True,
                                                                                            "shown": summary["shown"]}})
    st.last_items = args["items"]
    st.note("show_store_items", f'{summary["shown"]} items · route {summary.get("route_length_m") or 0} m',
            "store_layout")
    if summary.get("removed_by_safety"):
        st.note("safety_filter", f'{summary["removed_by_safety"]} item(s) removed by OTC rules', "guardrails")
    return summary


async def suggest_replies(items: list[str], tool_context: ToolContext) -> dict[str, Any]:
    """Shows 2–4 short reply suggestions (max ~4 words each) the shopper can tap next.

    Args:
      items: reply texts, e.g. "Find all on the map", "Anything for kids?", "Stock at nearby stores?".
    """
    st = _st(tool_context)
    st.chips = [str(x)[:24] for x in (items or []) if str(x).strip()][:4]
    return {"ok": True}


async def suggest_recipes(request: str, tool_context: ToolContext) -> dict[str, Any]:
    """Dish-first meal ideas: shows 3 dish cards on screen (the shopper can then customize servings / kid-friendly /
    quick and add all ingredients to the cart). Use for "what should I make for dinner?" style requests.

    Args:
      request: the shopper's request in a few words, e.g. "quick dinner kids like", "curry", "hot pot for 4".

    Returns:
      {dishes:[{id,title,minutes,servings,kid_friendly,tags,reason}]} (already shown on screen as cards).
    """
    st = _st(tool_context)
    try:
        from .. import features

        dishes = features.suggest(request or "", 3, st.customer_id)
    except Exception as e:  # noqa: BLE001
        log.warning("suggest_recipes failed: %s", e)
        dishes = []
    st.actions.append({"type": "action", "name": "show_recipes", "args": {"query": request or "", "store_id": st.store_id},
                       "result": {"ok": True, "shown": len(dishes)}})
    st.note("suggest_recipes", f'"{request}" → {len(dishes)} dishes', "recipes")
    return {"dishes": [{k: d.get(k) for k in ("id", "title", "minutes", "servings", "kid_friendly", "tags", "reason")}
                       for d in dishes]}


ALL_TOOLS = [search_products, get_product_details, check_availability, show_store_items, suggest_recipes, suggest_replies]
STATUS_LABELS = {
    "search_products": "Searching products…",
    "get_product_details": "Checking product details…",
    "check_availability": "Checking stock…",
    "show_store_items": "Preparing shelves and route…",
    "suggest_recipes": "Finding dishes…",
}
TRACE_LABELS = {
    "search_products": "Product search",
    "get_product_details": "Product details",
    "check_availability": "Stock check",
    "show_store_items": "Shelf locations & route",
    "suggest_recipes": "Recipe ideas",
    "safety_filter": "OTC safety rules",
    "red_flag_check": "Red-flag check",
    "persona": "Shopper profile",
}
SOURCE_LABELS = {
    "mcp": "Commerce MCP server → AI Commerce Search",
    "aics": "AI Commerce Search",
    "local": "Local catalog (offline demo)",
    "catalog": "Product catalog",
    "retail_local_inventory": "AI Commerce Search Local Inventory",
    "bigquery": "BigQuery store inventory",
    "store_layout": "Store layout & shelf map (A* route)",
    "guardrails": "Server-side guardrails",
    "profile": "BigQuery customer profile & purchase history",
    "recipes": "Recipe data (60 dishes) + product catalog",
}
