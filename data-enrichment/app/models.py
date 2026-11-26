"""Gemini text-model registry: Gemini 3.x-and-later candidates probed on this project (cached, TTL).

- Candidates are filtered to major version >= 3 (2.x and older are never offered).
- Each candidate is probed with a cheap generateContent ping @ global (max 16 output tokens); results are cached
  for PROBE_TTL_S seconds. Only models that answer are selectable.
- The selected model travels with every request (header `X-Enrich-Model`, or `?model=`) and is exposed to the
  handlers through a ContextVar (`current()`), set by `ModelMiddleware`.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
import time
from dataclasses import dataclass

from .config import settings
from .pricing import OFFICIAL_PRICES, price_for

log = logging.getLogger(__name__)

PROBE_TTL_S = 6 * 3600
HEADER = "x-enrich-model"


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    tier: str                      # flash / flash-lite / pro / frontier
    batch: bool | None             # Vertex AI Batch Prediction support (verified, see BATCH_NOTE)
    batch_note: str = ""


# Batch support verified on the original demo project @ global on 2026-11-11: a 1-request BigQuery batch job was
# created per model and every one reached JOB_STATE_SUCCEEDED (see README "Model selection"). A candidate added later
# keeps batch=None until verified, which disables batch submission for it with a reason.
CANDIDATES: list[Candidate] = [
    Candidate("gemini-3.8-flash", "Gemini 3.8 Flash", "flash", True),
    Candidate("gemini-3.7-flash", "Gemini 3.7 Flash", "flash", True),
    Candidate("gemini-3.6-flash", "Gemini 3.6 Flash", "flash", True),
    Candidate("gemini-3.5-flash", "Gemini 3.5 Flash", "flash", True),
    Candidate("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", "flash-lite", True),
    Candidate("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "flash-lite", True),
    Candidate("gemini-3-flash-preview", "Gemini 3 Flash Preview", "flash", True),
    Candidate("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview", "pro", True),
    Candidate("gemini-4-carbon", "Gemini 4 Carbon", "frontier", True),
    Candidate("gemini-4-argon", "Gemini 4 Argon", "frontier", True),
]
BY_ID = {c.id: c for c in CANDIDATES}

_VER = re.compile(r"^gemini-(\d+)(?:\.(\d+))?-")


def major_version(model_id: str) -> int | None:
    m = _VER.match(model_id or "")
    return int(m.group(1)) if m else None


def is_eligible(model_id: str) -> bool:
    """Gemini text model, major version >= 3, not an image/TTS/live/embedding variant."""
    v = major_version(model_id)
    if v is None or v < 3:
        return False
    return not re.search(r"(image|tts|live|embedding|audio|transcribe|translate)", model_id)


def eligible_candidates() -> list[Candidate]:
    return [c for c in CANDIDATES if is_eligible(c.id)]


# ── probe cache ─────────────────────────────────────────────────────────────
_lock = threading.Lock()
_cache: dict[str, dict] = {}          # id -> {available, reason, latency_ms, checked_at}
_probing = False


def _set(mid: str, **kw):
    with _lock:
        _cache[mid] = {**kw, "checked_at": time.time()}


async def _probe_one(mid: str, cli) -> None:
    from google.genai import types
    t0 = time.time()
    try:
        await asyncio.wait_for(cli.aio.models.generate_content(
            model=mid, contents="ping",
            config=types.GenerateContentConfig(max_output_tokens=16, temperature=0.0)), timeout=45)
        _set(mid, available=True, reason="", latency_ms=int((time.time() - t0) * 1000))
    except Exception as e:  # noqa: BLE001
        s = str(e)
        if "429" in s or "RESOURCE_EXHAUSTED" in s:
            # exists but busy right now → usable (quota is shared / transient)
            _set(mid, available=True, reason="Busy (429)", latency_ms=int((time.time() - t0) * 1000))
        else:
            reason = "Not available in this project" if ("404" in s or "NOT_FOUND" in s or "not found" in s.lower()) else s[:160]
            _set(mid, available=False, reason=reason, latency_ms=None)


def probe_all(force: bool = False) -> None:
    """Probe every eligible candidate (blocking). Skips fresh cache entries unless force."""
    global _probing
    if settings.offline:
        for c in eligible_candidates():
            _set(c.id, available=True, reason="offline", latency_ms=0)
        return
    now = time.time()
    todo = [c.id for c in eligible_candidates()
            if force or c.id not in _cache or now - _cache[c.id]["checked_at"] > PROBE_TTL_S]
    if not todo:
        return
    _probing = True
    try:
        # dedicated client: the probe runs in its own event loop (asyncio.run) which is closed afterwards, so it
        # must not share the request-serving client's aiohttp session.
        from google import genai
        cli = genai.Client(vertexai=True, project=settings.project_id, location=settings.vertex_location)

        async def run():
            await asyncio.gather(*[_probe_one(m, cli) for m in todo])
        asyncio.run(run())
        log.info("model probe: %s", {m: _cache[m]["available"] for m in todo})
    finally:
        _probing = False


def probe_async(force: bool = False) -> None:
    threading.Thread(target=probe_all, kwargs={"force": force}, daemon=True).start()


def ensure_fresh() -> None:
    now = time.time()
    if not _probing and any(c.id not in _cache or now - _cache[c.id]["checked_at"] > PROBE_TTL_S
                            for c in eligible_candidates()):
        probe_async()


def status(mid: str) -> dict | None:
    with _lock:
        return dict(_cache[mid]) if mid in _cache else None


def is_available(mid: str) -> bool:
    st = status(mid)
    if st is None:            # not probed yet → trust the configured default only
        return mid == settings.gemini_model
    return bool(st["available"])


def default_model() -> str:
    if is_available(settings.gemini_model):
        return settings.gemini_model
    for c in eligible_candidates():
        if is_available(c.id):
            return c.id
    return settings.gemini_model


def public_list() -> list[dict]:
    out = []
    for c in eligible_candidates():
        st = status(c.id)
        out.append({
            "id": c.id, "label": c.label, "tier": c.tier,
            "status": "probing" if st is None else ("available" if st["available"] else "unavailable"),
            "reason": (st or {}).get("reason", ""), "latency_ms": (st or {}).get("latency_ms"),
            "checked_at": (st or {}).get("checked_at"),
            "default": c.id == settings.gemini_model,
            "batch": c.batch, "batch_note": c.batch_note or batch_reason(c.id),
            "priced": bool(OFFICIAL_PRICES.get(c.id)),
            "price_in": (price_for(c.id).input_usd if price_for(c.id) else None),
            "price_out": (price_for(c.id).output_usd if price_for(c.id) else None),
            "price_in_usd_per_mtok": (price_for(c.id).input_usd if price_for(c.id) else None),
            "price_out_usd_per_mtok": (price_for(c.id).output_usd if price_for(c.id) else None),
        })
    return out


def batch_reason(mid: str) -> str:
    c = BY_ID.get(mid)
    if c is None:
        return "Unknown model"
    if c.batch is True:
        return ""
    if c.batch is False:
        return c.batch_note or "Vertex AI Batch Prediction is not supported"
    return c.batch_note or "Batch Prediction support is not verified, so batch is disabled (use a live run instead)"


# ── per-request selection ───────────────────────────────────────────────────
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("enrich_model", default=None)


def resolve(requested: str | None) -> str:
    """Validate a requested model id; unknown / ineligible / unavailable → default."""
    m = (requested or "").strip()
    if m and m in BY_ID and is_eligible(m) and is_available(m):
        return m
    return default_model()


def current() -> str:
    return _current.get() or default_model()


def set_current(mid: str | None):
    return _current.set(resolve(mid) if mid else None)


class ModelMiddleware:
    """Pure ASGI middleware: reads `X-Enrich-Model` / `?model=` and sets the ContextVar for the whole request
    (including StreamingResponse generators, which run in tasks that copy the current context)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = None
        for k, v in scope.get("headers") or []:
            if k.decode("latin-1").lower() == HEADER:
                req = v.decode("utf-8", "ignore")
                break
        if not req and scope.get("query_string"):
            from urllib.parse import parse_qs
            q = parse_qs(scope["query_string"].decode("utf-8", "ignore"))
            req = (q.get("model") or [None])[0]
        token = _current.set(resolve(req) if req else None)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
