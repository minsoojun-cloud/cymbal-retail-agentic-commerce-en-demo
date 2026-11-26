"""Scripted concierge (agentMode=mock, or when the live model is unavailable). Same SSE events as the live agent.

Covers the demo scenarios deterministically: fever / cold consultation (medicine + support items, numbered route),
red flags (see a doctor first, no medicine), abuse-prone OTC quantity requests, meal ideas (dish-first recipe cards),
plain product finding, greeting.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .. import deps, inventory, offers, repository, stores
from . import safety
from .tools import IN_STOCK, _shelf_text, build_store_items

IN = set(IN_STOCK)
AUTHOR = "store_concierge"


def _hay(n: dict) -> str:
    return (n.get("title", "") + " " + (n.get("category_path") or "")).lower()


def _pick(store_id: str, pred: Callable[[dict], bool], age: float | None = None, prefer: str = "") -> dict | None:
    cat = deps.get_catalog()
    best, bscore = None, None
    prefer = prefer.lower()
    for n in cat.items.values():
        if not pred(n):
            continue
        p = deps.to_product(n)
        if p is None:
            continue
        if age is not None and p.get("drug_class") in safety.MEDICINE_CLASSES and (p.get("age_min") or 0) > age:
            continue
        inv = inventory.inventory_for(store_id, p["id"])
        status = inv["status"] if inv else "not_carried"
        score = (0 if status in IN else 1 if status == "out_of_stock" else 2,
                 0 if prefer and prefer in p["title"].lower() else 1, len(p["title"]), p["id"])
        if bscore is None or score < bscore:
            best, bscore = p, score
    return best


def _has(rx: str, *, path_only: bool = False) -> Callable[[dict], bool]:
    r = re.compile(rx, re.I)
    if path_only:
        return lambda n: bool(r.search(n.get("category_path") or ""))
    return lambda n: bool(r.search(_hay(n)))


KIDS = re.compile(r"\b(kids?|children'?s|child|junior|pediatric|baby)\b", re.I)
PAIN_RX = r"(pain & fever relief|pain reliever)"
COLD_RX = r"(> cold medicine)"


def fever_items(store_id: str, age: float | None, flags: list[dict]) -> list[dict]:
    child = age is not None and age < 15
    items: list[dict] = []
    scen = repository.load_scenarios().get(f"fever_set_{store_id}") or []
    if not safety.is_blocking(flags) and not any(f["level"] == "crisis" for f in flags):
        if child:
            med = _pick(store_id, _has(COLD_RX, path_only=True), age, prefer="kids") or \
                _pick(store_id, _has(PAIN_RX, path_only=True), age)
        else:
            med = None
            for pid in scen:
                p = deps.to_product(pid)
                if p and KIDS.search(p.get("title", "")):
                    continue  # adult consultation → adult product
                if p and safety.symptom_group(p) == "fever_cold" and p.get("drug_class") in safety.MEDICINE_CLASSES:
                    med = p
                    break
            med = med or _pick(store_id, lambda n: bool(re.search(PAIN_RX, n.get("category_path") or "", re.I))
                               and not KIDS.search(n.get("title", "")), age)
        if med:
            is_pain = re.search(PAIN_RX, med.get("category_path") or "", re.I)
            items.append({"product_id": med["id"], "role": "Pain reliever" if is_pain else "Cold medicine",
                          "reason": "Matches fever and headache symptoms"})
    sheet = _pick(store_id, _has(r"(cooling (?:gel )?(?:sheet|patch)|cooling patches)"), prefer="kids" if child else "adult")
    ors = _pick(store_id, _has(r"oral rehydration"), prefer="500")
    thermo = _pick(store_id, _has(r"thermometer"), prefer="baby" if age is not None and age < 2 else "predictive")
    mask = _pick(store_id, _has(r"hygiene products > .*face masks", path_only=True), prefer="kids" if child else "regular")
    for p, role, reason in ((sheet, "Cooling sheet", "Cools the forehead so you can rest"),
                            (ors, "Hydration", "Fluids and electrolytes during a fever"),
                            (thermo, "Thermometer", "Keep track of your temperature"),
                            (mask, "Mask", "Helps protect the rest of the family")):
        if p:
            items.append({"product_id": p["id"], "role": role, "reason": reason})
    return items[:5]


FEVER_RX = r"\b(fever|feverish|temperature|cold|flu|sore throat|throat|cough|chills|achy|run down)\b"
MEAL_RX = r"\b(dinner|lunch|recipe|recipes|cook|cooking|meal|meals|what should i make|dish|dishes|menu|curry)\b"


def _first_name(persona: dict) -> str:
    return str(persona.get("name") or "").split(" ")[0]


def respond(message: str, store_id: str, customer_id: str, flags: list[dict], abuse: list[str],
            age: float | None, emit_preface: bool = True) -> list[dict[str, Any]]:
    """Returns the SSE payload list (without done) for one scripted turn."""
    out: list[dict[str, Any]] = [{"type": "status", "text": "Thinking…"}]
    persona = offers.get_persona(customer_id)
    name = _first_name(persona)
    store = stores.get_store(store_id) or {}
    pre = safety.red_flag_preface(flags)
    if pre and emit_preface:
        out.append({"type": "text", "text": pre + "\n\n", "author": AUTHOR})
    crisis = any(f["level"] == "crisis" for f in flags)
    if crisis:
        out.append({"type": "chips", "items": ["Talk to a pharmacist", "Store phone number?"]})
        return out
    if abuse:
        if emit_preface or pre:
            out.append({"type": "text", "text": safety.ABUSE_GUIDANCE, "author": AUTHOR})
        out.append({"type": "chips", "items": ["Talk to a pharmacist", "Just one, please", "Something for a sore throat"]})
        return out
    if re.search(FEVER_RX, message, re.I) and not re.search(MEAL_RX, message, re.I):
        out.append({"type": "status", "text": "Searching products…"})
        items = fever_items(store_id, age, flags)
        blocking = safety.is_blocking(flags)
        advice = ("- Drink plenty of fluids, keep warm and get enough rest\n- Humidify the room (about 50–60%)\n"
                  "- If the fever is high or lasts, see a doctor early")
        theme = "Support until you see a doctor (no medicines shown)" if blocking else \
            ("Self-care for your child's fever or cold" if age is not None and age < 15 else
             "Self-care for early cold symptoms")
        args, summary = build_store_items(store_id, customer_id, theme, advice, items, flags, age, red_flag=blocking)
        out.append({"type": "status", "text": "Preparing shelves and route…"})
        out.append({"type": "action", "name": "show_store_items", "args": args,
                    "result": {"ok": True, "shown": summary["shown"]}})
        numbered = [it for it in args["items"] if it.get("no")]
        lines = []
        if not pre:
            lines.append(f"Sorry you're not feeling well, {name}. Here are products at {store.get('short_name', '')} "
                         "that match your symptoms.")
        else:
            lines.append("Here are a few items that can help until you see a doctor.")
        if numbered:
            first = next((it for it in numbered if it["product"].get("drug_class")), numbered[0])
            sh = _shelf_text((first["availability"].get("current") or {}).get("shelf")) or ""
            lines.append(f"#{first['no']} \"{first['product']['title']}\" is at {sh}. Following the numbers, it's about "
                         f"{int((args['route'] or {}).get('length_m') or 0)} m to the checkout.")
        missing = [it for it in args["items"] if not it.get("no")]
        if missing:
            alt = missing[0]
            near = [n for n in alt["availability"]["nearby"] if n["status"] in IN]
            where = f"{near[0]['short_name']} (about {near[0]['distance_m'] / 1000:.1f} km)" if near else "online"
            lines.append(f"\"{alt['product']['title']}\" is out of stock here, but you can get it at {where}.")
        if customer_id == "CR-0002" and not blocking:
            lines.append("Please check with the pharmacist that it is OK with your current medicine.")
        if any(it["product"].get("drug_class") == safety.CLASS1 for it in args["items"]):
            lines.append("A pharmacist needs to explain the Pharmacist-only medicine at purchase.")
        lines.append("Please follow the directions on the package. Tap **Find all on the map** to walk the shelves in order.")
        out.append({"type": "text", "text": " ".join(lines), "author": AUTHOR})
        out.append({"type": "chips", "items": ["Find all on the map", "Anything for kids?", "Stock at nearby stores?"]})
        return out
    if re.fullmatch(r"\s*(hi|hello|hey|good (?:morning|afternoon|evening))\s*[!.]?\s*", message, re.I):
        out.append({"type": "text", "text": f"Hi {name}! I'm the AI Concierge at {store.get('short_name', '')}. "
                    "I can find products for a need or symptom, suggest recipes, and show stock, shelf locations and the "
                    "best route.", "author": AUTHOR})
        out.append({"type": "chips", "items": ["I feel feverish", "Dinner ideas", "Where is the oral rehydration solution?"]})
        return out
    if re.search(MEAL_RX, message, re.I):
        out.append({"type": "status", "text": "Finding dishes…"})
        allergy = "" if customer_id != "CR-0001" else " I've left out shrimp for Yui and Haru."
        out.append({"type": "action", "name": "show_recipes", "args": {"query": message, "store_id": store_id},
                    "result": {"ok": True}})
        out.append({"type": "text", "text": f"Here are 3 dish ideas for tonight, {name}.{allergy} Pick one to adjust "
                    "servings, make it kid-friendly or quick, then add all the ingredients to your cart.",
                    "author": AUTHOR})
        out.append({"type": "chips", "items": ["Something quicker", "Kid-friendly please", "Did I forget anything?"]})
        return out
    # plain product finding
    q = re.sub(r"(?i)\b(where (?:is|are|can i find)|do you have|i'?m looking for|i need|i want|looking for|find|"
               r"please|the|some|any)\b|[?.!]", " ", message)
    q = re.sub(r"\s+", " ", q).strip() or message
    out.append({"type": "status", "text": "Searching products…"})
    try:
        res = deps.get_engine().search(q, "", {}, [], "", "", "", "", 1, 12, "store-navi-mock", "")
        ids = [r["id"] for r in res.get("results") or []]
    except Exception:  # noqa: BLE001
        ids = [n["id"] for _, n in deps.get_catalog().text_search(q)[:12]]
    if not ids:
        ids = deps.symptom_search(q, 12)
    prods = deps.hydrate(ids)
    prods.sort(key=lambda p: (inventory.inventory_for(store_id, p["id"]) or {}).get("status") not in IN)
    items = [{"product_id": p["id"], "role": p.get("category_path", "").split(">")[-1].strip(),
              "reason": "Matches what you're looking for"} for p in prods[:3]]
    if not items:
        out.append({"type": "text", "text": f"Sorry, I couldn't find anything for \"{q}\". Please try other words.",
                    "author": AUTHOR})
        return out
    args, summary = build_store_items(store_id, customer_id, f"Options for \"{q}\"", "", items, flags, age)
    out.append({"type": "action", "name": "show_store_items", "args": args, "result": {"ok": True, "shown": summary["shown"]}})
    out.append({"type": "text", "text": f"Here {'is' if summary['shown'] == 1 else 'are'} {summary['shown']} "
                f"option{'s' if summary['shown'] != 1 else ''} for \"{q}\". I can guide you to the shelves in number order.",
                "author": AUTHOR})
    out.append({"type": "chips", "items": ["Find all on the map", "Stock at nearby stores?"]})
    return out
