"""Catalog cache + normalization (Retail Product JSON -> front-end Product shape).

Front-end Product (CONTRACT.md):
  {id,title,brand,category,price,original_price,on_sale,discount_rate,image,rating,
   rating_count,size,labels,availability,description}
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("cymbal-site.catalog")

APP_DIR = Path(__file__).resolve().parent
SITE_DIR = APP_DIR.parent

# Facet keys + labels (shared by live + local modes)
FACET_LABELS = {
    "attributes.top_category": "Category",
    "categories": "Subcategory",
    "brands": "Brand",
    "price": "Price",
    "attributes.on_sale": "Deals",
    "attributes.labels": "Features",
    "attributes.storage_type": "Storage",
    "attributes.origin": "Origin",
    "attributes.allergens": "Allergens",
    "attributes.brand_line": "Brand line",
    "attributes.material": "Material",
    "attributes.color": "Color",
    "attributes.dishes": "Dishes",
    "attributes.is_seasonal": "Seasonal",
    "attributes.channel": "Availability",
    "pickupInStore": "Pick up in store",
    "rating": "Rating",
    "colorFamilies": "Color",
}

# price intervals (JPY, tax included). max None == open ended
PRICE_INTERVALS = [
    (0, 300), (300, 500), (500, 1000), (1000, 2000), (2000, 5000), (5000, None),
]


def price_interval_label(lo: float | None, hi: float | None) -> str:
    lo_i = int(lo or 0)
    if hi is None:
        return f"¥{lo_i:,} & above"
    if lo_i == 0:
        return f"Under ¥{int(hi):,}"
    return f"¥{lo_i:,} – ¥{int(hi):,}"


def price_interval_value(lo: float | None, hi: float | None) -> str:
    return f"{int(lo or 0)}-{'' if hi is None else int(hi)}"


def parse_price_value(v: str) -> tuple[float, float | None]:
    lo, _, hi = str(v).partition("-")
    return float(lo or 0), (float(hi) if hi else None)


def _attr_map(raw: Any) -> dict[str, dict]:
    if isinstance(raw, dict):
        return raw
    out: dict[str, dict] = {}
    for a in raw or []:
        if isinstance(a, dict) and "key" in a:
            out[a["key"]] = a.get("value") or {}
    return out


def _attr_texts(attrs: dict, key: str) -> list[str]:
    v = attrs.get(key) or {}
    return [str(x) for x in (v.get("text") or [])]


def _attr_text(attrs: dict, key: str) -> str:
    t = _attr_texts(attrs, key)
    return t[0] if t else ""


def _attr_num(attrs: dict, key: str) -> float | None:
    v = attrs.get(key) or {}
    n = v.get("numbers") or []
    try:
        return float(n[0]) if n else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


_IMG_RE = re.compile(r"/img/products/([^/?#]+)$")


def local_image(uri: str) -> str:
    """Rewrite catalog image URIs (absolute ${SITE_BASE_URL}/img/products/x.jpg) to site-relative."""
    if not uri:
        return ""
    m = _IMG_RE.search(uri.split("?")[0])
    return f"/img/products/{m.group(1)}" if m else uri


def normalize(p: dict) -> dict:
    attrs = _attr_map(p.get("attributes"))
    price_info = p.get("priceInfo") or p.get("price_info") or {}
    price = _int(price_info.get("price")) or 0
    original = _int(price_info.get("originalPrice", price_info.get("original_price"))) or price
    on_sale = _attr_text(attrs, "on_sale").lower() == "true" or (original > price > 0)
    discount = _attr_num(attrs, "discount_rate")
    if discount and discount > 1:  # catalog stores percent (e.g. 20.0)
        discount = discount / 100.0
    if not discount and original > price > 0:
        discount = round((original - price) / original, 2)
    cats = p.get("categories") or []
    cat_path = cats[0] if cats else ""
    top = _attr_text(attrs, "top_category") or cat_path.split(">")[0].strip()
    images = [i.get("uri") for i in (p.get("images") or []) if isinstance(i, dict) and i.get("uri")]
    rating_info = p.get("rating") or {}
    avg = rating_info.get("averageRating", rating_info.get("average_rating"))
    cnt = _int(rating_info.get("ratingCount", rating_info.get("rating_count")))
    brands = p.get("brands") or []
    return {
        "id": str(p.get("id") or str(p.get("name", "")).rsplit("/", 1)[-1]),
        "title": p.get("title") or "",
        "brand": brands[0] if brands else "",
        "category": top,
        "category_path": cat_path,
        "price": price,
        "original_price": original,
        "on_sale": bool(on_sale),
        "discount_rate": float(discount or 0.0),
        "image": local_image(images[0]) if images else "",
        "rating": round(float(avg), 1) if avg is not None else 4.2,
        "rating_count": cnt if cnt is not None else 24,
        "size": _attr_text(attrs, "size"),
        "labels": _attr_texts(attrs, "labels"),
        "availability": p.get("availability") or "IN_STOCK",
        "description": p.get("description") or "",
        "promo_text": " ".join(_attr_text(attrs, "promo_text").split()),
        "channel": (_attr_text(attrs, "channel") or "omni").strip().lower(),
        "_attrs": {k: _attr_texts(attrs, k) for k in attrs if _attr_texts(attrs, k)},
        "_tags": p.get("tags") or [],
    }


def public(n: dict) -> dict:
    return {k: v for k, v in n.items() if not k.startswith("_")}


class Catalog:
    """In-memory product cache loaded from a Retail-schema JSONL file."""

    def __init__(self, path: str | os.PathLike | None):
        self.path = str(path) if path else ""
        self.items: dict[str, dict] = {}
        if self.path and os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if p.get("type", "PRIMARY") not in ("PRIMARY", ""):
                        continue
                    n = normalize(p)
                    n["_hay_title"] = (n["title"] + " " + n["brand"]).lower()
                    n["_hay"] = " ".join(
                        [n["title"], n["brand"], n["category_path"], n["description"], " ".join(n["_tags"]),
                         " ".join(n["labels"]), " ".join(sum(n["_attrs"].values(), []))]
                    ).lower()
                    self.items[n["id"]] = n
            log.info("Catalog loaded %d products from %s", len(self.items), self.path)
        else:
            log.warning("Catalog file not found: %r (hydration falls back to Retail get_product)", self.path)

    def __len__(self) -> int:
        return len(self.items)

    def get(self, pid: str) -> dict | None:
        return self.items.get(pid)

    def get_many(self, ids: Iterable[str]) -> list[dict]:
        return [public(self.items[i]) for i in ids if i in self.items]

    # ---- local search (offline / DATA_FILE mode) ----------------------------
    def facet_values(self, n: dict, key: str) -> list[str]:
        if key == "brands":
            return [n["brand"]] if n["brand"] else []
        if key == "attributes.top_category":
            return [n["category"]] if n["category"] else []
        if key == "categories":
            return [n["category_path"]] if n["category_path"] else []
        if key == "attributes.on_sale":
            return ["true" if n["on_sale"] else "false"]
        if key == "attributes.channel":
            return [n.get("channel") or "omni"]
        if key == "pickupInStore":
            return list(n.get("_pickup") or [])
        if key.startswith("attributes."):
            return n["_attrs"].get(key.split(".", 1)[1], [])
        return []

    def matches_facets(self, n: dict, facets: dict[str, list[str]]) -> bool:
        for key, vals in (facets or {}).items():
            if not vals:
                continue
            if key == "price":
                ok = False
                for v in vals:
                    lo, hi = parse_price_value(v)
                    if n["price"] >= lo and (hi is None or n["price"] < hi):
                        ok = True
                if not ok:
                    return False
            else:
                have = self.facet_values(n, key)
                if key == "categories":
                    if not any(h == v or h.startswith(v + " >") for v in vals for h in have):
                        return False
                elif not set(vals) & set(have):
                    return False
        return True

    def text_search(self, q: str) -> list[tuple[float, dict]]:
        terms = [t for t in re.split(r"[\s,]+", q.lower()) if t]
        out: list[tuple[float, dict]] = []
        for n in self.items.values():
            if not terms:
                out.append((n["rating"] + (0.5 if n["on_sale"] else 0), n))
                continue
            score = 0.0
            for t in terms:
                if t not in n["_hay"] and len(t) > 3 and t.endswith("s"):
                    t = t[:-1]
                if t in n["_hay_title"]:
                    score += 3
                elif t in n["_hay"]:
                    score += 1
                else:
                    score = -1
                    break
            if score > 0:
                out.append((score + n["rating"] / 10, n))
        out.sort(key=lambda x: -x[0])
        return out

    def suggest(self, q: str, limit: int = 8) -> list[str]:
        ql = q.strip().lower()
        if not ql:
            return []
        seen: list[str] = []
        cats = sorted({n["category"] for n in self.items.values()} |
                      {n["category_path"].split(">")[-1].strip() for n in self.items.values()})
        for c in cats:
            if c.lower().startswith(ql) and c not in seen:
                seen.append(c)
        for n in self.items.values():
            t = n["title"]
            for cand in (t,):
                if ql in cand.lower() and cand not in seen:
                    seen.append(cand)
            if len(seen) >= limit * 3:
                break
        seen.sort(key=lambda s: (not s.lower().startswith(ql), len(s)))
        return seen[:limit]


def default_data_file() -> str:
    env = os.environ.get("DATA_FILE")
    if env:
        return env
    for cand in (SITE_DIR / "data" / "products_retail.jsonl", SITE_DIR.parent / "data" / "products_retail.jsonl"):
        if cand.exists():
            return str(cand)
    return ""


# ---------------------------------------------------------------------------
# autocomplete suggestions
# ---------------------------------------------------------------------------
class Suggestions:
    """Local autocomplete index from data/autocomplete_suggestions.jsonl ({suggestion, score})."""

    def __init__(self, path: str | None):
        self.items: list[tuple[str, float]] = []
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("suggestion"):
                        self.items.append((str(d["suggestion"]), float(d.get("score") or 0)))
            self.items.sort(key=lambda x: -x[1])
            log.info("Suggestions loaded %d from %s", len(self.items), path)

    def __len__(self) -> int:
        return len(self.items)

    def match(self, q: str, limit: int = 10) -> list[str]:
        ql = (q or "").strip().lower()
        if not ql or not self.items:
            return []
        variants = {ql}
        prefix, sub = [], []
        for s, _ in self.items:
            sl = s.lower()
            if any(sl.startswith(v) for v in variants):
                prefix.append(s)
            elif any(v in sl for v in variants):
                sub.append(s)
            if len(prefix) >= limit:
                break
        out: list[str] = []
        for s in prefix + sub:
            if s not in out and s.lower() != ql:
                out.append(s)
        return out[:limit]


def default_suggestions_file() -> str:
    env = os.environ.get("SUGGESTIONS_FILE")
    if env:
        return env
    data = default_data_file()
    if data:
        cand = Path(data).parent / "autocomplete_suggestions.jsonl"
        if cand.exists():
            return str(cand)
    for cand in (SITE_DIR / "data" / "autocomplete_suggestions.jsonl", SITE_DIR.parent / "data" / "autocomplete_suggestions.jsonl"):
        if cand.exists():
            return str(cand)
    return ""


STOPWORDS = {"a", "an", "the", "for", "with", "and", "or", "of", "to", "in", "on", "my", "me", "i", "some", "any",
             "that", "this", "is", "are", "want", "need", "looking", "find", "show", "good", "best", "please", "something"}


def keyword_terms(query: str, vocab: list[str], limit: int = 3) -> list[str]:
    """Known vocabulary terms contained in a natural-language query (longest, non-overlapping first).
    e.g. 'a non-stick frying pan for my kitchen' -> ['frying pan', 'kitchen']"""
    q = " " + re.sub(r"[^a-z0-9&' -]+", " ", (query or "").lower()) + " "
    found = []
    for v in vocab:
        vl = v.lower()
        if len(vl) < 3 or vl in STOPWORDS:
            continue
        forms = {vl, vl[:-1] if vl.endswith("s") and len(vl) > 3 else vl}
        if any(f" {f} " in q or f" {f}s " in q for f in forms):
            found.append(min(forms, key=len))
    found = sorted(set(found), key=len, reverse=True)
    out: list[str] = []
    for v in found:
        if not any(v in o for o in out):
            out.append(v)
        if len(out) >= limit:
            break
    return out


def build_vocab(catalog: "Catalog", suggestions: "Suggestions") -> list[str]:
    v: set[str] = set()
    for n in catalog.items.values():
        v.add(n["category"])
        for part in (n.get("category_path") or "").split(">"):
            part = part.strip()
            if part:
                v.add(part)
                for p in re.split(r"\s*(?:&|/|,| and )\s*", part):
                    if p.strip():
                        v.add(p.strip())
    for s, _ in suggestions.items:
        if len(s.split()) <= 3 and len(s) <= 24:
            v.add(s)
    return sorted(x for x in v if x)
