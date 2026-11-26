"""Pure, deterministic shop logic (cart / checkout / orders) used by the page-action tools.

Mirrors online-store/static/js/{cart,checkout}.js so the agent can compute the same results the page will
produce when it applies the streamed `action` events (CONTRACT_JA_legacy.md v3 SSE protocol, CONTRACT_EN.md v14):
  - cart_count = sum(qty), cart_total_tax_included = sum(qty * price) (prices are tax-included JPY)
  - shipping ¥0 when subtotal >= ¥3,000 (or cart empty) else ¥330
  - slots d1_am / d1_pm / d1_eve / d2_am / d2_pm relative to Asia/Tokyo today
  - payment `Cymbal Pay (VISA **** 4242)`, order id `CR-YYYYMMDD-NNNN`
  - `store_only` products (catalog attribute `channel`) cannot be added to the delivery cart
No ADK imports here so it is unit-testable in isolation.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import threading
from dataclasses import dataclass, field
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
PAYMENT_METHOD = "Cymbal Pay (VISA **** 4242)"
FREE_SHIPPING_THRESHOLD = 3000
SHIPPING_FEE = 330
SLOT_DEFS = (
    ("d1_am", 1, "Tomorrow", "9:00-12:00"),
    ("d1_pm", 1, "Tomorrow", "14:00-17:00"),
    ("d1_eve", 1, "Tomorrow", "18:00-21:00"),
    ("d2_am", 2, "Day after tomorrow", "9:00-12:00"),
    ("d2_pm", 2, "Day after tomorrow", "14:00-17:00"),
)
STATUS_LABELS = {
    "placed": "Order placed",
    "preparing": "Preparing",
    "shipping": "Out for delivery",
    "delivered": "Delivered",
}
_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CHANNELS = ("omni", "store_only", "online_only")


def yen(amount: Any) -> str:
  """JPY formatted like en-US Intl currency: ¥1,280."""
  return f"¥{_int(amount, 0):,}"


def today_jst(now: Optional[dt.datetime] = None) -> dt.date:
  return (now or dt.datetime.now(JST)).astimezone(JST).date()


def date_label(d: dt.date) -> str:
  """en-US short date, e.g. `Thu, Nov 26`."""
  return f"{_WD[d.weekday()]}, {_MON[d.month - 1]} {d.day}"


def slots(now: Optional[dt.datetime] = None) -> list[dict[str, str]]:
  """Delivery slots exactly as the page generates them ({slot_id, label})."""
  return [{"slot_id": sid, "label": f"{rel} {time}"} for sid, _, rel, time in SLOT_DEFS]


def slot_label(slot_id: str) -> Optional[str]:
  for sid, _, rel, time in SLOT_DEFS:
    if sid == slot_id:
      return f"{rel} {time}"
  return None


def slot_date(slot_id: str, now: Optional[dt.datetime] = None) -> Optional[dt.date]:
  for sid, days, _, _ in SLOT_DEFS:
    if sid == slot_id:
      return today_jst(now) + dt.timedelta(days=days)
  return None


def shipping_for(subtotal: int) -> int:
  return 0 if subtotal >= FREE_SHIPPING_THRESHOLD or subtotal == 0 else SHIPPING_FEE


def new_order_id(now: Optional[dt.datetime] = None, existing: Optional[set[str]] = None,
                 rng: Optional[random.Random] = None) -> str:
  rng = rng or random.SystemRandom()
  d = today_jst(now).strftime("%Y%m%d")
  existing = existing or set()
  for _ in range(100):
    oid = f"CR-{d}-{rng.randint(0, 9999):04d}"
    if oid not in existing:
      return oid
  raise RuntimeError("could not allocate order id")


# ── Catalog (tax-included prices baked into the image) ──────────────────────────────────────────


class Catalog:
  """id -> {title, price, availability, channel} from data/products_retail.jsonl (Retail API schema).

  `channel` comes from a top-level `channel` key (compact catalog) or the `channel` text attribute.
  """

  def __init__(self, products: Optional[dict[str, dict[str, Any]]] = None):
    self.products: dict[str, dict[str, Any]] = products or {}

  @classmethod
  def load(cls, path: Optional[str] = None) -> "Catalog":
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [path, os.environ.get("CATALOG_FILE"),
                  os.path.join(here, "..", "data", "products_retail.jsonl"),
                  os.path.join(here, "..", "..", "data", "products_retail.jsonl")]
    for p in candidates:
      if p and os.path.exists(p):
        products: dict[str, dict[str, Any]] = {}
        with open(p, encoding="utf-8") as f:
          for line in f:
            line = line.strip()
            if not line:
              continue
            try:
              d = json.loads(line)
            except ValueError:
              continue
            pi = d.get("priceInfo") or {}
            products[str(d.get("id"))] = {
                "title": d.get("title", ""),
                "price": int(round(float(pi.get("price") or 0))),
                "availability": d.get("availability", "IN_STOCK"),
                "channel": _channel_of(d),
            }
        return cls(products)
    return cls({})

  def get(self, product_id: str) -> Optional[dict[str, Any]]:
    return self.products.get(str(product_id))

  def channel(self, product_id: str) -> str:
    return str((self.get(product_id) or {}).get("channel") or "")

  def __len__(self) -> int:
    return len(self.products)


# ── Per-session shop state ─────────────────────────────────────────────────────────────────────


@dataclass
class ShopState:
  cart: list[dict[str, Any]] = field(default_factory=list)  # [{product_id,title,price,qty}]
  checkout: dict[str, Any] = field(default_factory=lambda: {"state": "idle", "slot_id": None})
  orders: list[dict[str, Any]] = field(default_factory=list)  # newest first
  page_context: dict[str, Any] = field(default_factory=dict)
  last_dinner: Optional[dict[str, Any]] = None  # full recommend_dinner_set result
  current_set_index: Optional[int] = None  # set shown on page (index in last_dinner.sets)
  last_shown_ids: list[str] = field(default_factory=list)
  placed_this_turn: bool = False
  search_count: int = 0
  last_search: Optional[dict[str, Any]] = None
  channels: dict[str, str] = field(default_factory=dict)  # product_id -> channel seen in MCP results
  actions: list[dict[str, Any]] = field(default_factory=list)  # pending actions (drained by server)
  lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

  # -- seeding from request page_state ---------------------------------------------------------
  def seed(self, page_state: Optional[dict[str, Any]]) -> None:
    """Overwrite cart/checkout/orders with the page's truth (when provided)."""
    self.placed_this_turn = False
    self.search_count = 0
    if not isinstance(page_state, dict):
      return
    with self.lock:
      if isinstance(page_state.get("cart"), list):
        cart = []
        for l in page_state["cart"]:
          if not isinstance(l, dict) or not l.get("product_id"):
            continue
          qty = _int(l.get("qty", l.get("quantity", 1)), 1)
          if qty <= 0:
            continue
          cart.append({"product_id": str(l["product_id"]), "title": str(l.get("title") or ""),
                       "price": _int(l.get("price"), 0), "qty": qty})
        self.cart = cart
      if isinstance(page_state.get("checkout"), dict):
        co = page_state["checkout"]
        self.checkout = {"state": str(co.get("state") or "idle"), "slot_id": co.get("slot_id") or None}
      if isinstance(page_state.get("orders"), list):
        self.orders = [o for o in page_state["orders"] if isinstance(o, dict) and o.get("order_id")]
      if isinstance(page_state.get("page_context"), dict):
        self.page_context = page_state["page_context"]

  # -- derived ---------------------------------------------------------------------------------
  def count(self) -> int:
    return sum(l["qty"] for l in self.cart)

  def subtotal(self) -> int:
    return sum(l["qty"] * l["price"] for l in self.cart)

  def summary_text(self) -> str:
    if not self.cart:
      cart = "The cart is empty."
    else:
      items = ", ".join(f"{l['title']} x{l['qty']}" for l in self.cart[:8])
      more = f" and {len(self.cart) - 8} more" if len(self.cart) > 8 else ""
      cart = f"Cart: {self.count()} items, {yen(self.subtotal())} (tax incl.): {items}{more}"
    co = self.checkout.get("state", "idle")
    slot = slot_label(self.checkout.get("slot_id") or "") or "not selected"
    s = f"{cart} / checkout state: {co} (delivery slot: {slot})"
    if self.orders:
      o = self.orders[0]
      s += f" / latest order: {o.get('order_id')} ({STATUS_LABELS.get(o.get('status', 'placed'), o.get('status'))})"
    return s

  def _record(self, name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    self.actions.append({"type": "action", "name": name, "args": args, "result": result})
    return result

  def drain_actions(self) -> list[dict[str, Any]]:
    with self.lock:
      out, self.actions = self.actions, []
      return out

  def remember_channel(self, product_id: Any, channel: Any) -> None:
    if product_id and channel in CHANNELS:
      self.channels[str(product_id)] = str(channel)

  def channel_of(self, catalog: Catalog, product_id: str) -> str:
    return catalog.channel(product_id) or self.channels.get(str(product_id), "")

  # -- client functions (CONTRACT) -------------------------------------------------------------
  def add_to_cart(self, catalog: Catalog, product_id: str, quantity: int = 1,
                  title_hint: str = "", price_hint: int = 0) -> dict[str, Any]:
    product_id = str(product_id or "").strip()
    quantity = max(1, _int(quantity, 1))
    with self.lock:
      p = catalog.get(product_id)
      title = (p or {}).get("title") or title_hint
      price = (p or {}).get("price") or _int(price_hint, 0)
      if not product_id or (p is None and not price):
        return {"ok": False, "error": "UNKNOWN_PRODUCT", "cart_count": self.count(),
                "cart_total_tax_included": self.subtotal(), "title": title}
      if self.channel_of(catalog, product_id) == "store_only":
        return {"ok": False, "error": "STORE_ONLY", "product_id": product_id, "cart_count": self.count(),
                "cart_total_tax_included": self.subtotal(), "title": title,
                "message": f"{title or 'This item'} is sold in stores only and cannot be added to the delivery cart. "
                           "Check which Cymbal Retail store has it in stock and pick it up there."}
      if p is not None and p.get("availability") not in (None, "IN_STOCK"):
        return {"ok": False, "error": "OUT_OF_STOCK", "cart_count": self.count(),
                "cart_total_tax_included": self.subtotal(), "title": title}
      line = next((l for l in self.cart if l["product_id"] == product_id), None)
      if line:
        line["qty"] += quantity
      else:
        self.cart.append({"product_id": product_id, "title": title, "price": price, "qty": quantity})
      if self.checkout.get("state") == "placed":
        self.checkout = {"state": "idle", "slot_id": None}
      result = {"ok": True, "cart_count": self.count(), "cart_total_tax_included": self.subtotal(),
                "title": title}
      return self._record("add_to_cart", {"product_id": product_id, "quantity": quantity}, result)

  def get_cart(self) -> dict[str, Any]:
    with self.lock:
      result = {"lines": [dict(l) for l in self.cart], "cart_count": self.count(),
                "cart_total_tax_included": self.subtotal()}
      return self._record("get_cart", {}, result)

  def start_checkout(self) -> dict[str, Any]:
    with self.lock:
      if not self.cart:
        return self._record("start_checkout", {}, {"ok": False, "error": "EMPTY_CART"})
      if self.checkout.get("state") in ("placed", "idle"):
        self.checkout = {"state": "delivery", "slot_id": None}
      else:
        self.checkout = {"state": "delivery", "slot_id": self.checkout.get("slot_id")}
      result = {"ok": True, "slots": slots(), "cart_total_tax_included": self.subtotal()}
      return self._record("start_checkout", {}, result)

  def select_delivery_slot(self, slot_id: str) -> dict[str, Any]:
    with self.lock:
      slot_id = str(slot_id or "").strip()
      label = slot_label(slot_id)
      if not label:
        return {"ok": False, "error": "INVALID_SLOT", "slots": slots()}
      if not self.cart:
        return {"ok": False, "error": "EMPTY_CART"}
      sub = self.subtotal()
      ship = shipping_for(sub)
      self.checkout = {"state": "confirm", "slot_id": slot_id}
      result = {"ok": True, "slot_label": label, "payment_method": PAYMENT_METHOD,
                "total_tax_included": sub + ship, "subtotal_tax_included": sub, "shipping_fee": ship,
                "next": "confirm"}
      return self._record("select_delivery_slot", {"slot_id": slot_id}, result)

  def place_order(self, now: Optional[dt.datetime] = None,
                  rng: Optional[random.Random] = None) -> dict[str, Any]:
    with self.lock:
      if self.placed_this_turn:
        return {"ok": False, "error": "ALREADY_PLACED",
                "order_id": self.orders[0]["order_id"] if self.orders else ""}
      if not self.cart:
        return {"ok": False, "error": "EMPTY_CART"}
      slot_id = self.checkout.get("slot_id")
      label = slot_label(slot_id or "")
      if not label:
        return {"ok": False, "error": "NO_SLOT_SELECTED", "slots": slots()}
      sub = self.subtotal()
      total = sub + shipping_for(sub)
      oid = new_order_id(now, {o.get("order_id") for o in self.orders}, rng)
      self.orders.insert(0, {"order_id": oid, "status": "placed", "slot_label": label,
                             "total_tax_included": total})
      self.cart = []
      self.checkout = {"state": "placed", "slot_id": slot_id}
      self.placed_this_turn = True
      result = {"ok": True, "order_id": oid, "total_tax_included": total, "slot_label": label}
      return self._record("place_order", {"order_id": oid}, result)

  def get_order_status(self, order_id: str = "", now: Optional[dt.datetime] = None) -> dict[str, Any]:
    with self.lock:
      order = None
      if order_id:
        order = next((o for o in self.orders if o.get("order_id") == order_id), None)
      elif self.orders:
        order = self.orders[0]
      if not order:
        return {"ok": False, "error": "NO_ORDER", "order_id": order_id or ""}
      status = order.get("status") or "placed"
      label = order.get("slot_label") or ""
      eta = label
      for sid, _, _, _ in SLOT_DEFS:
        if slot_label(sid) == label:
          eta = f"{date_label(slot_date(sid, now))}, {label.rsplit(' ', 1)[1]} ({label})"
      result = {"order_id": order["order_id"], "status": status,
                "status_label": STATUS_LABELS.get(status, status), "eta": eta}
      args = {"order_id": order["order_id"]}
      return self._record("get_order_status", args, result)

  def show_products(self, args: dict[str, Any]) -> dict[str, Any]:
    with self.lock:
      ids = [str(x) for x in (args.get("product_ids") or []) if x]
      self.last_shown_ids = ids
      return self._record("show_products_on_page", args, {"shown": len(ids)})

  def show_comparison(self, args: dict[str, Any]) -> dict[str, Any]:
    with self.lock:
      return self._record("show_comparison", args, {"shown": bool(args.get("product_ids"))})

  def show_store_availability(self, args: dict[str, Any]) -> dict[str, Any]:
    """Page action (CONTRACT_EN v14): open the "Available in stores" view for these products."""
    with self.lock:
      ids = [str(x) for x in (args.get("product_ids") or []) if x]
      return self._record("show_store_availability", {"product_ids": ids, "message": str(args.get("message") or "")},
                          {"shown": len(ids)})


def _channel_of(d: dict[str, Any]) -> str:
  ch = d.get("channel")
  if not ch:
    for a in d.get("attributes") or []:
      if isinstance(a, dict) and a.get("key") == "channel":
        txt = (a.get("value") or {}).get("text") or []
        ch = txt[0] if txt else ""
        break
  return str(ch) if ch in CHANNELS else ""


def _int(v: Any, default: int) -> int:
  try:
    return int(float(v))
  except (TypeError, ValueError):
    return default
