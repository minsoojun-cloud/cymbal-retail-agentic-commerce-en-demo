"""Official Vertex AI list prices (Standard / online, Global endpoint, <=200K input tokens) + cost math.

Source: https://cloud.google.com/vertex-ai/generative-ai/pricing  — prices retrieved on PRICE_RETRIEVED_AT.
Only numbers that appear on that page are recorded here. A model whose price (input AND text output) is not on
the page has `None` and is shown as "See the official pricing page" and excluded from totals — never estimated.

Billing basis on the page: "Text output (response and reasoning)" → thinking tokens are billed at the output rate.
Batch / Flex / Priority prices are intentionally NOT used (the studio shows standard online list prices only).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

PRICING_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
PRICE_RETRIEVED_AT = "2026-11-11"          # price retrieval date (page fetched and transcribed on this date)
PRICE_REGION = "Global"                    # the studio calls Gemini @ global


@dataclass(frozen=True)
class PricePeriod:
    input_usd: float                       # per 1M input tokens (text)
    output_usd: float                      # per 1M output tokens (response + reasoning)
    start: str | None = None               # inclusive, YYYY-MM-DD
    end: str | None = None                 # inclusive, YYYY-MM-DD
    note: str = ""                         # label as written on the page
    promo: bool = False                    # time-limited promotional (discounted) price


_PROMO = "promotional price listed on the official page (50% of net spend credited back)"

# model id -> periods (Standard tab, Global, <= 200K input tokens). Transcribed from the official page on 2026-11-11.
OFFICIAL_PRICES: dict[str, list[PricePeriod] | None] = {
    "gemini-3.8-flash": [
        PricePeriod(0.75, 3.75, None, "2026-12-31", f"Until Dec 31, 2026: {_PROMO}", True),
        PricePeriod(1.50, 7.50, "2027-01-01", None, "List price (official page price from Jan 1, 2027)"),
    ],
    "gemini-3.7-flash": [
        PricePeriod(0.75, 3.75, None, "2026-12-31", f"Until Dec 31, 2026: {_PROMO}", True),
        PricePeriod(1.50, 7.50, "2027-01-01", None, "List price (official page price from Jan 1, 2027)"),
    ],
    "gemini-3.6-flash": [
        PricePeriod(0.75, 3.75, None, "2026-12-31", f"Until Dec 31, 2026: {_PROMO}", True),
        PricePeriod(1.50, 7.50, "2027-01-01", None, "List price (official page price from Jan 1, 2027)"),
    ],
    "gemini-3.5-flash": [PricePeriod(1.50, 9.00)],
    "gemini-3.5-flash-lite": [PricePeriod(0.30, 2.50)],
    "gemini-3.1-flash-lite": [PricePeriod(0.25, 1.50)],
    "gemini-3.1-pro-preview": [PricePeriod(2.00, 12.00)],
    # The Standard table lists only input prices for Gemini 3 Flash Preview (no text-output row) → not priced here.
    "gemini-3-flash-preview": None,
    "gemini-4-carbon": [
        PricePeriod(2.00, 10.00, None, "2026-11-30", f"Until Nov 30, 2026: {_PROMO}", True),
        PricePeriod(4.00, 20.00, "2026-12-01", None, "List price (official page price from Dec 1, 2026)"),
    ],
    "gemini-4-argon": [
        PricePeriod(2.00, 10.00, None, "2026-11-30", f"Until Nov 30, 2026: {_PROMO}", True),
        PricePeriod(4.00, 20.00, "2026-12-01", None, "List price (official page price from Dec 1, 2026)"),
    ],
}


def today_jst() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).date().isoformat()


LIST = "list"      # default: regular list price, NO promotion / discount applied
PROMO = "promo"    # reference only: time-limited promotional (discounted) price shown on the page for today
_ALIASES = {"regular": LIST, "current": PROMO}


def norm_basis(basis: str | None) -> str:
    b = _ALIASES.get(basis or LIST, basis or LIST)
    return b if b in (LIST, PROMO) else LIST


def price_for(model: str, on: str | None = None, basis: str = LIST) -> PricePeriod | None:
    """Official price for `model`.

    basis="list" (default): the regular list price — the open-ended, non-promotional period on the page
                            (e.g. Gemini 3.8 Flash $1.50 / $7.50). No promotion or discount is applied.
    basis="promo":          reference only — the price the page shows for date `on` (JST today), which may be a
                            time-limited promotional (discounted) price.
    Returns None when the model (or its output price) is not on the official page.
    """
    periods = OFFICIAL_PRICES.get(model)
    if not periods:
        return None
    if norm_basis(basis) == LIST:
        regular = [p for p in periods if not p.promo]
        return regular[-1] if regular else None
    d = on or today_jst()
    for p in periods:
        if (p.start is None or p.start <= d) and (p.end is None or d <= p.end):
            return p
    return periods[-1]


def cost_usd(input_tokens: float, output_tokens: float, thinking_tokens: float, price: PricePeriod | None) -> float | None:
    """Standard online cost. Thinking tokens are billed as output ("Text output (response and reasoning)")."""
    if price is None:
        return None
    return input_tokens / 1e6 * price.input_usd + (output_tokens + thinking_tokens) / 1e6 * price.output_usd


def cost_fields(input_tokens: float, output_tokens: float, model: str, usd_jpy: float,
                basis: str = LIST) -> dict:
    """List-price cost of a run as API response fields: {"cost_usd", "cost_jpy"} (None when not priced).

    `output_tokens` must already include thinking tokens (billed at the output rate).
    """
    usd = cost_usd(input_tokens, output_tokens, 0, price_for(model, basis=basis))
    if usd is None:
        return {"cost_usd": None, "cost_jpy": None}
    return {"cost_usd": round(usd, 6), "cost_jpy": round(usd * usd_jpy, 4)}


def estimate(per_sku: dict, skus: int, price: PricePeriod | None, usd_jpy: float) -> dict:
    """Extrapolate measured average tokens/SKU (3 calls: attributes + tags + GMC) to `skus` SKUs."""
    tin = float(per_sku.get("input_tokens") or 0)
    tout = float(per_sku.get("output_tokens") or 0)
    tth = float(per_sku.get("thinking_tokens") or 0)
    out = {
        "skus": skus,
        "tokens": {"input": round(tin * skus), "output": round(tout * skus), "thinking": round(tth * skus)},
        "price_available": price is not None,
    }
    if price is None:
        out.update(per_sku_usd=None, per_sku_jpy=None, total_usd=None, total_jpy=None, usd_jpy=usd_jpy,
                   breakdown=None)
        return out
    b_in = tin * skus / 1e6 * price.input_usd
    b_out = tout * skus / 1e6 * price.output_usd
    b_th = tth * skus / 1e6 * price.output_usd
    total = b_in + b_out + b_th
    out.update(
        per_sku_usd=round(total / max(skus, 1), 8),
        per_sku_jpy=round(total / max(skus, 1) * usd_jpy, 6),
        total_usd=round(total, 6),
        total_jpy=round(total * usd_jpy, 2),
        usd_jpy=usd_jpy,
        breakdown={
            "input": {"usd": round(b_in, 6), "jpy": round(b_in * usd_jpy, 2)},
            "output": {"usd": round(b_out, 6), "jpy": round(b_out * usd_jpy, 2)},
            "thinking": {"usd": round(b_th, 6), "jpy": round(b_th * usd_jpy, 2)},
        },
    )
    return out


def price_public(model: str, basis: str = LIST) -> dict:
    p = price_for(model, basis=basis)
    periods = OFFICIAL_PRICES.get(model) or []
    return {
        "available": p is not None,
        "input_usd_per_mtok": p.input_usd if p else None,
        "output_usd_per_mtok": p.output_usd if p else None,
        "note": (p.note if p.promo else ("List price (no promotion applied)" if any(x.promo for x in periods) else ""))
                if p else "See the official pricing page",
        "promo": bool(p and p.promo),
        "has_promo": any(x.promo for x in periods),
        "periods": [{"input_usd_per_mtok": x.input_usd, "output_usd_per_mtok": x.output_usd, "start": x.start,
                     "end": x.end, "note": x.note, "promo": x.promo} for x in periods],
        "url": PRICING_URL,
        "region": PRICE_REGION,
    }
