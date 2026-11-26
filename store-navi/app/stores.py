"""Store master: haversine distance, open-now (Asia/Tokyo), in-store detection (within radius_m)."""
from __future__ import annotations

import datetime as dt
import math
import threading
from typing import Any
from zoneinfo import ZoneInfo

from . import repository

JST = ZoneInfo("Asia/Tokyo")
_lock = threading.Lock()
_stores: list[dict] | None = None


def load_stores(force: bool = False) -> list[dict]:
    global _stores
    with _lock:
        if _stores is None or force:
            _stores = repository.load_stores()
        return _stores


def get_store(store_id: str) -> dict | None:
    return next((s for s in load_stores() if s.get("store_id") == store_id), None)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _within(hours: dict | None, now: dt.datetime) -> bool:
    if not hours:
        return False
    o, c = _minutes(hours.get("open", "")), _minutes(hours.get("close", ""))
    if o is None or c is None:
        return False
    t = now.hour * 60 + now.minute
    if c <= o:  # past midnight (e.g. 10:00-02:00) or 24h
        return t >= o or t < c
    return o <= t < c


_WD = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def open_now(store: dict, now: dt.datetime | None = None) -> bool:
    now = (now or dt.datetime.now(JST)).astimezone(JST)
    return _within(store.get("hours"), now)


def pharmacy_open_now(store: dict, now: dt.datetime | None = None) -> bool:
    ph = store.get("pharmacy")
    if not ph:
        return False
    now = (now or dt.datetime.now(JST)).astimezone(JST)
    closed = str(ph.get("closed") or "")
    cl = closed.lower()
    if _WD[now.weekday()].lower() in cl or ("weekend" in cl and now.weekday() >= 5):
        return False
    return _within(ph, now)


def store_distance_m(a: dict, b: dict) -> int:
    return int(round(haversine_m(float(a["lat"]), float(a["lng"]), float(b["lat"]), float(b["lng"]))))


def with_status(store: dict, lat: float | None = None, lng: float | None = None,
                now: dt.datetime | None = None) -> dict[str, Any]:
    d = None
    if lat is not None and lng is not None:
        d = int(round(haversine_m(lat, lng, float(store["lat"]), float(store["lng"]))))
    radius = float(store.get("radius_m") or 150)
    return {**store, "distance_m": d, "open_now": open_now(store, now),
            "pharmacy_open_now": pharmacy_open_now(store, now),
            "in_store": d is not None and d <= radius}


def list_stores(lat: float | None = None, lng: float | None = None, now: dt.datetime | None = None) -> dict:
    rows = [with_status(s, lat, lng, now) for s in load_stores()]
    if lat is not None and lng is not None:
        rows.sort(key=lambda s: s["distance_m"])
        nearest = rows[0]["store_id"] if rows else None
    else:
        nearest = None
    return {"stores": rows, "nearest_id": nearest}
