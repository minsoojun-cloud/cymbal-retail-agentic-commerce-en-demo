"""Store inventory + shelf locations cache and Availability assembly (CONTRACT v8).

Availability = {product_id, current:{store_id, short_name, status, quantity, shelf}, nearby:[…], online:{available, price, url}}
- current: ALWAYS present and fixed as the first row (even not_carried / out_of_stock)
- status: in_stock | low_stock (qty ≤ 3) | out_of_stock | not_carried (no row for the store)
- shelf: latest registered_at per (store_id, product_id); abuse_control products → "RX" (pharmacy counter)
Cache: full load at first use; deltas (shelf_locations / store_inventory_events newer than the last seen timestamp)
every STORE_DATA_REFRESH_SEC (default 20 s) or immediately after invalidate().
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from . import config, deps, layout, repository, stores

log = logging.getLogger("store-navi.inventory")

LOW_STOCK_MAX = 3
_lock = threading.RLock()
_inv: dict[tuple[str, str], dict] = {}
_shelf: dict[tuple[str, str], dict] = {}
_slot_idx: dict[tuple[str, str], set] = {}   # (store_id, slot_id) → product ids (many-to-many, CONTRACT v9)
_state: dict[str, Any] = {"loaded": False, "refreshed": 0.0, "shelf_ts": "", "event_ts": "", "dirty": False,
                          "thread": None}


def status_of(quantity: int | None, availability: str | None = None) -> str:
    if quantity is None and not availability:
        return "not_carried"
    q = int(quantity or 0)
    if q <= 0 or str(availability or "").upper() == "OUT_OF_STOCK":
        return "out_of_stock"
    return "low_stock" if q <= LOW_STOCK_MAX else "in_stock"


def _ts(v: Any) -> str:
    """Normalize timestamps (ISO strings with any offset / datetimes) to comparable UTC ISO strings."""
    if not v:
        return ""
    import datetime as _dt

    try:
        d = v if isinstance(v, _dt.datetime) else _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
    except (TypeError, ValueError):
        return str(v)


def _slots_of(r: dict) -> list[str]:
    """slot_ids of a shelf_locations row (v9) or derived from v8 (gondola_id, tier, position)."""
    v = r.get("slot_ids")
    if isinstance(v, str):
        v = [x.strip() for x in v.strip("[]").replace('"', "").split(",") if x.strip()]
    if v:
        return [str(x) for x in v]
    fid = str(r.get("fixture_id") or r.get("gondola_id") or "")
    if fid and r.get("tier") not in (None, "") and r.get("position") not in (None, ""):
        return [layout.slot_id(fid, int(r["tier"]), int(r["position"]))]
    return []


def _apply_shelf(r: dict) -> None:
    k = (str(r.get("store_id")), str(r.get("product_id")))
    old = _shelf.get(k)
    if old is None or _ts(r.get("registered_at")) >= _ts(old.get("registered_at")):
        if old is not None:
            for sid in _slots_of(old):
                _slot_idx.get((k[0], sid), set()).discard(k[1])
        _shelf[k] = r
        for sid in _slots_of(r):
            _slot_idx.setdefault((k[0], sid), set()).add(k[1])
    if _ts(r.get("registered_at")) > _state["shelf_ts"]:
        _state["shelf_ts"] = _ts(r.get("registered_at"))


def _apply_event(e: dict) -> None:
    k = (str(e.get("store_id")), str(e.get("product_id")))
    row = dict(_inv.get(k) or {"quantity": 0, "availability": "OUT_OF_STOCK"})
    if e.get("quantity") is not None:
        q = int(e["quantity"])
    else:
        q = int(row.get("quantity") or 0) + int(e.get("delta") or 0)
    q = max(0, q)
    row.update(quantity=q, availability="IN_STOCK" if q > 0 else "OUT_OF_STOCK", updated_at=e.get("at"))
    _inv[k] = row
    if _ts(e.get("at")) > _state["event_ts"]:
        _state["event_ts"] = _ts(e.get("at"))


def _full_load() -> None:
    t0 = time.time()
    inv = repository.load_inventory_rows()
    shelf = repository.load_shelf_rows()
    events = repository.load_inventory_events()
    vers = repository.latest_layout_versions()
    with _lock:
        _inv.clear()
        _shelf.clear()
        _slot_idx.clear()
        _state.update(shelf_ts="", event_ts="", layout_versions=vers)
        for r in inv:
            _inv[(str(r.get("store_id")), str(r.get("product_id")))] = {
                "quantity": int(r.get("quantity") or 0), "availability": r.get("availability") or "",
                "updated_at": r.get("updated_at")}
        for r in shelf:
            _apply_shelf(r)
        for e in sorted(events, key=lambda e: _ts(e.get("at"))):
            _apply_event(e)
        _state.update(loaded=True, refreshed=time.time(), dirty=False)
    log.info("inventory loaded: %d inventory rows, %d shelf rows, %d events (%.1fs)", len(_inv), len(_shelf),
             len(events), time.time() - t0)


def reload() -> None:
    _full_load()


def _delta() -> None:
    try:
        # Check if any store layout version has changed
        curr_vers = repository.latest_layout_versions()
        if _state.get("layout_versions") and curr_vers != _state["layout_versions"]:
            log.info("Layout version change detected (%s -> %s), reloading layout & inventory...",
                     _state["layout_versions"], curr_vers)
            layout.reload()
            _full_load()
            return
        _state["layout_versions"] = curr_vers

        shelf = repository.load_shelf_rows(since=_state["shelf_ts"] or None) if _state["shelf_ts"] else \
            repository.load_shelf_rows()
        events = repository.load_inventory_events(since=_state["event_ts"] or None)
    except Exception as e:  # noqa: BLE001
        log.warning("delta refresh failed: %s", e)
        return
    with _lock:
        for r in shelf:
            _apply_shelf(r)
        for e in sorted(events, key=lambda e: _ts(e.get("at"))):
            if _ts(e.get("at")) > _state["event_ts"] or not _state["event_ts"]:
                _apply_event(e)
        _state.update(refreshed=time.time(), dirty=False)


def ensure_loaded() -> None:
    if not _state["loaded"]:
        with _lock:
            if not _state["loaded"]:
                _full_load()
        return
    bg = _state.get("thread") is not None
    if _state["dirty"] or (not bg and time.time() - _state["refreshed"] > config.REFRESH_SEC):
        _delta()


def invalidate(full: bool = False) -> None:
    """Called after staff registrations / stock changes (and by tests). full=True reloads everything."""
    if full:
        _state["loaded"] = False
    else:
        _state["dirty"] = True


def start_background() -> None:
    """BigQuery mode: refresh deltas in a daemon thread so requests never wait on BigQuery."""
    if _state.get("thread") is not None:
        return

    def loop():
        while True:
            time.sleep(config.REFRESH_SEC)
            try:
                if _state["loaded"]:
                    _delta()
            except Exception:  # noqa: BLE001
                log.exception("background refresh failed")

    t = threading.Thread(target=loop, name="inventory-refresh", daemon=True)
    _state["thread"] = t
    t.start()


# ---------------------------------------------------------------------------------------------- lookups
def inventory_for(store_id: str, product_id: str) -> dict | None:
    ensure_loaded()
    r = _inv.get((store_id, product_id))
    if r is None:
        return None
    return {"quantity": int(r.get("quantity") or 0), "status": status_of(r.get("quantity"), r.get("availability")),
            "updated_at": r.get("updated_at")}


def set_quantity(store_id: str, product_id: str, quantity: int, by: str = "staff") -> dict:
    q = max(0, int(quantity))
    ev = repository.append_inventory_event({"store_id": store_id, "product_id": product_id, "delta": None,
                                            "quantity": q, "by": by})
    ensure_loaded()
    with _lock:
        _apply_event(ev)
    return {"quantity": q, "status": status_of(q, "IN_STOCK" if q else "OUT_OF_STOCK")}


def record_shelf(row: dict) -> None:
    """Apply a just-appended shelf_locations row (v8 or v9 with slot_ids) to this instance's cache immediately
    (slot → products index included, so the old slots lose the product and the new ones gain it)."""
    ensure_loaded()
    row = dict(row)
    slots = _slots_of(row)
    if slots:
        p = layout.parse_slot(slots[0])
        if p:
            row.setdefault("fixture_id", p[0])
            row.setdefault("tier", p[1])
            row.setdefault("position", p[2])
        row["slot_ids"] = slots
        row.setdefault("facings", len(slots))
    row.setdefault("gondola_id", row.get("fixture_id"))
    row.setdefault("registered_at", repository.now_iso())
    with _lock:
        _apply_shelf(row)


def _category_fixture(store_id: str, product: dict | None) -> dict | None:
    if not product:
        return None
    path = (product.get("category_path") or "").replace(" > ", ">")
    best, blen = None, -1
    for g in layout.fixtures(store_id):
        for c in g.get("categories") or []:
            cc = str(c).replace(" > ", ">")
            if path.startswith(cc) and len(cc) > blen:
                best, blen = g, len(cc)
    return best


_category_gondola = _category_fixture


def shelf_label(store_id: str, gondola_id: str, tier: int | None) -> str:
    if gondola_id == "RX":
        return "Pharmacy counter (ask at the register)"
    g = layout.fixture(store_id, gondola_id) or {}
    parts = [gondola_id]
    if g.get("label"):
        parts.append(str(g["label"]))
    if tier:
        parts.append(f"shelf {int(tier)}")
    return " ".join(parts)


def slot_label(slots: list[str]) -> str:
    """['A01-203','A01-204'] → "A01-203–204 (shelf 2, 3rd–4th from left)"."""
    ps = [layout.parse_slot(s) for s in slots]
    ps = [p for p in ps if p]
    if not ps:
        return ""
    first, last = slots[0], slots[-1]
    t, a, b = ps[0][1], ps[0][2], ps[-1][2]
    if len(ps) == 1 or a == b:
        return f"{first} (shelf {t}, {_ord(a)} from left)"
    return f"{first}–{last.rsplit('-', 1)[-1]} (shelf {t}, {_ord(a)}–{_ord(b)} from left)"


def _ord(n: int) -> str:
    n = int(n)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _shelf_obj(store_id: str, fid: str, slots: list[str], r: dict | None, source: str | None = None) -> dict:
    f = layout.fixture(store_id, fid) or {}
    p = layout.parse_slot(slots[0]) if slots else None
    tier = int(r["tier"]) if r and r.get("tier") not in (None, "") else (p[1] if p else None)
    pos = int(r["position"]) if r and r.get("position") not in (None, "") else (p[2] if p else None)
    return {"fixture_id": fid, "gondola_id": fid, "slot_ids": slots, "primary_slot": slots[0] if slots else None,
            "tier": tier, "position": pos, "facings": int((r or {}).get("facings") or len(slots) or 1),
            "side": (r or {}).get("side") or f.get("face"), "label": shelf_label(store_id, fid, tier),
            "fixture_label": f.get("label") or "", "slot_label": slot_label(slots) if fid != "RX" else
            (f"{slots[0]} (pharmacy counter)" if slots else "Pharmacy counter"),
            "fixture_type": f.get("type"), "slot_width_cm": f.get("slot_width_cm"),
            "photo_url": repository.photo_url((r or {}).get("photo_uri")), "counter": fid == "RX",
            "source": source or (r or {}).get("source"), "registered_by": (r or {}).get("registered_by"),
            "registered_at": (r or {}).get("registered_at"), "note": (r or {}).get("note") or ""}


def is_unassigned(r: dict | None) -> bool:
    if not r:
        return False
    if r.get("unassigned"):
        return True
    if r.get("source") == "layout_change" and not (r.get("fixture_id") or r.get("gondola_id")):
        return True
    note = r.get("note")
    if isinstance(note, dict) and note.get("unassigned"):
        return True
    if note and isinstance(note, str) and '"unassigned": true' in note.lower():
        return True
    return False


def shelf_for(store_id: str, product_id: str, product: dict | None = None) -> dict | None:
    """CONTRACT v9 shelf object (gondola_id kept = fixture_id), CONTRACT v11 unassigned handling."""
    ensure_loaded()
    product = product or deps.to_product(product_id)
    r = _shelf.get((store_id, product_id))
    if is_unassigned(r):
        return {
            "unassigned": True,
            "label": "Location being updated — please ask a staff member",
            "slot_label": "",
            "fixture_id": None,
            "gondola_id": None,
            "slot_ids": [],
            "primary_slot": "",
            "tier": None,
            "position": None,
            "facings": 0,
            "side": None,
            "fixture_type": None,
            "slot_width_cm": None,
            "photo_url": None,
            "counter": False,
            "source": "layout_change",
            "registered_by": r.get("registered_by"),
            "registered_at": r.get("registered_at"),
            "note": r.get("note") or "",
        }
    if product and product.get("abuse_control"):
        lay = layout.get_layout(store_id) or {}
        slots = [s for s in _slots_of(r or {}) if s.startswith("RX-")] or ["RX-101"]
        out = _shelf_obj(store_id, "RX", slots, r if (r or {}).get("fixture_id", (r or {}).get("gondola_id")) == "RX" else None,
                         source=(r or {}).get("source") or "rule")
        out.update(label=shelf_label(store_id, "RX", None), tier=None, position=None,
                   counter_label=(lay.get("counter") or {}).get("label") or "Pharmacy counter",
                   note="This item is handed over at the pharmacy counter",
                   photo_url=repository.photo_url((r or {}).get("photo_uri")))
        return out
    if r:
        fid = str(r.get("fixture_id") or r.get("gondola_id") or "")
        return _shelf_obj(store_id, fid, _slots_of(r), r)
    if (store_id, product_id) in _inv:
        g = _category_fixture(store_id, product)
        if g:
            out = _shelf_obj(store_id, g["id"], [], None, source="category")
            out.update(tier=None, position=None, slot_label="", side=g.get("face"))
            return out
    return None


def latest_shelf_rows(store_id: str | None = None) -> list[dict]:
    """CONTRACT v12: latest shelf_locations row per (store, product) from the in-memory cache (no BigQuery scan)."""
    ensure_loaded()
    with _lock:
        return [dict(r) for (sid, _pid), r in _shelf.items() if store_id is None or sid == store_id]


def fixture_product_counts(store_id: str) -> dict[str, int]:
    """CONTRACT v12: number of products currently registered on each fixture (by slot prefix)."""
    ensure_loaded()
    out: dict[str, int] = {}
    with _lock:
        for (sid, _pid), r in _shelf.items():
            if sid != store_id or is_unassigned(r):
                continue
            fids = {s.rsplit("-", 1)[0] for s in _slots_of(r)}
            for fid in fids:
                out[fid] = out.get(fid, 0) + 1
    return out


def unassigned_products(store_id: str) -> list[dict]:
    """CONTRACT v11: list all products with unassigned shelf status in store."""
    ensure_loaded()
    cat = deps.get_catalog()
    out = []
    with _lock:
        for (sid, pid), r in _shelf.items():
            if sid == store_id and is_unassigned(r):
                item = cat.get(pid) if cat else None
                prod = deps.to_product(item) if item else None
                note_val = r.get("note") or ""
                prev_slots = []
                prev_fid = None
                if isinstance(note_val, dict):
                    prev_slots = note_val.get("previous_slot_ids") or []
                    prev_fid = note_val.get("previous_fixture_id")
                elif isinstance(note_val, str) and note_val.startswith("{"):
                    try:
                        p_data = json.loads(note_val)
                        prev_slots = p_data.get("previous_slot_ids") or []
                        prev_fid = p_data.get("previous_fixture_id")
                    except Exception:
                        pass
                inv = _inv.get((sid, pid)) or {}
                imgs = (prod or {}).get("images") or ([prod["image"]] if (prod or {}).get("image") else [])
                out.append({
                    "product_id": pid,
                    "title": (prod or {}).get("title") or pid,
                    "thumbnail": imgs[0] if imgs else None,
                    "category": (prod or {}).get("category_path") or (prod or {}).get("primary_category"),
                    "quantity": inv.get("quantity", 0),
                    "status": inv.get("availability", "OUT_OF_STOCK"),
                    "previous_slot_ids": prev_slots,
                    "previous_fixture_id": prev_fid,
                    "registered_at": r.get("registered_at"),
                    "registered_by": r.get("registered_by"),
                })
    out.sort(key=lambda x: str(x.get("registered_at") or ""), reverse=True)
    return out


def fixture_grid(store_id: str, fixture_id: str) -> dict | None:
    """Front elevation: fixture + tiers (top → bottom) × slots with the products placed in each slot."""
    ensure_loaded()
    f = layout.fixture(store_id, fixture_id)
    if not f:
        return None
    cat = deps.get_catalog()
    n_slots, n_tiers = int(f["slots_per_tier"]), int(f["tiers"])
    for (sid_store, sid) in list(_slot_idx):     # v8 data may use positions beyond the nominal slot count
        if sid_store == store_id and sid.startswith(fixture_id + "-") and _slot_idx[(sid_store, sid)]:
            p = layout.parse_slot(sid)
            if p and p[0] == fixture_id:
                n_tiers, n_slots = max(n_tiers, p[1]), max(n_slots, p[2])
    disabled_set = set(f.get("disabled_slots") or [])
    tier_slots_cfg = f.get("tier_slots") or {}
    tiers = []
    for t in range(n_tiers, 0, -1):
        spt = int(tier_slots_cfg.get(str(t)) or tier_slots_cfg.get(t) or n_slots)
        slots = []
        for i in range(1, spt + 1):
            sid = layout.slot_id(fixture_id, t, i)
            disabled = sid in disabled_set
            prods = []
            if not disabled:
                for pid in sorted(_slot_idx.get((store_id, sid), ())):
                    n = cat.get(pid) if cat else None
                    if n:
                        p = deps.to_product(n) or {}
                        inv = inventory_for(store_id, pid)
                        prods.append({"id": pid, "title": n.get("title"), "image": n.get("image"),
                                      "drug_class": p.get("drug_class") or "", "status": inv["status"] if inv else "not_carried"})
            slots.append({"slot_id": sid, "index": i, "width_cm": f["slot_width_cm"], "products": prods, "disabled": disabled})
        tiers.append({"tier": t, "slots": slots})
    fx = {k: f.get(k) for k in ("id", "label", "type", "face", "tiers", "slots_per_tier", "slot_width_cm", "zone",
                                "length_cm", "x", "y", "w", "h", "body_id")}
    fx.update(tiers=n_tiers, slots_per_tier=n_slots)
    return {"store_id": store_id, "fixture": fx, "tiers": tiers}


def _row(store: dict, product_id: str, product: dict | None, li: dict[str, dict] | None = None) -> dict:
    inv = inventory_for(store["store_id"], product_id)
    if li is not None:
        rec = li.get(store["store_id"])
        if rec is None:
            inv = None if not inv else {**inv, "quantity": 0, "status": "out_of_stock"}
        else:
            q = int(rec.get("quantity") or 0)
            inv = {**(inv or {}), "quantity": q, "status": status_of(q, rec.get("availability"))}
    if channel_of(product) == "online_only":
        inv = None
    status = inv["status"] if inv else "not_carried"
    return {"store_id": store["store_id"], "short_name": store.get("short_name") or store.get("name"),
            "status": status, "quantity": inv["quantity"] if inv else 0,
            "shelf": shelf_for(store["store_id"], product_id, product) if inv else None}


IN_STOCK = ("in_stock", "low_stock")


def channel_of(product: dict | None) -> str:
    """CONTRACT v14 channel attribute: omni | store_only | online_only (default omni)."""
    if not product:
        return "omni"
    v = product.get("channel")
    if v is None:
        attrs = product.get("attributes") or product.get("_attrs") or {}
        if isinstance(attrs, dict):
            v = attrs.get("channel")
            if isinstance(v, dict):
                v = (v.get("text") or [None])[0]
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    v = str(v or "omni").strip().lower()
    return v if v in ("omni", "store_only", "online_only") else "omni"


# ------------------------------------------------------------------ Retail LocalInventory (CONTRACT v14)
_li_cache: dict[str, tuple[float, dict | None]] = {}
_li_client = None
LI_TTL = float(__import__("os").environ.get("LOCAL_INVENTORY_TTL_SEC", "20"))


def _retail_local_inventory(product_id: str) -> dict[str, dict] | None:
    """{store_id: {quantity, availability, shelf_id, slot_ids, tier}} from Retail Product.local_inventories, or None
    when disabled / unavailable (then the BigQuery cache is the source). Cached LI_TTL seconds per product."""
    global _li_client
    if not (config.RETAIL_LOCAL_INVENTORY and config.data_mode() == "bq" and config.PROJECT_NUMBER):
        return None
    now = time.time()
    hit = _li_cache.get(product_id)
    if hit and now - hit[0] < LI_TTL:
        return hit[1]
    out: dict[str, dict] | None = None
    try:
        if _li_client is None:
            from google.cloud import retail_v2

            _li_client = retail_v2.ProductServiceClient()
        name = (f"projects/{config.PROJECT_NUMBER}/locations/global/catalogs/default_catalog/branches/0/"
                f"products/{product_id}")
        prod = _li_client.get_product(name=name, timeout=4)
        out = {}
        for li in prod.local_inventories or []:
            rec: dict[str, Any] = {}
            for k, v in (li.attributes or {}).items():
                if v.numbers:
                    rec[k] = list(v.numbers)[0] if len(v.numbers) == 1 else list(v.numbers)
                elif v.text:
                    rec[k] = list(v.text)[0] if len(v.text) == 1 else list(v.text)
            q = rec.get("quantity")
            rec["quantity"] = int(q) if q not in (None, "") else (1 if "pickup-in-store" in li.fulfillment_types else 0)
            rec["availability"] = str(rec.get("availability") or ("IN_STOCK" if rec["quantity"] > 0 else "OUT_OF_STOCK"))
            out[li.place_id] = rec
    except Exception as e:  # noqa: BLE001
        log.info("Retail local inventory unavailable for %s (%s) → BigQuery cache", product_id, str(e)[:120])
        out = None
    if len(_li_cache) > 5000:
        _li_cache.clear()
    _li_cache[product_id] = (now, out)
    return out


def prefetch_local_inventory(product_ids: list[str]) -> bool:
    """Warm the Retail LocalInventory cache for a result page in parallel (≈1 RPC latency for 20 products).
    Returns True when Retail LocalInventory is the live source."""
    if not (config.RETAIL_LOCAL_INVENTORY and config.data_mode() == "bq" and config.PROJECT_NUMBER):
        return False
    ids = [i for i in dict.fromkeys(product_ids) if i not in _li_cache or time.time() - _li_cache[i][0] >= LI_TTL]
    if ids:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(16, len(ids))) as ex:
            list(ex.map(_retail_local_inventory, ids))
    return True


def get_availability(product_id: str, store_id: str, product: dict | None = None, live: bool = False) -> dict:
    """live=True (single-product endpoints) reads Retail LocalInventory first; lists use the BigQuery cache."""
    ensure_loaded()
    product = product or deps.to_product(product_id)
    channel = channel_of(product)
    li = _retail_local_inventory(product_id) if live else None
    source = "retail_local_inventory" if li is not None else ("bigquery" if config.data_mode() == "bq" else "local")
    all_stores = stores.load_stores()
    cur = stores.get_store(store_id) or (all_stores[0] if all_stores else None)
    out: dict[str, Any] = {"product_id": product_id, "channel": channel, "source": source, "current": None,
                           "nearby": [], "online": None}
    if cur:
        out["current"] = _row(cur, product_id, product, li)
        nearby = []
        for s in all_stores:
            if s["store_id"] == cur["store_id"]:
                continue
            row = _row(s, product_id, product, li)
            row["distance_m"] = stores.store_distance_m(cur, s)
            row["open_now"] = stores.open_now(s)
            nearby.append(row)
        nearby.sort(key=lambda r: (r["status"] not in IN_STOCK, r["distance_m"]))
        out["nearby"] = nearby
    price = int((product or {}).get("price") or 0)
    online_ok = (bool(product) and channel != "store_only"
                 and str(product.get("availability") or "IN_STOCK") == "IN_STOCK")
    out["online"] = {"available": online_ok, "price": price, "channel": channel,
                     "url": f"{config.ONLINE_STORE_URL}/?product={product_id}" if config.ONLINE_STORE_URL else ""}
    return out


def in_store_ids(store_id: str) -> set[str]:
    ensure_loaded()
    return {pid for (sid, pid), r in _inv.items()
            if sid == store_id and status_of(r.get("quantity"), r.get("availability")) in IN_STOCK}


def stats() -> dict:
    ensure_loaded()
    return {"inventory_rows": len(_inv), "shelf_rows": len(_shelf), "shelf_ts": _state["shelf_ts"],
            "event_ts": _state["event_ts"], "refreshed_ago_s": round(time.time() - _state["refreshed"], 1)}
