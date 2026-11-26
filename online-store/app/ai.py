""""AI Overview" — Gemini (Vertex AI, google-genai) summary + per-product highlight reasons + follow-up.

Returns JSON: {summary, highlights:[{product_id, reason}], followup:{question, answers:[str]}, source}
Falls back to a deterministic template when Gemini is unavailable (AI_MOCK=1, no credentials, timeout).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger("cymbal-site.ai")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_LOCATION = os.environ.get("GEMINI_LOCATION", "global")
PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
AI_TIMEOUT = float(os.environ.get("AI_TIMEOUT_SEC", "25"))

_client = None
_cache: dict[tuple, dict] = {}

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "highlights": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "product_id": {"type": "STRING"}, "reason": {"type": "STRING"}}, "required": ["product_id", "reason"]}},
        "followup": {"type": "OBJECT", "properties": {
            "question": {"type": "STRING"}, "answers": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["question", "answers"]},
    },
    "required": ["summary", "highlights", "followup"],
}

PROMPT = """You are the search assistant of the online store of "Cymbal Retail", a Japanese supermarket chain (food, daily goods, beauty, OTC).
Read the customer's search query and the top search results, and write a short summary like Google Search's "AI Overview".

# Rules
- Write everything in natural US English.
- summary: 2-3 sentences, at most 50 words. Give an overview of the options for the search intent (price range, sales, features).
  Do not exaggerate or invent facts.
- highlights: up to 3 especially recommended products. product_id must be an id from the list, unchanged. reason: at most 8 words
  (e.g. "20% off this week, rated 4.6").
- followup: a short question (at most 8 words) to narrow down the results, plus 3-5 answer options (1-3 words each, usable as
  extra search keywords).
- Prices are tax-included Japanese yen, written like ¥398.

# Search query
{q}

# Top results
{items}
"""


def _get_client():
    global _client
    if _client is None:
        from google import genai  # lazy

        _client = genai.Client(vertexai=True, project=PROJECT_ID or None, location=GEMINI_LOCATION)
    return _client


def template_summary(q: str, products: list[dict]) -> dict:
    if not products:
        return {"summary": f'No products matched "{q}". Try a different keyword.',
                "highlights": [], "followup": {"question": "", "answers": []}, "source": "template"}
    prices = [p["price"] for p in products if p.get("price")]
    sale = [p for p in products if p.get("on_sale")]
    cats = []
    for p in products:
        if p.get("category") and p["category"] not in cats:
            cats.append(p["category"])
    s = (f'We found {len(products)} top matches for "{q}", priced from ¥{min(prices):,} to ¥{max(prices):,}. '
         if prices else "")
    if sale:
        s += f"{len(sale)} of them {'is' if len(sale) == 1 else 'are'} on sale. "
    if cats:
        s += f"Main categories: {', '.join(cats[:3])}."
    top = sorted(products, key=lambda p: (-(p.get("on_sale") or 0), -(p.get("rating") or 0)))[:3]
    hl = []
    for p in top:
        bits = []
        if p.get("on_sale") and p.get("discount_rate"):
            bits.append(f"{int(round(p['discount_rate'] * 100))}% off")
        bits.append(f"rated ★{p.get('rating', 0)}")
        hl.append({"product_id": p["id"], "reason": ", ".join(bits).capitalize()})
    labels: list[str] = []
    for p in products:
        for l in p.get("labels") or []:
            if l not in labels:
                labels.append(l)
    return {"summary": s.strip(), "highlights": hl,
            "followup": {"question": "What matters most to you?", "answers": (labels[:4] or ["On sale", "Popular"])},
            "source": "template"}


def _items_text(products: list[dict]) -> str:
    lines = []
    for p in products[:10]:
        sale = f" sale {int(round((p.get('discount_rate') or 0) * 100))}% off (regular ¥{p.get('original_price', 0):,})" if p.get("on_sale") else ""
        lines.append(f"- id={p['id']} | {p['title']} | ¥{p.get('price', 0):,}{sale} | ★{p.get('rating')}({p.get('rating_count')}) | "
                     f"{p.get('category', '')} | {' '.join(p.get('labels') or [])}")
    return "\n".join(lines)


def _sanitize(data: dict, products: list[dict]) -> dict:
    ids = {p["id"] for p in products}
    hl = [h for h in (data.get("highlights") or []) if isinstance(h, dict) and h.get("product_id") in ids][:3]
    fu = data.get("followup") or {}
    return {"summary": str(data.get("summary") or "").strip(), "highlights": hl,
            "followup": {"question": str(fu.get("question") or ""), "answers": [str(a) for a in (fu.get("answers") or [])][:5]},
            "source": "gemini", "model": GEMINI_MODEL}


def _generate_sync(q: str, products: list[dict]) -> dict:
    from google.genai import types

    client = _get_client()
    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=PROMPT.format(q=q, items=_items_text(products)),
        config=types.GenerateContentConfig(
            temperature=0.3, response_mime_type="application/json", response_schema=SCHEMA,
            thinking_config=(types.ThinkingConfig(thinking_level="low") if "gemini-3" in GEMINI_MODEL
                             else types.ThinkingConfig(thinking_budget=0)),
        ),
    )
    return _sanitize(json.loads(resp.text), products)


async def ai_summary(q: str, products: list[dict]) -> dict:
    key = (q, tuple(p["id"] for p in products[:10]))
    if key in _cache:
        return _cache[key]
    if os.environ.get("AI_MOCK") == "1" or not products:
        return template_summary(q, products)
    try:
        out = await asyncio.wait_for(asyncio.to_thread(_generate_sync, q, products), timeout=AI_TIMEOUT)
        if not out["summary"]:
            raise ValueError("empty summary")
        _cache[key] = out
        if len(_cache) > 500:
            _cache.pop(next(iter(_cache)))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini summary failed (%s); using template", str(e)[:300])
        out = template_summary(q, products)
        out["error"] = str(e)[:200]
        return out
