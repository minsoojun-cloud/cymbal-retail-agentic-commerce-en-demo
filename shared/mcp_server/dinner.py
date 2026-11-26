"""Dinner-set composer for recommend_dinner_set (CONTRACT v2, English catalog v14).

Builds "tonight's dinner" product sets (Main / Side / Soup / Staple / Dessert & fruit) from:
  - persona: household size, family_preferences (per-member like_keywords), allergies / exclude_keywords, dislikes,
    cooking style (quick meals)
  - purchase-history frequency (product level + leaf-category level)
  - current sale items (attributes.on_sale) and seasonal items (attributes.is_seasonal / season_months)

Pure functions only (no I/O) so that it is fully unit-testable; server.py supplies the catalog pool, the profile and
the history rows. Deterministic for (customer, date, request) but varies by day through a small hash jitter.

Category matching is keyword based on the English category paths of shared/catalog/category_map_en.json
(e.g. "Seafood > Salmon", "Meat > Pork", "Deli & Meal Kits > Meal Kits", "Rice & Noodles > Packaged Rice"), so small
wording differences in sub-categories do not break the classification. store_only products are never proposed
(dinner sets go into the delivery cart).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from dataclasses import dataclass, field

JST = dt.timezone(dt.timedelta(hours=9))

ROLE_MAIN, ROLE_SIDE, ROLE_SOUP, ROLE_STAPLE, ROLE_DESSERT = "Main", "Side", "Soup", "Staple", "Dessert & fruit"
ROLE_ORDER = [ROLE_MAIN, ROLE_SIDE, ROLE_SOUP, ROLE_STAPLE, ROLE_DESSERT]
TAG_FREQ, TAG_FAMILY, TAG_SALE, TAG_SEASON, TAG_QUICK = "Frequently bought", "Family favorite", "On sale", "In season", "Quick"
TAG_ORDER = [TAG_FREQ, TAG_FAMILY, TAG_SALE, TAG_SEASON, TAG_QUICK]
REASON_SEP = "; "


def _rx(words) -> re.Pattern:
    """Word-start regex (case-insensitive) for a list of phrases; plural/suffix forms still match."""
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")", re.I)


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text or "", re.I) is not None


# Always excluded from dinner sets (persona dislike "oversized bulk packs" + non-dinner items).
BULK_RE = re.compile(r"\b(?:bulk|value pack|value size|economy size|family value|catering|commercial|food ?service|"
                     r"wholesale|case of|per case|gift|bundle|outlet|imperfect)", re.I)
NON_DINNER_RE = re.compile(r"\b(?:bento|lunch ?box|rice balls?|onigiri|sandwich|osechi|new year|party|platter|"
                           r"hors d|baby|infant|toddler|months\+|from \d+ months|weaning|trimmings|fish heads?|"
                           r"collars?|sashimi|sushi|furikake|ochazuke|rice seasoning)", re.I)
# Allergen spelling variants (persona exclude_keywords are merged on top). Matched as whole words.
ALLERGEN_VARIANTS = {
    "shrimp": ["shrimp", "prawn", "ebi", "sakura shrimp", "sweet shrimp", "shrimp chips", "shrimp crackers"],
    "crab": ["crab", "kani"],
    "egg": ["egg", "omelet", "omelette", "tamagoyaki", "dashimaki"],
    "milk": ["milk", "cheese", "butter", "cream", "dairy"],
    "wheat": ["wheat", "flour"],
    "buckwheat": ["buckwheat", "soba"],
    "peanut": ["peanut"],
    "walnut": ["walnut"],
}
DISLIKE_HINTS = {
    "extra hot": ["extra hot", "extra spicy", "super spicy", "fiery", "mala", "habanero", "ghost pepper"],
    "very spicy": ["extra hot", "extra spicy", "super spicy", "fiery", "mala", "habanero"],
    "mala": ["mala", "sichuan pepper"],
}
QUICK_RE = re.compile(r"\b(?:meal kits?|just (?:bake|fry|heat|grill|microwave)|ready to (?:cook|heat|eat|fry)|"
                      r"pre-?seasoned|marinated|seasoned|pre-?cut|cut vegetables?|microwav|boneless|"
                      r"cooked in-store|store-made|freeze-dried|instant|pouch|thaw|no prep|deli)", re.I)
# Seasonal nouns (late autumn / winter). Order matters: specific phrases first. The last entry is used only as a
# seasonal *ingredient*, not as the "In-season <noun>" reason (see season_reason).
SEASON_NOUNS = ["autumn salmon", "pacific saury", "saury", "bonito", "yellowtail", "mackerel", "cod", "oyster",
                "sweet potato", "chestnut", "kabocha", "pumpkin", "lotus root", "burdock", "taro", "shimeji", "maitake",
                "shiitake", "enoki", "mushroom", "napa cabbage", "chinese cabbage", "daikon", "leek", "shungiku",
                "spinach", "komatsuna", "broccoli", "persimmon", "apple", "grape", "shine muscat", "pear",
                "la france", "mandarin", "kiwi", "chicken meatball", "hot pot soup"]
MAIN_NOUNS = ["autumn salmon", "coho salmon", "sockeye salmon", "salted salmon", "salmon", "salted mackerel",
              "horse mackerel", "atka mackerel", "spanish mackerel", "mackerel", "yellowtail", "saury", "hokke", "cod",
              "flounder", "karaage", "fried chicken", "hamburg steak", "hamburger steak", "hamburg", "ginger pork",
              "tonkatsu", "pork cutlet", "sliced pork", "pork loin", "pork belly", "chicken thigh", "chicken breast",
              "chicken wings", "sliced beef", "yakitori", "gyoza", "chicken meatball", "tsukune", "yosenabe",
              "kimchi hot pot", "soy milk hot pot", "mizutaki", "sukiyaki", "shabu-shabu", "chanchan-yaki",
              "twice-cooked pork", "mapo", "curry"]
MAIN_NOUN_DISPLAY = {"hamburg": "hamburg steak", "hamburger steak": "hamburg steak"}

# Top-category families (English category_map_en.json top levels; matched on the first path segment).
TOP_FAMILIES = {
    "seafood": r"^(?:seafood|fish)",
    "meat": r"^meat",
    "deli": r"^(?:deli|prepared|meal kit)",
    "frozen": r"^frozen",
    "rice": r"^(?:rice|noodle|instant)",
    "dairy": r"^(?:eggs?|dairy)",
    "tofu": r"^(?:tofu|natto|soy)",
    "produce": r"^(?:produce|vegetables?|fruits?)",
    "pantry": r"^(?:pantry|canned|dry goods)",
    "seasoning": r"^(?:seasonings?|sauces?|condiments?)",
}
# Regexes used by server.py for the BigQuery dinner pool (RE2 syntax).
DINNER_TOP_REGEX = r"(?i)^(seafood|fish|meat|deli|prepared|frozen|rice|noodle|instant|eggs?|dairy|tofu|natto|produce|vegetable|fruit)"
DINNER_EXTRA_REGEX = r"(?i)corn|curry|hot ?pot|nabe"

NUM = str.maketrans("０１２３４５６７８９，", "0123456789,")
WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


# ── product normalisation ────────────────────────────────────────────────────


def _attr_map(attributes) -> dict:
    """Retail-schema attributes (list of {key, value:{text, numbers}} or dict) → {key: [values]}."""
    out = {}
    if isinstance(attributes, dict):
        for k, v in attributes.items():
            if isinstance(v, dict):
                out[k] = list(v.get("text") or v.get("numbers") or [])
            elif isinstance(v, (list, tuple)):
                out[k] = list(v)
            else:
                out[k] = [v]
        return out
    for a in attributes or []:
        v = a.get("value") or {}
        out[a.get("key")] = list(v.get("text") or v.get("numbers") or [])
    return out


def _first(v, default=None):
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return default if v is None else v


def _truthy(v) -> bool:
    return str(_first(v, "")).lower() == "true"


def normalize_product(row: dict) -> dict | None:
    """BigQuery cymbal.products / data/products_retail.jsonl row (Retail schema) → compact dict."""
    pid = row.get("id")
    if not pid:
        return None
    a = _attr_map(row.get("attributes"))
    cats = list(row.get("categories") or [])
    cat = cats[0] if cats else ""
    parts = [p.strip() for p in cat.split(">")]
    pi = row.get("priceInfo") or {}
    price = int(round(float(pi.get("price") or 0)))
    original = int(round(float(pi.get("originalPrice") or price or 0)))
    if original < price:
        original = price
    imgs = row.get("images") or []
    rating = row.get("rating") or {}
    months = []
    for m in a.get("season_months") or []:
        try:
            months.append(int(float(m)))
        except (TypeError, ValueError):
            pass
    return {
        "id": str(pid),
        "title": row.get("title") or "",
        "category": cat,
        "top": parts[0] if parts else "",
        "mid": parts[1] if len(parts) > 1 else "",
        "leaf": parts[-1] if parts else "",
        "price": price,
        "original_price": original,
        "on_sale": _truthy(a.get("on_sale")) or (0 < price < original),
        "discount_rate": float(_first(a.get("discount_rate"), 0) or 0),
        "seasonal": _truthy(a.get("is_seasonal")),
        "season_months": months,
        "size": str(_first(a.get("size"), "") or ""),
        "allergens": [str(x) for x in a.get("allergens") or [] if str(x).lower() not in ("none", "")],
        "dishes": [str(x) for x in a.get("dishes") or []],
        "is_food": _truthy(a.get("is_food")) if a.get("is_food") else True,
        "channel": str(_first(a.get("channel"), "omni") or "omni"),
        "image": (imgs[0].get("uri") if imgs and isinstance(imgs[0], dict) else "") or "",
        "rating": float(rating.get("averageRating") or 0),
        "rating_count": int(rating.get("ratingCount") or 0),
        "availability": row.get("availability") or "IN_STOCK",
        "description": row.get("description") or "",
    }


def normalize_summary(s: dict) -> dict:
    """search_products summary item (server.summarize_product) → compact dict (Retail-search fallback pool)."""
    cat = (s.get("topCategory") or "") + (" > " + s["subtitle"] if s.get("subtitle") else "")
    parts = [p.strip() for p in cat.split(">")]
    return {
        "id": s.get("productId"), "title": s.get("title") or "", "category": cat, "top": parts[0], "mid": parts[-1],
        "leaf": parts[-1], "price": int(s.get("price") or 0), "original_price": int(s.get("originalPrice") or s.get("price") or 0),
        "on_sale": bool(s.get("onSale")), "discount_rate": float(s.get("discountRate") or 0), "seasonal": False,
        "season_months": [], "size": s.get("size") or "",
        "allergens": [x.strip() for x in str(s.get("allergens") or "").split("/") if x.strip() and x.strip().lower() != "none"],
        "dishes": [], "is_food": True, "channel": s.get("channel") or "omni",
        "image": (s.get("imageUris") or [""])[0], "rating": float((s.get("rating") or {}).get("average") or 0),
        "rating_count": int((s.get("rating") or {}).get("count") or 0), "availability": "IN_STOCK", "description": "",
    }


def family(p: dict) -> str:
    """Top-category family key ('seafood', 'meat', ...) or ''."""
    top = (p.get("top") or "").strip()
    for k, pat in TOP_FAMILIES.items():
        if re.search(pat, top, re.I):
            return k
    return ""


# ── persona helpers ─────────────────────────────────────────────────────────


def exclusion_words(profile: dict) -> list:
    words = set()
    for w in profile.get("exclude_keywords") or []:
        words.add(str(w).lower())
    allergens = [str(a.get("allergen")) for a in profile.get("allergies") or [] if isinstance(a, dict) and a.get("allergen")]
    for fp in profile.get("family_preferences") or []:
        allergens += [str(x) for x in fp.get("allergies") or []]
    for al in allergens:
        al = al.lower().strip()
        words.add(al)
        for k, vs in ALLERGEN_VARIANTS.items():
            if k in al or al in vs:
                words.update(vs)
    return sorted(w for w in words if w)


def dislike_words(profile: dict) -> list:
    out = set()
    for d in profile.get("dislikes") or []:
        for k, vs in DISLIKE_HINTS.items():
            if k in str(d).lower():
                out.update(vs)
    return sorted(out)


def allergy_note(profile: dict) -> str:
    notes = []
    for a in profile.get("allergies") or []:
        if isinstance(a, dict) and a.get("allergen"):
            notes.append(f"{a['allergen']} (registered allergy)")
    return ", ".join(notes)


def family_rules(profile: dict) -> list:
    """→ [(call_name, relation, label, keyword)] from family_preferences[].like_keywords (falls back to likes)."""
    rules = []
    for fp in profile.get("family_preferences") or []:
        call = fp.get("call_name") or fp.get("name", "")
        rel = fp.get("relation", "")
        try:
            if int(fp.get("age") or 99) <= 12 and not is_kid(rel):
                rel = f"{rel} (child)".strip()
        except (TypeError, ValueError):
            pass
        lk = fp.get("like_keywords") or []
        if lk:
            for g in lk:
                for kw in g.get("keywords") or []:
                    rules.append((call, rel, g.get("label") or kw, kw))
        else:
            for like in fp.get("likes") or []:
                rules.append((call, rel, like, like))
    return rules


def is_kid(relation: str) -> bool:
    return _has(r"\b(?:son|daughter|child|children|kid|kids|boy|girl)\b", relation or "")


# ── history ─────────────────────────────────────────────────────────────────

_ORIGIN_WORDS = re.compile(
    r"^(?:domestic|japan|japanese|imported|hokkaido|aomori|nagano|iwate|miyagi|kagoshima|miyazaki|kumamoto|shizuoka|"
    r"chiba|ibaraki|gunma|aichi|kochi|ehime|wakayama|hyogo|shiga|kyoto|osaka|tochigi|yamagata|niigata|okinawa|"
    r"tottori|fukuoka|saga|nagasaki|oita|kagawa|tokushima|okayama|hiroshima|yamaguchi|shimane|fukushima|akita|gifu|"
    r"mie|nara|toyama|ishikawa|fukui|yamanashi|saitama|kanagawa|tokyo|prefecture|philippines?|ecuador|peru|"
    r"retail|value|large|small|half|whole|cut|1/2|1/4|"
    r"canadian|canada|american|us|usa|australian|australia|norwegian|norway|chilean|chile|mexican|mexico|"
    r"new zealand|thai|brazilian|danish|spanish|fresh|approx\.?|about|cymbal|basic|select|organic)$", re.I)
_UNIT_TOKEN = re.compile(r"\d|^(?:pcs?|pieces?|packs?|bunch|head|servings?|portions?|g|kg|ml|l|x|×|ct)$", re.I)


def short_name(title: str) -> str:
    """"Canadian Sliced Pork 400g" → "Sliced Pork"; "[Fresh+] Hokkaido Fresh Autumn Salmon Fillets 2 pcs (approx. 160g)"
    → "Autumn Salmon Fillets"."""
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title or "")
    t = re.sub(r"^[^:]{0,30}:\s*", "", t.split(",")[0])  # "Meal Kit: X" → "X"; drop ", 3 packs (…)" tails
    toks = [x.strip(",.;:") for x in t.split() if x.strip(",.;:")]
    toks = [x for x in toks if not re.search(r"-(?:grown|raised|caught|produced|made)$", x, re.I)]
    toks = [x for x in toks if not _ORIGIN_WORDS.match(x) and not _UNIT_TOKEN.search(x)]
    return " ".join(toks[:5]) or (title or "")[:24]


def history_signals(rows: list) -> dict:
    """Aggregate purchase rows → {by_product:{id: times}, by_leaf:{leaf: (times, short)}, top:[{...}]}."""
    by_product, info, by_leaf = {}, {}, {}
    for r in rows or []:
        pid = str(r.get("product_id") or "")
        if not pid:
            continue
        times = int(r.get("times") or 1)
        by_product[pid] = by_product.get(pid, 0) + times
        info.setdefault(pid, (r.get("product_title") or "", r.get("category") or ""))
    for pid, n in by_product.items():
        title, cat = info[pid]
        leaf = cat.split(">")[-1].strip() if cat else ""
        if not leaf:
            continue
        t, best, best_n = by_leaf.get(leaf, (0, "", 0))
        if n > best_n:
            best, best_n = short_name(title), n
        by_leaf[leaf] = (t + n, best, best_n)
    top = sorted(by_product.items(), key=lambda kv: -kv[1])
    return {
        "by_product": by_product,
        "by_leaf": {k: (v[0], v[1]) for k, v in by_leaf.items()},
        "top": [{"product_id": pid, "title": info[pid][0], "short": short_name(info[pid][0]), "times": n}
                for pid, n in top[:30]],
    }


# ── request parsing ─────────────────────────────────────────────────────────

BUDGET_RE = re.compile(r"(?:¥|jpy\s*)\s*([0-9][0-9,]{2,6})|([0-9][0-9,]{2,6})\s*(?:yen|jpy)\b", re.I)
SERVINGS_RE = re.compile(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight)\s*(?:people|persons?|servings?|portions?)\b"
                         r"|\b(?:for|serves|feed)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight)\b(?!\s*(?:yen|jpy|,|\d))",
                         re.I)


def parse_request(text: str) -> dict:
    t = (text or "").translate(NUM)
    req = {"meat": False, "fish": False, "nabe": False, "curry": False, "quick": False, "light": False,
           "kids": False, "staple": False, "budget": 0, "servings": 0, "text": text or ""}
    if _has(r"\b(?:meat|karaage|fried chicken|hamburg|ginger pork|tonkatsu|pork|beef|chicken|steak|yakiniku|bbq|hearty)", t):
        req["meat"] = True
    if _has(r"\b(?:fish|salmon|mackerel|yellowtail|saury|cod|seafood)", t):
        req["fish"] = True
    if _has(r"\b(?:hot ?pot|nabe|shabu|sukiyaki|something warm|warm me up|warming)", t):
        req["nabe"] = True
    if _has(r"\bcurry", t):
        req["curry"] = True
    if _has(r"\b(?:quick|easy|simple|fast|busy|tired|no time|lazy|minimal effort|(?:5|10|15|20)[ -]?min)", t):
        req["quick"] = True
    if _has(r"\b(?:healthy|light|lots of veg|more veg|veggie|plenty of vegetables)", t):
        req["light"] = True
    if _has(r"\b(?:kids?|children|child)\b", t):
        req["kids"] = True
    if _has(r"\b(?:rice too|with rice|staple|steamed rice|white rice|and rice)\b", t):
        req["staple"] = True
    m = BUDGET_RE.search(t)
    if m and _has(r"\b(?:budget|under|within|less than|below|up to|max|no more than|around|about|keep it)", t):
        try:
            req["budget"] = int((m.group(1) or m.group(2)).replace(",", ""))
        except ValueError:
            pass
    m = SERVINGS_RE.search(t)
    if m:
        v = (m.group(1) or m.group(2) or "").lower()
        n = WORD_NUMS.get(v) or (int(v) if v.isdigit() else 0)
        if 0 < n <= 12:
            req["servings"] = n
    return req


# ── classification ──────────────────────────────────────────────────────────

FISH_MID_RE = r"fish|salmon|mackerel|yellowtail|saury|sardine|cod|white ?fish|fillet|grilled|simmered|marinated|boneless|dried|swordfish|fried"
FISH_TITLE_RE = r"\b(?:salmon|mackerel|yellowtail|saury|cod|hokke|flounder|fillet)"
FISH_EXCL_RE = r"\b(?:canned|can|flake|flaked|whitebait|shirasu|mentaiko|pollock roe|cod roe|tarako|salmon roe|ikura|roe|sea urchin|sashimi|sushi|jerky)"
MEAT_MID_RE = r"pork|chicken|beef|seasoned|marinated|ground|minced|hamburg"
MEAT_MID_EXCL_RE = r"processed|\bham\b|charcuterie|roast|gift|sausage"
MEAT_EXCL_RE = r"\bham\b|sausage|bacon|salad chicken|liver|offal|gizzard|lard|roast beef|char ?siu|jerky"
DELI_MAIN_MID_RE = r"fried|fritter|grilled|meal kit|ready to fry|chinese|gyoza|shumai|dumpling|entree|main"
DELI_MAIN_TITLE_RE = r"hamburg|karaage|fried chicken|tonkatsu|pork cutlet|ginger pork|yakitori"
FROZEN_MAIN_RE = r"karaage|fried chicken|fried|meat|chinese|gyoza|dumpling|japanese prepared|japanese sides|entree|main dish|hamburg"
FROZEN_MAIN_EXCL_RE = r"fried rice|pilaf|noodle|udon|ramen|pizza|dessert|ice cream|vegetable|bento|lunch|fruit"
VEG_MID_RE = r"vegetable|leafy|greens|mushroom|salad|sprout|aromatic|root|organic|heirloom|herb|tomato|beans|local produce"
VEG_EXCL_TITLE_RE = r"for miso soup|for soup|for pork miso|for stew|for simmering|for curry|for hot ?pot"
NABE_VEG_RE = r"napa|chinese cabbage|leek|green onion|negi|shungiku|chrysanthemum|mizuna|shimeji|enoki|shiitake|maitake|mushroom|hot ?pot"
DELI_SIDE_MID_RE = r"salads?|japanese deli|^deli sides|chilled deli|western deli|kansai style deli|small sides|small plates?|egg dish|side dish"
DELI_SIDE_EXCL_RE = r"karaage|tonkatsu|cutlet|hamburg|croquette|bento"
SOUP_MID_RE = r"soup|miso|instant"
SOUP_TITLE_RE = r"miso soup|soup|tonjiru|pork miso|osuimono"
SOUP_EXCL_RE = r"cup noodle|ramen|vermicelli|harusame|bulk|commercial|potage mix"
RICE_MID_RE = r"microwav|packaged rice|cooked rice|ready rice|instant rice|rice packs?|pack rice"
RICE_EXCL_RE = r"takikomi|mixed rice|mushroom|gomoku|red bean|sekihan|fried rice|pilaf|okowa|doria|porridge|congee|risotto|bibimbap|kamameshi|wakame|chirashi|zosui"
UDON_EXCL_RE = r"\bcup\b|soup|broth|okonomi|yaki ?udon|stir-fried|sara udon|instant|curry|tempura|kitsune|nanban|kamo|niku|tanuki|kake"
DESSERT_MID_RE = r"dessert|jell|japanese sweets|pudding|sweets"


def _text(p: dict) -> str:
    return p["title"] + " " + p["category"]


def size_grams(p: dict):
    s = (p.get("size") or "") + " " + p["title"]
    s = s.translate(NUM)
    m = re.search(r"([0-9.]+)\s*kg\b", s, re.I)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"([0-9]+)\s*g(?:rams?)?\b", s)
    if m:
        return float(m.group(1))
    return None


_PIECE_UNITS = r"servings?|portions?|pieces?|pcs|fillets?|slices?|fish|whole|meals?|plates?|sheets?|ct|count|skewers?|patties|steaks?|balls?"


def pieces(p: dict):
    """"4 fillets" / "3-4 servings" / "8 pcs" / "180g x 3" → number of portions (None if unknown)."""
    s = ((p.get("size") or "") + " " + p["title"]).translate(NUM)
    m = re.search(r"([0-9]+)\s*(?:-|~|–|to)\s*([0-9]+)\s*(?:servings?|portions?|people)", s, re.I)
    if m:
        return int(m.group(2))
    m = re.search(r"\b([0-9]+)\s*(?:" + _PIECE_UNITS + r")\b", s, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"[x×]\s*([0-9]+)\b", s, re.I)
    if m:
        return int(m.group(1))
    return None


def serves(p: dict):
    """Number of people a pack serves when stated explicitly: "2 servings", "3-4 servings", "for 2" (None otherwise)."""
    s = ((p.get("size") or "") + " " + p["title"]).translate(NUM)
    m = re.search(r"([0-9]+)\s*(?:-|~|–|to)\s*([0-9]+)\s*(?:servings?|portions?|people)", s, re.I)
    if m:
        return int(m.group(2))
    m = re.search(r"\b([0-9]+)\s*(?:servings?|portions?|people)\b|\bfor\s+([0-9]+)\b(?!\s*(?:g|ml|kg|l)\b)", s, re.I)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def blocked(p: dict, excl: list, dislikes: list) -> bool:
    if p.get("availability", "IN_STOCK") != "IN_STOCK" or not p.get("is_food", True) or p["price"] <= 0:
        return True
    if (p.get("channel") or "omni") == "store_only":  # cannot be delivered
        return True
    hay = " ".join([p["title"], p["category"], " ".join(p["allergens"]), p.get("description", "")])  # not `dishes` (usage ideas)
    if excl and re.search(r"\b(?:" + "|".join(re.escape(w) for w in excl) + r")(?:s|es)?\b", hay, re.I):
        return True
    title_cat = _text(p)
    if dislikes and _rx(dislikes).search(title_cat):
        return True
    if BULK_RE.search(p["title"]):
        return True
    if NON_DINNER_RE.search(title_cat):
        return True
    g = size_grams(p)
    if g and g > 1200:
        return True
    return False


def classify(p: dict) -> set:
    """→ set of (role, kind) the product can fill."""
    t, fam, mid, title = _text(p), family(p), p["mid"], p["title"]
    midleaf = mid + " " + p["leaf"]
    out = set()
    if fam == "seafood" and (_has(FISH_MID_RE, mid) or _has(FISH_TITLE_RE, title)):
        if not _has(FISH_EXCL_RE, title):
            out.add((ROLE_MAIN, "fish"))
    if fam == "meat" and _has(MEAT_MID_RE, mid) and not _has(MEAT_MID_EXCL_RE, mid) and not _has(MEAT_EXCL_RE, title):
        out.add((ROLE_MAIN, "nabe_protein" if _has(r"hot ?pot|nabe", t) else "meat"))
    if fam == "deli" and (_has(DELI_MAIN_MID_RE, mid) or _has(DELI_MAIN_TITLE_RE, title)) and not _has(r"hot ?pot|soup|salad", mid):
        kind = "fish" if _has(r"salmon|mackerel|yellowtail|fish|saury|\bcod\b", title) else "meat"
        out.add((ROLE_MAIN, kind))
    if fam == "frozen" and (_has(FROZEN_MAIN_RE, mid) or _has(r"hamburg|karaage|fried chicken", title)) \
            and not _has(FROZEN_MAIN_EXCL_RE, midleaf + " " + title):
        out.add((ROLE_MAIN, "meat"))
    if fam == "deli" and _has(r"hot ?pot|nabe", mid):
        out.add((ROLE_MAIN, "nabe_set"))
    if _has(r"hot ?pot (?:soup|broth|base|stock)|nabe (?:soup|base|broth)|hot ?pot soups?", t):
        out.add((ROLE_SOUP, "nabe_soup"))
    if _has(r"curry roux|curry sauce mix|curry blocks?", t) or (
            fam == "seasoning" and _has(r"curry", mid) and _has(r"\bcurry", title) and _has(r"roux|block|flake|sauce mix", title)):
        out.add((ROLE_MAIN, "curry_roux"))
    if _has(r"for curry|curry[- ]cut|stew[- ]cut|for stew", title) and fam == "meat":
        out.add((ROLE_MAIN, "curry_meat"))
    if fam == "seafood" and _has(r"hot ?pot|nabe", mid + " " + title) and not _has(FISH_EXCL_RE, title):
        out.add((ROLE_MAIN, "nabe_protein"))
    if fam == "meat" and _has(r"shabu|meatball|tsukune|hot ?pot|nabe", title):
        out.add((ROLE_MAIN, "nabe_protein"))
    if fam == "seafood" and _has(r"\bcod\b", title) and _has(r"fillet|hot ?pot", title) and not _has(FISH_EXCL_RE, title):
        out.add((ROLE_MAIN, "nabe_protein"))
    # sides
    if fam == "produce" and _has(VEG_MID_RE, mid) and not _has(r"^fruits?$", mid):
        if not _has(VEG_EXCL_TITLE_RE, title):
            out.add((ROLE_SIDE, "veg"))
        if _has(NABE_VEG_RE, title):
            out.add((ROLE_SIDE, "nabe_veg"))
    if fam == "deli" and _has(DELI_SIDE_MID_RE, mid) and not _has(DELI_SIDE_EXCL_RE, title):
        out.add((ROLE_SIDE, "deli"))
    if _has(r"tamagoyaki|rolled omelet|japanese omelet|dashimaki|sweet omelet", title) \
            and not _has(r"mentaiko|cod roe|spicy|wasabi", title) and fam in ("frozen", "deli", "dairy"):
        out.add((ROLE_SIDE, "kids"))
    if _has(r"\bcorn\b", title) and fam in ("pantry", "frozen", "produce") and not _has(r"flake|soup|oil|cream|starch|potage|dog", title):
        out.add((ROLE_SIDE, "kids"))
    if fam == "frozen" and _has(r"vegetable", mid) and _has(r"spinach|broccoli|string beans|snap beans|edamame|kabocha|pumpkin|corn|mixed", title):
        out.add((ROLE_SIDE, "veg"))
    if fam == "tofu" and _has(r"\btofu\b", mid) and not _has(r"fried|deep|aburaage|atsuage", mid + " " + title):
        out.add((ROLE_SIDE, "tofu"))
        if _has(r"silken|firm|momen|kinu", title):
            out.add((ROLE_SOUP, "tofu"))
            out.add((ROLE_SIDE, "nabe_tofu"))
    # soup
    if fam == "rice" and (_has(SOUP_MID_RE, mid) or _has(r"miso soup", title)) and _has(SOUP_TITLE_RE, title):
        if not _has(SOUP_EXCL_RE, title):
            out.add((ROLE_SOUP, "instant"))
    if fam == "deli" and _has(r"soup", mid):
        out.add((ROLE_SOUP, "deli"))
    if _has(r"for miso soup", title) or _has(r"\bsoups?\b", p["leaf"]):
        out.add((ROLE_SOUP, "ingredient"))
    # staple
    if fam == "rice" and _has(RICE_MID_RE, midleaf + " " + title) and not _has(RICE_EXCL_RE, title):
        out.add((ROLE_STAPLE, "rice"))
    if fam in ("rice", "frozen") and _has(r"\budon\b", title) and not _has(UDON_EXCL_RE, title):
        out.add((ROLE_STAPLE, "udon"))
    # dessert / fruit
    if fam == "produce" and _has(r"^fruits?$", mid.strip()):
        out.add((ROLE_DESSERT, "fruit"))
    if fam == "dairy" and _has(DESSERT_MID_RE, mid):
        out.add((ROLE_DESSERT, "dessert"))
    return out


# ── scoring ─────────────────────────────────────────────────────────────────


def jitter(*parts) -> float:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _find_noun(nouns: list, title: str):
    for n in nouns:
        if re.search(r"\b" + re.escape(n), title, re.I):
            return n
    return None


def season_noun(p: dict) -> str:
    return _find_noun(SEASON_NOUNS, p["title"]) or short_name(p["title"]).lower()


def season_reason(p: dict) -> str:
    if _has(r"hot ?pot|shabu|nabe", p["title"]) and not _find_noun(SEASON_NOUNS[:-1], p["title"]):
        return "Perfect for hot pot season"
    return f"In-season {season_noun(p)}"


def main_noun(p: dict) -> str:
    n = _find_noun(MAIN_NOUNS, p["title"])
    if n:
        return MAIN_NOUN_DISPLAY.get(n, n).title()
    return short_name(p["title"])


@dataclass
class Ctx:
    profile: dict
    hist: dict
    req: dict
    today: dt.date
    customer_id: str
    servings: int
    rules: list = field(default_factory=list)
    quick_pref: bool = False


def signals_for(p: dict, ctx: Ctx) -> list:
    """→ [(tag, weight, reason)] ordered by weight."""
    out = []
    t = p["title"]
    tl = t.lower()
    n = ctx.hist["by_product"].get(p["id"], 0)
    if n >= 2:
        out.append((TAG_FREQ, 2.5 + min(n, 10) * 0.12, f"You buy {short_name(t)} often"))
    else:
        lt = ctx.hist["by_leaf"].get(p["leaf"])
        if lt and lt[0] >= 4:
            out.append((TAG_FREQ, 1.3, f"Same staple as your usual {lt[1]}"))
    seen_member = set()
    for call, rel, label, kw in ctx.rules:
        if call in seen_member or not re.search(r"\b" + re.escape(str(kw).lower()), tl):
            continue
        seen_member.add(call)
        w = 2.4 if is_kid(rel) else 1.6
        out.append((TAG_FAMILY, w, f"{call}'s favorite: {label}"))
    if p["on_sale"]:
        d = int(round(p["discount_rate"])) if p["discount_rate"] else 0
        out.append((TAG_SALE, 1.0 + min(d, 40) / 50, f"On sale this week ({d}% off)" if d else "On sale this week"))
    in_season = p["seasonal"] or (ctx.today.month in p["season_months"])
    if in_season:
        out.append((TAG_SEASON, 1.6, season_reason(p)))
    if QUICK_RE.search(t) or QUICK_RE.search(p["category"]):
        w = 2.2 if ctx.req["quick"] else (0.7 if ctx.quick_pref else 0.3)
        out.append((TAG_QUICK, w, "No prep needed, saves time"))
    return sorted(out, key=lambda x: -x[1])


SINGLE_SERVING_RE = re.compile(r"(?<![0-9~\-–])\b1\s*(?:servings?|portions?|meals?)\b(?!\s*[x×])", re.I)
SINGLE_PIECE_DESSERT_RE = re.compile(r"(?<![0-9~\-–])\b1\s*(?:pcs?|pieces?|cups?|bottles?)\b(?!\s*[x×])", re.I)
MULTI_PACK_RE = re.compile(r"[x×]\s*\d|\d+\s*-?\s*(?:pack|count|ct)\b|\bpack of\b|\bmulti-?pack|\d+\s*(?:pcs|pieces|servings|meals)\s*(?:per|in)\b", re.I)


def portion_qty(p: dict, role: str, servings: int) -> int:
    """Packs needed for `servings` people. Family/multi packs (3-4 servings, 5-pack, 1 bunch, 4 fillets …) → 1; only
    clearly single-serving items (1 serving, 1 meal, a single pudding …) scale with servings; mains with small packs
    scale by portions."""
    if servings <= 0:
        return 1
    txt = ((p.get("size") or "") + " " + p["title"]).translate(NUM)
    multi = bool(MULTI_PACK_RE.search(txt))
    if not multi and SINGLE_SERVING_RE.search(txt):
        return max(1, min(servings, 6))
    if role == ROLE_DESSERT and family(p) != "produce" and not multi and SINGLE_PIECE_DESSERT_RE.search(txt):
        return max(1, min(servings, 6))
    if role != ROLE_MAIN:
        # side / soup / staple packs that state how many people they serve ("approx. 400 g (2 servings)") scale to the
        # household; everything else (bunches, bags, family packs) stays 1
        k = serves(p)
        if k and k < servings and not multi:
            return max(1, min(3, math.ceil(servings / k)))
        return 1
    n = pieces(p)
    if n and n < 20:
        return max(1, min(3, math.ceil(servings / n)))
    g = size_grams(p)
    if g and family(p) in ("meat", "seafood", "deli", "frozen"):
        per = 80 if _has(r"for curry|curry[- ]cut", p["title"]) else 90
        return max(1, min(3, math.ceil(servings * per / g)))
    return 1


def score(p: dict, role: str, kind: str, ctx: Ctx, extra: float = 0.0) -> float:
    s = sum(w for _, w, _ in signals_for(p, ctx)) + extra
    if p["rating_count"]:
        s += (p["rating"] - 3.8) * 0.5
    if role == ROLE_MAIN:
        n = pieces(p)
        if n and ctx.servings and ctx.servings <= n <= ctx.servings + 2:
            s += 0.8
        elif n and n < ctx.servings / 2:
            s -= 0.6
    s += jitter(ctx.customer_id, ctx.today.isoformat(), ctx.req["text"], p["id"]) * (1.6 if role == ROLE_MAIN else 0.9)
    if role == ROLE_MAIN and (p["seasonal"] or ctx.today.month in p["season_months"]):
        s += 0.8  # prefer an in-season main
    if ctx.req["budget"]:
        s -= p["price"] * portion_qty(p, role, ctx.servings) / max(ctx.req["budget"], 1) * 3.0
    elif role == ROLE_MAIN and p["price"] * portion_qty(p, role, ctx.servings) > 2500:
        s -= 1.0
    if ctx.req["light"] and role in (ROLE_SIDE,) and kind == "veg":
        s += 0.8
    return s


# ── composition ─────────────────────────────────────────────────────────────

# Set templates: list of slots (role, allowed kinds, bonus keywords regex or None).
TEMPLATES = {
    "fish": [(ROLE_MAIN, {"fish"}, r"autumn salmon|salmon|mackerel|yellowtail"), (ROLE_SIDE, {"kids"}, None),
             (ROLE_SIDE, {"veg", "deli"}, r"mushroom|shimeji|maitake|spinach|komatsuna|ohitashi|dressed|aemono"),
             (ROLE_SOUP, {"instant", "deli", "tofu"}, r"miso soup|tonjiru|pork miso"), (ROLE_DESSERT, {"fruit"}, None)],
    "meat": [(ROLE_MAIN, {"meat"}, r"karaage|fried chicken|hamburg|ginger pork|sliced pork|chicken thigh|tonkatsu|pork cutlet"),
             (ROLE_SIDE, {"veg", "deli", "kids"}, r"salad|cabbage|broccoli|corn"),
             (ROLE_SOUP, {"instant", "deli", "tofu"}, r"miso soup|tonjiru|soup"), (ROLE_STAPLE, {"rice"}, None),
             (ROLE_DESSERT, {"fruit", "dessert"}, None)],
    "nabe": [(ROLE_MAIN, {"nabe_protein", "nabe_set"}, r"hot ?pot|meatball|tsukune|shabu"),
             (ROLE_SIDE, {"nabe_veg"}, r"napa|chinese cabbage|shimeji|leek|enoki"),
             (ROLE_SOUP, {"nabe_soup"}, r"yosenabe|soy milk|mizutaki|chanko"), (ROLE_STAPLE, {"udon"}, None),
             (ROLE_DESSERT, {"fruit"}, None)],
    "curry": [(ROLE_MAIN, {"curry_roux"}, r"medium[- ]hot|medium spicy|medium"), (ROLE_MAIN, {"curry_meat"}, r"pork|chicken|beef"),
              (ROLE_SIDE, {"veg", "deli", "kids"}, r"salad|corn|broccoli"), (ROLE_STAPLE, {"rice"}, None),
              (ROLE_DESSERT, {"fruit"}, None)],
}
CONCEPTS = {
    "fish": "A Japanese-style set starring {main}, with {side} and {soup} for plenty of vegetables. Finish with {dessert}.",
    "meat": "Hearty {main} the whole family will love, balanced with {side} and {soup}.",
    "nabe": "A warming hot pot of {main_short} and {side} in {soup}. Finish the pot with {staple}.",
    "curry": "Medium-hot curry the whole family can enjoy, served with {side}. Go for a big helping of rice!",
}
NAMES = {"fish": "{noun} Japanese Set Meal", "meat": "{noun} Family Dinner", "nabe": "{noun} Hot Pot Night",
         "curry": "Medium-Hot Curry Family Dinner"}


def _pick(pool: list, role: str, kinds: set, bonus_re, ctx: Ctx, used: set, used_other: set, budget_left=None):
    best, best_s = None, -1e9
    for p, roles in pool:
        if p["id"] in used:
            continue
        ks = {k for r, k in roles if r == role}
        if not ks & kinds:
            continue
        extra = 0.0
        if bonus_re and re.search(bonus_re, p["title"], re.I):
            extra += 1.0
        if p["id"] in used_other:
            extra -= 2.0
        kind = sorted(ks & kinds)[0]
        if kind == "kids":
            extra += 1.0
        if budget_left is not None and p["price"] * portion_qty(p, role, ctx.servings) > budget_left:
            continue
        s = score(p, role, kind, ctx, extra)
        if s > best_s:
            best, best_s = (p, kind), s
    return best


def _item(p: dict, role: str, ctx: Ctx) -> dict:
    sig = signals_for(p, ctx)
    tags = sorted({t for t, _, _ in sig}, key=TAG_ORDER.index)
    reasons = []
    for t, _, r in sig:
        if r not in reasons:
            reasons.append(r)
        if len(reasons) == 2:
            break
    if not reasons:
        defaults = {ROLE_MAIN: f"Right amount for {ctx.servings}" + (f" ({p['size']})" if p.get("size") else ""),
                    ROLE_SIDE: "Adds color and vegetables", ROLE_SOUP: "Rounds out the meal with a soup",
                    ROLE_STAPLE: "Rice for big appetites", ROLE_DESSERT: "A sweet finish after dinner"}
        reasons.append(defaults.get(role, "Goes well with this menu"))
    qty = portion_qty(p, role, ctx.servings)
    if qty > 1:
        unit = "pcs" if role == ROLE_DESSERT else ("servings" if SINGLE_SERVING_RE.search(p["title"] + " " + (p.get("size") or "")) else "packs")
        reasons = [f"{qty} {unit} for {ctx.servings} people"] + reasons[:1]
    return {"role": role, "product_id": p["id"], "title": p["title"], "price": p["price"], "quantity": qty,
            "subtotal": p["price"] * qty, "image": p["image"], "on_sale": p["on_sale"], "tags": tags,
            "reason": REASON_SEP.join(reasons)}


def _noun1(title: str) -> str:
    for n in ("sweet omelet", "tamagoyaki", "omelet", "corn", "potato salad", "salad", "udon", "packaged rice", "rice"):
        if re.search(r"\b" + re.escape(n), title, re.I):
            return n
    n = _find_noun(SEASON_NOUNS, title)
    if n:
        return n
    return short_name(title).lower()


def _soup_noun(title: str) -> str:
    for n, disp in (("pork miso soup", "pork miso soup"), ("tonjiru", "tonjiru"), ("miso soup", "miso soup"),
                    ("clear soup", "clear soup"), ("hot pot", "hot pot broth"), ("nabe", "hot pot broth"),
                    ("soup", "soup")):
        if re.search(r"\b" + re.escape(n), title, re.I):
            return disp
    if _has(r"\btofu\b", title):
        return "tofu miso soup"
    return short_name(title).lower()


def compose_set(kind: str, pool: list, ctx: Ctx, used_other: set) -> dict | None:
    slots = list(TEMPLATES[kind])
    want_staple = ctx.req["staple"] or kind in ("curry", "nabe")
    used, items = set(), []
    budget_left = ctx.req["budget"] or None
    for role, kinds, bonus in slots:
        if role == ROLE_STAPLE and not want_staple:
            continue
        got = _pick(pool, role, kinds, bonus, ctx, used, used_other, budget_left)
        if not got and role == ROLE_SIDE and "kids" in kinds:  # no kid-friendly side → any side
            got = _pick(pool, role, {"veg", "deli"}, bonus, ctx, used, used_other, budget_left)
        if not got:
            continue
        p, _ = got
        used.add(p["id"])
        it = _item(p, role, ctx)
        items.append(it)
        if budget_left is not None:
            budget_left -= it["subtotal"]
    items = items[:5]
    mains = [i for i in items if i["role"] == ROLE_MAIN]
    if not mains or len(items) < 4:
        return None
    items.sort(key=lambda i: ROLE_ORDER.index(i["role"]))
    by_role = {}
    for i in items:
        by_role.setdefault(i["role"], i)
    pmap = {p["id"]: p for p, _ in pool}
    noun = main_noun(pmap[mains[0]["product_id"]])
    fmt = {
        "main": noun,
        "main_short": re.sub(r"\s*\bhot pot\b(?:\s+kit)?", "", noun, flags=re.I).strip() or noun,
        "side": _noun1(by_role[ROLE_SIDE]["title"]) if ROLE_SIDE in by_role else "a side dish",
        "soup": _soup_noun(by_role[ROLE_SOUP]["title"]) if ROLE_SOUP in by_role else "a soup",
        "dessert": season_noun(pmap[by_role[ROLE_DESSERT]["product_id"]]) if ROLE_DESSERT in by_role else "fruit",
        "staple": _noun1(by_role[ROLE_STAPLE]["title"]) if ROLE_STAPLE in by_role else "udon",
    }
    name_noun = re.sub(r"\s*\bhot pot\b(?:\s+kit)?", "", noun, flags=re.I).strip() if kind == "nabe" else noun
    return {"kind": kind, "name": NAMES[kind].format(noun=name_noun or noun), "concept": CONCEPTS[kind].format(**fmt),
            "items": items, "total_tax_included": sum(i["subtotal"] for i in items)}


def choose_kinds(req: dict, today: dt.date) -> list:
    if req["nabe"]:
        order = ["nabe", "fish", "meat"]
    elif req["curry"]:
        order = ["curry", "meat", "fish"]
    elif req["meat"] and not req["fish"]:
        order = ["meat", "curry" if today.toordinal() % 2 else "nabe", "fish"]
    elif req["fish"] and not req["meat"]:
        order = ["fish", "nabe", "meat"]
    else:
        # default: fish-centred Japanese set meal (Sakura) first; alternatives meat (Ken & the kids) + nabe/curry by day
        order = ["fish", "meat", "nabe" if today.toordinal() % 2 == 0 else "curry"]
    return order


def theme_base(profile: dict) -> str:
    name = str(profile.get("name") or "").strip()
    first = name.split()[0] if name else ""
    return f"Tonight's dinner for {first}'s family" if first else "Tonight's dinner"


def recommend(profile: dict, history_rows: list, catalog: list, request: str = "", budget_jpy: int = 0,
              servings: int = 0, customer_id: str = "", today: dt.date | None = None) -> dict:
    today = today or dt.datetime.now(JST).date()
    req = parse_request(request)
    if budget_jpy and int(budget_jpy) > 0:
        req["budget"] = int(budget_jpy)
    servings = int(servings or 0) or req["servings"] or int(profile.get("household_size") or 0) or 4
    excl, dislikes = exclusion_words(profile), dislike_words(profile)
    hist = history_signals(history_rows)
    cooking = str(profile.get("cooking_style") or "")
    ctx = Ctx(profile=profile, hist=hist, req=req, today=today, customer_id=customer_id or profile.get("customer_id", ""),
              servings=servings, rules=family_rules(profile),
              quick_pref=_has(r"quick|time-saving|under \d+ min|short on time|meal kit", cooking))
    seen, pool, excluded = set(), [], 0
    for p in catalog:
        if not p or p["id"] in seen:
            continue
        seen.add(p["id"])
        if blocked(p, excl, dislikes):
            excluded += 1
            continue
        roles = classify(p)
        if roles:
            pool.append((p, roles))
    sets, used_mains = [], set()
    for kind in choose_kinds(req, today):
        used_all = {i["product_id"] for s in sets for i in s["items"]}
        s = compose_set(kind, pool, ctx, used_all)
        if s and s["items"][0]["product_id"] not in used_mains:
            used_mains.add(s["items"][0]["product_id"])
            sets.append(s)
        if len(sets) == 3:
            break
    base = theme_base(profile)
    theme = f"{base}: {sets[0]['name']}" if sets else base
    fam_sig = []
    for fp in profile.get("family_preferences") or []:
        fam_sig.append(f"{fp.get('call_name') or fp.get('name')}: {', '.join(fp.get('likes') or [])}"
                       + (f" (avoid: {', '.join(fp.get('ng'))})" if fp.get("ng") else ""))
    pmap = {p["id"]: p for p, _ in pool}
    seasonal = []
    for s in sets:
        for i in s["items"]:
            if TAG_SEASON in i["tags"]:
                n = season_noun(pmap[i["product_id"]]) if i["product_id"] in pmap else short_name(i["title"])
                if n not in seasonal:
                    seasonal.append(n)
    out = {
        "theme": theme,
        "servings": servings,
        "budget_jpy": req["budget"] or 0,
        "signals": {
            "frequent": list(dict.fromkeys(h["short"] for h in hist["top"]))[:6],
            "family": fam_sig,
            "sale_count": sum(1 for p, _ in pool if p["on_sale"]),
            "seasonal": seasonal[:6],
            "excluded": allergy_note(profile),
        },
        "sets": sets,
        "pool_size": len(pool),
        "excluded_count": excluded,
    }
    if sets:
        out["show_products_on_page_args"] = show_args(out, 0, base)
        for i in range(1, len(sets)):
            sets[i]["show_products_on_page_args"] = show_args(out, i, base)
    return out


def show_args(res: dict, idx: int = 0, base: str | None = None) -> dict:
    """Ready-to-pass args for the client function show_products_on_page (set idx, grouped by role)."""
    s = res["sets"][idx]
    sections, ids, reasons, qtys = [], [], [], []
    for role in ROLE_ORDER:
        its = [i for i in s["items"] if i["role"] == role]
        if not its:
            continue
        sections.append({"label": role, "product_ids": [i["product_id"] for i in its],
                         "reasons": [i["reason"] for i in its], "tags": [i["tags"] for i in its],
                         "quantities": [i["quantity"] for i in its]})
        ids += [i["product_id"] for i in its]
        reasons += [i["reason"] for i in its]
        qtys += [i["quantity"] for i in its]
    base = base or res["theme"].split(": ")[0]
    theme = f"{base}: {s['name']}"
    return {"message": f"{s['name']} (¥{s['total_tax_included']:,} incl. tax)", "theme": theme,
            "product_ids": ids, "reasons": reasons, "quantities": qtys, "sections": sections}
