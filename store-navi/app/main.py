"""Cymbal Store Navi BFF (FastAPI) — CONTRACT v8. One image, two services:

APP_MODE=customer → cymbal-store-app  (static/customer SPA + customer API + embedded ADK store concierge)
APP_MODE=staff    → cymbal-store-staff (static/staff SPA + /api/staff/* from app/staff.py)

Common routes: /config.js, /api/stores, /api/stores/{id}/layout, /api/autocomplete, /api/search,
/api/products/{id}/availability, /api/products, /api/route, /api/login, /api/me, /api/photos/{path}, /api/health,
/img/products/* (catalog packshots), /static/* (shared assets, staff demo photos).
Customer only: /api/stores/{id}/home, /api/agent/stream (SSE v3 events).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, deps, inventory, layout, offers, repository, stores

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("store-navi")

APP_MODE = config.APP_MODE
SPA_DIR = config.STATIC_DIR / APP_MODE
MAX_PAGE = 60

app = FastAPI(title=f"Cymbal Store Navi ({APP_MODE})")

staff_router = None
if APP_MODE == "staff":
    try:
        from .staff import router as staff_router  # owned by the staff worker

        app.include_router(staff_router)
        log.info("staff router included")
    except Exception as e:  # noqa: BLE001
        log.error("staff router unavailable: %s", e)

    try:
        from .admin import router as admin_router

        app.include_router(admin_router)
        log.info("admin router included")
    except Exception as e:  # noqa: BLE001
        log.error("admin router unavailable: %s", e)


if APP_MODE == "customer":
    try:
        from .features import router as features_router  # Shopping-assistant features (recipes, forgot-check, detail, voice)

        app.include_router(features_router)
        log.info("features router included")
    except Exception as e:  # noqa: BLE001
        log.warning("features router unavailable: %s", e)


def _warm() -> None:
    try:
        deps.get_engine()
        stores.load_stores()
        layout.get_layout(config.DEFAULT_STORE)
        inventory.ensure_loaded()
        offers.customers_brief()
        if config.data_mode() == "bq":
            inventory.start_background()
    except Exception:  # noqa: BLE001
        log.exception("warmup failed")


@app.on_event("startup")
async def _startup() -> None:
    threading.Thread(target=_warm, name="warmup", daemon=True).start()


def _customer_id(request: Request) -> str:
    cid = request.cookies.get("cr_customer") or config.DEFAULT_CUSTOMER
    return cid if cid in offers.personas() else config.DEFAULT_CUSTOMER


def _store_id(store_id: str | None) -> str:
    sid = (store_id or "").strip()
    return sid if stores.get_store(sid) else config.DEFAULT_STORE


# ---------------------------------------------------------------------------------------------- config
@app.get("/config.js")
async def config_js():
    from .agent import runner

    cfg: dict[str, Any] = {
        "appMode": APP_MODE,
        "onlineStoreUrl": config.ONLINE_STORE_URL,
        "customers": offers.customers_brief(),
        "agentMode": runner.agent_mode(),
        "agentEndpoint": "/api/agent/stream",
        "dataMode": config.data_mode(),
        "defaultStore": config.DEFAULT_STORE,
    }
    if config.http_url("STAFF_URL"):
        cfg["staffUrl"] = config.http_url("STAFF_URL")
    if config.http_url("CUSTOMER_URL"):
        cfg["customerUrl"] = config.http_url("CUSTOMER_URL")
    if config.http_url("ENRICH_URL"):
        cfg["enrichUrl"] = config.http_url("ENRICH_URL")
    body = "window.SN_CONFIG = " + json.dumps(cfg, ensure_ascii=False) + ";\n"
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------------------------- stores
@app.get("/api/stores")
async def api_stores(lat: float | None = None, lng: float | None = None):
    return await asyncio.to_thread(stores.list_stores, lat, lng)


@app.get("/api/stores/{store_id}/home")
async def api_store_home(store_id: str, request: Request):
    if not stores.get_store(store_id):
        return JSONResponse({"error": "STORE_NOT_FOUND"}, status_code=404)
    return await asyncio.to_thread(offers.home, store_id, _customer_id(request))


@app.get("/api/stores/{store_id}/layout")
async def api_layout(store_id: str):
    lay = layout.get_layout(store_id)
    if not lay:
        return JSONResponse({"error": "LAYOUT_NOT_FOUND"}, status_code=404)
    return lay


_ver_cache: dict[str, Any] = {"at": 0.0, "vers": {}}


def _layout_version(store_id: str) -> dict:
    """CONTRACT v13: cheap poll target so the staff app picks up shelf-number manager changes immediately. Checks the latest
    published version (BigQuery, cached 3 s) and reloads this instance's layout cache when it is behind."""
    import time as _t

    if _t.time() - _ver_cache["at"] > 3:
        try:
            _ver_cache.update(vers=repository.latest_layout_versions(), at=_t.time())
        except Exception:  # noqa: BLE001
            log.warning("latest_layout_versions failed", exc_info=True)
    lay = layout.get_layout(store_id) or {}
    latest = int((_ver_cache["vers"] or {}).get(store_id) or 0)
    if latest and int(lay.get("version") or 0) != latest:
        log.info("layout %s: cached v%s → published v%s, reloading", store_id, lay.get("version"), latest)
        layout.reload()
        inventory.invalidate()
        lay = layout.get_layout(store_id) or {}
    return {"store_id": store_id, "version": int(lay.get("version") or 0)}


@app.get("/api/stores/{store_id}/layout/version")
async def api_layout_version(store_id: str):
    if not stores.get_store(store_id):
        return JSONResponse({"error": "STORE_NOT_FOUND"}, status_code=404)
    return JSONResponse(await asyncio.to_thread(_layout_version, store_id), headers={"Cache-Control": "no-store"})


@app.get("/api/stores/{store_id}/fixtures/{fixture_id}")
async def api_fixture(store_id: str, fixture_id: str):
    """CONTRACT v9: fixture + front-elevation grid (tiers top→bottom × slots, products per slot)."""
    grid = await asyncio.to_thread(inventory.fixture_grid, store_id, fixture_id)
    if not grid:
        return JSONResponse({"error": "FIXTURE_NOT_FOUND"}, status_code=404)
    return grid


# ---------------------------------------------------------------------------------------------- search
@app.get("/api/autocomplete")
async def api_autocomplete(request: Request, q: str = ""):
    q = q.strip()
    if not q:
        return {"suggestions": [], "source": "none"}
    res = await asyncio.to_thread(deps.get_engine().autocomplete, q, request.cookies.get("cr_visitor", ""))
    return {"suggestions": res.get("suggestions") or [], "source": res.get("source") or "local"}


def _search(q: str, store_id: str, in_store_only: bool, page: int, page_size: int, visitor: str) -> dict:
    eng = deps.get_engine()
    fetch = min(100, max(page_size * 4, 48)) if in_store_only else page_size
    fpage = 1 if in_store_only else page
    # rq=q: same Retail search request as the storefront (query expansion, spell correction, facets…) without the
    # conversational turn (not used by the store app; avoids its extra latency).
    res = None
    if in_store_only and eng.mode == "retail":
        # Retail local inventory (fulfillment pickup-in-store set only when qty > 0, pushed by the data worker)
        try:
            res = eng.search(q, f'pickupInStore: ANY("{store_id}")', {}, [], "", "", "", "", 1, fetch,
                             visitor or "store-navi", q)
            if not res.get("results"):
                res = None
        except Exception as e:  # noqa: BLE001
            log.warning("pickupInStore filter failed: %s", e)
            res = None
    if res is None:
        res = eng.search(q, "", {}, [], "", "", "", "", fpage, fetch, visitor or "store-navi", q)
    ids = [r["id"] for r in res.get("results") or []]
    engine_name = res.get("engine") or eng.mode
    total_hint = int(res.get("total") or len(ids))
    # symptom-only queries (fever, cough, cold …) may return 0 / irrelevant hits from AICS → local symptom matching first
    if (not ids or q.lower() in deps.SYNONYMS) and fpage == 1:
        sym = deps.symptom_search(q, limit=fetch)
        if sym:
            ids = list(dict.fromkeys(sym + ids))[:fetch]
            engine_name = engine_name + "+symptoms"
            total_hint = max(total_hint, len(ids))
    prods = deps.hydrate(ids)
    live = inventory.prefetch_local_inventory([p["id"] for p in prods])
    rows = []
    for p in prods:
        av = inventory.get_availability(p["id"], store_id, p, live)
        rows.append({**p, "availability": av})
    total = total_hint if total_hint >= len(rows) else len(rows)
    if in_store_only:
        rows = [r for r in rows if (r["availability"]["current"] or {}).get("status") in inventory.IN_STOCK]
        total = len(rows)
        start = (page - 1) * page_size
        rows = rows[start:start + page_size]
    return {"results": rows, "total": total, "engine": engine_name, "store_id": store_id,
            "corrected_query": res.get("corrected_query") or "", "results_query": res.get("results_query") or q}


@app.get("/api/search")
async def api_search(request: Request, q: str = "", store_id: str = "", in_store_only: int = 0, page: int = 1,
                     page_size: int = 20):
    q = q.strip()
    if not q:
        return {"results": [], "total": 0, "engine": deps.get_engine().mode}
    page_size = max(1, min(int(page_size), MAX_PAGE))
    try:
        return await asyncio.to_thread(_search, q, _store_id(store_id), bool(in_store_only), max(1, int(page)),
                                       page_size, request.cookies.get("cr_visitor", ""))
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        return JSONResponse({"error": str(e)[:300]}, status_code=502)


@app.get("/api/products/{product_id}/availability")
async def api_availability(product_id: str, store_id: str = ""):
    p = deps.to_product(product_id)
    if not p:
        got = await asyncio.to_thread(deps.hydrate, [product_id])
        p = got[0] if got else None
    if not p:
        return JSONResponse({"error": "PRODUCT_NOT_FOUND"}, status_code=404)
    av = await asyncio.to_thread(inventory.get_availability, product_id, _store_id(store_id), p, True)
    return {**av, "product": p}


@app.get("/api/products")
async def api_products(ids: str = Query(""), store_id: str = ""):
    id_list = list(dict.fromkeys(i.strip() for i in ids.split(",") if i.strip()))[:MAX_PAGE]
    prods = await asyncio.to_thread(deps.hydrate, id_list)
    if store_id:
        sid = _store_id(store_id)
        prods = [{**p, "availability": inventory.get_availability(p["id"], sid, p)} for p in prods]
    return {"products": prods}


@app.post("/api/route")
async def api_route(payload: dict = Body(default={})):
    sid = _store_id(payload.get("store_id"))
    ids = [str(x) for x in (payload.get("product_ids") or []) if x][:20]
    return await asyncio.to_thread(layout.route_for_products, sid, ids)


# ---------------------------------------------------------------------------------------------- login
@app.post("/api/login")
async def api_login(payload: dict = Body(default={})):
    cid = str(payload.get("customer_id") or "")
    if cid not in offers.personas():
        return JSONResponse({"error": "UNKNOWN_CUSTOMER"}, status_code=400)
    p = offers.get_persona(cid)
    resp = JSONResponse({"customer": {"customer_id": cid, "name": p.get("name"), "avatar": p.get("avatar") or "🙂"}})
    resp.set_cookie("cr_customer", cid, max_age=60 * 60 * 24 * 30, path="/", samesite="lax")
    return resp


@app.get("/api/me")
async def api_me(request: Request):
    p = offers.get_persona(_customer_id(request))
    return {"customer": {"customer_id": p["customer_id"], "name": p.get("name"), "avatar": p.get("avatar") or "🙂",
                         "concerns": p.get("concerns") or []}}


# ---------------------------------------------------------------------------------------------- agent
@app.post("/api/agent/stream")
async def api_agent_stream(request: Request, payload: dict = Body(default={})):
    if APP_MODE != "customer":
        return JSONResponse({"error": "NOT_AVAILABLE"}, status_code=404)
    from .agent import runner

    message = str(payload.get("message") or "").strip()[:2000]
    if not message:
        return JSONResponse({"error": "EMPTY_MESSAGE"}, status_code=400)
    body = {"session_id": str(payload.get("session_id") or "")[:128] or "s-" + uuid.uuid4().hex,
            "message": message, "store_id": _store_id(payload.get("store_id")),
            "customer_id": _customer_id(request), "visitor_id": request.cookies.get("cr_visitor", ""),
            "page_state": payload.get("page_state") if isinstance(payload.get("page_state"), dict) else {}}
    return StreamingResponse(runner.stream(body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------------------------- photos
@app.get("/api/photos/{path:path}")
async def api_photos(path: str):
    got = await asyncio.to_thread(repository.read_photo, path)
    if not got:
        return JSONResponse({"error": "PHOTO_NOT_FOUND"}, status_code=404)
    data, ctype = got
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/health")
@app.get("/healthz")
async def health():
    from .agent import runner

    out: dict[str, Any] = {"ok": True, "app_mode": APP_MODE, "data_mode": config.data_mode(),
                           "agent_mode": runner.agent_mode(), "data_dir": str(config.data_dir()),
                           "staff_router": staff_router is not None}
    try:
        out["engine"] = deps.get_engine().mode
        out["catalog"] = len(deps.get_catalog())
        out["inventory"] = inventory.stats()
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


# ---------------------------------------------------------------------------------------------- static
@app.get("/")
async def index(request: Request):
    resp = FileResponse(SPA_DIR / "index.html", headers={"Cache-Control": "no-store"})
    if "cr_customer" not in request.cookies:
        resp.set_cookie("cr_customer", config.DEFAULT_CUSTOMER, max_age=60 * 60 * 24 * 30, path="/", samesite="lax")
    if "cr_visitor" not in request.cookies:
        resp.set_cookie("cr_visitor", "v-" + uuid.uuid4().hex[:16], max_age=60 * 60 * 24 * 365, path="/",
                        samesite="lax")
    return resp


if APP_MODE == "staff":
    @app.get("/admin")
    @app.get("/admin/")
    async def admin_index():
        """CONTRACT v12: standalone shelf-number manager screen (floor plan → shelves → shelf numbers)."""
        return FileResponse(config.STATIC_DIR / "admin" / "index.html", headers={"Cache-Control": "no-store"})


_img_dir = config.product_images_dir()
if _img_dir.exists():
    app.mount("/img/products", StaticFiles(directory=_img_dir), name="product-images")
else:
    log.warning("product images dir missing: %s", _img_dir)
if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
if SPA_DIR.exists():
    app.mount("/", StaticFiles(directory=SPA_DIR), name="spa")
