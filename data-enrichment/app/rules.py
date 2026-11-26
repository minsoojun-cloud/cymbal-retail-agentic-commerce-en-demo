"""Gate 1 (rule-based validation) and Gate 2 (composite confidence score) — Cymbal Retail.

Food safety rule (allergens): allergen values are NEVER auto-approved unless the evidence is an explicit
allergen statement found verbatim in the source (description / package label / document), and every allergen in the
value appears in that statement. Anything else (guess, structured "none" only, title mentions an allergen that is not in
the list, not found at all) goes to HITL review with the reason.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from .schema import ALLERGEN_VARIANTS, ALLERGENS_MANDATORY, ATTR_BY_KEY, LIST_SEP, NO_ALLERGENS

AUTO_APPROVED = "auto_approved"
NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"
APPROVED = "approved"          # approved by a human (HITL)
EDITED = "edited"              # corrected + approved by a human
HUMAN_REJECTED = "human_rejected"

FINAL_OK = {AUTO_APPROVED, APPROVED, EDITED}


# ── Evidence grounding ───────────────────────────────────────────────────────

def _squash(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s,.:;()\[\]{}\"'*#/|\u00b7\u2022-]", "", s).lower()


def evidence_match(evidence: str, source_text: str, raw_value: str = "") -> float:
    """1.0 exact substring, 0.8 match after NFKC/whitespace squash, 0.5 only the raw value is found, else 0."""
    if not evidence:
        return 0.0
    if evidence in source_text:
        return 1.0
    if _squash(evidence) and _squash(evidence) in _squash(source_text):
        return 0.8
    if raw_value and _squash(raw_value) and _squash(raw_value) in _squash(source_text):
        return 0.5
    return 0.0


def token_confidence(avg_logprobs: float | None) -> float | None:
    if avg_logprobs is None:
        return None
    try:
        return max(0.0, min(1.0, math.exp(float(avg_logprobs))))
    except (TypeError, ValueError, OverflowError):
        return None


def composite_score(ev: float, self_conf: float, tok: float | None) -> float:
    self_conf = max(0.0, min(1.0, float(self_conf or 0)))
    if tok is None:
        s = 0.6 * ev + 0.4 * self_conf
    else:
        s = 0.5 * ev + 0.3 * self_conf + 0.2 * tok
    return round(s, 3)


# ── Gate 1: rules ────────────────────────────────────────────────────────────

def check_limits(key: str, value: Any) -> str | None:
    d = ATTR_BY_KEY.get(key)
    if not d or d.limits is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    lo, hi = d.limits
    if value < lo or value > hi:
        return f"Outside physical limits: {value:g} {d.unit} (allowed {lo:g}-{hi:g} {d.unit})"
    return None


ALLERGEN_CONTEXT_RE = re.compile(r"(allergens?\b|allergy|contains\s*:|may contain|produced in a facility)", re.I)


_FALSE_HITS = {
    "buckwheat": None,
    "wheat": re.compile(r"buckwheat", re.I),            # "buckwheat" is not wheat
    "egg": re.compile(r"eggplants?", re.I),             # eggplant is a vegetable
    "milk": re.compile(r"coconut milk|soy ?milk|oat milk|almond milk|milk thistle", re.I),
    "peanut": None,
    "crab": re.compile(r"crab ?sticks? \(imitation\)|imitation crab", re.I),
    "orange": re.compile(r"orange (colou?r|peel extract)", re.I),
}


def _norm_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def allergen_mentions(text: str, only: list[str] | None = None) -> set[str]:
    """Canonical allergens whose spelling variants occur (as whole words) in `text` (optionally restricted to `only`)."""
    t = _norm_text(text)
    hits = set()
    for canon, variants in ALLERGEN_VARIANTS.items():
        if only is not None and canon not in only:
            continue
        fh = _FALSE_HITS.get(canon)
        tt = fh.sub(" ", t) if fh is not None else t
        for v in variants:
            if re.search(r"\b" + re.escape(v) + r"\b", tt):
                hits.add(canon)
                break
    return hits


def check_allergen_evidence(value: Any, evidence: str, ev: float, source_text: str, title: str = "",
                            source_field: str = "") -> str | None:
    """Return a review reason if the allergen value is not provably grounded, else None."""
    if ev < 0.8:
        return "Allergen evidence was not found verbatim in the source (never fill by guessing)"
    if not ALLERGEN_CONTEXT_RE.search(evidence or ""):
        return "Evidence is not an allergen statement (an explicit allergen label is required)"
    items = [x.strip() for x in str(value or "").split(",") if x.strip() and x.strip() != NO_ALLERGENS]
    ev_hits = allergen_mentions(evidence)
    missing = [x for x in items if x not in ev_hits]
    if missing:
        return f'Evidence does not list "{LIST_SEP.join(missing)}"'
    if str(value) == NO_ALLERGENS:
        # "none" must be stated in the product copy / label / document, not only a structured catalog field
        if source_field == "attributes" or (re.match(r"\s*allergens\s*:", evidence or "")
                                           and source_field not in ("description", "documents")):
            return '"none" appears only in a structured attribute. Confirm it on the package label'
    title_hits = allergen_mentions(title, only=ALLERGENS_MANDATORY)   # 8 mandatory allergens
    extra = sorted((h for h in title_hits if h not in items), key=ALLERGENS_MANDATORY.index)
    if extra:
        return f'Product name mentions "{LIST_SEP.join(extra)}" but it is not in the allergen list (check required)'
    return None


def check_consistency(values: dict[str, Any], title: str = "") -> dict[str, str]:
    """Cross-attribute consistency. Returns {attr_key: reason}."""
    issues: dict[str, str] = {}
    t = unicodedata.normalize("NFKC", title or "")
    st = values.get("storage_method")
    if isinstance(st, str):
        if re.search(r"\bfrozen\b", t, re.I) and st != "Frozen" and not re.search(r"thaw|defrost", t, re.I):
            issues["storage_method"] = f'Product name says "frozen" but storage is "{st}"'
        elif re.search(r"\b(raw|fresh fish|sashimi|fillets?|fresh meat|minced)\b", t, re.I) and st == "Ambient" \
                and values.get("_group") == "fresh":
            issues["storage_method"] = 'Storage "Ambient" is implausible for a fresh product'
    bb = values.get("best_before_days")
    if isinstance(bb, (int, float)) and values.get("storage_method") == "Chilled" and bb > 120:
        issues["best_before_days"] = f"Best before of {bb:g} days is implausible for a chilled product"
    w, d, h = values.get("width_cm"), values.get("depth_cm"), values.get("height_cm")
    dia = values.get("diameter_cm")
    if isinstance(dia, (int, float)) and isinstance(w, (int, float)) and dia > w * 1.5:
        issues["diameter_cm"] = f"Diameter {dia:g} cm is much larger than width {w:g} cm"
    ct = values.get("cook_time_min")
    if isinstance(ct, (int, float)) and values.get("_group") == "prepared" and ct > 120:
        issues["cook_time_min"] = f"Cooking time of {ct:g} min is implausible for deli / frozen food"
    vol, wt = values.get("net_volume_ml"), values.get("net_weight_g")
    if isinstance(vol, (int, float)) and isinstance(wt, (int, float)) and values.get("_group") in ("grocery", "prepared"):
        if wt > 0 and (vol / wt > 20 or wt / vol > 20):
            issues["net_volume_ml"] = f"Ratio between {wt:g} g and {vol:g} ml is implausible"
    _ = d, h
    return issues


# ── Gate decision ────────────────────────────────────────────────────────────

def decide(norm_ok: bool, norm_note: str, limit_issue: str | None, consistency_issue: str | None,
           ev: float, score: float, threshold: float, safety_issue: str | None = None) -> tuple[str, str, str]:
    """Returns (gate_status, gate, reason). gate in {gate1, gate2, gate3}."""
    if limit_issue:
        return REJECTED, "gate1", limit_issue
    if not norm_ok:
        return NEEDS_REVIEW, "gate1", f"Unit normalization failed: {norm_note}"
    if consistency_issue:
        return NEEDS_REVIEW, "gate1", consistency_issue
    if safety_issue:
        return NEEDS_REVIEW, "gate1", f"Food safety rule: {safety_issue}"
    if ev == 0:
        return NEEDS_REVIEW, "gate2", "Evidence snippet not found in the source (possible hallucination)"
    if score >= threshold:
        return AUTO_APPROVED, "gate2", f"Composite score {score:.2f} >= {threshold:.2f}"
    return NEEDS_REVIEW, "gate2", f"Composite score {score:.2f} < {threshold:.2f} (insufficient confidence)"
