"""Seasonal / promotional image presets and rule-based availability ("only if the product has the evidence").

Availability is decided by rules only (approved attributes, approved feature tags, raw catalog attributes and
keyword evidence in the source text) - deterministic, fast and explainable. Gemini is not used here.
Preset ids are stable (the static UI and stored image rows reference them).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

from .normalizer import parse_months
from .prompts import source_text
from .rules import FINAL_OK
from .schema import ATTR_BY_KEY, FOOD_GROUPS

ALL_GROUPS = frozenset({"fresh", "prepared", "grocery", "kitchen", "living", "beauty", "pet"})
FOOD = frozenset(FOOD_GROUPS)
SEASON_LABEL = {"spring": "Spring", "summer": "Summer", "autumn": "Autumn", "winter": "Winter",
                "yearend": "Year-end & New Year", "newlife": "New Life", "all": "All year"}


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    season: str
    icon: str
    groups: frozenset
    prompt: str
    attrs: tuple[str, ...] = ()        # approved golden attribute keys that satisfy the requirement
    raw_keys: tuple[str, ...] = ()     # raw catalog attribute keys that satisfy the requirement (non-empty)
    raw_true: tuple[str, ...] = ()     # raw catalog attribute keys whose value must be "true"
    tags: tuple[str, ...] = ()         # approved feature tags that satisfy the requirement (case/punctuation-insensitive)
    months: tuple[int, ...] = ()       # season months (approved `season` or raw season_months) overlapping these
    text_regex: str = ""               # keyword evidence in the source text (case-insensitive)
    attr_max: tuple[str, float] | None = None   # approved numeric attribute <= value (e.g. cook_time_min <= 15)
    facts: tuple[str, ...] = ()        # verified spec keys allowed in the image text
    need_label: str = ""               # used in the "not available" reason
    always: bool = False               # no requirement

    @property
    def requires_any(self) -> bool:
        return not self.always


PRESETS: list[Preset] = [
    Preset("spring-sakura", "Spring: Cherry Blossom", "spring", "🌸", FOOD,
           "A cheerful spring cherry-blossom picnic: pink sakura petals, a picnic blanket and bento boxes under blossoming "
           "trees, soft pastel pink and fresh green tones, bright natural daylight.",
           months=(3, 4), tags=("Good for bento", "Entertaining"),
           text_regex=r"cherry blossom|sakura|hanami|picnic|bento|lunch ?box",
           need_label="spring / cherry-blossom picnic use"),
    Preset("newlife", "Spring: New Life", "newlife", "🏡", frozenset({"kitchen", "living", "prepared", "grocery", "beauty"}),
           "A fresh start in spring: a bright, tidy studio apartment or new-home kitchen, light green and white tones, "
           "airy and optimistic.",
           tags=("Space-saving", "Stackable", "Time-saver", "Single-use size", "Good for bento"),
           text_regex=r"new life|living alone|single[- ]person|one[- ]person|studio|lunch ?box|bento|meal prep|"
                      r"space[- ]saving|compact|stackable",
           need_label="new life / living alone / lunchbox use"),
    Preset("summer-cool", "Summer: Cool Treats", "summer", "🧊", FOOD | {"kitchen"},
           "A cool, refreshing summer table: glassware, ice cubes and water droplets, crisp blue and white tones, "
           "bright summer sunlight.",
           months=(6, 7, 8), tags=("Cooling",),
           text_regex=r"serve chilled|serve cold|iced\b|ice cubes?|cold brew|somen|cold noodle|shaved ice|popsicle|ice cream|"
                      r"barley tea|cooling|summer",
           need_label="serving chilled / summer use"),
    Preset("summer-bbq", "Summer: BBQ", "summer", "🍖", FOOD | {"kitchen"},
           "A lively summer backyard barbecue: a charcoal grill with gentle smoke, picnic table, friends and family "
           "outdoors at golden hour, warm and appetizing.",
           tags=("Great for BBQ",),
           text_regex=r"bbq\b|barbecue|barbeque|grill|yakiniku|skewer|kebab|steak|sausage|charcoal",
           need_label="barbecue / grilling use"),
    Preset("autumn-taste", "Autumn Harvest", "autumn", "🍁", FOOD,
           "An autumn harvest table: wooden table with fall leaves, chestnuts and mushrooms as props, warm orange "
           "and brown tones. A photo that conveys the flavor of seasonal ingredients.",
           months=(9, 10, 11), tags=("In season",),
           text_regex=r"autumn|fall\b|harvest|new[- ]crop rice|chestnut|saury|sanma|salmon|mushroom|shimeji|maitake|"
                      r"sweet potato|pumpkin|kabocha|persimmon|pear|apple|grape",
           facts=("season", "origin"), need_label="autumn seasonality / harvest"),
    Preset("halloween", "Halloween", "autumn", "🎃", FOOD | {"kitchen", "living"},
           "A playful Halloween party table: carved jack-o'-lanterns, candles, orange and purple accents, "
           "fun but family-friendly, warm evening light.",
           tags=("Party", "Kid favorite"),
           text_regex=r"halloween|pumpkin|kabocha|jack-o|trick or treat|candy|party",
           need_label="Halloween / party use"),
    Preset("winter-nabe", "Winter Hot Pot", "winter", "🍲", FOOD | {"kitchen"},
           "A cozy winter evening: a family gathered around a steaming clay hot pot (nabe) on the dining table, "
           "rising steam, soft warm-white light, comforting mood.",
           tags=("Great for hot pot",),
           text_regex=r"hot ?pot|\bnabe\b|shabu|sukiyaki|oden|mizutaki|chanko|donabe|stew",
           facts=("net_weight_g", "origin"), need_label="hot pot cooking"),
    Preset("christmas", "Christmas", "winter", "🎄", FOOD | {"kitchen", "living", "beauty"},
           "A festive Christmas table: a decorated tree with twinkling lights in the background, red and green "
           "accents with gold ornaments, a warm family holiday celebration.",
           tags=("Party", "Entertaining", "Gift"),
           text_regex=r"christmas|x-?mas|holiday|festive|roast chicken|turkey|party|gift",
           need_label="Christmas / party / gift use"),
    Preset("yearend", "New Year", "yearend", "🎍", FOOD | {"beauty", "living"},
           "A celebratory Japanese New Year table: red, white and gold accents, pine and mizuhiki cord decorations, "
           "family and relatives gathering on a special day.",
           months=(12, 1), tags=("Entertaining", "Gift", "Party"),
           text_regex=r"new year|year[- ]end|osechi|ozoni|toshikoshi|year-end gift|oseibo|christmas|holiday|gift",
           need_label="New Year / gift / seasonal events"),
    Preset("quick-dinner", "Weeknight: Quick Dinner", "all", "⏱️", FOOD,
           "A weekday evening with a quickly prepared, well-presented dinner on the table: a busy two-income "
           "family kitchen, bright and clean.",
           tags=("Time-saver", "Microwavable", "Just heat"),
           attr_max=("cook_time_min", 15),
           text_regex=r"quick|time[- ]saving|microwave|just heat|heat (?:and|&) serve|ready in|easy|pre-?cut",
           facts=("cook_time_min",), need_label="quick cooking (cooking time / microwave)"),
    Preset("ec-white", "E-commerce: White Background", "all", "⬜", ALL_GROUPS,
           "Pure white background, product large and centered, soft contact shadow. A clean product photo for an "
           "e-commerce product page.", always=True),
    Preset("lifestyle", "Lifestyle", "all", "🏠", ALL_GROUPS,
           "The product used naturally in an ordinary Japanese home's dining room, kitchen or living room. "
           "Bright and clean photo.", always=True),
    Preset("sale", "Sale", "all", "🏷️", ALL_GROUPS,
           "A bright, energetic supermarket flyer / special-offer corner background (Cymbal blue #1a73e8 with red "
           "and yellow accents). Product large in the center. Do not draw any price numbers.",
           raw_true=("on_sale",), raw_keys=("promo_text",), text_regex=r"\bsale\b|special offer|deal|discount|bargain",
           need_label="sale / special offer (on_sale / promo text)"),
]
PRESET_BY_ID = {p.id: p for p in PRESETS}


def _norm_tag(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(t or "").lower())


def _approved(recs: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """Approved attributes by key, and approved tags as {normalized: original}."""
    attrs = {r["attr_key"]: r for r in recs if r.get("kind") == "attribute" and r.get("gate_status") in FINAL_OK
             and r.get("value") not in (None, "", False)}
    tags = {_norm_tag(r.get("value")): str(r.get("value")) for r in recs
            if r.get("kind") == "tag" and r.get("gate_status") in FINAL_OK}
    return attrs, tags


def _fmt(rec: dict) -> str:
    v = rec.get("value")
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    unit = rec.get("unit") or ""
    return f"{v} {unit}".strip()


def _months_txt(ms) -> str:
    return ", ".join(calendar.month_abbr[m] for m in ms if 1 <= m <= 12)


def verified_facts(keys: tuple[str, ...], recs: list[dict]) -> list[dict]:
    attrs, _ = _approved(recs)
    out = []
    for k in keys:
        if k in attrs:
            d = ATTR_BY_KEY.get(k)
            out.append({"key": k, "label": d.label if d else k, "value": _fmt(attrs[k])})
    return out


def evidence_view(product: dict) -> dict:
    """Product copy without internal catalog keys that are not product evidence (category paths included)."""
    raw = {k: v for k, v in (product.get("raw_attributes") or {}).items()
           if k not in ("base_id", "top_category", "price_tax_excluded", "original_price_tax_excluded", "discount_rate",
                        "is_food", "is_seasonal")}
    return {**product, "category": "", "raw_attributes": raw}


def _months_of(product: dict, attrs: dict) -> list[int]:
    if "season" in attrs:
        return parse_months(str(attrs["season"].get("value") or "")) or []
    raw = str((product.get("raw_attributes") or {}).get("season_months") or "")
    return parse_months(raw) or [] if raw else []


def evaluate_one(pr: Preset, product: dict, recs: list[dict]) -> dict:
    attrs, tags = _approved(recs)
    raw = product.get("raw_attributes") or {}
    evidence, ok = "", pr.always
    if not ok:
        hit_tag = next((tags[_norm_tag(t)] for t in pr.tags if _norm_tag(t) in tags), None)
        hit_attr = next((k for k in pr.attrs if k in attrs), None)
        hit_true = next((k for k in pr.raw_true if str(raw.get(k) or "").strip().lower() == "true"), None)
        hit_raw = next((k for k in pr.raw_keys if str(raw.get(k) or "").strip()), None)
        months = _months_of(product, attrs) if pr.months else []
        hit_month = sorted(set(months) & set(pr.months))
        hit_max = None
        if pr.attr_max and pr.attr_max[0] in attrs:
            v = attrs[pr.attr_max[0]].get("value")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= pr.attr_max[1]:
                hit_max = pr.attr_max[0]
        if hit_tag:
            ok, evidence = True, f'Approved tag "{hit_tag}"'
        elif hit_true:
            ok, evidence = True, f"{hit_true}: true" + (f" ({str(raw.get('promo_text'))[:30]})" if raw.get("promo_text") else "")
        elif hit_month:
            ok, evidence = True, f"In season: {_months_txt(months)} (matches {_months_txt(hit_month)})"
        elif hit_max:
            d = ATTR_BY_KEY.get(hit_max)
            ok, evidence = True, f"{d.label if d else hit_max} {_fmt(attrs[hit_max])}"
        elif hit_attr:
            d = ATTR_BY_KEY.get(hit_attr)
            ok, evidence = True, f"{d.label if d else hit_attr} {_fmt(attrs[hit_attr])}"
        elif hit_raw:
            ok, evidence = True, f"{hit_raw}: {str(raw[hit_raw])[:40]}"
        elif pr.text_regex:
            src = source_text(evidence_view(product))   # category paths are not product evidence
            m = re.search(rf"\b(?:{pr.text_regex})", src, re.I)   # word-start anchored
            if m:
                s, e = max(0, m.start() - 20), min(len(src), m.end() + 20)
                ok, evidence = True, src[s:e].replace("\n", " ").strip()
    return {
        "id": pr.id, "label": pr.label, "season": pr.season, "season_label": SEASON_LABEL.get(pr.season, ""),
        "icon": pr.icon, "available": ok,
        "reason": "" if ok else f"No evidence of {pr.need_label} for this product",
        "evidence": evidence, "facts": [{"label": f["label"], "value": f["value"]} for f in verified_facts(pr.facts, recs)],
        "prompt": pr.prompt,
    }


def evaluate(product: dict, recs: list[dict]) -> list[dict]:
    g = product.get("group") or "grocery"
    return [evaluate_one(p, product, recs) for p in PRESETS if g in p.groups]
