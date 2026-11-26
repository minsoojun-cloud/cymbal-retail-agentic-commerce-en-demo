"""Personalized coupons / offers for the store home screen (persona interest_categories + purchase history)."""
from __future__ import annotations

import datetime as dt
import threading
from collections import Counter
from typing import Any

from . import deps, inventory, repository, stores

_lock = threading.Lock()
_cache: dict[str, Any] = {}


def _data() -> dict[str, Any]:
    with _lock:
        if not _cache:
            _cache["personas"] = {p.get("customer_id"): p for p in repository.load_personas() if p.get("customer_id")}
            _cache["coupons"] = repository.load_coupons()
            _cache["offers"] = repository.load_offers()
            _cache["history"] = repository.load_purchase_history()
        return _cache


def reload() -> None:
    with _lock:
        _cache.clear()


DEFAULT_PERSONAS = {
    "CR-0001": {"customer_id": "CR-0001", "name": "Sakura Tanaka", "avatar": "👩", "interest_categories": []},
    "CR-0002": {"customer_id": "CR-0002", "name": "Hiroshi Sato", "avatar": "👴", "interest_categories": []},
    "CR-0003": {"customer_id": "CR-0003", "name": "Mika Ito", "avatar": "👩‍🦰", "interest_categories": []},
}


def personas() -> dict[str, dict]:
    p = dict(DEFAULT_PERSONAS)
    p.update(_data()["personas"])
    return p


def get_persona(customer_id: str) -> dict:
    return personas().get(customer_id) or personas()["CR-0001"]


def customers_brief() -> list[dict]:
    return [{"customer_id": c["customer_id"], "name": c.get("name", ""), "avatar": c.get("avatar") or "🙂",
             "age": c.get("age"), "concerns": c.get("concerns") or []}
            for c in sorted(personas().values(), key=lambda c: c["customer_id"])]


def _norm(c: str) -> str:
    return str(c or "").replace(" > ", ">").replace(" >", ">").replace("> ", ">").strip()


def purchased_categories(customer_id: str) -> Counter:
    cnt: Counter = Counter()
    cat = deps.get_catalog()
    for r in _data()["history"]:
        if r.get("customer_id") != customer_id:
            continue
        c = r.get("category")
        if not c and r.get("product_id"):
            n = cat.get(str(r["product_id"]))
            c = (n or {}).get("category_path")
        if c:
            parts = _norm(c).split(">")
            for i in range(1, len(parts) + 1):
                cnt[">".join(parts[:i])] += int(r.get("quantity") or 1)
    return cnt


def _matches(path: str, prefixes: list[str]) -> bool:
    p = _norm(path)
    return any(p.startswith(_norm(x)) for x in prefixes if x)


def _today() -> str:
    return dt.datetime.now(stores.JST).date().isoformat()


def ranked_coupons(store_id: str, customer_id: str) -> list[dict]:
    persona = get_persona(customer_id)
    interests = persona.get("interest_categories") or []
    bought = purchased_categories(customer_id)
    today = _today()
    rows = []
    for c in _data()["coupons"]:
        if c.get("customer_id") and c["customer_id"] != customer_id:
            continue
        if c.get("store_id") and c["store_id"] != store_id:
            continue
        cats = ((c.get("target") or {}).get("categories")) or []
        personal = bool(c.get("customer_id"))
        score = (100 if personal else 0) + (5 if c.get("store_id") else 0)
        if any(_matches(x, interests) or any(_norm(x) and k.startswith(_norm(x)) for k in bought) for x in cats):
            score += 20
        expired = bool(c.get("valid_until")) and str(c["valid_until"]) < today
        out = dict(c)
        out["personal"] = personal
        out["badge"] = (f"Just for {str(persona.get('name', '')).split(' ')[0]}" if personal
                        else ("This store only" if c.get("store_id") else "All stores"))
        out["_score"] = score - (1000 if expired else 0)
        rows.append(out)
    rows.sort(key=lambda c: (-c["_score"], str(c.get("coupon_id", ""))))
    for r in rows:
        r.pop("_score", None)
    return rows


def ranked_offers(store_id: str, customer_id: str, limit: int = 12) -> list[dict]:
    persona = get_persona(customer_id)
    interests = persona.get("interest_categories") or []
    bought = purchased_categories(customer_id)
    today = _today()
    rows = []
    for o in _data()["offers"]:
        if o.get("store_id") and o["store_id"] != store_id:
            continue
        p = deps.to_product(str(o.get("product_id")))
        if not p:
            continue
        live = (not o.get("start_date") or str(o["start_date"]) <= today) and \
               (not o.get("end_date") or str(o["end_date"]) >= today)
        path = p.get("category_path") or ""
        score = 0.0
        reason = ""
        if _matches(path, interests):
            score += 30
            reason = "Matches your interests"
        top = max((v for k, v in bought.items() if _norm(path).startswith(k)), default=0)
        if top:
            score += 10 + min(top, 10)
            reason = reason or "You often buy this category"
        orig = float(o.get("original_price") or p.get("price") or 0)
        sale = float(o.get("sale_price") or p.get("price") or 0)
        disc = (orig - sale) / orig if orig else 0
        score += disc * 20 + (5 if o.get("store_id") else 0) - (0 if live else 50)
        inv = inventory.inventory_for(store_id, p["id"])
        rows.append({**o, "product": p, "discount_rate": round(disc, 2), "reason": reason,
                     "stock_status": inv["status"] if inv else "not_carried", "_score": score})
    rows.sort(key=lambda r: (-r["_score"], str(r.get("product_id"))))
    for r in rows:
        r.pop("_score", None)
    return rows[:limit]


def home(store_id: str, customer_id: str) -> dict:
    s = stores.get_store(store_id)
    persona = get_persona(customer_id)
    return {"store": stores.with_status(s) if s else None,
            "coupons": ranked_coupons(store_id, customer_id),
            "offers": ranked_offers(store_id, customer_id),
            "customer": {"customer_id": persona["customer_id"], "name": persona.get("name", ""),
                         "avatar": persona.get("avatar") or "🙂"}}
