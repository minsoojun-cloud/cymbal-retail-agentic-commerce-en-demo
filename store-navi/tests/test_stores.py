"""Store master: haversine, nearest store, in-store radius, open-now / pharmacy hours (Asia/Tokyo)."""
from __future__ import annotations

import datetime as dt

from app import stores

JST = stores.JST
KAWASAKI_IN = (35.5314, 139.6978)
TOKYO_STATION = (35.6812, 139.7671)


def test_haversine_known_distance():
    d = stores.haversine_m(35.5313, 139.6977, 35.6595, 139.7005)   # Kawasaki Station → Shibuya ≈ 14 km
    assert 13500 < d < 15000


def test_nearest_and_in_store():
    r = stores.list_stores(*KAWASAKI_IN)
    assert r["nearest_id"] == "S01"
    s01 = r["stores"][0]
    assert s01["store_id"] == "S01" and s01["in_store"] is True and s01["distance_m"] < 50
    assert [s["distance_m"] for s in r["stores"]] == sorted(s["distance_m"] for s in r["stores"])
    assert all(not s["in_store"] for s in r["stores"][1:])


def test_far_away_not_in_store():
    r = stores.list_stores(*TOKYO_STATION)
    assert not any(s["in_store"] for s in r["stores"])
    assert r["stores"][0]["distance_m"] > 5000


def test_no_coordinates():
    r = stores.list_stores()
    assert r["nearest_id"] is None and all(s["distance_m"] is None for s in r["stores"])


def test_open_now_hours():
    s = {"hours": {"open": "09:00", "close": "22:00"}}
    assert stores.open_now(s, dt.datetime(2026, 11, 12, 10, 0, tzinfo=JST))
    assert not stores.open_now(s, dt.datetime(2026, 11, 12, 22, 30, tzinfo=JST))
    assert not stores.open_now(s, dt.datetime(2026, 11, 12, 8, 59, tzinfo=JST))
    late = {"hours": {"open": "10:00", "close": "02:00"}}
    assert stores.open_now(late, dt.datetime(2026, 11, 12, 1, 0, tzinfo=JST))
    # UTC input is converted to JST (01:00 UTC = 10:00 JST)
    assert stores.open_now(s, dt.datetime(2026, 11, 12, 1, 0, tzinfo=dt.timezone.utc))


def test_pharmacy_closed_on_sunday():
    s = {"pharmacy": {"open": "09:00", "close": "19:00", "closed": "Sun & holidays"}}
    assert stores.pharmacy_open_now(s, dt.datetime(2026, 11, 12, 10, 0, tzinfo=JST))      # Thursday
    assert not stores.pharmacy_open_now(s, dt.datetime(2026, 11, 15, 10, 0, tzinfo=JST))  # Sunday
    assert not stores.pharmacy_open_now({"pharmacy": None})
    wk = {"pharmacy": {"open": "10:00", "close": "19:00", "closed": "Weekends & holidays"}}
    assert not stores.pharmacy_open_now(wk, dt.datetime(2026, 11, 14, 11, 0, tzinfo=JST))       # Saturday
    assert stores.pharmacy_open_now(wk, dt.datetime(2026, 11, 13, 11, 0, tzinfo=JST))           # Friday


def test_store_records_have_contract_fields():
    for s in stores.load_stores():
        for k in ("store_id", "name", "short_name", "lat", "lng", "address", "phone", "hours", "radius_m"):
            assert k in s, k
