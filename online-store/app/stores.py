"""Omnichannel store availability (CONTRACT_EN v14).

GET /api/products/{id}/stores?lat=&lng= ->
  {product_id, channel, online_available, stores:[{store_id, name, short_name, address, phone, lat, lng, open_now, hours,
   quantity, availability, pickup, shelf_label?, distance_m?}], source: "retail_local_inventory" | "bigquery" | "local"}

Sources (first that works):
  1. Retail Product.local_inventories (placeId = store_id; attributes quantity / availability / shelf_id / slot_ids / tier)
     - only when the live Retail engine is used (not in DATA_FILE mode) and RETAIL_LOCAL_INVENTORY != 0;
  2. BigQuery `${PROJECT_ID}.${BQ_DATASET}.store_inventory` (+ latest `shelf_locations`), live mode only;
  3. local files (STORE_INVENTORY_FILE / SHELF_LOCATIONS_FILE, else `<site>/data/` or `<repo>/store-navi/data/`).
Store master: BigQuery `cymbal.stores` (live mode) else STORES_FILE / `<site>/data/stores.json` /
`<repo>/store-navi/data/stores.json`.

The local inventory index also feeds the local search engine's "Pick up in store" facet (`pickupInStore`).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("cymbal-site.stores")

APP_DIR = Path(__file__).resolve().parent
SITE_DIR = APP_DIR.parent
REPO_DIR = SITE_DIR.parent
JST = ZoneInfo("Asia/Tokyo")
CACHE_TTL = float(os.environ.get("STORE_CACHE_TTL_SEC", "600") or 600)
LI_TTL = float(os.environ.get("LOCAL_INVENTORY_TTL_SEC", "20") or 20)


def project_id() -> str:
    return os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""


def project_number() -> str:
    return os.environ.get("PROJECT_NUMBER") or ""


def bq_dataset() -> str:
    return os.environ.get("BQ_DATASET", "cymbal")


def live_mode() -> bool:
    """Cloud sources only when the site runs against live Retail (no DATA_FILE / SEARCH_MODE=local / STORE_SOURCE=local)."""
    if (os.environ.get("STORE_SOURCE") or "").lower() == "local":
        return False
    if os.environ.get("DATA_FILE") or os.environ.get("SEARCH_MODE") == "local":
        return False
    return bool(project_id())


def _first_existing(env: str, names: list[str]) -> str:
    if os.environ.get(env):
        return os.environ[env]
    for d in (SITE_DIR / "data", REPO_DIR / "seed" / "catalog", REPO_DIR / "store-navi" / "data"):
        for n in names:
            if (d / n).exists():
                return str(d / n)
    return ""


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------
def _hm(s: str) -> int | None:
    try:
        h, m = str(s).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _hours_dict(store: dict) -> dict:
    h = store.get("hours") or {}
    if isinstance(h, str):
        try:
            h = json.loads(h)
        except json.JSONDecodeError:
            h = {}
    return h if isinstance(h, dict) else {}


def hours_label(store: dict) -> str:
    h = _hours_dict(store)
    o, c = h.get("open"), h.get("close")
    if not (o and c):
        return ""

    def fmt(x: str) -> str:
        m = _hm(x)
        if m is None:
            return str(x)
        hh, mm = divmod(m, 60)
        suffix = "AM" if hh % 24 < 12 else "PM"
        return f"{(hh % 12) or 12}:{mm:02d} {suffix}"

    return f"{fmt(o)} – {fmt(c)}"


def open_now(store: dict, now: dt.datetime | None = None) -> bool:
    h = _hours_dict(store)
    o, c = _hm(h.get("open", "")), _hm(h.get("close", ""))
    if o is None or c is None:
        return True
    t = (now or dt.datetime.now(JST)).astimezone(JST)
    m = t.hour * 60 + t.minute
    return o <= m < c if o < c else (m >= o or m < c)


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(round(2 * r * math.asin(math.sqrt(a))))


def shelf_label_of(rec: dict) -> str:
    """'Aisle M18 • Shelf 3' from {shelf_id|gondola_id|fixture_id, tier}; the pharmacy counter for RX."""
    shelf = rec.get("shelf_id") or rec.get("gondola_id") or rec.get("fixture_id") or ""
    if isinstance(shelf, list):
        shelf = shelf[0] if shelf else ""
    if not shelf:
        slots = rec.get("slot_ids") or []
        if isinstance(slots, str):
            slots = [slots]
        shelf = str(slots[0]).split("-")[0] if slots else ""
    if not shelf:
        return ""
    if str(shelf).upper() == "RX":
        return "Pharmacy counter"
    tier = rec.get("tier")
    try:
        tier = int(float(tier)) if tier not in (None, "", []) else None
    except (TypeError, ValueError):
        tier = None
    return f"Aisle {shelf}" + (f" • Shelf {tier}" if tier else "")


# ---------------------------------------------------------------------------
# BigQuery (lazy, optional dependency)
# ---------------------------------------------------------------------------
_bq = None


def _bq_client():
    global _bq
    if _bq is None:
        from google.cloud import bigquery  # lazy

        _bq = bigquery.Client(project=project_id())
    return _bq


def _bq_query(sql: str, params: list | None = None, timeout: float = 8.0) -> list[dict]:
    from google.cloud import bigquery

    job = _bq_client().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params or []))
    return [dict(r.items()) for r in job.result(timeout=timeout)]


def _table(name: str) -> str:
    return f"`{project_id()}.{bq_dataset()}.{name}`"


# ---------------------------------------------------------------------------
# directory
# ---------------------------------------------------------------------------
class StoreDirectory:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._stores: tuple[float, list[dict]] | None = None
        self._local_inv: dict[str, dict[str, dict]] | None = None   # product_id -> store_id -> rec
        self._li_cache: dict[str, tuple[float, dict[str, dict] | None]] = {}
        self._retail = None

    # ---- store master ----
    def stores(self) -> list[dict]:
        hit = self._stores
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        rows: list[dict] = []
        if live_mode():
            try:
                rows = _bq_query(f"SELECT * FROM {_table('stores')} ORDER BY store_id")
            except Exception as e:  # noqa: BLE001
                log.warning("BigQuery stores unavailable (%s) -> local file", str(e)[:160])
                rows = []
        if not rows:
            path = _first_existing("STORES_FILE", ["stores_en.json", "stores.json"])
            if path:
                try:
                    rows = json.loads(Path(path).read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    log.warning("stores file %s unreadable: %s", path, e)
        out = []
        for r in rows or []:
            r = dict(r)
            r["hours"] = _hours_dict(r)
            out.append(r)
        self._stores = (time.time(), out)
        return out

    def store(self, store_id: str) -> dict | None:
        return next((s for s in self.stores() if s.get("store_id") == store_id), None)

    def short_name(self, store_id: str) -> str:
        s = self.store(store_id)
        if not s:
            return store_id
        return s.get("short_name") or str(s.get("name") or store_id).replace("Cymbal Retail ", "")

    # ---- local inventory index ----
    def local_inventory(self) -> dict[str, dict[str, dict]]:
        if self._local_inv is not None:
            return self._local_inv
        with self.lock:
            if self._local_inv is not None:
                return self._local_inv
            idx: dict[str, dict[str, dict]] = {}
            path = _first_existing("STORE_INVENTORY_FILE", ["store_inventory_en.jsonl", "store_inventory.jsonl"])
            if path:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if r.get("product_id") and r.get("store_id"):
                            idx.setdefault(str(r["product_id"]), {})[str(r["store_id"])] = {
                                "quantity": int(r.get("quantity") or 0),
                                "availability": r.get("availability") or "",
                            }
            shelf = _first_existing("SHELF_LOCATIONS_FILE", ["shelf_locations.jsonl"])
            if shelf and idx:
                latest: dict[tuple[str, str], tuple[str, dict]] = {}
                with open(shelf, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        k = (str(r.get("product_id")), str(r.get("store_id")))
                        if k[0] in idx and (k not in latest or str(r.get("registered_at", "")) >= latest[k][0]):
                            latest[k] = (str(r.get("registered_at", "")), r)
                for (pid, sid), (_, r) in latest.items():
                    if sid in idx[pid]:
                        idx[pid][sid]["shelf_label"] = shelf_label_of(r)
            log.info("local store inventory: %d products (%s)", len(idx), path)
            self._local_inv = idx
            return idx

    def pickup_store_ids(self, product_id: str) -> list[str]:
        recs = self.local_inventory().get(product_id) or {}
        return sorted(s for s, r in recs.items() if r.get("quantity", 0) > 0)

    # ---- live sources ----
    def _retail_li(self, product_id: str) -> dict[str, dict] | None:
        if (os.environ.get("RETAIL_LOCAL_INVENTORY", "1") or "1") in ("0", "false", "no") or not live_mode():
            return None
        hit = self._li_cache.get(product_id)
        if hit and time.time() - hit[0] < LI_TTL:
            return hit[1]
        out: dict[str, dict] | None = None
        try:
            if self._retail is None:
                from google.cloud import retail_v2

                self._retail = retail_v2.ProductServiceClient()
            proj = project_number() or project_id()
            name = (f"projects/{proj}/locations/global/catalogs/{os.environ.get('CATALOG', 'default_catalog')}/"
                    f"branches/{os.environ.get('BRANCH', '0')}/products/{product_id}")
            prod = self._retail.get_product(name=name, timeout=5)
            out = {}
            for li in prod.local_inventories or []:
                rec: dict[str, Any] = {}
                for k, v in (li.attributes or {}).items():
                    if v.numbers:
                        rec[k] = list(v.numbers)[0] if len(v.numbers) == 1 else list(v.numbers)
                    elif v.text:
                        rec[k] = list(v.text)[0] if len(v.text) == 1 else list(v.text)
                q = rec.get("quantity")
                pickup = "pickup-in-store" in list(li.fulfillment_types or [])
                rec["quantity"] = int(float(q)) if q not in (None, "") else (1 if pickup else 0)
                rec["availability"] = str(rec.get("availability") or ("IN_STOCK" if rec["quantity"] > 0 else "OUT_OF_STOCK"))
                rec["pickup"] = pickup or rec["quantity"] > 0
                rec["shelf_label"] = shelf_label_of(rec)
                out[li.place_id] = rec
            if not out:
                out = None  # LocalInventory not pushed yet -> BigQuery
        except Exception as e:  # noqa: BLE001
            log.info("Retail local inventory unavailable for %s (%s)", product_id, str(e)[:160])
            out = None
        if len(self._li_cache) > 5000:
            self._li_cache.clear()
        self._li_cache[product_id] = (time.time(), out)
        return out

    def _bq_inventory(self, product_id: str) -> dict[str, dict] | None:
        if not live_mode():
            return None
        try:
            from google.cloud import bigquery

            p = [bigquery.ScalarQueryParameter("pid", "STRING", product_id)]
            rows = _bq_query(f"SELECT store_id, quantity, availability FROM {_table('store_inventory')} "
                             f"WHERE product_id = @pid", p)
            out = {str(r["store_id"]): {"quantity": int(r.get("quantity") or 0),
                                        "availability": r.get("availability") or ""} for r in rows}
            try:
                shelves = _bq_query(
                    f"SELECT store_id, gondola_id, tier FROM {_table('shelf_locations')} WHERE product_id = @pid "
                    f"QUALIFY ROW_NUMBER() OVER (PARTITION BY store_id ORDER BY registered_at DESC) = 1", p)
                for r in shelves:
                    if str(r["store_id"]) in out:
                        out[str(r["store_id"])]["shelf_label"] = shelf_label_of(r)
            except Exception as e:  # noqa: BLE001
                log.info("shelf_locations unavailable: %s", str(e)[:120])
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("BigQuery store_inventory unavailable (%s) -> local", str(e)[:160])
            return None

    # ---- API ----
    def availability(self, product: dict | None, product_id: str, lat: float | None = None,
                     lng: float | None = None) -> dict:
        channel = ((product or {}).get("channel") or "omni").lower()
        inv, source = self._retail_li(product_id), "retail_local_inventory"
        if inv is None:
            inv, source = self._bq_inventory(product_id), "bigquery"
        if inv is None:
            inv, source = dict(self.local_inventory().get(product_id) or {}), "local"
        if channel == "online_only":
            inv = {}
        rows = []
        for s in self.stores():
            sid = s.get("store_id")
            rec = inv.get(sid)
            if rec is None:
                continue  # not carried at this store
            qty = int(rec.get("quantity") or 0)
            avail = rec.get("availability") or ("IN_STOCK" if qty > 0 else "OUT_OF_STOCK")
            if qty <= 0:
                avail = "OUT_OF_STOCK"
            row = {
                "store_id": sid,
                "name": s.get("name") or sid,
                "short_name": s.get("short_name") or str(s.get("name") or sid).replace("Cymbal Retail ", ""),
                "address": s.get("address") or "",
                "phone": s.get("phone") or "",
                "lat": s.get("lat"),
                "lng": s.get("lng"),
                "open_now": open_now(s),
                "hours": hours_label(s),
                "quantity": qty,
                "availability": avail,
                "pickup": bool(rec.get("pickup", qty > 0)) and qty > 0,
            }
            if rec.get("shelf_label"):
                row["shelf_label"] = rec["shelf_label"]
            if lat is not None and lng is not None and s.get("lat") is not None and s.get("lng") is not None:
                row["distance_m"] = distance_m(float(lat), float(lng), float(s["lat"]), float(s["lng"]))
            rows.append(row)
        if rows and "distance_m" in rows[0]:
            rows.sort(key=lambda r: (r["quantity"] <= 0, r.get("distance_m", 0)))
        else:
            rows.sort(key=lambda r: (r["quantity"] <= 0, r["store_id"]))
        return {"product_id": product_id, "channel": channel, "online_available": channel != "store_only",
                "stores": rows, "source": source}


_dir: StoreDirectory | None = None


def directory() -> StoreDirectory:
    global _dir
    if _dir is None:
        _dir = StoreDirectory()
    return _dir


def reset() -> None:
    global _dir
    _dir = None
