"""Stage 2: deterministic unit normalization (Cymbal Retail: grocery + general merchandise).

The LLM only transcribes raw values ("3 fillets (approx. 240 g)", "W45 x D45 x H40 cm", "Allergens: shrimp, wheat"); all
unit conversions happen here in plain Python so that the result is reproducible and auditable
(extraction and unit normalization are separated).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .schema import ALLERGEN_VARIANTS, ALLERGENS_28, ATTR_BY_KEY, LIST_SEP, NO_ALLERGENS

NUM = r"(\d+(?:\.\d+)?)"
COUNTERS = (r"(pcs|pc|pieces?|fillets?|slices?|bags?|packs?|packets?|servings?|portions?|cans?|bottles?|sticks?|sheets?|"
            r"heads?|bunch(?:es)?|rolls?|sets?|cups?|count|ct|whole|fish|tablets?|capsules?|units?|P|p)\b")


@dataclass
class NormResult:
    value: Any
    unit: str
    ok: bool
    note: str = ""


def clean(raw: str) -> str:
    s = unicodedata.normalize("NFKC", str(raw or ""))
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    s = re.sub(r"\b(approx\.?|approximately|about|around|up to|max\.?|maximum|ca\.)\s*", "", s, flags=re.I)
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("~", "-")
    s = s.replace("\u00d7", "x").replace("*", "x").replace("X", "x")
    return s.strip()


def _first_number(s: str) -> float | None:
    m = re.search(NUM, s)
    return float(m.group(1)) if m else None


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(v, 2)


# ── amounts ──────────────────────────────────────────────────────────────────

def parse_weight_g(s: str) -> tuple[float, str] | None:
    """'3 fillets (240 g)' -> 240, '1 kg' -> 1000, '200 g x 3 bags' -> 600 (summed), '400g' -> 400."""
    m = re.search(NUM + r"\s*(kg|g)\b\s*x\s*(\d+)\s*(?:" + COUNTERS + ")?", s, re.I)
    if m:
        unit_g = float(m.group(1)) * (1000 if m.group(2).lower() == "kg" else 1)
        n = int(m.group(3))
        return round(unit_g * n, 1), f"summed {_num(unit_g)} g x {n}"
    m = re.search(NUM + r"\s*(kg|g|grams?|kilograms?)\b", s, re.I)
    if not m:
        return None
    is_kg = m.group(2).lower().startswith("k")
    v = float(m.group(1)) * (1000 if is_kg else 1)
    return round(v, 1), ("converted kg to g" if is_kg else "")


def parse_volume_ml(s: str) -> tuple[float, str] | None:
    """'1.8 L' -> 1800, '500 ml x 24 bottles' -> 500 (per bottle), '700ml x 3' -> 700."""
    m = re.search(NUM + r"\s*(ml|l|cc|liters?|litres?)\b", s, re.I)
    if not m:
        return None
    u = m.group(2).lower()
    v = float(m.group(1)) * (1 if u in ("ml", "cc") else 1000)
    note = "converted L to ml" if u not in ("ml", "cc") else ""
    if re.search(re.escape(m.group(0)) + r"\s*x\s*\d+", s):
        note = (note + "; " if note else "") + "capacity per unit (x n is the pack count)"
    return round(v, 1), note


def parse_count(s: str) -> tuple[int, str] | None:
    """'10 pcs' -> 10, '3 fillets (240 g)' -> 3, '200 g x 3 servings' -> 3, 'pack of 5' -> 5, '2 whole fish' -> 2."""
    m = re.search(r"x\s*(\d+)\b", s)
    if m:
        return int(m.group(1)), "pack count from x n notation"
    m = re.search(r"(?:pack|box|set|case|bag) of\s*(\d+)", s, re.I)
    if m:
        return int(m.group(1)), ""
    m = re.search(r"(\d+)\s*(?:-\s*)?" + COUNTERS, s, re.I)
    if m:
        return int(m.group(1)), ""
    m = re.fullmatch(r"\s*(\d+)\s*", s)
    if m:
        return int(m.group(1)), "unitless number treated as pack count"
    return None


def parse_servings(s: str) -> float | None:
    m = re.search(r"(?:serves|for)\s*(\d+)\s*-\s*(\d+)", s, re.I)
    if m:
        return float(m.group(2))
    m = re.search(r"(?:serves|for)\s*" + NUM + r"(?:\s*(?:people|persons?|servings?))?\b", s, re.I)
    if m:
        return float(m.group(1))
    m = re.search(NUM + r"\s*-\s*" + NUM + r"\s*(servings?|portions?|people)", s, re.I)
    if m:
        return float(m.group(2))
    m = re.search(NUM + r"\s*(servings?|portions?|people|persons?)\b", s, re.I)
    return float(m.group(1)) if m else None


# ── food facts ───────────────────────────────────────────────────────────────

def normalize_storage(s: str) -> str | None:
    if re.search(r"frozen|freezer|freeze|-18 ?\u00b0?C|0 ?\u00b0F", s, re.I):
        return "Frozen"
    if re.search(r"chill|refrigerat|fridge|10 ?\u00b0?C or (below|less)|below 10|keep cold|cold storage", s, re.I):
        return "Chilled"
    if re.search(r"ambient|room temperature|shelf[- ]stable|direct sunlight|cool,? dry place|high humidity|dry goods", s, re.I):
        return "Ambient"
    return None


_STATEMENT_RE = re.compile(r"^.*?\b(allergens?|allergy information|allergy|contains)\b\s*(?:information)?\s*:?", re.I)


def parse_allergens(s: str) -> tuple[list[str] | None, str]:
    """'Allergens: shrimp, wheat, egg, milk' -> ['shrimp','wheat','egg','milk']; 'none' -> []; unknown token -> None."""
    t = _STATEMENT_RE.sub("", s, count=1)
    t = re.sub(r"\(.*?\)|\[.*?\]", "", t)
    t = re.sub(r"\b(contains|includes?|may contain|and|traces? of)\b", ",", t, flags=re.I).strip()
    t = t.strip(" .;:")
    if re.fullmatch(r"\s*(none|nil|n/a|not applicable|no allergens?|free|-)?\s*", t, re.I):
        return [], "no labelled allergens"
    tokens = [x.strip(" .").lower() for x in re.split(r"[,;/\u00b7|]+", t) if x.strip(" .")]
    out, unknown = [], []
    for tok in tokens:
        canon = next((c for c, vs in ALLERGEN_VARIANTS.items() if tok in vs), None)
        if canon is None:
            canon = next((c for c, vs in sorted(ALLERGEN_VARIANTS.items(), key=lambda kv: -max(map(len, kv[1])))
                          if any(re.search(r"\b" + re.escape(v) + r"\b", tok) for v in vs)), None)
        if canon:
            if canon not in out:
                out.append(canon)
        else:
            unknown.append(tok)
    if unknown:
        return None, f"not one of the 28 labelled allergens: {', '.join(unknown)[:40]}"
    out.sort(key=ALLERGENS_28.index)
    return out, ""


def parse_days(s: str) -> float | None:
    m = re.search(NUM + r"\s*(years?|yrs?)\b", s, re.I)
    if m:
        return round(float(m.group(1)) * 365)
    m = re.search(NUM + r"\s*(months?|mos?)\b", s, re.I)
    if m:
        return round(float(m.group(1)) * 30)
    m = re.search(NUM + r"\s*(days?)\b", s, re.I)
    if m:
        return float(m.group(1))
    return None


def parse_minutes(s: str) -> float | None:
    h = re.search(NUM + r"\s*(hours?|hrs?|h)\b", s, re.I)
    mi = re.search(NUM + r"\s*(minutes?|mins?|m)\b", s, re.I)
    sec = re.search(NUM + r"\s*(seconds?|secs?|s)\b", s, re.I)
    if not (h or mi or sec):
        return None
    total = (float(h.group(1)) * 60 if h else 0) + (float(mi.group(1)) if mi else 0) + (float(sec.group(1)) / 60 if sec else 0)
    return round(total, 2)


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_RX = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"


def _month_no(tok: str) -> int:
    return MONTHS.index(tok.lower()[:3]) + 1


def _month_range(a: int, b: int) -> list[int]:
    out, x = [], a
    while True:
        out.append(x)
        if x == b or len(out) > 12:
            break
        x = x % 12 + 1
    return out


def parse_months(s: str) -> list[int] | None:
    """'Oct-Feb' / 'October to February' / '10-2' -> [10,11,12,1,2]; '10 / 11 / 12' -> [10,11,12]; 'autumn' -> [9,10,11]."""
    s = unicodedata.normalize("NFKC", s or "").replace("\u2013", "-").replace("~", "-")
    m = re.search(r"\b" + _MONTH_RX + r"\s*(?:-|to|through|until)\s*" + _MONTH_RX, s, re.I)
    if m:
        return _month_range(_month_no(m.group(1)), _month_no(m.group(2)))
    m = re.fullmatch(r"\s*(\d{1,2})\s*(?:-|to)\s*(\d{1,2})\s*", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 12 and 1 <= b <= 12:
            return _month_range(a, b)
    names = [_month_no(x) for x in re.findall(r"\b" + _MONTH_RX + r"(?![a-z])", s, re.I)]
    if names:
        return list(dict.fromkeys(names))
    if re.fullmatch(r"[\d.\s/,]+", s):
        nums = [int(float(x)) for x in re.findall(r"\d{1,2}(?:\.0)?", s)]
        nums = [n for n in nums if 1 <= n <= 12]
        if nums:
            return list(dict.fromkeys(nums))
    seasons = {"spring": [3, 4, 5], "summer": [6, 7, 8], "autumn": [9, 10, 11], "fall": [9, 10, 11], "winter": [12, 1, 2]}
    out: list[int] = []
    for k, v in seasons.items():
        if re.search(r"\b" + k + r"\b", s, re.I):
            out += [x for x in v if x not in out]
    return out or None


def fmt_months(ms: list[int]) -> str:
    if not ms:
        return ""
    if len(ms) >= 3 and all(ms[i + 1] == ms[i] % 12 + 1 for i in range(len(ms) - 1)):
        return f"{MONTH_ABBR[ms[0] - 1]}-{MONTH_ABBR[ms[-1] - 1]}"
    return LIST_SEP.join(MONTH_ABBR[m - 1] for m in ms)


COOKING = [("Microwave", r"microwav"), ("Frying pan", r"frying pan|fry pan|skillet|pan[- ]fry|stir[- ]fry|saut"),
           ("Boil-in-bag", r"boil[- ]in[- ](the[- ])?bag|hot water bath|immerse .*boiling|in boiling water for"),
           ("Pot", r"\bpot\b|simmer|\bboil(ing)?\b|stew"), ("Toaster", r"toaster"), ("Oven", r"\boven\b|bake"),
           ("Deep-fry", r"deep[- ]fr|fryer"), ("Ready to eat", r"ready to eat|no cooking|just thaw|eat as is|straight from"),
           ("Rice cooker", r"rice cooker")]


def normalize_cooking(s: str) -> str | None:
    hits = [name for name, rx in COOKING if re.search(rx, s, re.I)]
    if "Boil-in-bag" in hits and "Pot" in hits:
        hits.remove("Pot")
    return LIST_SEP.join(hits) if hits else None


def normalize_pet(s: str) -> str | None:
    dog = re.search(r"\b(dogs?|pupp(y|ies)|canine)\b", s, re.I)
    cat = re.search(r"\b(cats?|kittens?|feline)\b", s, re.I)
    if dog and cat:
        return "Dog & Cat"
    if dog:
        return "Dog"
    if cat:
        return "Cat"
    if re.search(r"rabbit|hamster|small animal|ferret|guinea pig", s, re.I):
        return "Small animal"
    return None


def normalize_origin(s: str) -> str:
    s = re.sub(r"^(origin|country of origin|place of origin|produced in|product of|made in|ingredient origin)\s*:\s*",
               "", s, flags=re.I).strip()
    return s


# ── dimensions ───────────────────────────────────────────────────────────────

def _to_cm(v: float, unit: str) -> float:
    unit = (unit or "cm").lower()
    return round(v * {"mm": 0.1, "cm": 1, "m": 100}.get(unit, 1), 1)


def parse_dimensions(raw: str) -> dict[str, float] | None:
    """'W45 x D45 x H40 cm' / 'Width 45 x Depth 45 x Height 40 cm' / '45x45x40cm' (W x D x H) -> {'w','d','h'} in cm."""
    s = clean(raw)
    unit_m = re.search(r"\d\s*(mm|cm|m)\b", s, re.I)
    unit = unit_m.group(1) if unit_m else "cm"
    found = {}
    for k, rx in (("w", r"(?:\b(?i:width)|\bW)\s*:?\s*" + NUM), ("d", r"(?:\b(?i:depth|gusset)|\bD)\s*:?\s*" + NUM),
                  ("h", r"(?:\b(?i:height)|\bH)\s*:?\s*" + NUM)):
        m = re.search(rx, s)
        if not m:
            m = re.search(NUM + r"\s*(?:mm|cm|m)?\s*(?i:" + {"w": "wide|width", "d": "deep|depth", "h": "high|tall|height"}[k] + r")\b", s)
        if m:
            found[k] = _to_cm(float(m.group(1)), unit)
    if len(found) >= 2:
        return found
    m = re.search(NUM + r"\s*x\s*" + NUM + r"\s*x\s*" + NUM + r"\s*(mm|cm|m)\b", s, re.I)
    if m:
        u = m.group(4)
        return {"w": _to_cm(float(m.group(1)), u), "d": _to_cm(float(m.group(2)), u), "h": _to_cm(float(m.group(3)), u),
                "_assumed": 1}
    m = re.search(NUM + r"\s*x\s*" + NUM + r"\s*(mm|cm|m)\b", s, re.I)
    if m:
        u = m.group(3)
        return {"w": _to_cm(float(m.group(1)), u), "d": _to_cm(float(m.group(2)), u), "_assumed": 1}
    return found or None


def _single_length(s: str) -> float | None:
    m = re.search(NUM + r"\s*(mm|cm|m)\b", s, re.I)
    if not m:
        return None
    return _to_cm(float(m.group(1)), m.group(2))


# ── entry point ──────────────────────────────────────────────────────────────

def normalize(key: str, raw_value: str) -> NormResult:
    """Normalize a raw transcribed value into the canonical unit / vocabulary for `key`."""
    r = _normalize(key, raw_value)
    if not r.ok and not r.note:
        r.note = f'Cannot interpret value "{str(raw_value)[:40]}"'
    return r


def _normalize(key: str, raw_value: str) -> NormResult:
    d = ATTR_BY_KEY.get(key)
    if d is None:
        return NormResult(None, "", False, f"Unknown attribute key: {key}")
    s = clean(raw_value)
    if not s:
        return NormResult(None, d.unit, False, "Value is empty")
    kind = d.kind
    try:
        if kind == "weight_g":
            r = parse_weight_g(s)
            return NormResult(_num(r[0]), "g", True, r[1]) if r else NormResult(None, "g", False, "No g/kg amount found")
        if kind == "volume_ml":
            r = parse_volume_ml(s)
            return NormResult(_num(r[0]), "ml", True, r[1]) if r else NormResult(None, "ml", False, "No ml/L amount found")
        if kind == "count":
            r = parse_count(s)
            return NormResult(r[0], d.unit, True, r[1]) if r else NormResult(None, d.unit, False, "No count unit found (pcs, fillets, bags, etc.)")
        if kind == "servings":
            v = parse_servings(s)
            return NormResult(_num(v), "servings", True, "") if v is not None else NormResult(None, "servings", False, "No servings statement found")
        if kind == "storage":
            v = normalize_storage(s)
            return NormResult(v, "", True, "Standardized to Chilled / Frozen / Ambient") if v \
                else NormResult(None, "", False, "Cannot determine Chilled / Frozen / Ambient")
        if kind == "allergens":
            items, note = parse_allergens(s)
            if items is None:
                return NormResult(None, "", False, note)
            return NormResult(LIST_SEP.join(items) if items else NO_ALLERGENS, "", True, note or "Standardized to the 28-allergen vocabulary")
        if kind == "days":
            v = parse_days(s)
            return NormResult(_num(v), "days", True, "Converted years / months to days" if re.search(r"year|month|\byr|\bmo", s, re.I) else "") \
                if v is not None else NormResult(None, "days", False, "No day / month / year statement found")
        if kind == "minutes":
            v = parse_minutes(s)
            return NormResult(_num(v), "min", True, "") if v is not None else NormResult(None, "min", False, "No minutes / seconds / hours found")
        if kind == "cooking":
            v = normalize_cooking(s)
            return NormResult(v, "", True, "Classified to standard cooking methods") if v else NormResult(s, "", True, "Kept as written (no standard method matched)")
        if kind == "months":
            ms = parse_months(s)
            return NormResult(fmt_months(ms), "", True, "Standardized to a month range") if ms \
                else NormResult(None, "", False, "Cannot interpret the season (months / seasons)")
        if kind == "pet":
            v = normalize_pet(s)
            return NormResult(v, "", True, "") if v else NormResult(None, "", False, "Cannot determine the pet type")
        if kind == "origin":
            return NormResult(normalize_origin(s), "", True, "")
        if kind == "list":
            items = [x.strip() for x in re.split(r"[,;/\u00b7]+", s) if x.strip()]
            return NormResult(LIST_SEP.join(dict.fromkeys(items)), "", True, "")
        if kind in ("dim_w", "dim_d", "dim_h"):
            dims = parse_dimensions(s)
            k = kind[-1]
            if dims and k in dims:
                note = "Split assuming W x D x H order" if dims.get("_assumed") else "Split dimensions and converted to cm"
                return NormResult(_num(dims[k]), "cm", True, note)
            if not dims:
                v = _single_length(s)
                if v is not None and re.search(r"(width|\bW\b|depth|\bD\b|height|\bH\b)", s, re.I) is None and kind == "dim_w":
                    return NormResult(_num(v), "cm", True, "Single dimension treated as width")
            return NormResult(None, "cm", False, "Cannot interpret the dimensions")
        if kind == "diameter":
            m = re.search(r"(?:diameter|dia\.?|\u00f8|\u03c6|\u03a6)\s*:?\s*" + NUM + r"\s*(mm|cm|m)?", s, re.I)
            if m:
                return NormResult(_num(_to_cm(float(m.group(1)), m.group(2) or "cm")), "cm", True, "")
            v = _single_length(s)
            return NormResult(_num(v), "cm", True, "Single dimension treated as diameter") if v is not None \
                else NormResult(None, "cm", False, "Cannot interpret the diameter")
        if kind == "bool":
            if key == "ih_compatible" and re.search(r"induction only|IH only", s, re.I):
                return NormResult(True, "", True, "Induction only")
            neg = re.search(r"(\bnot\b|\bno\b|non-|incompatible|unsuitable|cannot|can't|do not|don't|\bonly\b|\bNG\b|\bfalse\b)", s, re.I)
            return NormResult(not bool(neg), "", True, "")
        # text
        return NormResult(s, "", True, "")
    except Exception as e:  # pragma: no cover - defensive
        return NormResult(None, d.unit, False, f"Normalization error: {e}")
