"""Unit tests for cymbal-commerce-mcp (Retail / BigQuery clients are mocked).

Run:
  cd shared/mcp_server && uv run --no-project --with-requirements requirements.txt \
      --with pytest --with httpx pytest -q tests
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


# ── fakes ────────────────────────────────────────────────────────────────────
def attr(text=None, numbers=None):
    return SimpleNamespace(text=text or [], numbers=numbers or [])


def make_product(pid, full=True, price=322.0, original=322.0, on_sale="false"):
    if not full:
        return SimpleNamespace(name=server.product_name(pid), title="", images=[], uri="", attributes={},
                               categories=[], price_info=None)
    return SimpleNamespace(
        name=server.product_name(pid),
        title=f"Domestic Carrots {pid}",
        categories=["Produce > Root Vegetables > Carrots", "Themes > Autumn"],
        price_info=SimpleNamespace(price=price, original_price=original),
        attributes={
            "on_sale": attr(text=[on_sale]),
            "discount_rate": attr(numbers=[10.0 if on_sale == "true" else 0.0]),
            "price_tax_excluded": attr(numbers=[298.0]),
            "promo_text": attr(text=["10% off when you buy 2"]) if on_sale == "true" else attr(),
            "size": attr(text=["350g"]),
            "origin": attr(text=["Hokkaido"]),
            "allergens": attr(text=["wheat", "milk"]),
        },
        images=[SimpleNamespace(uri=f"https://img/{pid}/1.jpg"), SimpleNamespace(uri=f"https://img/{pid}/2.jpg")],
        uri=f"https://site.example/?product={pid}",
        description="Extra-sweet carrots",
        brands=[],
        availability=SimpleNamespace(name="IN_STOCK"),
        rating=SimpleNamespace(average_rating=4.5, rating_count=12),
    )


class FakePager(list):
    def __init__(self, items, total):
        super().__init__(items)
        self.total_size = total


class FakeSearchClient:
    def __init__(self, n=5, full=True, fail_first_with=None):
        self.n, self.full, self.fail_first_with = n, full, fail_first_with
        self.requests = []

    def search(self, request, timeout=None):
        self.requests.append(request)
        if self.fail_first_with and len(self.requests) == 1:
            raise self.fail_first_with
        items = [SimpleNamespace(id=f"P{i:03d}", product=make_product(f"P{i:03d}", self.full)) for i in range(self.n)]
        return FakePager(items, total=self.n)


class FakeProductClient:
    def __init__(self):
        self.calls = []

    def get_product(self, name, timeout=None):
        self.calls.append(name)
        pid = name.rsplit("/", 1)[-1]
        return make_product(pid, full=True, price=270.0, original=300.0, on_sale="true")


@pytest.fixture(autouse=True)
def no_bq_fallback(monkeypatch):
    monkeypatch.setattr(server, "SEARCH_BQ_FALLBACK", False)


@pytest.fixture
def fakes(monkeypatch):
    sc, pc = FakeSearchClient(), FakeProductClient()
    monkeypatch.setattr(server, "_search_client", lambda: sc)
    monkeypatch.setattr(server, "_product_client", lambda: pc)
    return sc, pc


def run(coro):
    return asyncio.run(coro)


# ── page size clamp ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("given,expected", [(500, 100), (100, 100), (101, 100), (0, 1), (-5, 1), (None, 20), ("abc", 20), ("30", 30)])
def test_clamp_page_size(given, expected):
    assert server.clamp_page_size(given) == expected


def test_search_clamps_to_100(fakes):
    sc, _ = fakes
    sc.n = 150  # backend returns more than asked → still capped
    res = json.loads(run(server.search_products(query="carrots", page_size=500)))
    assert sc.requests[0].page_size == 100
    assert len(res["productDetails"]) == 100


# ── filter composition ───────────────────────────────────────────────────────
def test_filter_default_in_stock_only():
    f, cap = server.build_filter(None, "carrots")
    assert f == 'availability: ANY("IN_STOCK")' and cap is None


def test_filter_user_and_in_stock():
    f, cap = server.build_filter('attributes.on_sale: ANY("true")', "deals")
    assert f == '(attributes.on_sale: ANY("true")) AND (availability: ANY("IN_STOCK"))'
    assert cap is None


@pytest.mark.parametrize("q,cap", [("carrots under ¥500", 500), ("salmon within 1,000 yen", 1000), ("¥300 or less", 300), ("snacks below 300 yen", 300),
                                  ("up to JPY 2000 frying pan", 2000), ("carrots", None), ("2 for 1", None)])
def test_price_cap_helper(q, cap):
    f, got = server.build_filter(None, q)
    assert got == cap
    if cap:
        assert f == f'(price: IN(*, {float(cap)}i)) AND (availability: ANY("IN_STOCK"))'


def test_user_filter_wins_over_price_helper():
    f, cap = server.build_filter('categories: ANY("Produce")', "under ¥500")
    assert cap is None and "price" not in f


def test_search_passes_filter_and_visitor(fakes):
    sc, _ = fakes
    run(server.search_products(query="carrots under ¥500", page_size=5, visitor_id="v-123"))
    req = sc.requests[0]
    assert "price: IN(*, 500.0i)" in req.filter and 'availability: ANY("IN_STOCK")' in req.filter
    assert req.visitor_id == "v-123"
    assert req.placement.endswith("/catalogs/default_catalog/servingConfigs/default_search")
    assert req.branch.endswith("/branches/0")


def test_price_cap_post_filter(fakes):
    sc, _ = fakes
    res = json.loads(run(server.search_products(query="carrots under ¥300")))
    assert res["appliedPriceCap"] == 300
    assert all(p["price"] <= 300 for p in res["productDetails"])


def test_invalid_filter_retry_with_warning(fakes):
    sc, _ = fakes
    from google.api_core import exceptions as gexc

    sc.fail_first_with = gexc.InvalidArgument("Unsupported field attributes.on_sale")
    res = json.loads(run(server.search_products(query="deals", filter='attributes.on_sale: ANY("true")')))
    assert "warning" in res and len(sc.requests) == 2
    assert sc.requests[1].filter == 'availability: ANY("IN_STOCK")'
    assert len(res["productDetails"]) == 5


# ── response shape / hydration ───────────────────────────────────────────────
KEYS = {"productId", "title", "subtitle", "price", "priceTaxExcluded", "originalPrice", "onSale",
        "discountRate", "promoText", "size", "allergens", "uri", "imageUris", "brand", "topCategory", "labels",
        "origin", "material", "color", "capacity", "rating", "channel"}


def test_response_shape(fakes):
    sc, pc = fakes
    res = json.loads(run(server.search_products(query="carrots", page_size=3)))
    assert set(res) >= {"productDetails", "totalSize"}
    p = res["productDetails"][0]
    assert set(p) == KEYS
    assert p["subtitle"] == "Carrots" and p["price"] == 322 and p["priceTaxExcluded"] == 298
    assert p["onSale"] is False and p["size"] == "350g" and len(p["imageUris"]) == 2
    assert p["allergens"] == "wheat / milk" and p["channel"] == "omni"
    assert pc.calls == []  # full products → no hydration


def test_hydration_when_search_result_is_sparse(fakes):
    sc, pc = fakes
    sc.full = False
    res = json.loads(run(server.search_products(query="carrots", page_size=4)))
    assert len(pc.calls) == 4
    p = res["productDetails"][0]
    assert p["title"] and p["onSale"] is True and p["discountRate"] == 10.0
    assert p["price"] == 270 and p["originalPrice"] == 300 and p["promoText"] == "10% off when you buy 2"


def test_get_product_details(fakes):
    d = json.loads(run(server.get_product_details("P001")))
    assert d["description"] and d["attributes"]["origin"] == "Hokkaido"
    assert d["rating"] == {"average": 4.5, "count": 12} and d["availability"] == "IN_STOCK"


# ── BigQuery tools with fallback ─────────────────────────────────────────────
@pytest.fixture
def bq_down(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("bq unavailable")

    monkeypatch.setattr(server, "_bq_client", boom)
    rows = []
    for i, d in enumerate(["2025-10-04", "2025-10-11", "2026-03-07", "2026-10-03"]):
        rows.append({"order_id": f"O{i}", "customer_id": "CR-0001", "order_date": d, "product_id": "P1",
                     "product_title": "Autumn Salmon", "category": "Seafood", "quantity": 1, "unit_price_tax_included": 500, "amount": 500})
    (tmp_path / "purchase_history.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    (tmp_path / "personas.json").write_text(json.dumps([{"customer_id": "CR-0001", "name": "Sakura Tanaka"}], ensure_ascii=False))
    monkeypatch.setattr(server, "DATA_DIRS", [tmp_path])


def test_profile_fallback(bq_down):
    prof = json.loads(run(server.get_customer_profile("CR-0001")))
    assert prof["name"] == "Sakura Tanaka" and prof["_source"] == "bundled"
    assert "error" in json.loads(run(server.get_customer_profile("NOPE")))


def test_history_fallback_month_year(bq_down):
    h = json.loads(run(server.get_purchase_history("CR-0001", month=10)))
    assert h["orderCount"] == 3 and h["_source"] == "bundled"
    h = json.loads(run(server.get_purchase_history("CR-0001", month="2025-10")))
    assert h["orderCount"] == 2 and h["purchases"][0]["order_date"] == "2025-10-11"
    assert h["topProducts"][0]["product_title"] == "Autumn Salmon"


def test_history_bq_query(monkeypatch):
    captured = {}

    def fake_query(sql, params):
        captured["sql"], captured["params"] = sql, params
        return [{"order_id": "O1", "customer_id": "CR-0001", "order_date": "2025-10-04", "product_id": "P1",
                 "product_title": "Pacific Saury", "category": "Seafood", "quantity": 2, "unit_price_tax_included": 300, "amount": 600}]

    monkeypatch.setattr(server, "_bq_query", fake_query)
    h = json.loads(run(server.get_purchase_history("CR-0001", month=10, limit=5)))
    assert "EXTRACT(MONTH FROM order_date) = @m" in captured["sql"]
    assert h["_source"] == "bigquery" and h["totalAmount"] == 600


# ── GECX compatibility ───────────────────────────────────────────────────────
def test_list_tools_minimal_clean_schema():
    tools = run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"search_products", "get_product_details", "compare_products", "get_customer_profile",
                     "get_purchase_history", "recommend_dinner_set", "check_store_inventory"}
    blob = json.dumps([t.inputSchema for t in tools])
    assert '"title"' not in blob and '"default"' not in blob


def test_http_initialize_and_tools_list_loose_accept():
    from starlette.testclient import TestClient

    with TestClient(server.app) as c:
        hdr = {"Content-Type": "application/json", "Accept": "*/*"}  # loose Accept
        r = c.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                         "clientInfo": {"name": "t", "version": "1"}}})
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "CymbalCommerceMCP"
        r = c.post("/mcp", headers=hdr, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 7


@pytest.mark.parametrize("q,expected", [("carrots under ¥300", "carrots"), ("under 500 yen carrots", "carrots"), ("salmon fillets under ¥1,000", "salmon fillets"),
                                    ("snacks for kids ¥300 or less", "snacks for kids")])
def test_strip_price_phrase(q, expected):
    assert server.strip_price_phrase(q) == expected


def test_search_query_stripped_when_price_filter(fakes):
    sc, _ = fakes
    run(server.search_products(query="carrots under ¥300"))
    assert sc.requests[0].query == "carrots"


@pytest.mark.parametrize("v,exp", [(10, (10, None)), ("10", (10, None)), ("Oct", (10, None)), ("October 2025", (10, 2025)), ("2025-10", (10, 2025)),
                                   ("2025/10", (10, 2025)), ("2025-10-01", (10, 2025)), (None, (None, None))])
def test_parse_month(v, exp):
    assert server.parse_month(v) == exp


def test_history_month_int_via_mcp_call(bq_down):
    """MCP-level call: int month must be accepted even though the schema type is string."""
    res = run(server.mcp.call_tool("get_purchase_history", {"customer_id": "CR-0001", "month": 10, "limit": 5}))
    blocks = res[0] if isinstance(res, tuple) else res
    h = json.loads(blocks[0].text)
    assert h["orderCount"] == 3 and len(h["purchases"]) == 3
    res = run(server.mcp.call_tool("get_purchase_history", {"customer_id": "CR-0001", "month": "2025-10"}))
    blocks = res[0] if isinstance(res, tuple) else res
    assert json.loads(blocks[0].text)["orderCount"] == 2


def test_month_schema_is_plain_string():
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    props = tools["get_purchase_history"].inputSchema["properties"]
    assert props["month"] == {"type": "string"} and set(props) == {"customer_id", "month", "limit"}


# ── general merchandise + compare_products ───────────────────────────────────
def make_gm_product(pid, title, price, material, size, labels, rating=(4.2, 30), brand="Cymbal"):
    return SimpleNamespace(
        name=server.product_name(pid), title=title,
        categories=["Kitchen > Pots & Pans > Frying Pans"],
        price_info=SimpleNamespace(price=float(price), original_price=float(price)),
        attributes={"on_sale": attr(text=["false"]), "size": attr(text=[size]), "material": attr(text=[material]),
                    "labels": attr(text=labels), "is_food": attr(text=["false"]),
                    "top_category": attr(text=["Kitchen"])},
        images=[], uri=f"https://site/?product={pid}", description="", brands=[brand],
        availability=SimpleNamespace(name="IN_STOCK"),
        rating=SimpleNamespace(average_rating=rating[0], rating_count=rating[1]),
    )


class GMProductClient:
    def __init__(self, products):
        self.products, self.calls = products, []

    def get_product(self, name, timeout=None):
        self.calls.append(name)
        pid = name.rsplit("/", 1)[-1]
        if pid not in self.products:
            raise RuntimeError("404 not found")
        return self.products[pid]


@pytest.fixture
def gm(monkeypatch):
    prods = {
        "F1": make_gm_product("F1", "Cymbal Induction Frying Pan 26 cm", 2178, "Aluminum (non-stick coating)", "26 cm", ["Induction compatible"]),
        "F2": make_gm_product("F2", "Cymbal Select Iron Frying Pan 28 cm", 3828, "Iron", "28 cm", ["Induction compatible", "Made in Japan"], (4.6, 88)),
        "F3": make_gm_product("F3", "Cymbal Basic Frying Pan 20 cm", 1078, "Aluminum", "20 cm", []),
    }
    pc = GMProductClient(prods)
    monkeypatch.setattr(server, "_product_client", lambda: pc)
    return pc


def test_summary_general_merchandise_fields(gm):
    s = server.summarize_product("F2", gm.products["F2"])
    assert s["material"] == "Iron" and s["labels"] == ["Induction compatible", "Made in Japan"] and s["brand"] == "Cymbal"
    assert s["topCategory"] == "Kitchen" and s["rating"] == {"average": 4.6, "count": 88}


def test_summary_channel_store_only(gm):
    p = gm.products["F1"]
    p.attributes["channel"] = attr(text=["store_only"])
    assert server.summarize_product("F1", p)["channel"] == "store_only"
    monkey = server.COMPACT_RESULTS
    try:
        server.COMPACT_RESULTS = True
        assert server.summarize_product("F1", p)["channel"] == "store_only"  # kept in compact mode
    finally:
        server.COMPACT_RESULTS = monkey


def test_compare_products_rows_general_merchandise(gm):
    res = json.loads(run(server.compare_products(["F1", "F2"])))
    assert res["product_ids"] == ["F1", "F2"] and res["title"] == "Frying Pans comparison"
    rows = {r["label"]: r["values"] for r in res["rows"]}
    assert rows["Price (incl. tax)"] == ["¥2,178", "¥3,828"]
    assert rows["Size"] == ["26 cm", "28 cm"]
    assert rows["Labels"] == ["Induction compatible", "Induction compatible / Made in Japan"]
    assert rows["Rating"][1] == "★4.6 (88 reviews)"
    assert rows["Material"] == ["Aluminum (non-stick coating)", "Iron"]
    assert "Origin" not in rows
    assert all(len(v) == 2 for v in rows.values())


def test_compare_products_food_has_origin(fakes):
    res = json.loads(run(server.compare_products(["P001", "P002"])))
    rows = {r["label"]: r["values"] for r in res["rows"]}
    assert rows["Origin"] == ["Hokkaido", "Hokkaido"] and "Material" not in rows
    assert rows["Price (incl. tax)"][0] == "¥270 (reg. ¥300)"
    assert rows["Allergens"][0] == "wheat / milk"


def test_compare_products_accepts_string_and_handles_missing(gm):
    res = json.loads(run(server.compare_products("F1, F3, NOPE")))
    assert res["product_ids"] == ["F1", "F3"] and res["missing"] == ["NOPE"]
    via_mcp = run(server.mcp.call_tool("compare_products", {"product_ids": "F1,F2"}))
    blocks = via_mcp[0] if isinstance(via_mcp, tuple) else via_mcp
    assert json.loads(blocks[0].text)["product_ids"] == ["F1", "F2"]


def test_compare_products_needs_two(gm):
    assert "error" in json.loads(run(server.compare_products(["F1"])))


@pytest.mark.parametrize("v,exp", [(["a", "b", "a"], ["a", "b"]), ("a,b;c", ["a", "b", "c"]), ('["x","y"]', ["x", "y"]),
                                   ("1 2 3 4 5", ["1", "2", "3", "4"])])
def test_parse_product_ids(v, exp):
    assert server.parse_product_ids(v) == exp


def test_compare_schema_is_array():
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    assert tools["compare_products"].inputSchema["properties"]["product_ids"]["type"] == "array"


def test_bundled_persona_is_cr0001():
    data = json.loads((Path(server.__file__).parent / "data" / "personas.json").read_text(encoding="utf-8"))
    assert any(p["customer_id"] == "CR-0001" for p in data)


def test_bq_fallback_when_retail_empty(fakes, monkeypatch):
    sc, _ = fakes
    sc.n = 0
    monkeypatch.setattr(server, "SEARCH_BQ_FALLBACK", True)
    captured = {}

    def fake_query(sql, params):
        captured["sql"], captured["params"] = sql, params
        return [{"id": "F9", "title": "Cymbal Induction Frying Pan 26 cm", "categories": ["Kitchen > Frying Pans"],
                 "brands": ["Cymbal"], "description": "", "availability": "IN_STOCK", "uri": "u", "images": [{"uri": "i"}],
                 "attributes": [{"key": "material", "value": {"text": ["Iron"], "numbers": []}},
                                {"key": "labels", "value": {"text": ["Induction compatible"], "numbers": []}}],
                 "priceInfo": {"price": 2178.0, "originalPrice": 2178.0}, "rating": {"averageRating": 4.4, "ratingCount": 20}}]

    monkeypatch.setattr(server, "_bq_query", fake_query)
    res = json.loads(run(server.search_products(query="frying pan under ¥3000", filter=None)))
    assert res["_source"] == "bigquery_fallback" and res["productDetails"][0]["productId"] == "F9"
    assert res["productDetails"][0]["material"] == "Iron" and "priceInfo.price <= @cap" in captured["sql"]
    assert [p.value for p in captured["params"] if p.name == "t0"] == ["frying"]  # lower-cased, price phrase stripped
