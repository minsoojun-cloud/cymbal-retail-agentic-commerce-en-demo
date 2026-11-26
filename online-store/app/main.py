"""Cymbal Retail demo storefront (FastAPI BFF + static SPA).

Routes (CONTRACT.md):
  GET  /                 SPA (sets cookies cr_customer=CR-0001, cr_visitor)
  GET  /config.js        window.CR_CONFIG (agentMode live|mock, deliverySpeedSec, customers)
  GET  /api/search       AI Commerce Search (Retail v2alpha) – facets, tiles, conversational search
  GET  /api/autocomplete CompletionService.complete_query (fallback: local prefix match)
  POST /api/ai-summary   Gemini "AI Overview" {summary, highlights, followup}
  GET  /api/products     products by ids (local catalog cache / Retail get_product)
  GET  /api/products/{id}/stores?lat=&lng=  store availability (Retail LocalInventory -> BigQuery -> local files)
  GET  /api/features     live / fallback status of each AICS feature
  GET  /api/recommendations?type=for_you|buy_again|sale|seasonal|new|popular  home rows (catalog + persona files)
  POST /api/agent/stream SSE proxy to ${ADK_AGENT_URL}/api/chat/stream (ID token); 503 -> page mock concierge
  POST /api/voice/stt    (VOICE_ENABLED=1 only): audio -> {text} (Speech-to-Text v2 Chirp, en-US / Gemini)
  POST /api/voice/tts    (VOICE_ENABLED=1 only): {text} -> audio/mpeg (en-US-Chirp3-HD-Aoede)
  GET  /healthz

Local mode: DATA_FILE=/path/products_retail.jsonl → offline search engine with the same response shape.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent_proxy, ai, stores, voice
from .catalog import Catalog, default_data_file
from .recommend import Recommender
from .search import LocalSearch, RetailSearch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cymbal-site")

STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parent.parent / "static"))
MAX_IDS = 100
CUSTOMERS = {"CR-0001": {"customer_id": "CR-0001", "name": "Sakura Tanaka", "first_name": "Sakura"}}
DEFAULT_CUSTOMER = "CR-0001"

_engine: LocalSearch | RetailSearch | None = None


def get_engine() -> LocalSearch | RetailSearch:
    global _engine
    if _engine is None:
        catalog = Catalog(default_data_file())
        if os.environ.get("DATA_FILE") or os.environ.get("SEARCH_MODE") == "local":
            _engine = LocalSearch(catalog)
        else:
            try:
                _engine = RetailSearch(catalog)
            except Exception as e:  # noqa: BLE001 (no credentials etc.)
                log.error("Retail client init failed (%s) → local engine", e)
                _engine = LocalSearch(catalog)
        log.info("search engine = %s (catalog %d products)", _engine.mode, len(catalog))
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None


def _json_param(raw: str, default: Any) -> Any:
    if not raw:
        return default
    try:
        v = json.loads(raw)
        return v if isinstance(v, type(default)) else default
    except json.JSONDecodeError:
        return default


app = FastAPI(title="Cymbal Retail Demo Site")


@app.on_event("startup")
async def _startup() -> None:
    get_engine()


@app.get("/api/search")
async def api_search(request: Request, q: str = "", filter: str = "", facets: str = "", tiles: str = "",
                     conversation_id: str = "", answer: str = "", answer_key: str = "", answer_value: str = "",
                     page: int = 1, page_size: int = 24, rq: str = ""):
    import asyncio

    page_size = max(1, min(int(page_size), MAX_IDS))
    page = max(1, int(page))
    fac = {str(k): [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in _json_param(facets, {}).items()}
    tl = [t for t in _json_param(tiles, []) if isinstance(t, dict)]
    visitor = request.cookies.get("cr_visitor", "")
    try:
        return await asyncio.to_thread(get_engine().search, q.strip(), filter.strip(), fac, tl, conversation_id,
                                       answer.strip(), answer_key, answer_value, page, page_size, visitor,
                                       rq.strip())
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/autocomplete")
async def api_autocomplete(request: Request, q: str = ""):
    import asyncio

    q = q.strip()
    if not q:
        return {"suggestions": []}
    return await asyncio.to_thread(get_engine().autocomplete, q, request.cookies.get("cr_visitor", ""))


@app.post("/api/ai-summary")
async def api_ai_summary(payload: dict = Body(default={})):
    q = str(payload.get("q") or "").strip()
    ids = [str(i) for i in (payload.get("product_ids") or [])][:10]
    import asyncio

    products = await asyncio.to_thread(get_engine().hydrate, ids)
    return await ai.ai_summary(q, products)


@app.get("/api/products")
async def api_products(ids: str = Query("", description="comma separated product ids")):
    import asyncio

    id_list: list[str] = []
    for i in ids.split(","):
        i = i.strip()
        if i and i not in id_list:
            id_list.append(i)
    id_list = id_list[:MAX_IDS]
    return {"products": await asyncio.to_thread(get_engine().hydrate, id_list)}


@app.get("/api/products/{product_id}/stores")
async def api_product_stores(product_id: str, lat: float | None = None, lng: float | None = None):
    """Omnichannel availability: which Cymbal Retail stores carry the product, quantity, pickup, open now."""
    import asyncio

    product_id = product_id.strip()[:64]
    prods = await asyncio.to_thread(get_engine().hydrate, [product_id])
    if not prods:
        return JSONResponse({"error": "PRODUCT_NOT_FOUND", "product_id": product_id}, status_code=404)
    return await asyncio.to_thread(stores.directory().availability, prods[0], product_id, lat, lng)


@app.get("/api/stores")
async def api_stores():
    import asyncio

    rows = await asyncio.to_thread(stores.directory().stores)
    return {"stores": [{k: s.get(k) for k in ("store_id", "name", "short_name", "address", "phone", "lat", "lng")}
                       | {"hours": stores.hours_label(s), "open_now": stores.open_now(s)} for s in rows]}


@app.get("/api/features")
async def api_features():
    eng = get_engine()
    return {"engine": eng.mode, "features": eng.feature_status(), "catalog_size": len(eng.catalog),
            "gemini_model": ai.GEMINI_MODEL}


_home_cache: dict | None = None
NEW_CATEGORIES = ["Kitchen", "Beauty & Personal Care", "Pet", "Home & Living"]


@app.get("/api/home")
async def api_home():
    """Home page rows built from the local catalog cache (no search call needed)."""
    global _home_cache
    if _home_cache is not None:
        return _home_cache
    from .catalog import public

    items = list(get_engine().catalog.items.values())
    cats: dict[str, dict] = {}
    for n in items:
        c = cats.setdefault(n["category"], {"name": n["category"], "count": 0, "image": "", "_best": -1.0})
        c["count"] += 1
        score = n["rating"] * (n["rating_count"] or 1)
        if n["image"] and score > c["_best"]:
            c["_best"], c["image"] = score, n["image"]
    categories = sorted(({k: v for k, v in c.items() if not k.startswith("_")} for c in cats.values() if c["name"]),
                        key=lambda c: (c["name"] not in NEW_CATEGORIES, -c["count"]))
    sale = sorted([n for n in items if n["on_sale"] and n["image"]],
                  key=lambda n: (-n["discount_rate"], -n["rating"]))
    # one product per base image for visual variety
    def dedupe(lst: list[dict], k: int) -> list[dict]:
        seen, out = set(), []
        for n in lst:
            if n["image"] in seen:
                continue
            seen.add(n["image"])
            out.append(public(n))
            if len(out) >= k:
                break
        return out

    rec = sorted([n for n in items if n["image"] and n["rating_count"] >= 50], key=lambda n: (-n["rating"], -n["rating_count"]))
    gm = sorted([n for n in items if n["category"] in NEW_CATEGORIES and n["image"]],
                key=lambda n: (-n["rating"], -n["rating_count"]))
    _home_cache = {"categories": categories, "sale": dedupe(sale, 16), "recommended": dedupe(rec, 16),
                   "new_gm": dedupe(gm, 16)}
    return _home_cache


def agent_backend() -> str:
    """'adk' | 'mock'. The ADK agent (ADK_AGENT_URL) is the only live backend; without it (or with
    AGENT_BACKEND=mock) the page uses its scripted mock concierge."""
    want = (os.environ.get("AGENT_BACKEND") or "adk").strip().lower()
    if want == "mock":
        return "mock"
    return "adk" if agent_proxy.agent_url() else "mock"


AGENT_LABELS = {"adk": "Agent Development Kit", "mock": ""}


def _http_env(name: str) -> str:
    url = (os.environ.get(name) or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def enrich_url() -> str:
    """Header icon link to the Cymbal Data Enrichment Studio (env ENRICH_URL, http(s) only)."""
    return _http_env("ENRICH_URL")


def flyer_url() -> str:
    """Header icon link to the Cymbal Flyer Studio (env FLYER_URL, http(s) only)."""
    return _http_env("FLYER_URL")


def store_app_url() -> str:
    """Header icon link to the Cymbal Retail store app (env STORE_APP_URL, http(s) only)."""
    return _http_env("STORE_APP_URL")


@app.get("/config.js")
async def config_js():
    backend = agent_backend()
    cfg = {
        "agentMode": "mock" if backend == "mock" else "live",
        "agentBackend": backend,
        "agentPlatform": AGENT_LABELS[backend],
        "agentEndpoint": "/api/agent/stream",
        "deliverySpeedSec": float(os.environ.get("DELIVERY_SPEED_SEC", "8") or 8),
        "customers": CUSTOMERS,
        "searchEngine": get_engine().mode,
    }
    if enrich_url():
        cfg["enrichUrl"] = enrich_url()
    if flyer_url():
        cfg["flyerUrl"] = flyer_url()
    if store_app_url():
        cfg["storeAppUrl"] = store_app_url()
    if voice.enabled():
        # chat voice (mic + read-aloud) only where VOICE_ENABLED=1
        cfg["voiceEnabled"] = True
        cfg["voice"] = {"sttEndpoint": "/api/voice/stt", "ttsEndpoint": "/api/voice/tts", "lang": voice.STT_LANG,
                        "ttsVoice": voice.TTS_VOICE}
    body = "window.CR_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n"
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.post("/api/voice/stt")
async def api_voice_stt(request: Request):
    """CONTRACT v6: recorded utterance (raw body, audio/webm;codecs=opus) → {text, engine, ms}."""
    if not voice.enabled():
        return JSONResponse({"error": "VOICE_DISABLED"}, status_code=404)
    audio = await request.body()
    if not audio:
        return JSONResponse({"error": "EMPTY_AUDIO"}, status_code=400)
    if len(audio) > voice.MAX_AUDIO_BYTES:
        return JSONResponse({"error": "AUDIO_TOO_LARGE"}, status_code=413)
    try:
        return await voice.transcribe(audio, request.headers.get("content-type", "audio/webm"))
    except Exception as e:  # noqa: BLE001
        log.exception("stt failed")
        return JSONResponse({"error": "STT_FAILED", "detail": str(e)[:200]}, status_code=502)


@app.post("/api/voice/tts")
async def api_voice_tts(payload: dict = Body(default={})):
    """{text, format?: mp3|ogg} -> audio (en-US-Chirp3-HD-Aoede); markdown/emoji stripped; cached."""
    if not voice.enabled():
        return JSONResponse({"error": "VOICE_DISABLED"}, status_code=404)
    text = voice.clean_for_speech(str(payload.get("text") or ""))
    if not text:
        return JSONResponse({"error": "EMPTY_TEXT"}, status_code=400)
    try:
        audio, mime, hit = await voice.synthesize(text, "ogg" if payload.get("format") == "ogg" else "mp3")
    except Exception as e:  # noqa: BLE001
        log.exception("tts failed")
        return JSONResponse({"error": "TTS_FAILED", "detail": str(e)[:200]}, status_code=502)
    return Response(audio, media_type=mime, headers={"Cache-Control": "private, max-age=86400",
                                                     "X-TTS-Cache": "hit" if hit else "miss"})


_recommender: Recommender | None = None


def get_recommender() -> Recommender:
    global _recommender
    if _recommender is None or _recommender.catalog is not get_engine().catalog:
        _recommender = Recommender(get_engine().catalog)
    return _recommender


@app.get("/api/recommendations")
async def api_recommendations(request: Request, type: str = "for_you", limit: int = 16):
    """Home rows: for_you / buy_again / sale / seasonal / new / popular (local catalog + seed/personas)."""
    import asyncio

    cid = request.cookies.get("cr_customer") or DEFAULT_CUSTOMER
    return await asyncio.to_thread(get_recommender().recommend, type, cid, limit)


@app.post("/api/agent/stream")
async def api_agent_stream(request: Request, payload: dict = Body(default={})):
    """Proxy the chat turn to the ADK agent; SSE contract unchanged."""
    backend = agent_backend()
    if backend == "mock":
        return JSONResponse({"error": "AGENT_NOT_CONFIGURED", "mode": "mock"}, status_code=503)
    cid = request.cookies.get("cr_customer") or DEFAULT_CUSTOMER
    cust = CUSTOMERS.get(cid, {"name": ""})
    message = str(payload.get("message") or "").strip()[:2000]
    if not message:
        return JSONResponse({"error": "EMPTY_MESSAGE"}, status_code=400)
    page_state = payload.get("page_state") if isinstance(payload.get("page_state"), dict) else {}
    body = {
        "session_id": str(payload.get("session_id") or "")[:128] or "s-" + uuid.uuid4().hex,
        "customer_id": cid,
        "customer_name": cust.get("name", ""),
        "visitor_id": request.cookies.get("cr_visitor", "") or str(payload.get("visitor_id") or ""),
        "message": message,
        "page_state": page_state,
    }
    return StreamingResponse(agent_proxy.stream(body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.get("/healthz")
@app.get("/api/health")  # Cloud Run reserves /healthz on *.run.app
async def healthz():
    return {"ok": True}


@app.get("/")
async def index(request: Request):
    resp = FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})
    if "cr_customer" not in request.cookies:
        resp.set_cookie("cr_customer", DEFAULT_CUSTOMER, max_age=60 * 60 * 24 * 30, path="/", samesite="lax")
    if "cr_visitor" not in request.cookies:
        resp.set_cookie("cr_visitor", "v-" + uuid.uuid4().hex[:16], max_age=60 * 60 * 24 * 365, path="/",
                        samesite="lax")
    return resp


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
