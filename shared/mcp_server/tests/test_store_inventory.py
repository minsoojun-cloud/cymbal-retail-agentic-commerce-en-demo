"""Unit tests for check_store_inventory (Retail local inventory → BigQuery → local file fallbacks). No network.

Run:
  cd shared/mcp_server && uv run --no-project --with-requirements requirements.txt --with pytest --with httpx pytest -q tests
"""
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
STORES = [
    {"store_id": "S01", "name": "Cymbal Retail Kawasaki Station", "address": "1-1 Ekimae-honcho, Kawasaki-ku, Kawasaki, Kanagawa",
     "hours": {"open": "09:00", "close": "22:00"}},
    {"store_id": "S02", "name": "Cymbal Retail Musashi-Kosugi", "address": "2-2 Kosugi-machi, Nakahara-ku, Kawasaki, Kanagawa",
     "hours": json.dumps({"open": "08:00", "close": "23:00"})},  # BQ may return JSON strings
    {"store_id": "S03", "name": "Cymbal Retail Shibuya", "address": "3-3 Dogenzaka, Shibuya-ku, Tokyo", "hours": "10:00-21:00"},
]


def attr(text=None, numbers=None):
    return NS(text=text or [], numbers=numbers or [])


def li(place, qty, shelf="", slots=(), tier=None):
    a = {"quantity": attr(numbers=[float(qty)]), "availability": attr(text=["IN_STOCK" if qty > 0 else "OUT_OF_STOCK"])}
    if shelf:
        a["shelf_id"] = attr(text=[shelf])
    if slots:
        a["slot_ids"] = attr(text=list(slots))
    if tier:
        a["tier"] = attr(numbers=[float(tier)])
    return NS(place_id=place, attributes=a, fulfillment_types=["pickup-in-store"] if qty > 0 else [])


PRODUCTS = {
    "B1": NS(title="Cymbal Bakery Fresh Croissant 2 pcs", attributes={"channel": attr(text=["store_only"])},
             local_inventories=[li("S02", 12, "N03", ["N03-201", "N03-202"], 2), li("S01", 0)]),
    "O1": NS(title="Cymbal Basic Silken Tofu 300g", attributes={"channel": attr(text=["omni"])},
             local_inventories=[li("S01", 2, "K05", ["K05-301"], 3), li("S02", 30), li("S03", 8)]),
    "W1": NS(title="Cymbal Cat Food Bulk 10kg", attributes={"channel": attr(text=["online_only"])}, local_inventories=[]),
    "E1": NS(title="Cymbal Select Sesame Dressing", attributes={"channel": attr(text=["omni"])}, local_inventories=[]),
}


class FakePC:
    def __init__(self):
        self.calls = []

    def get_product(self, name, timeout=None):
        pid = name.rsplit("/", 1)[-1]
        self.calls.append(pid)
        if pid not in PRODUCTS:
            raise RuntimeError("404 NOT_FOUND")
        return PRODUCTS[pid]


@pytest.fixture
def env(monkeypatch):
    pc = FakePC()
    monkeypatch.setattr(server, "_product_client", lambda: pc)
    monkeypatch.setattr(server, "_stores_cache", {"at": 0.0, "stores": None, "source": ""})
    queries = []

    def fake_bq(sql, params):
        queries.append(sql)
        if ".stores`" in sql:
            return [dict(s) for s in STORES]
        if "store_inventory" in sql:
            ids = next(p.values for p in params if p.name == "ids")
            rows = [{"store_id": "S03", "product_id": "E1", "quantity": 5, "availability": "IN_STOCK",
                     "fixture_id": "H07", "slot_ids": ["H07-402"], "tier": 4}]
            return [r for r in rows if r["product_id"] in ids]
        if ".products`" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(server, "_bq_query", fake_bq)
    return pc, queries


NOON = dt.datetime(2026, 11, 25, 12, 0, tzinfo=JST)


def by_id(res):
    return {i["product_id"]: i for i in res["items"]}


def test_shape_and_retail_source(env):
    res = server.check_store_inventory_sync(["O1", "B1"], now=NOON)
    items = by_id(res)
    o1 = items["O1"]
    assert set(o1) >= {"product_id", "title", "channel", "stores", "online_available"}
    assert o1["channel"] == "omni" and o1["online_available"] is True and o1["title"].startswith("Cymbal Basic")
    assert [s["store_id"] for s in o1["stores"]] == ["S01", "S02", "S03"]
    s01 = o1["stores"][0]
    assert set(s01) >= {"store_id", "name", "address", "open_now", "hours", "quantity", "availability", "pickup"}
    assert s01["quantity"] == 2 and s01["low_stock"] and s01["pickup"] is True and s01["availability"] == "IN_STOCK"
    assert s01["shelf_label"] == "Aisle K05, shelf 3 from the bottom (slot K05-301)"
    assert s01["hours"] == "9:00 AM \u2013 10:00 PM" and s01["open_now"] is True and s01["carried"] is True
    assert o1["stores"][2]["hours"] == "10:00 AM \u2013 9:00 PM"
    assert res["_source"] == {"inventory": "retail_local_inventory", "stores": "bigquery"}


def test_store_only_not_online_and_sorted_in_stock_first(env):
    b1 = by_id(server.check_store_inventory_sync(["B1"], now=NOON))["B1"]
    assert b1["channel"] == "store_only" and b1["online_available"] is False and b1["in_stock_store_count"] == 1
    assert [s["store_id"] for s in b1["stores"]] == ["S02", "S01", "S03"]
    assert b1["stores"][1]["availability"] == "OUT_OF_STOCK" and b1["stores"][1]["pickup"] is False
    assert b1["stores"][1]["carried"] is True
    s3 = b1["stores"][2]
    assert s3["availability"] == "OUT_OF_STOCK" and s3["carried"] is False and "shelf_label" not in s3


def test_online_only_has_no_store_stock(env):
    w1 = by_id(server.check_store_inventory_sync(["W1"], now=NOON))["W1"]
    assert w1["online_available"] is True and w1["in_stock_store_count"] == 0
    assert all(s["availability"] == "OUT_OF_STOCK" and not s["carried"] and not s["pickup"] for s in w1["stores"])


def test_bigquery_fallback_when_no_local_inventories(env):
    _, queries = env
    res = server.check_store_inventory_sync(["E1"], now=NOON)
    e1 = by_id(res)["E1"]
    s03 = next(s for s in e1["stores"] if s["store_id"] == "S03")
    assert s03["quantity"] == 5 and s03["shelf_label"].endswith("(slot H07-402)")
    assert res["_source"]["inventory"] == "bigquery" and any("shelf_locations" in q for q in queries)


def test_store_filter_and_unknown_store(env):
    o1 = by_id(server.check_store_inventory_sync(["O1"], store_id="s03", now=NOON))["O1"]
    assert [s["store_id"] for s in o1["stores"]] == ["S03"]
    assert "error" in server.check_store_inventory_sync(["O1"], store_id="S99")
    assert "error" in server.check_store_inventory_sync([])


@pytest.mark.parametrize("hour,minute,exp", [(8, 59, False), (9, 0, True), (21, 59, True), (22, 0, False)])
def test_open_now_asia_tokyo(hour, minute, exp):
    assert server.is_open_now(STORES[0], dt.datetime(2026, 11, 25, hour, minute, tzinfo=JST)) is exp


def test_open_now_converts_from_utc():
    # 2026-11-25 01:00 UTC = 10:00 JST → S03 (10:00-21:00) open; 12:30 UTC = 21:30 JST → closed
    assert server.is_open_now(STORES[2], dt.datetime(2026, 11, 25, 1, 0, tzinfo=dt.timezone.utc)) is True
    assert server.is_open_now(STORES[2], dt.datetime(2026, 11, 25, 12, 30, tzinfo=dt.timezone.utc)) is False


def test_everything_down_uses_local_files(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(server, "_product_client", boom)
    monkeypatch.setattr(server, "_bq_query", boom)
    monkeypatch.setattr(server, "_stores_cache", {"at": 0.0, "stores": None, "source": ""})
    monkeypatch.setattr(server, "_catalog_index", {"items": None})
    (tmp_path / "stores.json").write_text(json.dumps(STORES))
    (tmp_path / "store_inventory.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"store_id": "S01", "product_id": "X1", "quantity": 4, "availability": "IN_STOCK"},
        {"store_id": "S02", "product_id": "X1", "quantity": 0, "availability": "OUT_OF_STOCK"}]))
    cat = tmp_path / "cat.jsonl"
    cat.write_text(json.dumps({"id": "X1", "title": "Cymbal Sushi Platter 8 pcs",
                               "attributes": [{"key": "channel", "value": {"text": ["store_only"]}}]}))
    monkeypatch.setattr(server, "DATA_DIRS", [tmp_path])
    monkeypatch.setattr(server, "STORE_FILES", ["stores.json"])
    monkeypatch.setattr(server, "INVENTORY_FILES", ["store_inventory.jsonl"])
    monkeypatch.setattr(server, "DINNER_CATALOG_FILE", str(cat))
    res = server.check_store_inventory_sync("X1", now=NOON)
    x1 = res["items"][0]
    assert x1["title"] == "Cymbal Sushi Platter 8 pcs" and x1["channel"] == "store_only" and not x1["online_available"]
    assert x1["stores"][0]["store_id"] == "S01" and x1["stores"][0]["quantity"] == 4
    assert res["_source"] == {"inventory": "file", "stores": "file"}


def test_mcp_tool_and_schema(env):
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    schema = tools["check_store_inventory"].inputSchema
    assert schema["required"] == ["product_ids"] and schema["properties"]["product_ids"]["type"] == "array"
    assert schema["properties"]["store_id"] == {"type": "string"}
    out = asyncio.run(server.mcp.call_tool("check_store_inventory", {"product_ids": "O1,B1", "store_id": ""}))
    blocks = out[0] if isinstance(out, tuple) else out
    res = json.loads(blocks[0].text)
    assert [i["product_id"] for i in res["items"]] == ["O1", "B1"]


def test_unknown_product_reports_error(env):
    res = server.check_store_inventory_sync(["NOPE"], now=NOON)
    assert res["items"][0]["error"] == "product not found"
