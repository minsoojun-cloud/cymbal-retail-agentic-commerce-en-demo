"""Guardrails for the store AI concierge — English OTC rules (Cymbal Retail demo).

Rules (also stated in the agent instruction; enforced here server-side):
- No diagnosis. The AI only offers general information and "products that match your symptoms" as options.
- Severe / persistent / red-flag symptoms → recommend seeing a doctor first (emergency → call 119 in Japan);
  no medicine suggestions in that case (support items only).
- Pharmacist-only (Class 1) items require a pharmacist consultation at purchase.
- Abuse-prone OTC (abuse_control: some cough / cold / allergy medicines): one per customer, pharmacist first for
  multi-pack / long-term / off-label signals.
- Pregnancy, breastfeeding, chronic conditions, other medicines → "talk to a doctor or pharmacist before use".

API:
- detect_red_flags(text)  → [{code, level: emergency|consult|crisis, label}]
- red_flag_preface(flags) text that MUST open the reply (server emits it before the model text)
- detect_abuse(text)      multi-pack / long-term / off-label use signals
- TextFilter              post-filter for prohibited claims (sentence-buffered for streaming)
- filter_items(...)       one medicine per symptom group, age_min, red-flag → no medicines
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

CLASS1 = "Pharmacist-only (Class 1)"
DESIGNATED2 = "Designated Class 2"
QUASI = "Quasi-drug"
MEDICINE_CLASSES = (CLASS1, DESIGNATED2, "Class 2", "Class 3")

DISCLAIMER = ("This service provides general information only and does not replace a diagnosis by a doctor. "
              "Read the package insert and follow the directions for use. If symptoms persist or get worse, "
              "see a doctor. For questions, ask the pharmacist or registered seller in store.")

DRUG_CLASS_NOTES = {
    CLASS1: "Pharmacist-only (Class 1): a pharmacist must explain this medicine at purchase. "
            "Not available when no pharmacist is on duty.",
    DESIGNATED2: "Designated Class 2: check the warnings (who should not use it) before use. "
                 "Ask a pharmacist or registered seller if unsure.",
    "Class 2": "Class 2 OTC medicine: ask a pharmacist or registered seller if you have questions.",
    "Class 3": "Class 3 OTC medicine",
    QUASI: "Quasi-drug",
}
ABUSE_NOTE = ("Purchase limit: one per customer. Customers under 18 may buy one small pack only (ID/age check). "
              "This item is handed over at the pharmacy counter.")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    return t.replace("°C", "℃").replace("° C", "℃")


_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "a": 1, "an": 1, "a couple of": 2, "a few": 3, "several": 3}


def _num(s: str) -> float | None:
    s = (s or "").strip().lower()
    if not s:
        return None
    if s in _WORD_NUM:
        return float(_WORD_NUM[s])
    try:
        return float(s)
    except ValueError:
        return None


_NUMW = r"(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|a couple of|a few|several|an?)"


def _temperatures(t: str) -> list[float]:
    """Celsius temperatures mentioned (38.5℃ / 38.5 C / 38.5 degrees); Fahrenheit (101F) converted."""
    out = []
    for m in re.finditer(r"(\d{2,3}(?:\.\d)?)\s*(℃|°|degrees?\s*(?:c|celsius|f|fahrenheit)?|c\b|f\b)", t, re.I):
        v = float(m.group(1))
        unit = m.group(2).lower()
        if ("f" in unit and "c" not in unit) or v >= 90:
            v = round((v - 32) * 5 / 9, 1)
        if 34 <= v <= 43:
            out.append(v)
    for m in re.finditer(r"(?:temperature|fever|temp)\s+(?:of\s+|is\s+|at\s+)?(\d{2,3}(?:\.\d)?)\b", t, re.I):
        v = float(m.group(1))
        if v >= 90:
            v = round((v - 32) * 5 / 9, 1)
        if 34 <= v <= 43:
            out.append(v)
    return out


def _days(t: str) -> float:
    best = 0.0
    for m in re.finditer(_NUMW + r"\s*days?\b", t, re.I):
        v = _num(m.group(1))
        if v:
            best = max(best, v)
    for m in re.finditer(_NUMW + r"\s*weeks?\b", t, re.I):
        v = _num(m.group(1))
        if v:
            best = max(best, v * 7)
    if re.search(r"\b(a week|over a week|more than a week|since last week)\b", t, re.I):
        best = max(best, 7)
    if re.search(r"\b(day before yesterday)\b", t, re.I):
        best = max(best, 2)
    return best


def _infant_months(t: str) -> float | None:
    m = re.search(_NUMW + r"[\s-]*(months?|weeks?)[\s-]*old", t, re.I)
    if m:
        v = _num(m.group(1))
        if v is not None and m.group(2).lower().startswith("week"):
            return v / 4.3
        return v
    if re.search(r"\b(newborn)\b", t, re.I):
        return 0.5
    return None


def child_age(text: str) -> float | None:
    """Age (years) of the person the consultation is about, when stated (e.g. "my 5-year-old", "2 months old")."""
    t = _norm(text)
    mo = _infant_months(t)
    if mo is not None:
        return mo / 12
    m = re.search(_NUMW + r"[\s-]*(?:years?|yrs?)[\s-]*old", t, re.I) or \
        re.search(r"\b(?:aged?|age)\s+(\d{1,2})\b", t, re.I) or \
        re.search(r"\b(\d{1,2})[\s-]*(?:yo|y/o)\b", t, re.I)
    if m:
        return _num(m.group(1))
    if re.search(r"\b(baby|infant)\b", t, re.I):
        return 0.5
    if re.search(r"\b(toddler|preschooler)\b", t, re.I):
        return 3
    if re.search(r"\b(grade schooler|elementary school)\b", t, re.I):
        return 8
    return None


FEVER_WORDS = r"\b(fever|feverish|temperature)\b"
KID = r"(baby|infant|toddler|child|kid|son|daughter|little one)"
RULES: list[tuple[str, str, str, str]] = [
    # code, level, regex, label
    ("crisis", "crisis", r"(kill myself|want to die|end my life|suicid|self[- ]harm|hurt myself|cut myself|overdos|"
                         r"\bOD\b|take (?:them |it )?all at once|take a lot of pills|whole (?:box|bottle) at once)",
     "signs of self-harm or overdose"),
    ("breathing", "emergency", r"(can'?t breathe|cannot breathe|hard to breathe|trouble breathing|difficulty breathing|"
                               r"short(?:ness)? of breath|wheez|chest pain|chest hurts|lips (?:are |turned )?(?:blue|purple))",
     "difficulty breathing or chest pain"),
    ("seizure", "emergency", r"(seizure|convulsion|fainted|passed out|unconscious|not responding|unresponsive|"
                             r"confused and drowsy)",
     "seizure or loss of consciousness"),
    ("severe_headache", "consult", r"(worst headache|severe headache|splitting headache|stiff neck|can'?t bend (?:my )?neck)",
     "severe headache or stiff neck"),
    ("vomiting", "consult", r"(keep(?:s)? (?:on )?(?:throwing up|vomiting)|vomit(?:ing|ed)? (?:again and again|repeatedly|"
                            r"several times|many times)|can'?t stop (?:vomiting|throwing up))",
     "repeated vomiting"),
    ("dehydration", "consult", r"(can'?t keep (?:water|fluids|anything) down|can'?t drink|not drinking|"
                               r"(?:hasn'?t|haven'?t|not) (?:peed|urinated)|no urine)",
     "unable to keep fluids down / not urinating"),
    ("listless_child", "consult", KID + r".{0,20}(listless|limp|floppy|very lethargic|won'?t wake)",
     "listless infant or child"),
    ("elderly_appetite", "consult", r"(elderly|grandma|grandpa|grandmother|grandfather|my (?:mother|father|mom|dad)).{0,25}"
                                    r"(lost (?:his |her |their )?appetite|stopped eating|not eating)",
     "sudden loss of appetite in an older person"),
    ("pregnancy", "consult", r"(pregnan|breastfeed|breast-feed|nursing)", "pregnant or breastfeeding"),
    ("chronic", "consult", r"(chronic|heart (?:disease|condition|problem)|kidney (?:disease|problem)|liver (?:disease|problem)|"
                           r"diabet|high blood pressure|hypertension|blood pressure (?:medicine|medication|pills)|asthma)",
     "existing medical condition"),
    ("other_meds", "consult", r"(other (?:medicine|medication)s?|taking (?:medicine|medication|pills|tablets)|"
                              r"on (?:medication|meds)|prescri(?:bed|ption))",
     "taking other medicines"),
    ("long_lasting", "consult", r"((?:[5-9]|five|six|seven|eight|nine|ten|\d{2})\s*days|(?:a|one|two|\d)\s*weeks?|"
                                r"getting worse|not getting better|won'?t go away)",
     "symptoms lasting 5+ days or getting worse"),
    ("bleeding", "consult", r"(blood in (?:my |the )?(?:stool|poop)|bloody stool|black stool|black poop|severe stomach pain|"
                            r"severe abdominal pain)",
     "bloody/black stool or severe stomach pain"),
    ("side_effect", "consult", r"((medicine|medication|pill|tablet|took).{0,25}(rash|hives|itchy|swelling|hard to breathe)|"
                               r"(rash|hives).{0,25}(after (?:taking|the medicine)))",
     "possible side effect"),
]


def detect_red_flags(text: str) -> list[dict[str, str]]:
    t = _norm(text)
    flags: list[dict[str, str]] = []
    for code, level, rx, label in RULES:
        if re.search(rx, t, re.I):
            flags.append({"code": code, "level": level, "label": label})
    temps = _temperatures(t)
    days = _days(t)
    if temps and max(temps) >= 41:
        flags.append({"code": "fever_41", "level": "emergency", "label": "fever of 41°C or higher"})
    elif temps and max(temps) >= 38.5 and days >= 3:
        flags.append({"code": "high_fever_3days", "level": "consult", "label": "fever of 38.5°C+ for 3+ days"})
    elif re.search(FEVER_WORDS, t, re.I) and re.search(r"(won'?t (?:go|come) down|not going down|still high|keeps? going)",
                                                        t, re.I) and (days >= 3 or (temps and max(temps) >= 38.5)):
        flags.append({"code": "fever_persistent", "level": "consult", "label": "high fever that won't come down"})
    mo = _infant_months(t)
    if mo is not None and mo < 3 and re.search(FEVER_WORDS, t, re.I):
        flags.append({"code": "infant_fever", "level": "consult", "label": "fever in a baby under 3 months"})
    seen, out = set(), []
    for f in flags:
        if f["code"] not in seen:
            seen.add(f["code"])
            out.append(f)
    return out


def is_blocking(flags: list[dict]) -> bool:
    """True when product suggestions must be limited to non-medicine support items (or none)."""
    return any(f["level"] in ("emergency", "crisis") or f["code"] in (
        "high_fever_3days", "fever_persistent", "infant_fever", "severe_headache", "vomiting", "dehydration",
        "listless_child", "bleeding", "side_effect", "elderly_appetite", "long_lasting") for f in flags)


def only_consult(flags: list[dict]) -> bool:
    """Pregnancy / chronic condition / other medicines: suggestions allowed only with a doctor/pharmacist check."""
    return bool(flags) and all(f["code"] in ("pregnancy", "chronic", "other_meds") for f in flags)


def red_flag_preface(flags: list[dict]) -> str:
    if not flags:
        return ""
    levels = {f["level"] for f in flags}
    labels = ", ".join(f["label"] for f in flags[:3])
    if "crisis" in levels:
        return ("Thank you for telling me how you feel. You don't have to handle this alone — please reach out now:\n"
                "- TELL Lifeline (English, Japan): 03-5774-0992\n"
                "- If you have taken too much medicine or feel unwell, call 119 (ambulance)\n"
                "I won't suggest any products for this. Our pharmacist is also here for you at any time.")
    if "emergency" in levels:
        return (f"**Please seek medical care now.** With {labels}, call 119 (ambulance) right away. "
                "If you are unsure, call #7119 (emergency medical advice line).")
    if only_consult(flags):
        return (f"**Please talk to a doctor or pharmacist before using any medicine** ({labels}). "
                "Our in-store pharmacist can check interactions and whether it is right for you.")
    return (f"**We recommend seeing a doctor** ({labels}). "
            "If you are unsure, call #7119 for medical advice. The items below are only to help until you see a doctor.")


ABUSE_RX = [
    r"\b([2-9]|two|three|four|five|six|several|multiple|many|lots of)\s*(boxes|packs|bottles|packets)\b",
    r"\b(in bulk|bulk|stock up|stockpile|a lot of|a dozen|a case of)\b",
    r"\b(every day for (?:months|weeks|years)|for months|for years|long[- ]term|keep taking)\b",
    r"\b(can'?t sleep|to get high|feel high|get a buzz|take extra|double (?:the )?dose)\b",
]


def detect_abuse(text: str) -> list[str]:
    t = _norm(text)
    medicine_ctx = re.search(r"\b(medicine|meds|cough (?:syrup|medicine|drops)|cold (?:medicine|tablets)|allergy|"
                             r"antihistamine|syrup|tablets?|pills?|capsules?)\b", t, re.I)
    if not medicine_ctx:
        return []
    return [rx for rx in ABUSE_RX if re.search(rx, t, re.I)]


ABUSE_GUIDANCE = ("Some cough, cold and allergy medicines (abuse-prone OTC) are limited to **one per customer**. "
                  "Customers under 18 may buy one small pack only, with an age check. If you need more or plan to use it "
                  "long term, please talk to our pharmacist.")

# prohibited claims → compliant replacements (applied to every model text)
REPLACEMENTS: list[tuple[str, str]] = [
    (r"(?i)\b(?:will|is guaranteed to|guaranteed to) (?:cure|fix|heal)\b", "may help relieve"),
    (r"(?i)\bcures?\b", "may help relieve"),
    (r"(?i)\b(?:works|work) (?:instantly|immediately|every time|100%)\b", "may help"),
    (r"(?i)\bguaranteed\b", "expected"),
    (r"(?i)\b(?:has |have |with )?(?:no|zero) side[- ]effects\b", "may have side effects — check the package insert"),
    (r"(?i)\b(?:completely |totally |perfectly |100% )safe\b", "should be used as directed"),
    (r"(?m)^\s*[-*_]{3,}\s*$", ""),
    (r"(?i)\b(?:the )?(?:strongest|most effective|number one|no\.?\s*1|#1)\b(?: medicine| remedy| product)?", "a suitable option"),
    # "best" only as a medicine claim ("the best cold medicine"), not "the best route"
    (r"(?i)\b(?:the )?best (?:cold |cough |pain |allergy |eye )?(?:medicine|remedy|reliever|drops|tablets|cure)\b", "a suitable option"),
    (r"(?i)\b(?:doctors?|pharmacists?|experts?) (?:recommend(?:ed)?|approved?|endorse[ds]?)\b", "a matching option"),
    (r"(?i)\byou (?:probably |likely |may |might )?have (?:the )?(flu|influenza|covid(?:-19)?|pneumonia|gastroenteritis|"
     r"an? (?:infection|ulcer|virus))\b",
     r"symptoms like these can have many causes — please see a doctor if you are concerned"),
    (r"(?i)\b(?:it'?s|this is|sounds like) (?:the )?(flu|influenza|covid(?:-19)?|pneumonia|gastroenteritis)\b",
     "a doctor can check symptoms like these"),
]
BANNED_CHECK = re.compile(r"(?i)(\bwill cure\b|\bguaranteed\b|\bno side[- ]effects\b|\bnumber one\b|\bno\.?\s*1\b|"
                          r"\bstrongest\b|\bcompletely safe\b)")


def sanitize(text: str) -> str:
    out = text
    for rx, rep in REPLACEMENTS:
        out = re.sub(rx, rep, out)
    return out


def has_banned(text: str) -> bool:
    return bool(BANNED_CHECK.search(text or ""))


class TextFilter:
    """Streaming post-filter: buffers model text until a sentence boundary so a banned phrase split across deltas
    is still replaced."""

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, delta: str) -> str:
        self.buf += delta or ""
        cut = -1
        for m in re.finditer(r"[.!?](?=\s)|\n", self.buf):
            cut = m.end() - 1
        if cut < 0:
            return ""
        out, self.buf = self.buf[:cut + 1], self.buf[cut + 1:]
        return sanitize(out)

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return sanitize(out)


# symptom groups: at most one medicine per group (avoid duplicate active ingredients). Matched case-insensitively
# against the 2nd level of the English category path (shared/catalog/category_map_en.json, "Health & Medicine > <group>").
GROUPS = [
    ("fever_cold", r"(cold medicine|pain & fever relief|pain reliever|fever)"),
    ("cough_throat", r"(throat & cough|cough|throat)"),
    ("nose", r"(nasal allergy|allergy|nasal)"),
    ("stomach", r"(gastrointestinal|stomach|digestive)"),
    ("eye", r"(eye drops|eye)"),
    ("topical", r"(topical|patch)"),
    ("vitamin", r"(vitamin|tonic)"),
    ("skin", r"(skin)"),
]


def symptom_group(product: dict) -> str:
    path = str(product.get("category_path") or "")
    parts = [x.strip() for x in path.split(">")]
    sub = parts[1] if len(parts) > 1 else path
    for g, rx in GROUPS:
        if re.search(rx, sub, re.I):
            return g
    return "other:" + path


KID_TITLE = re.compile(r"(?i)\b(kids?|children'?s|child|junior|pediatric|infant|baby)\b")


def filter_items(items: list[dict], flags: list[dict], age: float | None = None,
                 max_medicines: int = 2, abuse: bool = False) -> tuple[list[dict], list[str]]:
    """items: [{product: Product, …}]. Returns (kept, notes) applying the OTC rules server-side."""
    notes: list[str] = []
    kept: list[dict] = []
    blocking = is_blocking(flags)
    crisis = any(f["level"] == "crisis" for f in flags)
    groups_used: set[str] = set()
    meds = 0
    for it in items:
        p = it.get("product") or {}
        dc = p.get("drug_class") or ""
        is_med = dc in MEDICINE_CLASSES
        if crisis:
            notes.append("crisis: all products removed")
            continue
        if abuse and p.get("abuse_control"):
            notes.append(f"abuse signal: removed abuse_control product {p.get('id')} (pharmacist consultation)")
            continue
        if is_med and blocking:
            notes.append(f"red flag: removed medicine {p.get('id')}")
            continue
        if is_med and age is not None:
            amin = p.get("age_min")
            if amin and age < float(amin):
                notes.append(f"age_min {amin} > {age}: removed {p.get('id')}")
                continue
            title = str(p.get("title") or "")
            if age < 15 and not KID_TITLE.search(title) and (amin or 0) >= 15:
                notes.append(f"under 15: removed adult medicine {p.get('id')}")
                continue
        if is_med:
            g = symptom_group(p)
            if g in groups_used or meds >= max_medicines:
                notes.append(f"duplicate medicine group {g}: removed {p.get('id')}")
                continue
            groups_used.add(g)
            meds += 1
        kept.append(it)
    return kept, notes


def compliance_notes(product: dict) -> list[str]:
    out = []
    dc = product.get("drug_class")
    if dc in DRUG_CLASS_NOTES:
        out.append(DRUG_CLASS_NOTES[dc])
    if dc == CLASS1:
        out.append("In store, ask the pharmacist at the pharmacy counter. Online orders ship after a pharmacist check.")
    if product.get("abuse_control"):
        out.append(ABUSE_NOTE)
    return out


def item_limits(product: dict) -> dict[str, Any]:
    return {"max_qty": 1 if product.get("abuse_control") else 10,
            "pharmacist_required": product.get("drug_class") == CLASS1}
