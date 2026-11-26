"""Home-page recommendations from the local catalog + persona files (Retail recommendations are not available).

GET /api/recommendations?type=for_you|buy_again|sale|seasonal|new|popular&limit=

- for_you    purchase-history frequency (categories / repeat products) + family preferences (like_keywords) +
             favourite categories, small boost for sale / in-season items. Allergen keywords and bulk (food-service) sizes are excluded.
- buy_again  most frequently purchased products ("Buy it again")
- sale       on-sale items by discount rate (persona-relevant first)
- seasonal   attributes.is_seasonal = true
- new        general merchandise (Kitchen, Beauty & Personal Care, Pet, Home & Living), round-robin across the four categories
- popular    rating × log(review count) ranking

Persona files: env PERSONA_DIR, else `<site>/data/personas/` (staged by deploy.sh), else `<repo>/seed/personas/`.
Each item = front-end Product + {reason, tags[, rank, purchase_count]}.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import os
from pathlib import Path
from typing import Any

from .catalog import Catalog, public

log = logging.getLogger("cymbal-site.recommend")

SITE_DIR = Path(__file__).resolve().parent.parent
NEW_CATEGORIES = ["Kitchen", "Beauty & Personal Care", "Pet", "Home & Living"]
TYPES = ("for_you", "buy_again", "sale", "seasonal", "new", "popular")
TITLES = {
    "for_you": "Recommended for {name}",
    "buy_again": "Buy it again",
    "sale": "This week's deals",
    "seasonal": "In season now",
    "new": "New arrivals: Kitchen, Beauty, Pet & Home",
    "popular": "Best sellers",
}
BULK_WORDS = ("food service", "food-service", "bulk", "commercial size")


def persona_dir() -> Path | None:
    env = os.environ.get("PERSONA_DIR")
    for cand in ([Path(env)] if env else []) + [SITE_DIR / "data" / "personas", SITE_DIR.parent / "seed" / "personas"]:
        if (cand / "personas.json").exists():
            return cand
    return None


def _md(date: str) -> str:
    """'2026-11-02' -> 'Nov 2'"""
    try:
        import datetime as _dt
        d = _dt.date.fromisoformat(str(date)[:10])
        return d.strftime("%b ") + str(d.day)
    except ValueError:
        return str(date)[:10]


def _attr_true(n: dict, key: str) -> bool:
    return any(str(v).lower() == "true" for v in n["_attrs"].get(key, []))


class Recommender:
    def __init__(self, catalog: Catalog, pdir: Path | None = None):
        self.catalog = catalog
        self.personas: dict[str, dict] = {}
        self.history: dict[str, list[dict]] = collections.defaultdict(list)
        pdir = pdir if pdir is not None else persona_dir()
        if pdir:
            try:
                for p in json.loads((pdir / "personas.json").read_text(encoding="utf-8")):
                    self.personas[p["customer_id"]] = p
            except Exception as e:  # noqa: BLE001
                log.warning("personas.json unreadable: %s", e)
            hp = pdir / "purchase_history.jsonl"
            if hp.exists():
                for line in hp.read_text(encoding="utf-8").splitlines():
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.history[r.get("customer_id", "")].append(r)
        log.info("recommender: %d personas, %d history rows (dir=%s)", len(self.personas),
                 sum(len(v) for v in self.history.values()), pdir)
        self._cache: dict[tuple, dict] = {}

    # ------------------------------------------------------------------ helpers
    def _excluded(self, n: dict, persona: dict | None) -> bool:
        if not n.get("image") or n.get("availability") == "OUT_OF_STOCK":
            return True
        title = n["title"]
        if any(w in title.lower() for w in BULK_WORDS):
            return True
        if persona:
            hay = n["_hay"]
            if any(k.lower() in hay for k in persona.get("exclude_keywords", [])):
                return True
        return False

    @staticmethod
    def _dedupe(scored: list[tuple[float, dict, dict]], limit: int, per_sub: int = 2) -> list[dict]:
        """highest score first, one product per image (variants share images), at most `per_sub` per sub-category"""
        seen, out = set(), []
        subs: collections.Counter = collections.Counter()
        for _, n, extra in sorted(scored, key=lambda t: -t[0]):
            sub = " > ".join(n["category_path"].split(" > ")[:2])
            if n["image"] in seen or (per_sub and subs[sub] >= per_sub):
                continue
            seen.add(n["image"])
            subs[sub] += 1
            out.append({**public(n), **extra})
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _popularity(n: dict) -> float:
        return float(n["rating"] or 0) * math.log10(10 + (n["rating_count"] or 0))

    def _signals(self, customer_id: str) -> dict:
        hist = self.history.get(customer_id, [])
        prod = collections.Counter(r["product_id"] for r in hist)
        sub = collections.Counter(" > ".join(r.get("category", "").split(" > ")[:2]) for r in hist)
        top = collections.Counter(r.get("category", "").split(" > ")[0] for r in hist)
        return {"products": prod, "subcats": sub, "topcats": top}

    # ------------------------------------------------------------------ types
    def recommend(self, kind: str, customer_id: str, limit: int = 16) -> dict:
        kind = kind if kind in TYPES else "for_you"
        limit = max(1, min(int(limit or 16), 48))
        key = (kind, customer_id, limit)
        if key in self._cache:
            return self._cache[key]
        persona = self.personas.get(customer_id)
        items = getattr(self, "_" + kind)(persona, customer_id, limit)
        name = ((persona or {}).get("name") or "you").split()[0]
        out = {"type": kind, "title": TITLES[kind].format(name=name), "customer_id": customer_id,
               "source": "local-catalog+persona", "items": items}
        self._cache[key] = out
        return out

    def _for_you(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        sig = self._signals(cid)
        likes: list[tuple[str, str, list[str]]] = []  # (call_name, label, keywords)
        for f in (persona or {}).get("family_preferences", []):
            for lk in f.get("like_keywords", []):
                likes.append((f.get("call_name") or f.get("name", ""), lk.get("label", ""), lk.get("keywords", [])))
        fav = set((persona or {}).get("favorite_categories", []))
        max_sub = max(sig["subcats"].values(), default=1)
        scored = []
        for n in self.catalog.items.values():
            if self._excluded(n, persona) or n["category"] in NEW_CATEGORIES:
                continue
            score, reason, tags = 0.0, "", []
            sub = " > ".join(n["category_path"].split(" > ")[:2])
            if sig["subcats"].get(sub):
                score += 2.0 * sig["subcats"][sub] / max_sub
                reason = "You often buy " + sub.split(" > ")[-1]
                tags.append("Frequently bought")
            hit = None
            for who, label, kws in likes:
                if any(k and k.lower() in n["_hay_title"] for k in kws):
                    hit = (who, label)
                    break
            if hit:
                score += 2.5
                reason = f"{hit[0]} likes: {hit[1]}"
                tags.append("Family favorite")
            if not score:
                continue
            if any(f and f[:2] in n["category"] for f in fav):
                score += 0.3
            if n["on_sale"]:
                score += 0.6
                tags.append("On sale")
            if _attr_true(n, "is_seasonal"):
                score += 0.5
                tags.append("In season")
            if sig["products"].get(n["id"]):
                score -= 1.0  # buy_again row covers repeat products → favour discovery here
            score += self._popularity(n) * 0.08
            scored.append((score, n, {"reason": reason, "tags": list(dict.fromkeys(tags))}))
        ranked = self._dedupe(scored, limit * 3)
        # diversity: round-robin over the reasons (family members' likes / frequent categories)
        groups: dict[str, list[dict]] = {}
        for p in ranked:
            groups.setdefault(p["reason"].split(":")[0], []).append(p)
        out: list[dict] = []
        while len(out) < limit and any(groups.values()):
            for g in list(groups.values()):
                if g and len(out) < limit:
                    out.append(g.pop(0))
        return out

    def _buy_again(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        sig = self._signals(cid)
        last: dict[str, str] = {}
        for r in self.history.get(cid, []):
            last[r["product_id"]] = max(last.get(r["product_id"], ""), r.get("order_date", ""))
        scored = []
        for pid, cnt in sig["products"].items():
            n = self.catalog.items.get(pid)
            if not n or self._excluded(n, persona):
                continue
            tags = ["Frequently bought"] + (["On sale"] if n["on_sale"] else [])
            scored.append((cnt + (0.01 if n["on_sale"] else 0), n,
                           {"reason": f"Bought {cnt} time{'s' if cnt != 1 else ''}" + (f" • last on {_md(last[pid])}" if last.get(pid) else ""),
                            "tags": tags, "purchase_count": cnt}))
        return self._dedupe(scored, limit)

    def _sale(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        sig = self._signals(cid)
        scored = []
        for n in self.catalog.items.values():
            if not n["on_sale"] or self._excluded(n, persona) or n["price"] < 150:
                continue
            rel = 0.12 if sig["topcats"].get(n["category"]) else 0.0
            pct = round((n["discount_rate"] or 0) * 100)
            scored.append((n["discount_rate"] + rel + self._popularity(n) * 0.03 + min(n["price"], 2000) / 20000, n,
                           {"reason": f"{pct}% off • reg. ¥{n['original_price']:,}" if pct else "This week's deal", "tags": ["On sale"]}))
        return self._dedupe(scored, limit)

    def _seasonal(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        scored = []
        for n in self.catalog.items.values():
            if not _attr_true(n, "is_seasonal") or self._excluded(n, persona):
                continue
            tags = ["In season"] + (["On sale"] if n["on_sale"] else [])
            fresh = 1.2 if any(w in n["category"] for w in ("Produce", "Seafood", "Fruit", "Vegetable")) else 0.0
            scored.append((self._popularity(n) + fresh + (0.4 if n["on_sale"] else 0), n,
                           {"reason": "In season • " + (n["category_path"].split(" > ")[-1] or n["category"]), "tags": tags}))
        return self._dedupe(scored, limit)

    def _new(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        per_cat: dict[str, list[tuple[float, dict, dict]]] = {c: [] for c in NEW_CATEGORIES}
        for n in self.catalog.items.values():
            if n["category"] in per_cat and not self._excluded(n, persona):
                per_cat[n["category"]].append((self._popularity(n), n, {"reason": "New • " + n["category"], "tags": ["New"]}))
        lists = [self._dedupe(v, limit) for v in per_cat.values()]
        out: list[dict] = []
        for i in range(limit):
            for lst in lists:
                if i < len(lst) and len(out) < limit:
                    out.append(lst[i])
        return out

    def _popular(self, persona: dict | None, cid: str, limit: int) -> list[dict]:
        scored = [(self._popularity(n), n, {"reason": f"★{n['rating']} ({n['rating_count']:,} reviews)", "tags": []})
                  for n in self.catalog.items.values() if n["rating_count"] >= 30 and not self._excluded(n, persona)]
        out = self._dedupe(scored, limit, per_sub=1)
        for i, p in enumerate(out):
            p["rank"] = i + 1
        return out


def _demo(argv: list[str]) -> Any:  # pragma: no cover - manual check helper
    from .catalog import default_data_file

    r = Recommender(Catalog(default_data_file()))
    for t in TYPES:
        res = r.recommend(t, "CR-0001", 6)
        print(t, [(p["title"][:18], p["reason"]) for p in res["items"]])


if __name__ == "__main__":  # pragma: no cover
    import sys

    _demo(sys.argv)
