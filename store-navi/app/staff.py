"""Cymbal Store Navi — staff API (CONTRACT v8, mounted by app/main.py when APP_MODE=staff).

  GET   /api/staff/demo-photos                 {photos:[{id,url,store_id,hint,...}]}
  GET   /api/staff/demo-photos/{file}          demo photo bytes (static/staff/demo_photos)
  POST  /api/staff/analyze-photo               {photo_id | image_base64, store_id}
        -> {gondola_id, tier_guess, side_guess, ocr_text, candidates:[Product], confidence, plate_text, ...}
  POST  /api/staff/shelf-locations             {store_id, product_id, gondola_id, side, tier, position, note,
                                                photo_id | image_base64, staff_name} -> {ok, location}
  GET   /api/staff/stock-search?q=             {results:[{product, stores:[{store_id, short_name, quantity, status, shelf}]}]}
  PATCH /api/staff/inventory                   {store_id, product_id, quantity} -> {ok, quantity, status}
  GET   /api/staff/registrations?store_id=     {registrations:[location + product]}

Shared modules (owned by the customer worker) are used through a thin adapter (`_shared`) so this file keeps working
while their public names settle; each call has a conservative local fallback (DATA_MODE=local files).
Vision: Gemini `STAFF_VISION_MODEL` (default gemini-3-flash-preview) @ global, structured JSON output.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import io
import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import inventory

log = logging.getLogger("store-navi.staff")

router = APIRouter(prefix="/api/staff", tags=["staff"])

APP_DIR = Path(__file__).resolve().parent
NAVI_DIR = APP_DIR.parent
DEMO_DIR = Path(os.environ.get("STAFF_DEMO_DIR", str(NAVI_DIR / "static" / "staff" / "demo_photos")))
DEMO_MANIFEST = DEMO_DIR / "demo_photos.json"
LOCAL_PHOTO_DIR = Path(os.environ.get("LOCAL_PHOTO_DIR", "/tmp/store_navi_photos"))

PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
VISION_MODEL = os.environ.get("STAFF_VISION_MODEL", "gemini-3-flash-preview")
VISION_LOCATION = os.environ.get("GEMINI_LOCATION", "global")
PHOTO_BUCKET = os.environ.get("PHOTO_BUCKET") or (f"{PROJECT_ID}-store-navi" if PROJECT_ID else "")
STORE_IDS = ("S01", "S02", "S03")
SIDES = ("N", "S", "E", "W")


def data_mode() -> str:
    cfg = _mod("config")
    if cfg is not None and hasattr(cfg, "data_mode"):
        return cfg.data_mode()
    m = (os.environ.get("DATA_MODE") or "").lower()
    return m if m in ("local", "bq") else ("bq" if os.environ.get("K_SERVICE") else "local")


def _data_dir() -> Path:
    cfg = _mod("config")
    if cfg is not None and hasattr(cfg, "data_dir"):
        return Path(cfg.data_dir())
    return Path(os.environ.get("DATA_DIR") or (NAVI_DIR / "data"))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def status_for(qty: int | None, carried: bool = True) -> str:
    if not carried or qty is None:
        return "not_carried"
    if qty <= 0:
        return "out_of_stock"
    if qty <= 3:
        return "low_stock"
    return "in_stock"


# ---------------------------------------------------------------------------
# Shared-module adapter
# ---------------------------------------------------------------------------
def _mod(name: str):
    try:
        return __import__(f"{__package__}.{name}", fromlist=[name]) if __package__ else __import__(name)
    except Exception as e:  # pragma: no cover - module not there yet
        log.debug("shared module %s unavailable: %s", name, e)
        return None


def _fn(module: str, *names: str):
    m = _mod(module)
    if m is None:
        return None
    for n in names:
        f = getattr(m, n, None)
        if callable(f):
            return f
    return None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


_lock = threading.Lock()
_registrations: list[dict] = []  # in-process log (also used when the repository has no reader)


# ---- stores / layouts ----
def load_stores() -> list[dict]:
    f = _fn("stores", "load_stores", "list_stores", "all_stores", "get_stores")
    if f:
        try:
            s = f()
            if isinstance(s, dict):
                s = list(s.values())
            if s:
                return [dict(x) for x in s]
        except Exception:
            log.exception("stores adapter failed")
    p = _data_dir() / "stores.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def store_map() -> dict[str, dict]:
    return {s["store_id"]: s for s in load_stores()}


def get_layout(store_id: str) -> dict | None:
    f = _fn("layout", "get_layout", "load_layout", "layout_for")
    if f:
        try:
            lay = f(store_id)
            if lay:
                return lay if isinstance(lay, dict) else getattr(lay, "raw", None) or getattr(lay, "data", None)
        except Exception:
            log.exception("layout adapter failed")
    p = _data_dir() / "layouts" / f"{store_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _norm_fixture(f: dict) -> dict:
    f = dict(f)
    f.setdefault("tiers", 5)
    f["tiers"] = int(f.get("tiers") or 1)
    if not f.get("slots_per_tier"):
        width = float(f.get("slot_width_cm") or 30)
        length = float(f.get("length_cm") or (max(f.get("w") or 0.9, f.get("h") or 0.9) * 100))
        f["slots_per_tier"] = max(1, int(round(length / width)))
    f["slots_per_tier"] = int(f["slots_per_tier"])
    f.setdefault("slot_width_cm", 30)
    if not f.get("face"):
        f["face"] = (f.get("faces") or ["S"])[0]
    return f


def fixture_index(store_id: str) -> dict[str, dict]:
    """fixture_id → normalized fixture (v2 `fixtures`, v1 `gondolas` fallback, + counter RX)."""
    out: dict[str, dict] = {}
    f = _fn("layout", "fixtures")
    if f:
        try:
            for x in f(store_id) or []:
                out[x["id"]] = _norm_fixture(x)
        except Exception:
            log.exception("layout.fixtures adapter failed")
    if not out:
        lay = get_layout(store_id) or {}
        for x in (lay.get("fixtures") or lay.get("gondolas") or []):
            out[x["id"]] = _norm_fixture(x)
    lay = get_layout(store_id) or {}
    raw_rx = next((x for x in lay.get("fixtures") or [] if x.get("id") == "RX"), None) or lay.get("counter") or {}
    if "RX" in out and int(raw_rx.get("slots_per_tier") or 0) > int(out["RX"].get("slots_per_tier") or 0):
        out["RX"] = {**out["RX"], "slots_per_tier": int(raw_rx["slots_per_tier"]), "tiers": int(raw_rx.get("tiers") or 1)}
    c = lay.get("counter")
    if c and c.get("id") and c["id"] not in out:
        out[c["id"]] = _norm_fixture({**c, "type": "counter", "tiers": c.get("tiers") or 1,
                                      "slots_per_tier": c.get("slots_per_tier") or 10})
    return out


gondola_index = fixture_index  # v8 name


_SLOT_RE = re.compile(r"([A-Z]{1,2})\s*[-\u2010\uff0d\u30fc\u2015_ ]?\s*(\d{2})\s*[-\u2010\uff0d\u30fc\u2015_ ]\s*(\d)\s*(\d{2})")
_RX_SLOT_RE = re.compile(r"\bRX\s*[-\u2010\uff0d\u30fc\u2015_ ]\s*(\d)\s*(\d{2})")
_FIX_RE = re.compile(r"([A-Z]{1,2})\s*[-\u2010\uff0d\u30fc\u2015_ ]?\s*(\d{1,2})")


def parse_slot(text: str) -> tuple[str, int, int] | None:
    """'A01-203' / 'Ａ０１－２０３' / 'a01 203' → ('A01', 2, 3)."""
    t = unicodedata.normalize("NFKC", text or "").upper()
    m = _RX_SLOT_RE.search(t)
    if m:
        return "RX", int(m.group(1)), int(m.group(2))
    m = _SLOT_RE.search(t)
    if not m:
        return None
    return f"{m.group(1)}{m.group(2)}", int(m.group(3)), int(m.group(4))


def slot_id(fixture_id: str, tier: int, index: int) -> str:
    return f"{fixture_id}-{int(tier)}{int(index):02d}"


def normalize_fixture(text: str, known: dict[str, dict] | None = None) -> str | None:
    """'A01' / 'A-01' / 'Ａ１' / 'A01-203' → 'A01' (validated against known fixtures when given)."""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).upper().strip()
    known = known or {}
    if t in known:
        return t
    if "RX" in t and (not known or "RX" in known):
        return "RX"
    s = parse_slot(t)
    if s and (not known or s[0] in known):
        return s[0]
    for m in _FIX_RE.finditer(t):
        for cand in (f"{m.group(1)}{int(m.group(2)):02d}", f"{m.group(1)}-{int(m.group(2)):02d}"):
            if not known or cand in known:
                return cand
    return None


def validate_slots(store_id: str, slot_ids: list[str], known: dict[str, dict] | None = None) -> tuple[dict, int, list[int]]:
    """Return (fixture, tier, indexes) or raise HTTPException: same fixture, same tier, contiguous, in range, 1–3."""
    known = known if known is not None else fixture_index(store_id)
    if not slot_ids or len(slot_ids) > 3:
        raise HTTPException(400, "Select 1-3 adjacent slots on the same tier")
    parsed = []
    for s in slot_ids:
        p = parse_slot(s)
        if not p:
            raise HTTPException(400, f"Invalid slot number: {s}")
        parsed.append(p)
    fids, tiers = {p[0] for p in parsed}, {p[1] for p in parsed}
    if len(fids) != 1 or len(tiers) != 1:
        raise HTTPException(400, "Select slots on the same fixture and the same tier")
    fid, tier = fids.pop(), tiers.pop()
    fx = known.get(fid)
    if known and fx is None:
        raise HTTPException(400, f"Fixture {fid} is not in this store's layout")
    fx = fx or {"id": fid, "tiers": 5, "slots_per_tier": 99}
    idx = sorted({p[2] for p in parsed})
    if tier < 1 or tier > int(fx["tiers"]):
        raise HTTPException(400, f"{fid} has {fx['tiers']} tiers")
    if idx[0] < 1 or idx[-1] > int(fx["slots_per_tier"]):
        raise HTTPException(400, f"Each tier of {fid} has {fx['slots_per_tier']} slots")
    if idx[-1] - idx[0] + 1 != len(idx):
        raise HTTPException(400, "Select adjacent slots")
    return fx, tier, idx


def fixture_grid(store_id: str, fixture_id: str) -> dict | None:
    """Front-elevation grid via layout.elevation (customer worker) – fallback: empty grid from the fixture meta."""
    f = _fn("layout", "elevation", "fixture_grid") or _fn("inventory", "fixture_grid", "elevation")
    if f:
        try:
            g = f(store_id, fixture_id)
            if g:
                return g
        except Exception:
            log.exception("elevation adapter failed")
    fx = fixture_index(store_id).get(fixture_id)
    if not fx:
        return None
    return {"fixture": fx, "tiers": [{"tier": t, "slots": [{"slot_id": slot_id(fixture_id, t, i), "index": i,
                                                             "width_cm": fx.get("slot_width_cm"), "products": []}
                                                            for i in range(1, fx["slots_per_tier"] + 1)]}
                                     for t in range(fx["tiers"], 0, -1)]}


def _slot_products(grid: dict | None, sid: str) -> list:
    for t in (grid or {}).get("tiers") or []:
        for s in t.get("slots") or []:
            if s.get("slot_id") == sid:
                return s.get("products") or s.get("product_ids") or []
    return []


# ---- catalog / search ----
_local_catalog = None
_local_engine = None


def _site_lib():
    for name in (f"{__package__.rsplit('.', 1)[0]}.site_lib" if __package__ and "." in __package__ else None,
                 "site_lib", "app.site_lib"):
        if not name:
            continue
        try:
            return __import__(name, fromlist=["catalog", "search"])
        except Exception:
            continue
    return None


def get_catalog():
    f = _fn("deps", "get_catalog") or _fn("search_engine", "get_catalog") or _fn("catalog", "get_catalog") \
        or _fn("inventory", "get_catalog") or _fn("main", "get_catalog")
    if f:
        try:
            c = f()
            if c is not None:
                return c
        except Exception:
            log.exception("catalog adapter failed")
    global _local_catalog
    if _local_catalog is None:
        lib = _site_lib()
        if lib is None:
            return None
        from importlib import import_module
        cat = import_module(lib.__name__ + ".catalog")
        path = os.environ.get("DATA_FILE") or str(NAVI_DIR / "data" / "products_retail.jsonl")
        if not os.path.exists(path):
            path = str(NAVI_DIR.parent / "seed" / "catalog" / "products_retail.jsonl")
        _local_catalog = cat.Catalog(path)
    return _local_catalog


def get_engine():
    f = _fn("deps", "get_engine") or _fn("search_engine", "get_engine") or _fn("search", "get_engine") \
        or _fn("main", "get_engine")
    if f:
        try:
            e = f()
            if e is not None:
                return e
        except Exception:
            log.exception("engine adapter failed")
    global _local_engine
    if _local_engine is None:
        lib, catalog = _site_lib(), get_catalog()
        if lib is None or catalog is None:
            return None
        from importlib import import_module
        _local_engine = import_module(lib.__name__ + ".search").LocalSearch(catalog)
    return _local_engine


_DRUG_KEYS = ("drug_class", "abuse_control", "age_min", "symptoms", "base_id")


def to_product(p: dict | None) -> dict | None:
    """Front-end Product + drug_class / abuse_control / age_min (CONTRACT v8)."""
    if not p:
        return None
    f = _fn("deps", "to_product", "product_view") or _fn("inventory", "to_product", "product_view") \
        or _fn("search_engine", "to_product")
    if f:
        try:
            r = f(p)
            if r:
                return r
        except Exception:
            pass
    out = {k: v for k, v in p.items() if not str(k).startswith("_")}
    attrs = p.get("_attrs") or {}
    if not attrs:
        cat = get_catalog()
        raw = cat.get(p.get("id")) if cat is not None else None
        attrs = (raw or {}).get("_attrs") or {}
    out.setdefault("drug_class", (attrs.get("drug_class") or [""])[0] or None)
    out.setdefault("abuse_control", (attrs.get("abuse_control") or ["false"])[0] == "true")
    age = (attrs.get("age_min") or [None])[0]
    try:
        out.setdefault("age_min", int(float(age)) if age not in (None, "") else None)
    except (TypeError, ValueError):
        out.setdefault("age_min", None)
    if attrs.get("base_id"):
        out.setdefault("base_id", attrs["base_id"][0])
    return out


def product_by_id(pid: str) -> dict | None:
    cat = get_catalog()
    n = cat.get(pid) if cat is not None else None
    if n is None:
        eng = get_engine()
        try:
            got = eng.hydrate([pid]) if eng is not None else []
            n = got[0] if got else None
        except Exception:
            n = None
    return to_product(n) if n else None


def search_products(q: str, limit: int = 8) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    eng = get_engine()
    if eng is None:
        return []
    try:
        res = eng.search(q, page_size=limit, visitor_id="store-navi-staff")
    except TypeError:
        res = eng.search(q)
    except Exception:
        log.exception("search failed for %r", q)
        return []
    items = (res.get("results") or res.get("products") or []) if isinstance(res, dict) else list(res or [])
    out = []
    for it in items[:limit]:
        p = it.get("product") if isinstance(it, dict) and "product" in it and isinstance(it["product"], dict) else it
        tp = to_product(p)
        if tp:
            out.append(tp)
    return out


# ---- inventory / shelves ----
def invalidate_cache() -> None:
    for name in ("invalidate", "invalidate_cache", "clear_cache", "reset_cache"):
        f = _fn("inventory", name)
        if f:
            try:
                f()
            except Exception:
                log.exception("inventory invalidate failed")
            return


def _local_inventory() -> dict[tuple[str, str], dict]:
    inv: dict[tuple[str, str], dict] = {}
    for r in _read_jsonl(_data_dir() / "store_inventory.jsonl"):
        inv[(r["store_id"], r["product_id"])] = r
    for e in _read_jsonl(_local_events_path()):
        k = (e["store_id"], e["product_id"])
        cur = inv.get(k, {"store_id": k[0], "product_id": k[1], "quantity": 0})
        if e.get("quantity") is not None:
            cur["quantity"] = int(e["quantity"])
        elif e.get("delta") is not None:
            cur["quantity"] = max(0, int(cur.get("quantity") or 0) + int(e["delta"]))
        inv[k] = cur
    return inv


def _local_events_path() -> Path:
    return Path(os.environ.get("LOCAL_EVENTS_FILE", str(_data_dir() / "store_inventory_events.jsonl")))


def _local_shelf_path() -> Path:
    return _data_dir() / "shelf_locations.jsonl"


def slot_label(slot_ids: list[str] | None, tier: int | None = None) -> str:
    """['A01-203','A01-204'] → 'A01-203–204 (tier 2, positions 3–4 from left)'."""
    ps = [p for p in (parse_slot(x) for x in slot_ids or []) if p]
    if not ps:
        return ""
    ps.sort(key=lambda p: p[2])
    first, last = ps[0], ps[-1]
    head = slot_id(*first) + (f"–{last[1]}{last[2]:02d}" if len(ps) > 1 else "")
    pos = (f"positions {first[2]}–{last[2]}" if len(ps) > 1 else f"position {first[2]}")
    return f"{head} (tier {tier or first[1]}, {pos} from left)"


def shelf_label(sh: dict | None) -> str:
    if not sh:
        return ""
    fid = sh.get("fixture_id") or sh.get("gondola_id") or ""
    if fid == "RX" and not sh.get("slot_ids"):
        return "Pharmacy counter (handed over at checkout)"
    if sh.get("slot_ids"):
        return slot_label(sh["slot_ids"], sh.get("tier"))
    parts = [fid]
    if sh.get("tier"):
        parts.append(f"tier {sh['tier']}")
    if sh.get("position"):
        parts.append(f"position {sh['position']} from left")
    return " ".join(p for p in parts if p)


def photo_url(uri: str | None) -> str | None:
    if not uri:
        return None
    if uri.startswith("demo:"):
        return f"/api/staff/demo-photos/{uri[5:]}"
    f = _fn("repository", "photo_url")
    if f:
        try:
            u = f(uri)
            if u:
                return u
        except Exception:
            pass
    if uri.startswith("gs://"):
        path = uri.split("/", 3)[3] if uri.count("/") >= 3 else ""
        return f"/api/photos/{path}"
    if uri.startswith("local://"):
        return f"/api/photos/{uri[len('local://'):]}"
    if uri.startswith("/") or uri.startswith("http"):
        return uri
    return f"/api/photos/{uri}"


def _local_shelf(store_id: str, product_id: str) -> dict | None:
    best = None
    for r in _read_jsonl(_local_shelf_path()) + list(_registrations):
        if r.get("store_id") == store_id and r.get("product_id") == product_id:
            if best is None or str(r.get("registered_at") or "") >= str(best.get("registered_at") or ""):
                best = r
    if not best:
        return None
    sh = {k: best.get(k) for k in ("fixture_id", "slot_ids", "gondola_id", "side", "tier", "position", "facings")}
    sh["fixture_id"] = sh.get("fixture_id") or sh.get("gondola_id")
    sh["slot_label"] = shelf_label(sh)
    sh["label"] = sh["slot_label"]
    sh["photo_url"] = photo_url(best.get("photo_uri"))
    return sh


def store_status(store_id: str, product_id: str) -> dict:
    """{quantity, status, shelf} for one store, through the customer inventory module when possible."""
    inv_f, shelf_f = _fn("inventory", "inventory_for"), _fn("inventory", "shelf_for")
    if inv_f and shelf_f:
        try:
            inv = inv_f(store_id, product_id)
            if inv is None:
                return {"quantity": None, "status": "not_carried", "shelf": None}
            q = inv.get("quantity")
            return {"quantity": q, "status": inv.get("status") or status_for(q), "shelf": shelf_f(store_id, product_id),
                    "updated_at": inv.get("updated_at")}
        except Exception:
            log.exception("inventory_for adapter failed")
    f = _fn("inventory", "get_availability", "availability", "build_availability")
    if f:
        try:
            av = f(product_id, store_id)
            if hasattr(av, "model_dump"):
                av = av.model_dump()
            cur = (av or {}).get("current") or {}
            if cur:
                return {"quantity": cur.get("quantity"), "status": cur.get("status"), "shelf": cur.get("shelf")}
        except Exception:
            log.exception("availability adapter failed")
    inv = _local_inventory().get((store_id, product_id))
    if inv is None:
        return {"quantity": None, "status": "not_carried", "shelf": None}
    q = int(inv.get("quantity") or 0)
    return {"quantity": q, "status": status_for(q), "shelf": _local_shelf(store_id, product_id)}


# ---- writes ----
def save_shelf_location(row: dict) -> None:
    f = _fn("repository", "append_shelf_location", "insert_shelf_location", "add_shelf_location", "save_shelf_location")
    if f:
        saved = f(row)
        if isinstance(saved, dict):
            row.update(saved)
        rec = _fn("inventory", "record_shelf")
        if rec:
            rec(row)  # apply to this instance's cache immediately (customer view in the same process)
    else:
        _append_jsonl(_local_shelf_path() if data_mode() == "local" else LOCAL_PHOTO_DIR / "shelf_locations.jsonl", row)
    with _lock:
        _registrations.append(row)
        del _registrations[:-200]
    invalidate_cache()


def save_inventory(store_id: str, product_id: str, quantity: int, by: str) -> None:
    setq = _fn("inventory", "set_quantity", "set_inventory_quantity")
    if setq:  # appends the store_inventory_events row + updates the cache itself
        try:
            setq(store_id, product_id, int(quantity), by)
        except TypeError:
            setq(store_id, product_id, int(quantity))
        return
    ev = {"store_id": store_id, "product_id": product_id, "delta": None, "quantity": int(quantity), "by": by, "at": now_iso()}
    f = _fn("repository", "append_inventory_event", "insert_inventory_event", "add_inventory_event")
    if f:
        f(ev)
    else:
        _append_jsonl(_local_events_path(), ev)
    invalidate_cache()


def upload_photo(store_id: str, data: bytes, content_type: str = "image/jpeg") -> str:
    fname = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jpg"
    path = f"shelf_photos/{store_id}/{fname}"
    f = _fn("repository", "upload_photo", "put_photo", "save_photo")
    if f:
        try:
            return f(store_id, fname, data, content_type)
        except TypeError:
            return f(path, data)
    if data_mode() == "local":
        dst = LOCAL_PHOTO_DIR / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return f"local://{path}"
    from google.cloud import storage  # lazy
    blob = storage.Client(project=PROJECT_ID).bucket(PHOTO_BUCKET).blob(path)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{PHOTO_BUCKET}/{path}"


def push_retail_local_inventory(store_id: str, product_id: str, gondola_id: str, tier: int,
                                slot_ids: list[str] | None = None) -> dict:
    """Best effort: Retail addLocalInventories attributes shelf_id / tier / slot_ids for place = store_id.

    Returns a small status dict that the staff UI shows as a toast
    (`status`: synced | submitted | skipped | failed)."""
    out = {"status": "skipped", "place_id": store_id, "product_id": product_id, "target": "AI Commerce Search Local Inventory"}
    flag = os.environ.get("RETAIL_LOCAL_INVENTORY", "1")
    if flag == "0" or (data_mode() == "local" and flag != "force"):  # "force": push even from a local-data dev server
        out["reason"] = "RETAIL_LOCAL_INVENTORY=0" if flag == "0" else "local mode"
        return out
    if not PROJECT_NUMBER and not PROJECT_ID:
        out["reason"] = "PROJECT_NUMBER not set"
        return out
    try:
        from google.cloud import retail_v2alpha as retail
        from google.protobuf import field_mask_pb2

        client = retail.ProductServiceClient()
        name = (f"projects/{PROJECT_NUMBER or PROJECT_ID}/locations/global/catalogs/default_catalog/branches/0/products/{product_id}")
        li = retail.LocalInventory(place_id=store_id, attributes={
            "shelf_id": retail.CustomAttribute(text=[gondola_id]),
            "tier": retail.CustomAttribute(numbers=[float(tier)]),
            **({"slot_ids": retail.CustomAttribute(text=[",".join(slot_ids)])} if slot_ids else {}),
        })
        op = client.add_local_inventories(request=retail.AddLocalInventoriesRequest(
            product=name, local_inventories=[li], allow_missing=True,
            add_mask=field_mask_pb2.FieldMask(paths=["attributes.shelf_id", "attributes.tier"]
                                              + (["attributes.slot_ids"] if slot_ids else []))))
        op_name = getattr(getattr(op, "operation", None), "name", "") or ""
        log.info("retail addLocalInventories %s@%s -> %s", product_id, store_id, op_name or "submitted")
        out.update(status="synced", operation=op_name)
    except Exception as e:
        log.warning("retail addLocalInventories failed (non-fatal): %s", e)
        out.update(status="failed", reason=str(e)[:160])
    return out


def _push_with_timeout(*args, timeout: float = 4.0) -> dict:
    """Run the Retail push off the request thread; answer within `timeout` s (status 'submitted' if still running)."""
    box: dict = {}

    def run():
        box["r"] = push_retail_local_inventory(*args)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("r") or {"status": "submitted", "place_id": args[0], "product_id": args[1],
                            "target": "AI Commerce Search Local Inventory"}


# ---------------------------------------------------------------------------
# Demo photos
# ---------------------------------------------------------------------------
def load_demo_manifest() -> list[dict]:
    if not DEMO_MANIFEST.exists():
        return []
    try:
        d = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
        return d.get("photos", d) if isinstance(d, dict) else d
    except Exception:
        log.exception("bad demo manifest")
        return []


def demo_photo(photo_id: str) -> dict | None:
    return next((p for p in load_demo_manifest() if p.get("id") == photo_id), None)


_prewarm_started = [False]


def _prewarm() -> None:
    """Analyze every demo photo once in the background so the live demo answers instantly (cache 30 min)."""
    for p in load_demo_manifest():
        key = f"{p.get('store_id')}:{p['id']}"
        if key in _analysis_cache:
            continue
        try:
            res = analyze(p["store_id"], (DEMO_DIR / p["file"]).read_bytes(), "image/jpeg", p)
            if not res.get("vision_error"):
                _analysis_cache[key] = (time.time(), res)
        except Exception as e:  # noqa: BLE001
            log.warning("prewarm %s failed: %s", p.get("id"), e)


def maybe_prewarm() -> None:
    flag = os.environ.get("STAFF_PREWARM", "1" if os.environ.get("K_SERVICE") else "0")
    if flag == "1" and not _prewarm_started[0] and os.environ.get("STAFF_VISION_MOCK") != "1":
        _prewarm_started[0] = True
        threading.Thread(target=_prewarm, daemon=True).start()


@router.get("/demo-photos")
async def api_demo_photos(store_id: str | None = None):
    maybe_prewarm()
    out = []
    for p in load_demo_manifest():
        if store_id and p.get("store_id") != store_id:
            continue
        prod = product_by_id(p.get("product_id", "")) if p.get("product_id") else None
        out.append({
            "id": p["id"], "url": f"/api/staff/demo-photos/{p['file']}", "store_id": p.get("store_id"),
            "hint": p.get("hint") or "", "gondola_id": p.get("fixture_id") or p.get("gondola_id"),
            "fixture_id": p.get("fixture_id") or p.get("gondola_id"), "slot_ids": p.get("slot_ids") or [],
            "product_title": (prod or {}).get("title"), "product_image": (prod or {}).get("image"),
        })
    return {"photos": out}


@router.get("/demo-photos/{file}")
async def api_demo_photo_file(file: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(jpg|jpeg|png|webp)", file):
        raise HTTPException(404)
    p = DEMO_DIR / file
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "public, max-age=3600"})


# ---------------------------------------------------------------------------
# Vision analysis
# ---------------------------------------------------------------------------
VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "slot_label_text": {"type": "STRING", "description": "Exact text of the shelf-edge slot label tag on the price rail directly under the main product, e.g. 'A01-203'. Empty if not readable."},
        "other_slot_labels": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Other slot label tags readable in the photo (exact text)."},
        "plate_text": {"type": "STRING", "description": "Exact text of the fixture number plate at the shelf end / header, e.g. 'A01'. Empty if none."},
        "tier": {"type": "INTEGER", "description": "Shelf tier of the main product, 1 = bottom shelf ... 5 = top shelf."},
        "tier_count": {"type": "INTEGER", "description": "Number of shelf tiers visible on the fixture."},
        "facings_slots": {"type": "INTEGER", "description": "How many 30 cm slots (label tags) the main product's facings span (1-3)."},
        "package_text": {"type": "STRING", "description": "All readable text on the main product package, transcribed as printed."},
        "product_type": {"type": "STRING", "description": "Short generic product type in English, e.g. 'cold medicine tablets', 'facial toner', 'cooling gel sheet'."},
        "brand": {"type": "STRING"},
        "search_query": {"type": "STRING", "description": "Best English search query (2-5 words) to find this product in the catalog, e.g. 'Cymbal moisturizing toner'."},
        "confidence": {"type": "NUMBER", "description": "0..1 overall confidence."},
    },
    "required": ["slot_label_text", "plate_text", "tier", "package_text", "product_type", "search_query", "confidence"],
}

VISION_PROMPT = """You are an assistant that helps store staff register product shelf locations in a supermarket / drugstore.
The photo shows a store display fixture (shelf).
1) Read exactly the shelf-edge "slot number label" on the price rail directly under the most prominent product in the center of the
   photo (the product to register). Format example: "A01-203" = fixture A01, tier 2, 3rd slot from the left.
   Put any other readable slot number labels in other_slot_labels.
2) Read exactly the fixture number plate at the end or top of the shelf (example: "A01").
3) Estimate which tier the product is on (1 = bottom shelf) and how many slots it spans horizontally
   (1 slot = 30 cm = one label).
4) Transcribe the readable text on the product package, and return the product type, brand and a search query in English.
If an item is hard to determine, return an empty string or 0."""

_vision_client = None
VISION_THINKING = os.environ.get("STAFF_VISION_THINKING", "low")
_thinking_ok = [VISION_THINKING not in ("", "none", "off")]


def _gemini_client():
    global _vision_client
    if _vision_client is None:
        from google import genai
        _vision_client = genai.Client(vertexai=True, project=PROJECT_ID, location=VISION_LOCATION)
    return _vision_client


def gemini_analyze(image: bytes, mime: str = "image/jpeg") -> dict:
    """Call Gemini vision with structured output (monkeypatched in tests)."""
    from google.genai import types

    client = _gemini_client()
    last = None
    for attempt in range(3):
        try:
            kw = dict(response_mime_type="application/json", response_schema=VISION_SCHEMA, temperature=0)
            if _thinking_ok[0]:
                try:
                    kw["thinking_config"] = types.ThinkingConfig(thinking_level=VISION_THINKING)
                except Exception:
                    _thinking_ok[0] = False
            try:
                r = client.models.generate_content(
                    model=VISION_MODEL, contents=[types.Part.from_bytes(data=image, mime_type=mime), VISION_PROMPT],
                    config=types.GenerateContentConfig(**kw))
            except Exception as e:
                if "thinking" not in str(e).lower() or "thinking_config" not in kw:
                    raise
                _thinking_ok[0] = False  # model/SDK without thinking_level → retry plain
                kw.pop("thinking_config")
                r = client.models.generate_content(
                    model=VISION_MODEL, contents=[types.Part.from_bytes(data=image, mime_type=mime), VISION_PROMPT],
                    config=types.GenerateContentConfig(**kw))
            return json.loads(r.text)
        except Exception as e:
            last = e
            if any(k in str(e) for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500")):
                time.sleep(1.5 * (attempt + 1))
                continue
            break
    raise RuntimeError(f"vision failed: {last}")


_PLATE_RE = re.compile(r"([A-Z]{1,2})\s*[-\u2010\uff0d\u30fc\u2015_ ]?\s*(\d{1,3})")


def normalize_plate(text: str, known: dict[str, dict] | None = None) -> str | None:
    """'a01' / 'Ａ－０１' / 'A 1' -> 'A-01' (matched against known gondola ids when given)."""
    if not text:
        return None
    t = unicodedata.normalize("NFKC", text).upper()
    known = known or {}
    if t.strip() in known:
        return t.strip()
    if "RX" in t and "RX" in known:
        return "RX"
    for m in _PLATE_RE.finditer(t):
        letter, num = m.group(1), int(m.group(2))
        for cand in (f"{letter}-{num:02d}", f"{letter}-{num}", f"{letter}{num:02d}", f"{letter}-{num:03d}"):
            if not known or cand in known:
                return cand
    return None


def _clamp(v: Any, lo: int, hi: int, default: int | None = None) -> int | None:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v)) if v else default


_analysis_cache: dict[str, tuple[float, dict]] = {}


class AnalyzeReq(BaseModel):
    store_id: str = "S01"
    photo_id: str | None = None
    image_base64: str | None = None


def _decode_image(b64: str) -> tuple[bytes, str]:
    mime = "image/jpeg"
    if b64.startswith("data:"):
        head, b64 = b64.split(",", 1)
        mime = head[5:].split(";")[0] or mime
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception:
        raise HTTPException(400, "image_base64 is invalid")
    if not data:
        raise HTTPException(400, "The image is empty")
    return _shrink(data, mime)


def _shrink(data: bytes, mime: str, max_px: int = 1280) -> tuple[bytes, str]:
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(data))
        im = ImageOps.exif_transpose(im).convert("RGB")
        if max(im.size) > max_px:
            im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=86)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, mime


def _photo_bytes(req_photo_id: str | None, b64: str | None) -> tuple[bytes, str, dict | None]:
    if req_photo_id:
        meta = demo_photo(req_photo_id)
        if not meta:
            raise HTTPException(404, f"demo photo not found: {req_photo_id}")
        p = DEMO_DIR / meta["file"]
        if not p.exists():
            raise HTTPException(404, "demo photo file missing")
        return p.read_bytes(), "image/jpeg", meta
    if b64:
        data, mime = _decode_image(b64)
        return data, mime, None
    raise HTTPException(400, "Specify photo_id or image_base64")


def analyze(store_id: str, image: bytes, mime: str, meta: dict | None) -> dict:
    known = fixture_index(store_id)
    t0 = time.time()
    vision_error = None
    try:
        if os.environ.get("STAFF_VISION_MOCK") == "1" and meta:  # offline e2e: echo the demo manifest
            v = {"slot_label_text": (meta.get("slot_ids") or [""])[0], "plate_text": meta.get("fixture_id", ""),
                 "tier": meta.get("tier"), "facings_slots": len(meta.get("slot_ids") or []) or 1,
                 "package_text": "", "product_type": meta.get("hint", ""), "search_query": meta.get("hint", ""),
                 "confidence": 0.9}
        else:
            v = gemini_analyze(image, mime) or {}
    except Exception as e:
        log.warning("vision failed: %s", e)
        vision_error = str(e)[:200]
        v = {}
    slot_text = (v.get("slot_label_text") or "").strip()
    plate_text = (v.get("plate_text") or "").strip()
    # 1) slot label (most specific) → fixture/tier/index; 2) plate → fixture; 3) demo hint
    fid, tier, idx, source = None, None, None, None
    for txt in [slot_text] + list(v.get("other_slot_labels") or []):
        ps = parse_slot(txt)
        if ps and (not known or ps[0] in known):
            fx = known.get(ps[0]) or {}
            if 1 <= ps[1] <= int(fx.get("tiers") or 9) and 1 <= ps[2] <= int(fx.get("slots_per_tier") or 99):
                fid, tier, idx = ps
                source = "slot_label" if txt == slot_text else "nearby_label"
                break
    if fid is None:
        f2 = normalize_fixture(plate_text, known)
        if f2:
            fid, source = f2, "plate"
    if fid is None and meta and (meta.get("fixture_id") in known or not known):
        fid, source = meta.get("fixture_id"), "demo_hint"
    fx = known.get(fid or "") or {}
    tiers = int(fx.get("tiers") or 5)
    if tier is None:
        tier = _clamp(v.get("tier"), 1, tiers, None)
    if tier is None and meta:
        tier = meta.get("tier")
    facings = _clamp(v.get("facings_slots"), 1, 3, 1) or 1
    slot_ids: list[str] = []
    if fid and tier:
        if idx is None and meta and meta.get("fixture_id") == fid and meta.get("slot_ids"):
            mp = parse_slot(meta["slot_ids"][0])
            idx = mp[2] if mp and mp[1] == tier else None
            if idx is not None and not v.get("facings_slots"):
                facings = len(meta["slot_ids"])
        if idx is not None:
            spt = int(fx.get("slots_per_tier") or 99)
            if source == "nearby_label":
                facings = 1
            slot_ids = [slot_id(fid, tier, i) for i in range(idx, min(spt, idx + facings - 1) + 1)]
            if meta and meta.get("slot_ids") and meta["slot_ids"][0] == slot_ids[0]:
                slot_ids = list(meta["slot_ids"])  # demo photo: label read OK → use the known facing width
    ocr = (v.get("package_text") or "").strip()
    queries = [q for q in [v.get("search_query"), " ".join(x for x in [v.get("brand"), v.get("product_type")] if x),
                           v.get("product_type"), (meta or {}).get("hint")] if q and str(q).strip()]
    cands: list[dict] = []
    seen = set()
    if meta and meta.get("product_id"):
        p = product_by_id(meta["product_id"])
        if p:
            p["match"] = "demo_hint"
            cands.append(p)
            seen.add(p["id"])
    engine_used = getattr(get_engine(), "mode", None)
    lists = [search_products(str(q), 8) for q in dict.fromkeys(str(q).strip() for q in queries[:3])]
    for i in range(8):  # round-robin merge of the per-query result lists
        for lst in lists:
            if i < len(lst) and lst[i]["id"] not in seen:
                lst[i]["match"] = "search"
                cands.append(lst[i])
                seen.add(lst[i]["id"])
    grid = fixture_grid(store_id, fid) if fid else None
    existing = []
    for sid in slot_ids:
        for x in _slot_products(grid, sid):
            pid = x if isinstance(x, str) else x.get("id") or x.get("product_id")
            if pid and pid not in [e["id"] for e in existing]:
                pr = product_by_id(pid) if isinstance(x, str) else x
                existing.append({k: (pr or {}).get(k) for k in ("id", "title", "image", "drug_class")} | {"id": pid})
    conf = v.get("confidence")
    try:
        conf = round(float(conf), 2)
    except (TypeError, ValueError):
        conf = 0.0
    if not fid:
        conf = min(conf, 0.3)
    elif not slot_ids:
        conf = min(conf, 0.6)
    return {
        "fixture_id": fid, "slot_id": slot_ids[0] if slot_ids else None, "slot_ids": slot_ids,
        "slot_label": slot_label(slot_ids, tier) if slot_ids else "",
        "slot_label_text": slot_text, "plate_text": plate_text, "detect_source": source,
        "fixture": {k: fx.get(k) for k in ("id", "label", "type", "face", "tiers", "slots_per_tier", "slot_width_cm", "zone")}
        if fx else None,
        "existing_products": existing,
        # v8-compatible fields
        "gondola_id": fid, "gondola_valid": bool(fid), "gondola_label": fx.get("label"),
        "tier_guess": tier, "tier_count": tiers, "side_guess": fx.get("face"),
        "position_guess": int(slot_ids[0].rsplit("-", 1)[1][1:]) if slot_ids else None,
        "facings_guess": len(slot_ids) or facings,
        "ocr_text": ocr, "product_type": v.get("product_type") or "", "search_query": queries[0] if queries else "",
        "candidates": cands[:8], "confidence": conf, "model": VISION_MODEL, "engine": engine_used,
        "vision_error": vision_error, "elapsed_ms": int((time.time() - t0) * 1000),
    }


@router.post("/analyze-photo")
async def api_analyze_photo(req: AnalyzeReq):
    if req.store_id not in store_map() and store_map():
        raise HTTPException(400, f"unknown store_id {req.store_id}")
    image, mime, meta = _photo_bytes(req.photo_id, req.image_base64)
    key = f"{req.store_id}:{req.photo_id}" if req.photo_id else None
    if key and key in _analysis_cache and time.time() - _analysis_cache[key][0] < 1800:
        return {**_analysis_cache[key][1], "cached": True}
    res = await asyncio.to_thread(analyze, req.store_id, image, mime, meta)
    if key and not res.get("vision_error"):
        _analysis_cache[key] = (time.time(), res)
    return res


# ---------------------------------------------------------------------------
# Register shelf location
# ---------------------------------------------------------------------------
class ShelfReq(BaseModel):
    store_id: str
    product_id: str
    slot_ids: list[str] | None = None          # v9: contiguous slots on one tier, e.g. ["A01-203", "A01-204"]
    fixture_id: str | None = None
    gondola_id: str | None = None              # v8 compat (→ fixture_id)
    side: str | None = None
    tier: int | None = Field(default=None, ge=1, le=9)
    position: int | None = Field(default=None, ge=1, le=99)
    facings: int | None = Field(default=None, ge=1, le=3)
    note: str | None = ""
    photo_id: str | None = None
    image_base64: str | None = None
    staff_name: str | None = "Staff"
    reassign: bool | None = False


@router.get("/unassigned")
async def api_staff_unassigned(store_id: str = Query(default="S01")):
    """CONTRACT v11: list products with unassigned shelf status."""
    items = inventory.unassigned_products(store_id)
    return {
        "store_id": store_id,
        "count": len(items),
        "items": items,
    }


@router.post("/shelf-locations")
async def api_shelf_locations(req: ShelfReq):
    stores = store_map()
    if stores and req.store_id not in stores:
        raise HTTPException(400, f"unknown store_id {req.store_id}")
    known = fixture_index(req.store_id)
    slot_ids = [unicodedata.normalize("NFKC", x).upper().strip() for x in (req.slot_ids or []) if x and x.strip()]
    if not slot_ids:  # v8-style body → derive slots
        fid = normalize_fixture(req.fixture_id or req.gondola_id or "", known)
        if not fid:
            raise HTTPException(400, f"Fixture {req.fixture_id or req.gondola_id} is not in this store's layout")
        if not req.tier:
            raise HTTPException(400, "Specify slot_ids or tier")
        start = req.position or 1
        slot_ids = [slot_id(fid, req.tier, i) for i in range(start, start + (req.facings or 1))]
    else:
        ps = [parse_slot(x) for x in slot_ids]
        slot_ids = [slot_id(*p) if p else x for p, x in zip(ps, slot_ids)]
    fx, tier, idx = validate_slots(req.store_id, slot_ids, known)
    slot_ids = [slot_id(fx["id"], tier, i) for i in idx]
    prod = product_by_id(req.product_id)
    if prod is None:
        raise HTTPException(404, f"product not found: {req.product_id}")
    photo_uri = None
    if req.photo_id or req.image_base64:
        image, mime, meta = _photo_bytes(req.photo_id, req.image_base64)
        try:
            photo_uri = await asyncio.to_thread(upload_photo, req.store_id, image, mime)
        except Exception as e:
            log.warning("photo upload failed: %s", e)
            if meta:
                photo_uri = f"demo:{meta['file']}"
    row = {
        "store_id": req.store_id, "product_id": req.product_id, "fixture_id": fx["id"], "slot_ids": slot_ids,
        "tier": tier, "facings": len(slot_ids), "gondola_id": fx["id"], "side": fx.get("face") or req.side,
        "position": idx[0], "photo_uri": photo_uri, "source": "staff",
        "registered_by": (req.staff_name or "Staff")[:40], "registered_at": now_iso(), "note": (req.note or "")[:200],
    }
    try:
        await asyncio.to_thread(save_shelf_location, row)
    except Exception as e:
        log.exception("save shelf failed")
        raise HTTPException(500, f"Registration failed: {e}")
    local_inventory = await asyncio.to_thread(_push_with_timeout, req.store_id, req.product_id, fx["id"], tier, slot_ids)
    loc = {**row, "primary_slot": slot_ids[0], "slot_label": slot_label(slot_ids, tier),
           "label": fx.get("label") or fx["id"], "fixture_type": fx.get("type"), "slot_width_cm": fx.get("slot_width_cm"),
           "photo_url": photo_url(photo_uri), "product": prod,
           "fixture": {k: fx.get(k) for k in ("id", "label", "type", "zone", "x", "y", "w", "h", "face", "tiers",
                                               "slots_per_tier", "slot_width_cm")}}
    loc["gondola"] = loc["fixture"]
    return {"ok": True, "location": loc, "local_inventory": local_inventory}


# ---------------------------------------------------------------------------
# Stock search / inventory / registrations
# ---------------------------------------------------------------------------
def _store_rows(product_id: str) -> list[dict]:
    stores = load_stores()
    ids = [s["store_id"] for s in stores] or list(STORE_IDS)
    names = {s["store_id"]: s.get("short_name") or s.get("name") for s in stores}
    out = []
    for sid in ids:
        st = store_status(sid, product_id)
        sh = st.get("shelf")
        if sh and not sh.get("slot_label") and sh.get("slot_ids"):
            sh["slot_label"] = shelf_label(sh)
        if sh and not sh.get("label"):
            sh["label"] = shelf_label(sh)
        out.append({"store_id": sid, "short_name": names.get(sid, sid), "quantity": st.get("quantity"),
                    "status": st.get("status"), "shelf": sh})
    return out


@router.get("/stock-search")
async def api_stock_search(q: str = "", limit: int = 10):
    q = q.strip()
    if not q:
        return {"results": []}

    def run():
        prods = []
        if re.fullmatch(r"\d{8,14}", q):
            p = product_by_id(q)
            if p:
                prods = [p]
        if not prods:
            prods = search_products(q, max(1, min(limit, 20)))
        return [{"product": p, "stores": _store_rows(p["id"])} for p in prods]

    results = await asyncio.to_thread(run)
    for r in results:
        here = [s for s in r["stores"] if s["status"] in ("in_stock", "low_stock")]
        r["in_stock_stores"] = [s["store_id"] for s in here]
    return {"results": results, "engine": getattr(get_engine(), "mode", None)}


class InvReq(BaseModel):
    store_id: str
    product_id: str
    quantity: int = Field(ge=0, le=9999)
    staff_name: str | None = "Staff"


@router.patch("/inventory")
async def api_inventory(req: InvReq):
    stores = store_map()
    if stores and req.store_id not in stores:
        raise HTTPException(400, f"unknown store_id {req.store_id}")
    if product_by_id(req.product_id) is None:
        raise HTTPException(404, f"product not found: {req.product_id}")
    try:
        await asyncio.to_thread(save_inventory, req.store_id, req.product_id, req.quantity, req.staff_name or "Staff")
    except Exception as e:
        log.exception("inventory update failed")
        raise HTTPException(500, f"Inventory update failed: {e}")
    st = await asyncio.to_thread(store_status, req.store_id, req.product_id)
    q = st.get("quantity") if st.get("quantity") is not None else req.quantity
    return {"ok": True, "quantity": q, "status": st.get("status") or status_for(q)}


def _list_registrations(store_id: str | None, limit: int) -> list[dict]:
    f = _fn("repository", "staff_registrations", "list_shelf_locations", "recent_shelf_locations", "list_registrations")
    rows: list[dict] = []
    if f:
        try:
            try:
                rows = list(f(store_id, limit) or [])
            except TypeError:
                rows = list(f() or [])
        except Exception:
            log.exception("registrations adapter failed")
            rows = []
    if not rows:
        path = _local_shelf_path() if data_mode() == "local" else LOCAL_PHOTO_DIR / "shelf_locations.jsonl"
        rows = _read_jsonl(path)
    keyset = {(r.get("store_id"), r.get("product_id"), str(r.get("registered_at"))) for r in rows}
    rows += [r for r in _registrations if (r.get("store_id"), r.get("product_id"), str(r.get("registered_at"))) not in keyset]
    rows = [r for r in rows if r.get("source") == "staff" and (not store_id or r.get("store_id") == store_id)]
    rows.sort(key=lambda r: str(r.get("registered_at") or ""), reverse=True)
    out = []
    for r in rows[:limit]:
        r = dict(r)
        if isinstance(r.get("registered_at"), dt.datetime):
            r["registered_at"] = r["registered_at"].isoformat()
        if isinstance(r.get("slot_ids"), str):
            try:
                r["slot_ids"] = json.loads(r["slot_ids"])
            except Exception:
                r["slot_ids"] = [x for x in re.split(r"[,\s]+", r["slot_ids"]) if x]
        r["fixture_id"] = r.get("fixture_id") or r.get("gondola_id")
        r["slot_label"] = shelf_label(r)
        r["label"] = r["slot_label"]
        r["photo_url"] = photo_url(r.get("photo_uri"))
        p = product_by_id(r.get("product_id", ""))
        r["product"] = {k: (p or {}).get(k) for k in ("id", "title", "image", "drug_class")} if p else None
        out.append(r)
    return out


@router.get("/registrations")
async def api_registrations(store_id: str | None = None, limit: int = 30):
    return {"registrations": await asyncio.to_thread(_list_registrations, store_id, max(1, min(limit, 100)))}
