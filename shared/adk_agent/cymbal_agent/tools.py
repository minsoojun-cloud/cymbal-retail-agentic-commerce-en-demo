"""ADK function tools: MCP-backed commerce tools + page-action tools (CONTRACT client functions).

Page-action tools compute results server-side on the per-session ShopState (seeded each turn from the
request `page_state`) and record an action {name,args,result}; server.py streams these as `action` SSE
events in order so the page applies the identical change.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from google.adk.tools import ToolContext

from . import mcp_client
from .shop import Catalog, ShopState, yen

logger = logging.getLogger("cymbal_agent.tools")

CATALOG = Catalog.load()
STATES: dict[str, ShopState] = {}
MAX_SEARCHES_PER_TURN = 3
DEFAULT_EXCLUDE = ["shrimp", "prawn"]


def shop_state(session_id: str) -> ShopState:
  st = STATES.get(session_id)
  if st is None:
    st = STATES[session_id] = ShopState()
  return st


def _st(ctx: ToolContext) -> ShopState:
  return shop_state(ctx.session.id)


def _customer_id(ctx: ToolContext) -> str:
  return str(ctx.state.get("customer_id") or "")


def _exclude_keywords(ctx: ToolContext) -> list[str]:
  kws = ctx.state.get("exclude_keywords")
  return list(kws) if isinstance(kws, list) else []


def _has_excluded(p: dict[str, Any], kws: list[str]) -> bool:
  if not kws:
    return False
  hay = " ".join(str(p.get(k) or "") for k in ("title", "allergens", "description"))
  if isinstance(p.get("allergens"), list):
    hay += " " + " ".join(map(str, p["allergens"]))
  hay = hay.lower()
  return any(str(k).lower() in hay for k in kws if str(k).strip())


def _product_channel(p: dict[str, Any]) -> str:
  ch = p.get("channel")
  if not ch and isinstance(p.get("attributes"), dict):
    ch = p["attributes"].get("channel")
  if isinstance(ch, list):
    ch = ch[0] if ch else ""
  return str(ch or "")


# ── MCP-backed tools ─────────────────────────────────────────────────────────────────────────


async def search_products(query: str, tool_context: ToolContext, page_size: int = 20,
                          filter: str = "") -> dict[str, Any]:
  """Searches the Cymbal Retail catalog (in-stock items only; price is tax-included JPY).

  Args:
    query: Short catalog-friendly keywords (1-3 words), e.g. "frying pan", "medium-hot curry roux",
      "shampoo refill". Including "under 3000 yen" turns into a price filter.
    page_size: Number of results (10-30, default 20).
    filter: Optional Retail filter expression. Items on sale: attributes.on_sale: ANY("true").
      Items that can be picked up in a store: pickupInStore: ANY("S01") (S01 Kawasaki Station,
      S02 Musashi-Kosugi, S03 Shibuya).

  Returns:
    {productDetails:[{productId,title,price,originalPrice,onSale,size,brand,topCategory,labels,rating,channel,...}],
     totalSize, excluded_by_allergy}. At most 3 searches per user message.
  """
  st = _st(tool_context)
  st.search_count += 1
  if st.search_count > MAX_SEARCHES_PER_TURN:
    return {"error": "SEARCH_LIMIT",
            "message": "Search limit (3 per message) reached. Tell the customer nothing suitable was found."}
  args = {"query": query, "page_size": max(5, min(int(page_size or 20), 30))}
  if filter:
    args["filter"] = filter
  vid = tool_context.state.get("visitor_id")
  if vid:
    args["visitor_id"] = vid
  res = await mcp_client.call_tool("search_products", args)
  if isinstance(res, dict) and isinstance(res.get("productDetails"), list):
    kws = _exclude_keywords(tool_context)
    kept, dropped = [], 0
    for p in res["productDetails"]:
      if _has_excluded(p, kws):
        dropped += 1
        continue
      p.pop("image", None)
      p.pop("uri", None)
      st.remember_channel(p.get("productId"), _product_channel(p))
      kept.append(p)
    res["productDetails"] = kept
    res["excluded_by_allergy"] = dropped
  st.last_search = res
  return res


async def get_product_details(product_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Gets product details (description, origin/material, allergens, storage, size, price, rating, channel).

  Args:
    product_id: A productId that appeared in earlier tool results (e.g. search results).
  """
  res = await mcp_client.call_tool("get_product_details", {"product_id": product_id})
  if isinstance(res, dict):
    res.pop("images", None)
    _st(tool_context).remember_channel(res.get("productId") or product_id, _product_channel(res))
  return res


async def get_purchase_history(tool_context: ToolContext, month: str = "", limit: int = 30) -> dict[str, Any]:
  """Gets the customer's purchase history (including frequently bought topProducts). Use it for re-order suggestions.

  Args:
    month: Optional. "10" or "2026-10". Empty = most recent.
    limit: Maximum number of entries (default 30).
  """
  return await mcp_client.call_tool("get_purchase_history", {
      "customer_id": _customer_id(tool_context), "month": month or "", "limit": int(limit or 30)})


async def compare_products(product_ids: list[str], tool_context: ToolContext, title: str = "") -> dict[str, Any]:
  """Builds a comparison table for 2-4 products and shows it on the page right away (as show_comparison).

  Args:
    product_ids: productIds to compare (2-4, in display order). "Top 3" = the first 3 items last shown on the page.
    title: Optional table title (e.g. "Frying pan comparison").

  Returns:
    {displayed: true, title, products:[{productId,title,price}], rows:[{label,values}]}.
    The table is already on the page, so do not call show_comparison.
  """
  ids = [str(x) for x in (product_ids or []) if x][:4]
  res = await mcp_client.call_tool("compare_products", {"product_ids": ids})
  if not isinstance(res, dict) or res.get("error") or not res.get("rows"):
    return res if isinstance(res, dict) else {"error": "compare failed"}
  args = {"title": title or res.get("title") or "Product comparison",
          "product_ids": res.get("product_ids") or ids, "rows": res.get("rows")}
  _st(tool_context).show_comparison(args)
  return {"displayed": True, **{k: res.get(k) for k in ("title", "products", "rows")}, "title": args["title"]}


def _set_show_args(dinner: dict[str, Any], idx: int) -> Optional[dict[str, Any]]:
  sets = dinner.get("sets") or []
  if not (0 <= idx < len(sets)):
    return None
  s = sets[idx]
  args = s.get("show_products_on_page_args") or (dinner.get("show_products_on_page_args") if idx == 0 else None)
  if not args:  # build from items (section labels = MCP roles: Main / Side / Soup / Staple / Dessert & fruit)
    items = s.get("items") or []
    sections: list[dict[str, Any]] = []
    for it in items:
      sec = next((x for x in sections if x["label"] == it.get("role")), None)
      if sec is None:
        sec = {"label": it.get("role") or "", "product_ids": [], "reasons": [], "tags": [], "quantities": []}
        sections.append(sec)
      sec["product_ids"].append(it["product_id"])
      sec["reasons"].append(it.get("reason") or "")
      sec["tags"].append(it.get("tags") or [])
      sec["quantities"].append(int(it.get("quantity") or 1))
    args = {"message": f"{s.get('name')} ({yen(s.get('total_tax_included'))}, tax incl.)",
            "theme": dinner.get("theme") or s.get("name"),
            "product_ids": [it["product_id"] for it in items],
            "reasons": [it.get("reason") or "" for it in items],
            "quantities": [int(it.get("quantity") or 1) for it in items],
            "sections": sections}
  return json.loads(json.dumps(args, ensure_ascii=False))


def _compact_dinner(dinner: dict[str, Any], shown_idx: int) -> dict[str, Any]:
  return {
      "theme": dinner.get("theme"),
      "servings": dinner.get("servings"),
      "excluded": (dinner.get("signals") or {}).get("excluded"),
      "displayed_set_index": shown_idx,
      "sets": [{
          "index": i, "name": s.get("name"), "concept": s.get("concept"),
          "total_tax_included": s.get("total_tax_included"),
          "items": [{k: it.get(k) for k in ("role", "title", "price", "quantity", "tags", "reason")}
                    for it in s.get("items") or []],
      } for i, s in enumerate(dinner.get("sets") or [])],
  }


async def recommend_dinner_set(tool_context: ToolContext, request: str = "", budget_jpy: int = 0,
                               servings: int = 0) -> dict[str, Any]:
  """Builds a "dinner product set" (1 main proposal + up to 2 alternatives) and shows the main one on the page.

  Family preferences, allergy exclusions, frequently bought items, sale items and seasonal items are applied
  automatically. The main proposal (sets[0]) is already displayed on the page as show_products_on_page
  (with theme / sections / quantities). Section roles: Main / Side / Soup / Staple / Dessert & fruit; item tags:
  "Frequently bought" / "Family favorite" / "On sale" / "In season" / "Quick".

  Args:
    request: The customer's exact words (e.g. "Recommend dinner for tonight", "Something with meat", "Hot pot").
    budget_jpy: Budget in tax-included JPY (0 = none).
    servings: Number of people (0 = the household size).
  """
  st = _st(tool_context)
  res = await mcp_client.call_tool("recommend_dinner_set", {
      "customer_id": _customer_id(tool_context), "request": request or "",
      "budget_jpy": int(budget_jpy or 0), "servings": int(servings or 0)})
  if not isinstance(res, dict) or res.get("error") or not res.get("sets"):
    return res if isinstance(res, dict) else {"error": "dinner set failed", "sets": []}
  st.last_dinner = res
  st.current_set_index = 0
  args = _set_show_args(res, 0)
  if args:
    st.show_products(args)
  return _compact_dinner(res, 0)


async def show_dinner_set(set_index: int, tool_context: ToolContext) -> dict[str, Any]:
  """Shows an alternative (sets[set_index]) from the last recommend_dinner_set on the page ("Another idea").

  Args:
    set_index: Index of the set to show (1 or 2).
  """
  st = _st(tool_context)
  if not st.last_dinner:
    return {"error": "NO_DINNER_SET", "message": "Call recommend_dinner_set first."}
  args = _set_show_args(st.last_dinner, int(set_index))
  if not args:
    return {"error": "NO_SUCH_SET", "available": len(st.last_dinner.get("sets") or [])}
  st.current_set_index = int(set_index)
  st.show_products(args)
  s = st.last_dinner["sets"][int(set_index)]
  return {"shown": len(args["product_ids"]), "set": _compact_dinner(st.last_dinner, int(set_index))["sets"][int(set_index)],
          "name": s.get("name")}


async def add_dinner_set_to_cart(tool_context: ToolContext, set_index: int = -1) -> dict[str, Any]:
  """Adds every product of the displayed dinner set to the cart with the set quantities (one add_to_cart each).

  Args:
    set_index: Index of the set to add. -1 (default) = the set currently shown on the page.
  """
  st = _st(tool_context)
  if not st.last_dinner:
    return {"ok": False, "error": "NO_DINNER_SET"}
  idx = st.current_set_index if int(set_index) < 0 else int(set_index)
  sets = st.last_dinner.get("sets") or []
  if idx is None or not (0 <= idx < len(sets)):
    return {"ok": False, "error": "NO_SUCH_SET"}
  added = []
  last: dict[str, Any] = {}
  for it in sets[idx].get("items") or []:
    last = st.add_to_cart(CATALOG, it["product_id"], int(it.get("quantity") or 1),
                          title_hint=it.get("title") or "", price_hint=int(it.get("price") or 0))
    entry = {"title": last.get("title"), "quantity": int(it.get("quantity") or 1), "ok": last.get("ok")}
    if not last.get("ok"):
      entry["error"] = last.get("error")
    added.append(entry)
  return {"ok": all(a["ok"] for a in added), "set_name": sets[idx].get("name"), "added": added,
          "items_added": sum(1 for a in added if a["ok"]), "cart_count": st.count(),
          "cart_total_tax_included": st.subtotal()}


def _fallback_store_stock(ids: list[str], st: ShopState, error: str) -> dict[str, Any]:
  return {
      "error": error,
      "message": "Live store stock is temporarily unavailable. Suggest checking the Store App or asking store staff.",
      "items": [{"product_id": pid, "title": (CATALOG.get(pid) or {}).get("title") or "",
                 "channel": st.channel_of(CATALOG, pid) or "unknown", "stores": [],
                 "online_available": st.channel_of(CATALOG, pid) != "store_only"} for pid in ids],
  }


async def find_store_stock(product_ids: list[str], tool_context: ToolContext, store_id: str = "") -> dict[str, Any]:
  """Checks in-store stock and pickup availability at Cymbal Retail stores for 1-10 products.

  Use when the customer asks "can I buy it in store?", "can I pick it up?", "which store has it?", or when a
  product's channel is store_only. Then call show_store_availability with the same product_ids.

  Args:
    product_ids: productIds from earlier tool results (1-10).
    store_id: Optional store filter: S01 (Kawasaki Station), S02 (Musashi-Kosugi), S03 (Shibuya). Empty = all stores.

  Returns:
    {items:[{product_id, title, channel, stores:[{store_id, name, address, open_now, hours, quantity, availability,
     pickup, shelf_label?}], online_available}]}. channel: omni | store_only (cannot be delivered) | online_only
     (not sold in stores).
  """
  st = _st(tool_context)
  ids = [str(x) for x in (product_ids or []) if x][:10]
  if not ids:
    return {"error": "NO_PRODUCTS", "items": []}
  args: dict[str, Any] = {"product_ids": ids}
  if store_id:
    args["store_id"] = str(store_id).strip().upper()
  res = await mcp_client.call_tool("check_store_inventory", args)
  if not isinstance(res, dict) or res.get("error") or not isinstance(res.get("items"), list):
    err = res.get("error") if isinstance(res, dict) else "bad MCP response"
    logger.warning("check_store_inventory failed: %s", str(err)[:200])
    return _fallback_store_stock(ids, st, str(err or "STORE_STOCK_UNAVAILABLE"))
  for it in res["items"]:
    if isinstance(it, dict):
      st.remember_channel(it.get("product_id"), it.get("channel"))
  return res


# ── Page-action tools (names = CONTRACT client functions) ─────────────────────────────────────


async def show_products_on_page(message: str, product_ids: list[str], reasons: list[str],
                                tool_context: ToolContext, theme: str = "") -> dict[str, Any]:
  """Shows recommended products on the main page with reason badges (for regular search recommendations).

  Args:
    message: Heading above the grid (up to ~60 characters), e.g. "Frying pans picked for Sakura".
    product_ids: productIds from the search results in recommendation order (usually 4-8, max 24).
    reasons: One short reason per product (~30 characters each), same order and count as product_ids.
    theme: Optional theme heading.
  """
  st = _st(tool_context)
  ids = [str(x) for x in (product_ids or []) if x][:24]
  rs = [str(r) for r in (reasons or [])][:len(ids)]
  rs += [""] * (len(ids) - len(rs))
  args: dict[str, Any] = {"message": message, "product_ids": ids, "reasons": rs}
  if theme:
    args["theme"] = theme
  if ids and ids == st.last_shown_ids and any(a["name"] == "show_products_on_page" for a in st.actions):
    return {"shown": len(ids)}
  return st.show_products(args)


async def show_comparison(title: str, product_ids: list[str], rows: list[dict[str, Any]],
                          tool_context: ToolContext) -> dict[str, Any]:
  """Shows a custom comparison table on the page. Not needed after compare_products (already displayed).

  Args:
    title: Table title.
    product_ids: 2-4 productIds.
    rows: [{label: str, values: [str]}] (values in the same order and count as product_ids).
  """
  return _st(tool_context).show_comparison({"title": title, "product_ids": product_ids, "rows": rows})


async def show_store_availability(product_ids: list[str], message: str, tool_context: ToolContext) -> dict[str, Any]:
  """Opens the "Available in stores" view on the page (store list with stock, hours and pickup) for products.

  Call after find_store_stock with the same product_ids.

  Args:
    product_ids: productIds to show store availability for (1-10).
    message: Short heading, e.g. "Where to buy the sushi platter today".
  """
  ids = [str(x) for x in (product_ids or []) if x][:10]
  return _st(tool_context).show_store_availability({"product_ids": ids, "message": message or ""})


async def add_to_cart(product_id: str, tool_context: ToolContext, quantity: int = 1) -> dict[str, Any]:
  """Adds a product to the delivery cart (reflected in the page cart). Call once per product.

  store_only products are refused with ok=false, error=STORE_ONLY (offer find_store_stock instead).

  Args:
    product_id: A productId that appeared in this conversation's tool results.
    quantity: Quantity (default 1).
  """
  return _st(tool_context).add_to_cart(CATALOG, product_id, quantity)


async def get_cart(tool_context: ToolContext) -> dict[str, Any]:
  """Gets the current cart (lines, item count, tax-included total) and shows a cart card in the chat."""
  return _st(tool_context).get_cart()


async def start_checkout(tool_context: ToolContext) -> dict[str, Any]:
  """Starts checkout and returns the available delivery slots. Empty cart → ok=false, error=EMPTY_CART."""
  return _st(tool_context).start_checkout()


async def select_delivery_slot(slot_id: str, tool_context: ToolContext) -> dict[str, Any]:
  """Selects a delivery slot and returns the payment method and total including shipping (next=confirm).

  Args:
    slot_id: One of d1_am (Tomorrow 9:00-12:00) / d1_pm (Tomorrow 14:00-17:00) / d1_eve (Tomorrow 18:00-21:00) /
      d2_am (Day after tomorrow 9:00-12:00) / d2_pm (Day after tomorrow 14:00-17:00).
  """
  return _st(tool_context).select_delivery_slot(slot_id)


async def place_order(tool_context: ToolContext) -> dict[str, Any]:
  """Places the order. Call only after presenting slot, payment method and total, and the customer clearly agreed."""
  return _st(tool_context).place_order()


async def get_order_status(tool_context: ToolContext, order_id: str = "") -> dict[str, Any]:
  """Gets the delivery status (status / status_label / eta) of an order and shows the order timeline on the page.

  Args:
    order_id: Optional order number (default = latest order).
  """
  return _st(tool_context).get_order_status(order_id or "")


ALL_TOOLS = [
    search_products, get_product_details, get_purchase_history, compare_products,
    recommend_dinner_set, show_dinner_set, add_dinner_set_to_cart, find_store_stock,
    show_products_on_page, show_comparison, show_store_availability, add_to_cart, get_cart, start_checkout,
    select_delivery_slot, place_order, get_order_status,
]

STATUS_LABELS = {
    "search_products": "Searching products…",
    "get_product_details": "Checking product details…",
    "get_purchase_history": "Checking your usual items…",
    "compare_products": "Building a comparison…",
    "recommend_dinner_set": "Planning a dinner set for your family…",
    "show_dinner_set": "Showing another idea…",
    "add_dinner_set_to_cart": "Adding the set to your cart…",
    "find_store_stock": "Checking store stock…",
    "show_products_on_page": "Showing recommendations…",
    "show_comparison": "Showing the comparison…",
    "show_store_availability": "Showing store availability…",
    "add_to_cart": "Adding to your cart…",
    "get_cart": "Checking your cart…",
    "start_checkout": "Preparing checkout…",
    "select_delivery_slot": "Setting your delivery time…",
    "place_order": "Placing your order…",
    "get_order_status": "Checking delivery status…",
}
