"""Shared accessors (no FastAPI imports, no main.py import → usable by staff.py / agent without cycles).

get_engine()   site_lib LocalSearch (DATA_MODE=local or SEARCH_MODE=local) / RetailSearch (AICS, default on Cloud Run)
get_catalog()  site_lib Catalog (data/products_retail.jsonl [+ DATA_DIR/products_extra.jsonl dev fixtures])
to_product(n)  online-store front-end Product + drug_class, abuse_control, age_min, symptoms (CONTRACT v8)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Iterable

from . import config
from .site_loader import catalog_module, search_module

log = logging.getLogger("store-navi.deps")

_lock = threading.Lock()
_catalog = None
_engine = None
_otc: dict[str, dict] = {}

# CONTRACT_EN drug_class vocabulary (shared/catalog/category_map_en.json)
CLASS1 = "Pharmacist-only (Class 1)"
QUASI = "Quasi-drug"
MEDICINE_CLASSES = (CLASS1, "Designated Class 2", "Class 2", "Class 3")
DRUG_CLASSES = MEDICINE_CLASSES + (QUASI,)


def products_file() -> str:
    env = os.environ.get("DATA_FILE")
    if env:
        return env
    for cand in (config.SN_DIR / "data" / "products_retail.jsonl", config.REPO_DIR / "seed" / "catalog" / "products_retail.jsonl"):
        if cand.exists():
            return str(cand)
    return ""


def suggestions_file() -> str:
    env = os.environ.get("SUGGESTIONS_FILE")
    if env:
        return env
    pf = products_file()
    if pf:
        cand = Path(pf).parent / "autocomplete_suggestions.jsonl"
        if cand.exists():
            return str(cand)
    cand = config.REPO_DIR / "seed" / "catalog" / "autocomplete_suggestions.jsonl"
    return str(cand) if cand.exists() else ""


def _extra_files() -> list[str]:
    out = []
    env = os.environ.get("EXTRA_PRODUCTS_FILE")
    if env:
        out += [p for p in env.split(",") if p.strip()]
    cand = config.data_dir() / "products_extra.jsonl"
    if cand.exists():
        out.append(str(cand))
    return out


def _scan_otc_attrs(path: str) -> None:
    """Raw OTC attributes (age_min is a numbers attribute that site_lib's normalize() drops)."""
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            if '"drug_class"' not in line and '"age_min"' not in line and '"abuse_control"' not in line:
                continue
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                continue
            attrs = p.get("attributes") or []
            amap = attrs if isinstance(attrs, dict) else {a.get("key"): a.get("value") or {} for a in attrs
                                                           if isinstance(a, dict)}
            rec: dict[str, Any] = {}
            for k in ("drug_class", "abuse_control", "symptoms", "age_min"):
                v = amap.get(k) or {}
                if v.get("numbers"):
                    rec[k] = v["numbers"]
                elif v.get("text"):
                    rec[k] = v["text"]
            _otc[str(p.get("id"))] = rec


def get_catalog():
    global _catalog
    with _lock:
        if _catalog is None:
            cm = catalog_module()
            path = products_file()
            if suggestions_file():
                os.environ.setdefault("SUGGESTIONS_FILE", suggestions_file())
            cat = cm.Catalog(path)
            _scan_otc_attrs(path)
            for extra in _extra_files():
                ex = cm.Catalog(extra)
                added = 0
                for pid, n in ex.items.items():
                    if pid not in cat.items:
                        cat.items[pid] = n
                        added += 1
                _scan_otc_attrs(extra)
                log.info("extra products %s: +%d", extra, added)
            _catalog = cat
        return _catalog


def get_engine():
    global _engine
    if _engine is None:
        cat = get_catalog()
        sm = search_module()
        with _lock:
            if _engine is None:
                if config.data_mode() == "local" or os.environ.get("SEARCH_MODE") == "local":
                    _engine = sm.LocalSearch(cat)
                else:
                    try:
                        _engine = sm.RetailSearch(cat)
                    except Exception as e:  # noqa: BLE001
                        log.error("Retail client init failed (%s) → local engine", e)
                        _engine = sm.LocalSearch(cat)
                log.info("search engine=%s catalog=%d", _engine.mode, len(cat))
    return _engine


def reset() -> None:
    global _catalog, _engine
    with _lock:
        _catalog = None
        _engine = None
        _otc.clear()


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


def otc_fields(pid: str, n: dict | None = None) -> dict:
    rec = _otc.get(pid) or {}
    attrs = (n or {}).get("_attrs") or {}
    dc = _first(rec.get("drug_class")) or _first(attrs.get("drug_class")) or ""
    ab = str(_first(rec.get("abuse_control")) or _first(attrs.get("abuse_control")) or "false").lower() == "true"
    age = _first(rec.get("age_min"))
    if age is None and attrs.get("age_min"):
        age = _first(attrs.get("age_min"))
    try:
        age_i = int(float(age)) if age not in (None, "") else None
    except (TypeError, ValueError):
        age_i = None
    sym = rec.get("symptoms") or attrs.get("symptoms") or []
    if isinstance(sym, str):
        sym = [sym]
    ch = str(_first(attrs.get("channel")) or _first(rec.get("channel")) or "omni").strip().lower()
    return {"drug_class": dc if dc in DRUG_CLASSES else (dc or ""), "abuse_control": ab, "age_min": age_i,
            "symptoms": [str(s) for s in sym],
            "channel": ch if ch in ("omni", "store_only", "online_only") else "omni"}


def to_product(n_or_id: Any) -> dict | None:
    cat = get_catalog()
    if isinstance(n_or_id, str):
        n = cat.get(n_or_id)
        if n is None:
            return None
    else:
        n = n_or_id
        if n is None:
            return None
        if "_attrs" not in n and n.get("id") and cat.get(n["id"]):
            n = cat.get(n["id"])
    out = {k: v for k, v in n.items() if not str(k).startswith("_")}
    out.update(otc_fields(str(out.get("id")), n))
    return out


def hydrate(ids: Iterable[str]) -> list[dict]:
    out = []
    eng = None
    for i in ids:
        p = to_product(i)
        if p is None:
            eng = eng or get_engine()
            got = eng.hydrate([i]) if hasattr(eng, "hydrate") else []
            if got:
                p = to_product(got[0])
        if p:
            out.append(p)
    return out


def is_medicine(p: dict | None) -> bool:
    return bool(p) and p.get("drug_class") in MEDICINE_CLASSES


# ---------------------------------------------------------------------------------------------- symptom fallback
# AICS may return 0 for symptom-only queries ("fever", "cough" …) → local matching over symptoms / title / category.
# English OTC symptom vocabulary (keys = what shoppers type, values = catalog `symptoms` / sub-category terms).
SYNONYMS: dict[str, list[str]] = {
    "fever": ["fever", "pain relief"], "feverish": ["fever"], "high fever": ["fever"], "temperature": ["fever"],
    "cold": ["cold", "fever", "sore throat"], "a cold": ["cold", "fever"], "flu": ["cold", "fever"],
    "chills": ["fever", "cold"],
    "cough": ["cough"], "coughing": ["cough"], "phlegm": ["phlegm"], "mucus": ["phlegm"],
    "runny nose": ["runny nose"], "stuffy nose": ["nasal congestion"], "blocked nose": ["nasal congestion"],
    "congestion": ["nasal congestion"], "sneezing": ["sneezing"], "hay fever": ["runny nose", "sneezing", "itchy eyes"],
    "allergy": ["runny nose", "sneezing", "itchy eyes"], "allergies": ["runny nose", "sneezing", "itchy eyes"],
    "sore throat": ["sore throat"], "throat": ["sore throat"],
    "headache": ["headache"], "migraine": ["headache"], "period pain": ["menstrual pain"],
    "cramps": ["menstrual pain"], "toothache": ["toothache"],
    "stomach ache": ["stomach pain"], "stomachache": ["stomach pain"], "upset stomach": ["upset stomach"],
    "indigestion": ["indigestion"], "heartburn": ["heartburn"], "diarrhea": ["diarrhea"], "constipation": ["constipation"],
    "tired eyes": ["eye strain"], "eye strain": ["eye strain"], "eyes are tired": ["eye strain"],
    "dry eyes": ["dry eyes"], "dry eye": ["dry eyes"], "itchy eyes": ["itchy eyes"],
    "stiff shoulders": ["stiff shoulders"], "back pain": ["back pain"], "knee": ["joint pain", "knee pain"],
    "knee pain": ["joint pain", "knee pain"], "joint pain": ["joint pain"], "sore muscles": ["muscle pain"],
    "muscle pain": ["muscle pain"], "itch": ["itching"], "itchy": ["itching"], "itching": ["itching"],
    "bug bite": ["insect bites"], "insect bite": ["insect bites"], "mouth ulcer": ["mouth ulcers"],
    "canker sore": ["mouth ulcers"], "tired": ["fatigue"], "fatigue": ["fatigue"], "motion sickness": ["motion sickness"],
}


def _symptom_terms(q: str) -> list[str]:
    import re as _re

    q = (q or "").strip().lower()
    terms: list[str] = []
    for k in sorted(SYNONYMS, key=len, reverse=True):   # longest phrase first ("high fever" before "fever")
        if k == q or _re.search(r"\b" + _re.escape(k) + r"\b", q):
            terms += [v for v in SYNONYMS[k] if v not in terms]
    return terms


def symptom_search(q: str, limit: int = 40) -> list[str]:
    """Product ids whose OTC symptoms / title / sub-category match the (synonym-expanded) symptom query."""
    terms = _symptom_terms(q)
    if not terms:
        return []
    cat = get_catalog()
    scored: list[tuple[float, str]] = []
    for pid, rec in _otc.items():
        syms = [str(s) for s in (rec.get("symptoms") or [])]
        n = cat.get(pid)
        if not n:
            continue
        hay = (n.get("title", "") + " " + (n.get("category_path") or "")).lower()
        syms = [x.lower() for x in syms]
        score = 0.0
        for i, t in enumerate(terms):
            w = 1.0 if i == 0 else 0.7
            if any(t in s or s in t for s in syms):
                score += 3 * w
            if t in hay:
                score += 2 * w
        if score > 0:
            scored.append((score + float(n.get("rating") or 0) / 10, pid))
    # non-medicine support items (cooling gel sheets, oral rehydration …) through the normal local text search on the first term
    scored.sort(key=lambda x: -x[0])
    out = [pid for _, pid in scored[:limit]]
    if len(out) < limit and terms:
        for _, n in cat.text_search(terms[0])[: limit - len(out)]:
            if n["id"] not in out:
                out.append(n["id"])
    return out[:limit]


def is_symptom_query(q: str) -> bool:
    return bool(_symptom_terms(q))
