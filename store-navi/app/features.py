"""Shopper features ported to the Cymbal Retail store app (English only).

Endpoints (router included by main.py):
  GET  /api/recipes/suggest?q=&store_id=&limit=3     dish-first: 3 dish cards for a free-text request
  GET  /api/recipes/{id}?servings=&kid_friendly=&quick=&store_id=   customized recipe card with in-store ingredients
  POST /api/forgot-check {store_id, cart_ids, recipe_id?}    "Did you forget?" (replenish cycle + recipe gaps)
  GET  /api/products/{id}/detail?store_id=           product + availability + purchase-history line
  POST /api/voice/transcribe (multipart "audio" or raw audio/* body) -> {text}  (Gemini, en-US)

Recipes come from data/recipes_en.json (60 recipes). When that file is missing a small built-in English sample is
used and its ingredients are matched to catalog products by keyword search.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config, deps, inventory, offers, repository, stores

log = logging.getLogger("store-navi.features")
router = APIRouter()

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_JA = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")

# ------------------------------------------------------------------------------------------------ common helpers


def _customer_id(request: Request) -> str:
    cid = request.cookies.get("cr_customer") or config.DEFAULT_CUSTOMER
    try:
        return cid if cid in offers.personas() else config.DEFAULT_CUSTOMER
    except Exception:  # noqa: BLE001
        return config.DEFAULT_CUSTOMER


def _store_id(store_id: str | None) -> str:
    sid = (store_id or "").strip()
    try:
        return sid if stores.get_store(sid) else config.DEFAULT_STORE
    except Exception:  # noqa: BLE001
        return sid or getattr(config, "DEFAULT_STORE", "S01")


def _today() -> dt.date:
    return dt.datetime.now(stores.JST).date()


def _md(d: dt.date) -> str:
    """en-US short date, e.g. 'Nov 2'."""
    return f"{d:%b} {d.day}"


def _cycle_text(days: int) -> str:
    if days <= 1:
        return "every day"
    if days <= 10:
        return "every week" if days == 7 else f"every {days} days"
    if days <= 24:
        w = max(1, round(days / 7))
        return "every week" if w == 1 else f"every {w} weeks"
    m = max(1, round(days / 30))
    return "every month" if m == 1 else f"every {m} months"


def _english(s: Any) -> str:
    s = str(s or "").strip()
    return "" if _JA.search(s) else s


def _availability(pid: str, store_id: str, product: dict | None, live: bool = False) -> dict | None:
    try:
        if live:
            try:
                return inventory.get_availability(pid, store_id, product, live=True)
            except TypeError:
                pass
        return inventory.get_availability(pid, store_id, product)
    except Exception as e:  # noqa: BLE001
        log.warning("availability %s/%s failed: %s", store_id, pid, e)
        return None


NON_FOOD = ("Kitchen", "Home", "Household", "Beauty", "Pet", "Health", "Baby", "Flowers")


def _search_product(keywords: str, prefer: set[str] | None = None, cat_prefix: str = "") -> dict | None:
    """Best catalog product for an English keyword phrase (local text search; tries each word if the phrase misses).
    `prefer`: product ids (e.g. in stock at the store) ranked first among the top hits."""
    cat = deps.get_catalog()
    tries = [keywords] + [w for w in keywords.split() if len(w) > 2]
    for q in tries:
        try:
            hits = cat.text_search(q)
        except Exception:  # noqa: BLE001
            hits = []
        top = [n for _, n in hits[:30] if str(n.get("availability") or "IN_STOCK") == "IN_STOCK"
               and not str(n.get("category_path") or "").startswith(NON_FOOD)
               and (not cat_prefix or str(n.get("category_path") or "").startswith(cat_prefix))][:12]
        if prefer:
            top.sort(key=lambda n: n.get("id") not in prefer)
        if top:
            return deps.to_product(top[0])
    return None


# ------------------------------------------------------------------------------------------------ recipes

BUILTIN_RECIPES: list[dict] = [
    {
        "recipe_id": "CR-R001", "title": "Mild Chicken Curry", "subtitle": "Family-style curry with potatoes and carrots",
        "description": "A weeknight Japanese-style curry rice the whole family enjoys.", "prep_minutes": 40, "servings": 4,
        "tags": ["curry", "dinner", "family", "rice", "kid-friendly"], "kids_friendly": True, "contains_shrimp": False,
        "steps": [
            {"no": 1, "text": "Cut the chicken, potatoes, carrots and onion into bite-size pieces.", "tip": ""},
            {"no": 2, "text": "Saute the chicken and onion in oil until the onion turns translucent.", "tip": ""},
            {"no": 3, "text": "Add the potatoes, carrots and 800 ml of water; simmer for 15 minutes.", "tip": "Skim the foam for a cleaner taste."},
            {"no": 4, "text": "Turn off the heat, dissolve the curry roux, then simmer 5 more minutes. Serve over rice.", "tip": ""},
        ],
        "variations": [
            {"id": "kids", "label": "Kid-friendly", "note": "Use mild curry roux and stir in a spoon of honey or grated apple."},
            {"id": "quick", "label": "Quick", "note": "Use pre-cut curry vegetables and microwave the potatoes for 4 minutes first."},
        ],
        "ingredients": [
            {"name": "Chicken thigh", "qty": "400 g", "keywords": "chicken thigh", "cat": "Meat"},
            {"name": "Potatoes", "qty": "3", "keywords": "potatoes", "cat": "Produce"},
            {"name": "Carrot", "qty": "1", "keywords": "carrots", "cat": "Produce"},
            {"name": "Onion", "qty": "2", "keywords": "onions", "cat": "Produce"},
            {"name": "Curry roux", "qty": "1 box", "keywords": "curry roux", "cat": "Seasonings"},
            {"name": "Rice", "qty": "4 cups", "keywords": "rice", "cat": "Rice & Noodles"},
        ],
    },
    {
        "recipe_id": "CR-R002", "title": "Teriyaki Salmon Rice Bowl", "subtitle": "Glazed salmon over rice with greens",
        "description": "Sweet-savory teriyaki salmon ready in about 20 minutes.", "prep_minutes": 20, "servings": 4,
        "tags": ["fish", "quick", "dinner", "rice bowl", "kid-friendly"], "kids_friendly": True, "contains_shrimp": False,
        "steps": [
            {"no": 1, "text": "Pat the salmon dry and dust lightly with flour.", "tip": ""},
            {"no": 2, "text": "Pan-fry the salmon 3 minutes per side.", "tip": ""},
            {"no": 3, "text": "Add soy sauce, mirin and sugar; spoon the glaze over until glossy.", "tip": ""},
            {"no": 4, "text": "Serve over rice with blanched spinach.", "tip": ""},
        ],
        "variations": [
            {"id": "kids", "label": "Kid-friendly", "note": "Remove skin and bones, and cut into small flakes."},
            {"id": "quick", "label": "Quick", "note": "Use ready-made teriyaki sauce and microwaveable rice."},
        ],
        "ingredients": [
            {"name": "Salmon fillets", "qty": "4", "keywords": "salmon", "cat": "Seafood"},
            {"name": "Soy sauce", "qty": "3 tbsp", "keywords": "soy sauce", "cat": "Seasonings"},
            {"name": "Mirin", "qty": "3 tbsp", "keywords": "mirin", "cat": "Seasonings"},
            {"name": "Spinach", "qty": "1 bunch", "keywords": "spinach", "cat": "Produce"},
            {"name": "Rice", "qty": "4 cups", "keywords": "rice", "cat": "Rice & Noodles"},
        ],
    },
    {
        "recipe_id": "CR-R003", "title": "Pork and Vegetable Stir-fry", "subtitle": "Ginger pork with cabbage and peppers",
        "description": "A 15-minute one-pan stir-fry for busy evenings.", "prep_minutes": 15, "servings": 4,
        "tags": ["pork", "quick", "stir-fry", "dinner", "easy"], "kids_friendly": False, "contains_shrimp": False,
        "steps": [
            {"no": 1, "text": "Slice the cabbage and peppers; grate the ginger.", "tip": ""},
            {"no": 2, "text": "Stir-fry the pork over high heat until browned.", "tip": ""},
            {"no": 3, "text": "Add the vegetables and toss for 2 minutes.", "tip": ""},
            {"no": 4, "text": "Season with soy sauce and ginger; serve with rice.", "tip": ""},
        ],
        "variations": [
            {"id": "kids", "label": "Kid-friendly", "note": "Go easy on the ginger and add a little honey to the sauce."},
            {"id": "quick", "label": "Quick", "note": "Use a pre-cut stir-fry vegetable mix."},
        ],
        "ingredients": [
            {"name": "Sliced pork", "qty": "300 g", "keywords": "pork", "cat": "Meat"},
            {"name": "Cabbage", "qty": "1/4 head", "keywords": "cabbage", "cat": "Produce"},
            {"name": "Bell pepper", "qty": "2", "keywords": "pepper", "cat": "Produce"},
            {"name": "Ginger", "qty": "1 knob", "keywords": "ginger", "cat": "Produce"},
            {"name": "Soy sauce", "qty": "2 tbsp", "keywords": "soy sauce", "cat": "Seasonings"},
        ],
    },
]


def _recipes_file() -> Path | None:
    env = os.environ.get("RECIPES_FILE")
    cands = [Path(env)] if env else []
    cands += [config.data_dir() / "recipes_en.json", config.SN_DIR / "data" / "recipes_en.json",
              config.REPO_DIR / "seed" / "catalog" / "recipes_en.json"]
    for c in cands:
        if c.exists():
            return c
    return None


def load_recipes() -> list[dict]:
    with _lock:
        if "recipes" not in _cache:
            rows: list[dict] = []
            path = _recipes_file()
            if path:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    rows = data.get("recipes", []) if isinstance(data, dict) else list(data)
                except Exception as e:  # noqa: BLE001
                    log.error("recipes file %s unreadable: %s", path, e)
            if rows:
                # the demo persona loves curry: supplement the built-in curry when the dataset has none
                if not any("curry" in str(r.get("title", "")).lower() for r in rows):
                    rows = rows + [dict(r) for r in BUILTIN_RECIPES if "curry" in r["title"].lower()]
            _cache["recipes"] = rows or [dict(r) for r in BUILTIN_RECIPES]
            _cache["builtin"] = not rows
        return _cache["recipes"]


def reload() -> None:
    with _lock:
        _cache.clear()


def _rid(r: dict) -> str:
    return str(r.get("recipe_id") or r.get("id") or "")


def get_recipe(rid: str) -> dict | None:
    return next((r for r in load_recipes() if _rid(r) == rid), None)


def _minutes(r: dict) -> int:
    try:
        return int(r.get("prep_minutes") or r.get("minutes") or 30)
    except (TypeError, ValueError):
        return 30


def _kid(r: dict) -> bool:
    return bool(r.get("kids_friendly") or r.get("kid_friendly"))


def _tags(r: dict) -> list[str]:
    return [t for t in (_english(x) for x in (r.get("tags") or [])) if t]


QUICK_WORDS = {"quick", "fast", "easy", "busy", "simple", "weeknight", "minutes", "min", "speedy", "short"}
KID_WORDS = {"kid", "kids", "child", "children", "family", "toddler", "picky", "son", "daughter"}
STOP = {"a", "an", "the", "for", "and", "or", "to", "with", "something", "some", "i", "want", "make", "like", "likes",
        "that", "my", "me", "we", "dinner", "lunch", "tonight", "recipe", "recipes", "dish", "meal", "idea", "ideas",
        "what", "should", "cook", "can", "of", "in", "on", "please", "today", "will", "would", "could", "love", "loves",
        "enjoy", "eat", "eats", "need", "needs", "give", "show", "find", "get", "have", "has", "had", "do", "does", "is",
        "are", "be", "it", "its", "this", "these", "those", "them", "they", "our", "us", "you", "your", "any", "good",
        "nice", "great", "best", "recommend", "suggest", "suggestion", "suggestions", "tomorrow", "night", "evening",
        "breakfast", "supper", "menu", "food", "dishes", "meals", "cooking", "kind", "sort", "type", "help", "let",
        "lets", "let's", "how", "about", "from", "at", "by", "into", "up", "all", "everyone", "whole", "so", "too",
        "very", "really", "just", "maybe", "also", "thing", "things", "new", "try", "tonight's", "i'd", "i'm", "we're"}


def _persona_avoid_shrimp(customer_id: str) -> bool:
    try:
        p = offers.get_persona(customer_id)
    except Exception:  # noqa: BLE001
        return customer_id == config.DEFAULT_CUSTOMER
    return "shrimp" in json.dumps(p, ensure_ascii=False).lower() or customer_id == config.DEFAULT_CUSTOMER


def suggest(q: str, limit: int = 3, customer_id: str = "") -> list[dict]:
    words = [w for w in re.split(r"[^a-z0-9]+", (q or "").lower()) if w]
    want_quick = any(w in QUICK_WORDS for w in words)
    want_kid = any(w in KID_WORDS or w.startswith("kid") for w in words)
    terms = [w.rstrip("s") if len(w) > 3 else w for w in words
             if len(w) > 2 and w not in STOP and w not in QUICK_WORDS and w not in KID_WORDS and not w.isdigit()]
    avoid_shrimp = _persona_avoid_shrimp(customer_id)
    scored: list[tuple[float, dict, list[str]]] = []
    for r in load_recipes():
        title = _english(r.get("title"))
        if not title:
            continue
        hay_title = title.lower()
        hay = " ".join([hay_title, _english(r.get("subtitle")).lower(), _english(r.get("description")).lower(),
                        " ".join(_tags(r)).lower(), _english(r.get("main_category")).lower(),
                        " ".join(_english(i.get("name")).lower() for i in r.get("ingredients") or [])])
        score, why = 0.0, []
        hit_terms = []
        for t in terms:
            if t in hay_title:
                score += 5
                hit_terms.append(t)
            elif t in hay:
                score += 2
                hit_terms.append(t)
        if terms and not hit_terms:
            score -= 3
        mins = _minutes(r)
        if want_quick:
            score += 4 if mins <= 20 else (1 if mins <= 30 else -2)
        if want_kid:
            score += 4 if _kid(r) else -1
        if r.get("contains_shrimp") and avoid_shrimp:
            score -= 10
        if customer_id and customer_id in json.dumps(r.get("persona_fit") or []):
            score += 1.5
        if mins <= 20:
            why.append(f"Ready in {mins} min")
        if _kid(r):
            why.append("Kids love it")
        if avoid_shrimp and not r.get("contains_shrimp"):
            why.append("Shrimp-free")
        if hit_terms:
            why.insert(0, "Matches \u201c" + " ".join(dict.fromkeys(hit_terms)) + "\u201d")
        scored.append((score + (0.5 if _kid(r) else 0) - mins / 1000, r, why))
    scored.sort(key=lambda x: -x[0])
    return [_dish_card(r, why) for _, r, why in scored[:max(1, min(limit, 12))]]


def _dish_card(r: dict, why: list[str]) -> dict:
    return {"id": _rid(r), "title": _english(r.get("title")), "subtitle": _english(r.get("subtitle")),
            "image": r.get("image_uri") or r.get("image") or "", "minutes": _minutes(r),
            "servings": int(r.get("servings") or 4), "kid_friendly": _kid(r), "tags": _tags(r)[:4],
            "reason": " \u2022 ".join(why[:3])}


_NUM = re.compile(r"^\s*(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\s*(.*)$")


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 0.05:
        return str(int(round(x)))
    fr = Fraction(x).limit_denominator(4)
    if abs(float(fr) - x) < 0.05 and fr.denominator in (2, 3, 4):
        whole, rem = divmod(fr.numerator, fr.denominator)
        return (f"{whole} " if whole else "") + f"{rem}/{fr.denominator}"
    return f"{x:.1f}"


def scale_amount(qty: str, factor: float) -> str:
    qty = _english(qty)
    if not qty or abs(factor - 1) < 1e-9:
        return qty
    m = _NUM.match(qty)
    if not m:
        return qty
    raw, unit = m.group(1), m.group(2)
    try:
        val = float(sum(Fraction(p) for p in raw.split()))
    except (ValueError, ZeroDivisionError):
        return qty
    return (_fmt_num(val * factor) + (" " + unit if unit else "")).strip()


def _ingredient_product(ing: dict) -> dict | None:
    for pid in [ing.get("product_id")] + list(ing.get("substitute_ids") or []):
        if pid:
            p = deps.to_product(str(pid))
            if p:
                return p
    kw = ing.get("keywords") or _english(ing.get("name"))
    if not kw:
        return None
    return (_search_product(kw, cat_prefix=ing["cat"]) if ing.get("cat") else None) or _search_product(kw)


def recipe_card(rid: str, servings: int = 0, kid_friendly: bool = False, quick: bool = False,
                store_id: str = "", with_stock: bool = True) -> dict | None:
    r = get_recipe(rid)
    if not r:
        return None
    base = int(r.get("servings") or 4) or 4
    servings = servings if servings in range(1, 13) else base
    factor = servings / base
    variations = {str(v.get("id")): v for v in r.get("variations") or []}
    notes: list[str] = []
    steps = [{"no": i + 1, "text": _english(s.get("text") if isinstance(s, dict) else s),
              "tip": _english(s.get("tip")) if isinstance(s, dict) else ""}
             for i, s in enumerate(r.get("steps") or [])]
    steps = [s for s in steps if s["text"]]
    minutes = _minutes(r)
    if quick:
        v = variations.get("quick") or {}
        note = _english(v.get("note")) or "Use pre-cut vegetables and microwave to cut down cooking time."
        steps.insert(0, {"no": 0, "text": "Shortcut: " + note, "tip": ""})
        for i, s in enumerate(steps):
            s["no"] = i + 1
        minutes = min(minutes, max(5, round(minutes * 0.6)))
        notes.append("Quick mode: about " + str(minutes) + " min with the shortcut above.")
    if kid_friendly:
        v = variations.get("kids") or {}
        notes.append("Kid-friendly: " + (_english(v.get("note")) or
                                         "keep it mild (skip chili and strong spices) and cut everything bite-size."))
    if r.get("contains_shrimp"):
        notes.append("Contains shrimp \u2014 check family allergies.")
    tip = _english(r.get("clerk_tip"))
    if tip:
        notes.append("Staff tip: " + tip)
    ings = []
    for ing in r.get("ingredients") or []:
        name = _english(ing.get("name"))
        p = _ingredient_product(ing)
        if not name and p:
            name = p.get("title", "")
        if not name:
            continue
        ings.append({"name": name, "amount": scale_amount(str(ing.get("qty") or ing.get("amount") or ""), factor),
                     "optional": bool(ing.get("optional")), "role": _english(ing.get("role")), "product": p,
                     "availability": _availability(p["id"], store_id, p) if (p and with_stock) else None})
    return {"id": _rid(r), "title": _english(r.get("title")), "subtitle": _english(r.get("subtitle")),
            "description": _english(r.get("description")), "image": r.get("image_uri") or r.get("image") or "",
            "servings": servings, "base_servings": base, "minutes": minutes, "kid_friendly": kid_friendly,
            "quick": quick, "steps": steps, "ingredients": ings, "notes": notes, "tags": _tags(r)}


# ------------------------------------------------------------------------------------------------ purchase history

DEMO_STAPLES = [("milk", 7), ("eggs", 10), ("bread", 7), ("banana", 7), ("natto", 10), ("rice", 30), ("yogurt", 14)]


def _parse_date(s: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _history_rows(customer_id: str) -> list[dict]:
    with _lock:
        hist = _cache.get("history")
    if hist is None:
        try:
            hist = repository.load_purchase_history()
        except Exception as e:  # noqa: BLE001
            log.warning("purchase history unavailable: %s", e)
            hist = []
        with _lock:
            _cache["history"] = hist
    rows = [r for r in hist if r.get("customer_id") == customer_id]
    return rows or _persona_history(customer_id) or _demo_history(customer_id)


def _persona_history(customer_id: str) -> list[dict]:
    """Online purchase history (seed/personas/purchase_history.jsonl or DATA_DIR/purchase_history.jsonl) for customers
    without in-store rows (e.g. CR-0001)."""
    with _lock:
        ph = _cache.get("persona_history")
    if ph is None:
        ph = []
        for cand in (config.data_dir() / "purchase_history.jsonl", config.SN_DIR / "data" / "purchase_history.jsonl",
                     config.REPO_DIR / "seed" / "personas" / "purchase_history.jsonl"):
            if cand.exists():
                try:
                    ph = [json.loads(line) for line in cand.read_text(encoding="utf-8").splitlines() if line.strip()]
                except Exception as e:  # noqa: BLE001
                    log.warning("persona history %s unreadable: %s", cand, e)
                    ph = []
                if ph:
                    break
        with _lock:
            _cache["persona_history"] = ph
    return [r for r in ph if r.get("customer_id") == customer_id]


def _demo_history(customer_id: str) -> list[dict]:
    """Deterministic demo replenishment history (staples) for customers without purchase rows."""
    key = "demo_hist:" + customer_id
    with _lock:
        if key in _cache:
            return _cache[key]
    today = _today()
    rows: list[dict] = []
    try:
        prefer = inventory.in_store_ids(config.DEFAULT_STORE)
    except Exception:  # noqa: BLE001
        prefer = set()
    for i, (kw, cycle) in enumerate(DEMO_STAPLES):
        p = _search_product(kw, prefer)
        if not p:
            continue
        last = today - dt.timedelta(days=cycle + (i % 3) - 1)
        for k in range(4):
            rows.append({"customer_id": customer_id, "product_id": p["id"], "quantity": 1, "demo": True,
                         "order_date": (last - dt.timedelta(days=cycle * k)).isoformat()})
    with _lock:
        _cache[key] = rows
    return rows


def product_history(customer_id: str, product_id: str, rows: list[dict] | None = None) -> dict | None:
    rows = rows if rows is not None else _history_rows(customer_id)
    dates = sorted({d for d in (_parse_date(r.get("order_date")) for r in rows
                                if str(r.get("product_id")) == product_id) if d})
    if not dates:
        return None
    last = dates[-1]
    cycle = None
    if len(dates) >= 2:
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0)
        cycle = gaps[len(gaps) // 2] if gaps else None  # median: robust to seasonal breaks
    label = "Last bought " + _md(last) + (" \u2022 usually " + _cycle_text(cycle) if cycle else "")
    days_since = (_today() - last).days
    return {"last_bought": _md(last), "last_bought_date": last.isoformat(), "count": len(dates),
            "cycle_days": cycle, "days_since": days_since, "label": label}


def forgot_check(customer_id: str, store_id: str, cart_ids: list[str], recipe_id: str = "", limit: int = 6) -> list[dict]:
    in_cart = {str(x) for x in cart_ids or []}
    items: list[dict] = []
    seen: set[str] = set(in_cart)
    if recipe_id:
        card = recipe_card(recipe_id, store_id=store_id, with_stock=False)
        if card:
            for ing in card["ingredients"]:
                p = ing.get("product")
                if not p or ing.get("optional") or p["id"] in seen:
                    continue
                if len(items) >= max(1, limit // 2):
                    break
                seen.add(p["id"])
                items.append({"product": p, "kind": "recipe", "reason": "Needed for " + card["title"]})
    rows = _history_rows(customer_id)
    cands = []
    for pid in {str(r.get("product_id")) for r in rows if r.get("product_id")}:
        if pid in seen:
            continue
        h = product_history(customer_id, pid, rows)
        if not h or not h["cycle_days"]:
            continue
        ratio = h["days_since"] / max(1, h["cycle_days"])
        # due (>= 80% of the usual cycle) but still an active habit (not a seasonal item from months ago)
        if 0.8 <= ratio <= 3.0 and h["count"] >= 3:
            cands.append((min(ratio, 2.0) + min(h["count"], 12) / 24, pid, h))
    cands.sort(key=lambda x: (-x[0], x[1]))
    for _, pid, h in cands:
        if len(items) >= limit:
            break
        p = deps.to_product(pid)
        if not p:
            continue
        seen.add(pid)
        items.append({"product": p, "kind": "replenish", "history": h,
                      "reason": "Usually " + _cycle_text(h["cycle_days"]) + " \u2014 last bought " + h["last_bought"]})
    for it in items[:limit]:
        it["availability"] = _availability(it["product"]["id"], store_id, it["product"])
    return items[:limit]


# ------------------------------------------------------------------------------------------------ routes


@router.get("/api/recipes/suggest")
async def api_recipe_suggest(request: Request, q: str = "", store_id: str = "", limit: int = 3):
    import asyncio
    dishes = await asyncio.to_thread(suggest, q, limit, _customer_id(request))
    return {"query": q, "dishes": dishes}


@router.get("/api/recipes/{recipe_id}")
async def api_recipe(recipe_id: str, servings: int = 0, kid_friendly: int = 0, quick: int = 0, store_id: str = ""):
    import asyncio
    card = await asyncio.to_thread(recipe_card, recipe_id, servings, bool(kid_friendly), bool(quick),
                                   _store_id(store_id))
    if not card:
        return JSONResponse({"error": "Recipe not found"}, status_code=404)
    return card


@router.post("/api/forgot-check")
async def api_forgot_check(request: Request):
    import asyncio
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    cart_ids = [str(x) for x in (body.get("cart_ids") or []) if x]
    items = await asyncio.to_thread(forgot_check, _customer_id(request), _store_id(body.get("store_id")), cart_ids,
                                    str(body.get("recipe_id") or ""))
    return {"items": items}


@router.get("/api/products/{product_id}/detail")
async def api_product_detail(request: Request, product_id: str, store_id: str = ""):
    import asyncio

    def work():
        p = deps.to_product(product_id)
        if not p:
            got = deps.hydrate([product_id])
            p = got[0] if got else None
        if not p:
            return None
        sid = _store_id(store_id)
        return {"product": p, "availability": _availability(product_id, sid, p, live=True),
                "history": product_history(_customer_id(request), product_id)}

    out = await asyncio.to_thread(work)
    if out is None:
        return JSONResponse({"error": "Product not found"}, status_code=404)
    return out


# ------------------------------------------------------------------------------------------------ voice

MAX_AUDIO_BYTES = 8 * 1024 * 1024
TRANSCRIBE_PROMPT = ("Transcribe this en-US shopping request. The speaker is a shopper using a supermarket app. "
                     "Output only the transcript text in English, with punctuation, no quotes or commentary. "
                     "If the audio is silent or unintelligible, output nothing.")
_MIME = {"webm": "audio/webm", "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav",
         "mp4": "audio/mp4", "m4a": "audio/mp4", "aac": "audio/aac", "mp3": "audio/mpeg", "flac": "audio/flac"}


def _norm_mime(ct: str | None, filename: str | None) -> str:
    c = (ct or "").split(";")[0].strip().lower()
    if c.startswith("video/"):
        c = "audio/" + c.split("/", 1)[1]
    if c in ("audio/x-wav", "audio/wave"):
        c = "audio/wav"
    if c in ("audio/x-m4a", "audio/m4a"):
        c = "audio/mp4"
    if c.startswith("audio/"):
        return c
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    return _MIME.get(ext, "audio/webm")


def _transcribe(data: bytes, mime: str) -> str:
    from google import genai
    from google.genai import types

    project = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("PROJECT_ID is not set")
    client = genai.Client(vertexai=True, project=project, location=os.environ.get("GEMINI_LOCATION", "global"))
    resp = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        contents=[types.Content(role="user", parts=[types.Part.from_bytes(data=data, mime_type=mime),
                                                    types.Part.from_text(text="Transcribe this audio.")])],
        config=types.GenerateContentConfig(system_instruction=TRANSCRIBE_PROMPT, temperature=0.0,
                                           max_output_tokens=512))
    text = (getattr(resp, "text", "") or "").strip().strip("\"'\u201c\u201d ")
    return re.sub(r"^(transcript|transcription)\s*:\s*", "", text, flags=re.I)


@router.post("/api/voice/transcribe")
async def api_voice_transcribe(request: Request):
    import asyncio
    data, mime = b"", ""
    ct = request.headers.get("content-type", "")
    try:
        if ct.startswith("multipart/form-data"):
            form = await request.form()
            f = form.get("audio") or form.get("file")
            if f is not None and hasattr(f, "read"):
                data = await f.read()
                mime = _norm_mime(getattr(f, "content_type", ""), getattr(f, "filename", ""))
        else:
            data = await request.body()
            mime = _norm_mime(ct, "")
    except Exception as e:  # noqa: BLE001  (e.g. python-multipart missing)
        log.warning("voice upload parse failed: %s", e)
        return JSONResponse({"error": "Could not read the audio upload"}, status_code=400)
    if not data:
        return JSONResponse({"error": "No audio received"}, status_code=400)
    if len(data) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "Audio too long (max about 30 seconds)"}, status_code=413)
    try:
        text = await asyncio.wait_for(asyncio.to_thread(_transcribe, data, mime), timeout=25)
    except Exception as e:  # noqa: BLE001
        log.warning("voice transcription unavailable: %s", str(e)[:200])
        return JSONResponse({"error": "Voice transcription is unavailable right now"}, status_code=503)
    return {"text": text}
