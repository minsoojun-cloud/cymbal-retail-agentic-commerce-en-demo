"""Unit tests for the Cymbal Retail storefront backend (local DATA_FILE mode, Gemini mocked).

run: pytest tests/test_site_api.py   (from repo root)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "online-store"
sys.path.insert(0, str(SITE))

TESTDATA = SITE / "testdata"           # small English fixture (catalog, stores, store inventory, persona)
DATA = Path(os.environ.get("SITE_TEST_DATA") or TESTDATA / "products_en.jsonl")
OMNI, STORE_ONLY, ONLINE_ONLY = "4991002207381", "4921573667079", "4986811137936"
JP = __import__("re").compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")


@pytest.fixture(scope="module")
def client():
    os.environ["DATA_FILE"] = str(DATA)
    os.environ["STORES_FILE"] = str(TESTDATA / "stores.json")
    os.environ["STORE_INVENTORY_FILE"] = str(TESTDATA / "store_inventory.jsonl")
    os.environ["PERSONA_DIR"] = str(TESTDATA / "personas")
    os.environ["AI_MOCK"] = "1"
    for k in ("ADK_AGENT_URL", "AGENT_BACKEND", "ENRICH_URL", "STORE_APP_URL", "FLYER_URL"):
        os.environ.pop(k, None)
    os.environ["DELIVERY_SPEED_SEC"] = "3"
    from fastapi.testclient import TestClient
    from app import main, stores

    stores.reset()
    main.reset_engine()
    with TestClient(main.app) as c:
        yield c


def test_index_sets_cookies(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Cymbal" in r.text
    assert r.cookies.get("cr_customer") == "CR-0001"
    assert r.cookies.get("cr_visitor", "").startswith("v-")


def test_config_js(client):
    r = client.get("/config.js")
    assert r.status_code == 200
    body = r.text
    assert body.startswith("window.CR_CONFIG = ")
    cfg = json.loads(body[len("window.CR_CONFIG = "):].rstrip(";\n"))
    assert "deploymentName" not in cfg and "toolIdMap" not in cfg and "tokenBroker" not in cfg
    assert cfg["agentMode"] == "mock" and cfg["agentEndpoint"] == "/api/agent/stream"
    assert cfg["deliverySpeedSec"] == 3
    assert cfg["customers"]["CR-0001"]["name"] == "Sakura Tanaka"
    assert not JP.search(body)


def test_search_shape(client):
    r = client.get("/api/search", params={"q": "curry", "page_size": 12})
    assert r.status_code == 200
    j = r.json()
    for k in ("results", "total", "facets", "tiles", "conversation", "corrected_query", "applied_filter"):
        assert k in j
    assert 0 < len(j["results"]) <= 12
    assert not JP.search(json.dumps([f["label"] for f in j["facets"]] + [j["conversation"]["followup_question"]]))
    assert j["total"] >= len(j["results"])
    p = j["results"][0]
    for k in ("id", "title", "brand", "category", "price", "original_price", "on_sale", "discount_rate", "image",
              "rating", "rating_count", "size", "labels", "availability", "description"):
        assert k in p, k
    assert p["image"].startswith("/img/products/")
    assert all(0 <= x["discount_rate"] <= 1 for x in j["results"])
    keys = {f["key"] for f in j["facets"]}
    assert {"attributes.top_category", "brands", "price"} <= keys
    for f in j["facets"]:
        assert f["label"] and all({"value", "count", "selected"} <= set(v) for v in f["values"])
    assert j["tiles"] and all({"label", "filter"} <= set(t) for t in j["tiles"])
    conv = j["conversation"]
    assert {"id", "refined_query", "followup_question", "suggested_answers"} <= set(conv)
    assert conv["followup_question"] and conv["suggested_answers"]


def test_search_facet_filter_and_price(client):
    base = client.get("/api/search", params={"q": "curry"}).json()
    cat = next(f for f in base["facets"] if f["key"] == "attributes.top_category")["values"][0]["value"]
    j = client.get("/api/search", params={"q": "curry", "facets": json.dumps({"attributes.top_category": [cat]})}).json()
    assert j["total"] <= base["total"]
    assert all(p["category"] == cat for p in j["results"])
    assert 'attributes.top_category: ANY("' in j["applied_filter"]
    j2 = client.get("/api/search", params={"q": "curry", "facets": json.dumps({"price": ["0-300"]})}).json()
    assert all(p["price"] < 300 for p in j2["results"])
    assert "price: IN(0i, 300e)" in j2["applied_filter"]


def test_search_tiles_and_answer(client):
    base = client.get("/api/search", params={"q": "curry"}).json()
    t = base["tiles"][0]
    j = client.get("/api/search", params={"q": "curry", "tiles": json.dumps([t])}).json()
    assert j["total"] <= base["total"] and j["total"] > 0
    act = base["conversation"]["answer_actions"][0]
    j2 = client.get("/api/search", params={"q": "curry", "conversation_id": base["conversation"]["id"],
                                           "answer": act["label"], "answer_key": act["key"], "answer_value": act["value"]}).json()
    assert j2["total"] <= base["total"]


def test_search_pagination(client):
    p1 = client.get("/api/search", params={"q": "curry", "page": 1, "page_size": 5}).json()
    p2 = client.get("/api/search", params={"q": "curry", "page": 2, "page_size": 5}).json()
    assert {p["id"] for p in p1["results"]}.isdisjoint({p["id"] for p in p2["results"]})


def test_autocomplete(client):
    j = client.get("/api/autocomplete", params={"q": "Cur"}).json()
    assert j["suggestions"] and any("cur" in s.lower() for s in j["suggestions"])
    assert client.get("/api/autocomplete", params={"q": ""}).json() == {"suggestions": []}


def test_products(client):
    ids = [p["id"] for p in client.get("/api/search", params={"q": "curry", "page_size": 3}).json()["results"]]
    j = client.get("/api/products", params={"ids": ",".join(ids + ["nope"])}).json()
    assert [p["id"] for p in j["products"]] == ids


def test_ai_summary_template(client):
    ids = [p["id"] for p in client.get("/api/search", params={"q": "curry", "page_size": 6}).json()["results"]]
    j = client.post("/api/ai-summary", json={"q": "curry", "product_ids": ids}).json()
    assert j["summary"]
    assert all(h["product_id"] in ids for h in j["highlights"])
    assert "followup" in j


def test_home_and_features(client):
    h = client.get("/api/home").json()
    assert h["categories"] and h["sale"] and h["recommended"]
    assert all(p["on_sale"] for p in h["sale"])
    f = client.get("/api/features").json()
    assert f["engine"] == "local" and f["catalog_size"] >= 20
    assert not JP.search(json.dumps([c["name"] for c in h["categories"]]))


def test_static_assets(client):
    for path in ("/css/theme.css", "/js/app.js", "/js/chat.js", "/js/checkout.js", "/js/orders.js", "/js/cart.js",
                 "/js/chat_rich.js", "/js/chat_cards.js", "/js/home.js", "/js/router.js"):
        assert client.get(path).status_code == 200, path
    img = client.get("/api/search", params={"q": "curry", "page_size": 1}).json()["results"][0]["image"]
    assert client.get(img).status_code == 200


def test_index_loads_chat_rich_modules_in_order(client):
    """chat_rich.js (card injection into our panel) must load before chat_cards.js, both after chat.js."""
    html = client.get("/").text
    i_chat, i_rich, i_cards = (html.index(f'src="/js/{n}.js"') for n in ("chat", "chat_rich", "chat_cards"))
    assert i_chat < i_rich < i_cards
    # CONTRACT v3: own slide-over panel, no CES chat-messenger widget
    for el_id in ("cr-chat", "cr-chat-log", "cr-chat-input", "cr-chat-typing", "cr-chat-status", "cr-chat-reset", "cr-fab"):
        assert f'id="{el_id}"' in html, el_id
    chat = client.get("/js/chat.js").text
    for banned in ("chat-messenger", "gstatic.com/chat-messenger", "deploymentName", "toolIdMap"):
        assert banned not in chat, banned
    for needed in ("/api/agent/stream", "page_state", "sessionStorage", "isComposing", "compositionend", "order_id",
                   "case 'status'", "case 'text'", "case 'action'", "case 'chips'", "case 'done'", "case 'error'"):
        assert needed in chat, needed
    assert "placeOrder(orderId)" in client.get("/js/checkout.js").text
    cards = client.get("/js/chat_cards.js").text
    for phrase in ("Proceed to checkout", ", please", "Please place my order", "C.PAYMENT", "Delivered"):
        assert phrase in cards, phrase
    search_js = client.get("/js/search.js").text
    assert "sections" in search_js and "cr-set-sec" in search_js
    # CONTRACT v2.1: optional per-section quantities → ×N display, Σ price×qty total, set add with quantities
    assert "quantities" in search_js and "* it.qty" in search_js
    assert "quantities" in cards and "x.qty" in cards


def test_dinner_set_mock_queries_return_products(client):
    """The mock-chat dinner set is built from real catalog ids via /api/search (one per role)."""
    for q in ("salmon", "tofu", "miso", "rice", "apple"):
        res = client.get("/api/search", params={"q": q, "page_size": 10}).json()
        assert res["results"], q
        ids = [p["id"] for p in res["results"]]
        got = client.get("/api/products", params={"ids": ",".join(ids[:3])}).json()["products"]
        assert len(got) == len(ids[:3])


def test_filter_builder():
    from app.search import facets_to_filter, merge_filters

    f = facets_to_filter({"brands": ["A", 'B"q'], "price": ["0-300", "5000-"]})
    assert 'brands: ANY("A", "B\\"q")' in f
    assert "(price: IN(0i, 300e) OR price: IN(5000i, *))" in f
    assert merge_filters("a", "", "b") == "(a) AND (b)"


def test_retail_feature_fallback(monkeypatch):
    """RetailSearch drops rejected advanced features and still answers (no network: fake client)."""
    from app import search as S
    from app.catalog import Catalog

    class Resp:
        def __init__(self):
            self.results, self.facets, self.total_size, self.corrected_query = [], [], 0, ""
            self.tile_navigation_result = None
            self.conversational_search_result = None

    class FakeClient:
        def __init__(self):
            self.calls = []

        def search(self, request, timeout=None):
            self.calls.append(request)
            if request.tile_navigation_spec.tile_navigation_requested or request.conversational_search_spec.followup_conversation_requested:
                raise ValueError("400 feature not enabled")
            return Resp()

    eng = S.RetailSearch.__new__(S.RetailSearch)
    from google.cloud import retail_v2alpha
    eng.retail = retail_v2alpha
    eng.catalog = Catalog(str(DATA))
    eng.local = S.LocalSearch(eng.catalog)
    eng.client = FakeClient()
    eng.catalog_name = "projects/1/locations/global/catalogs/default_catalog"
    eng.placement = eng.catalog_name + "/servingConfigs/default_search"
    eng.branch = eng.catalog_name + "/branches/0"
    eng.disabled, eng.conv_answers, eng.pcache = {}, {}, {}
    eng.status = {f: "untested" for f in S.ADVANCED}
    import threading
    eng.lock = threading.Lock()
    out = eng.search("curry")
    assert out["engine"] == "retail"
    assert out["features"]["tile_navigation"] == "fallback"
    assert out["features"]["conversational"] == "fallback"
    assert out["features"]["query_expansion"] == "live"
    assert "tile_navigation" in eng.disabled and "conversational" in eng.disabled


def test_natural_language_keyword_retry(client):
    j = client.get("/api/search", params={"q": "chicken thighs for dinner tonight"}).json()
    assert j["total"] > 0 and "chicken" in j["results_query"].lower()
    s = client.get("/api/autocomplete", params={"q": "Pers"}).json()["suggestions"]
    assert s and not any(JP.search(x) for x in s if x.lower().startswith("pers"))


def test_keyword_terms_helper():
    from app.catalog import keyword_terms
    assert keyword_terms("a non-stick frying pan for my kitchen", ["Frying Pans", "Kitchen", "Pan"]) == ["frying pan", "kitchen"]


# ---------------------------------------------------------------------------- v3: home recommendations
def test_recommendations_all_types(client):
    seen_titles = {}
    for t in ("for_you", "buy_again", "sale", "seasonal", "new", "popular"):
        j = client.get("/api/recommendations", params={"type": t, "limit": 12}).json()
        assert j["type"] == t and j["title"] and j["customer_id"] == "CR-0001", j
        items = j["items"]
        assert 1 <= len(items) <= 12, (t, len(items))
        for p in items:
            for k in ("id", "title", "price", "image", "reason", "tags", "channel"):
                assert k in p, (t, k)
            assert p["image"].startswith("/img/products/")
            assert "shrimp" not in p["title"].lower(), (t, p["title"])
            assert not JP.search(p["reason"] + " ".join(p["tags"])), (t, p["reason"])
        assert len({p["image"] for p in items}) == len(items), t  # one product per image
        seen_titles[t] = j["title"]
    assert seen_titles["for_you"] == "Recommended for Sakura"
    assert seen_titles["buy_again"] == "Buy it again"


def test_recommendations_semantics(client):
    fy = client.get("/api/recommendations", params={"type": "for_you", "limit": 16}).json()["items"]
    reasons = " ".join(p["reason"] for p in fy)
    assert sum(n in reasons for n in ("Sakura", "Ken", "Yui", "Haru")) >= 3, reasons
    assert any("Family favorite" in p["tags"] for p in fy)
    again = client.get("/api/recommendations", params={"type": "buy_again"}).json()["items"]
    counts = [p["purchase_count"] for p in again]
    assert counts == sorted(counts, reverse=True) and counts[0] >= 10, counts
    assert again[0]["reason"].startswith("Bought 12 times")
    sale = client.get("/api/recommendations", params={"type": "sale"}).json()["items"]
    assert all(p["on_sale"] and p["price"] < p["original_price"] for p in sale)
    assert all("% off" in p["reason"] for p in sale)
    new = client.get("/api/recommendations", params={"type": "new", "limit": 8}).json()["items"]
    assert {p["category"] for p in new} <= {"Kitchen", "Beauty & Personal Care", "Pet", "Home & Living"}
    pop = client.get("/api/recommendations", params={"type": "popular", "limit": 5}).json()["items"]
    assert [p["rank"] for p in pop] == [1, 2, 3, 4, 5]
    seas = client.get("/api/recommendations", params={"type": "seasonal", "limit": 6}).json()["items"]
    assert seas and all("In season" in p["tags"] for p in seas)
    assert client.get("/api/recommendations", params={"type": "bogus"}).json()["type"] == "for_you"


# ---------------------------------------------------------------------------- v14: omnichannel store availability
def test_product_channel_field(client):
    got = {p["id"]: p["channel"] for p in
           client.get("/api/products", params={"ids": f"{OMNI},{STORE_ONLY},{ONLINE_ONLY}"}).json()["products"]}
    assert got == {OMNI: "omni", STORE_ONLY: "store_only", ONLINE_ONLY: "online_only"}


def test_product_stores_shape_and_sort(client):
    j = client.get(f"/api/products/{OMNI}/stores", params={"lat": 35.6595, "lng": 139.7005}).json()
    assert j["product_id"] == OMNI and j["channel"] == "omni" and j["online_available"] is True
    assert j["source"] in ("retail_local_inventory", "bigquery", "local")
    assert len(j["stores"]) == 3
    for s in j["stores"]:
        for k in ("store_id", "name", "address", "open_now", "hours", "quantity", "availability", "pickup", "distance_m"):
            assert k in s, k
        assert s["name"].startswith("Cymbal Retail ") and not JP.search(s["name"] + s["address"] + s["hours"])
        assert s["pickup"] == (s["quantity"] > 0)
        assert s["availability"] == ("IN_STOCK" if s["quantity"] > 0 else "OUT_OF_STOCK")
    # in-stock stores first, nearest first (S03 Shibuya is at the given point)
    assert j["stores"][0]["store_id"] == "S03" and j["stores"][0]["distance_m"] < 100
    assert j["stores"][-1]["quantity"] == 0


def test_product_stores_channels(client):
    so = client.get(f"/api/products/{STORE_ONLY}/stores").json()
    assert so["channel"] == "store_only" and so["online_available"] is False
    assert any(s["quantity"] > 0 for s in so["stores"])
    oo = client.get(f"/api/products/{ONLINE_ONLY}/stores").json()
    assert oo["channel"] == "online_only" and oo["stores"] == [] and oo["online_available"] is True
    assert client.get("/api/products/nope/stores").status_code == 404


def test_open_now_and_hours():
    import datetime as dt
    from app.stores import JST, hours_label, open_now, shelf_label_of
    st = {"hours": {"open": "09:00", "close": "22:00"}}
    assert hours_label(st) == "9:00 AM – 10:00 PM"
    assert open_now(st, dt.datetime(2026, 11, 25, 21, 59, tzinfo=JST))
    assert not open_now(st, dt.datetime(2026, 11, 25, 22, 0, tzinfo=JST))
    assert not open_now(st, dt.datetime(2026, 11, 25, 8, 0, tzinfo=JST))
    assert shelf_label_of({"gondola_id": "M18", "tier": 3}) == "Aisle M18 • Shelf 3"
    assert shelf_label_of({"shelf_id": "RX"}) == "Pharmacy counter"


def test_pickup_in_store_facet_and_filter(client):
    j = client.get("/api/search", params={"q": "curry"}).json()
    f = next(f for f in j["facets"] if f["key"] == "pickupInStore")
    assert f["label"] == "Pick up in store"
    labels = {v["value"]: v["label"] for v in f["values"]}
    assert labels.get("S03") == "Shibuya"
    k = client.get("/api/search", params={"q": "", "facets": json.dumps({"pickupInStore": ["S01"]})}).json()
    assert k["total"] > 0 and 'pickupInStore: ANY("S01")' in k["applied_filter"]
    ids = [p["id"] for p in k["results"]]
    assert ONLINE_ONLY not in ids and OMNI not in ids  # curry roux is out of stock at S01
    k2 = client.get("/api/search", params={"q": "", "filter": 'pickupInStore: ANY("S01")'}).json()
    assert k2["total"] == k["total"]


def test_stores_list(client):
    j = client.get("/api/stores").json()
    assert [s["store_id"] for s in j["stores"]] == ["S01", "S02", "S03"]


# ---------------------------------------------------------------------------- v3: ADK agent proxy
def test_agent_stream_mock_mode_returns_503(client):
    r = client.post("/api/agent/stream", json={"message": "Hello"})
    assert r.status_code == 503 and r.json()["error"] == "AGENT_NOT_CONFIGURED"


UPSTREAM_SSE = (
    'data: {"type":"status","text":"Searching products…"}\n\n'
    'data: {"type":"text","text":"Tonight","author":"cymbal_concierge"}\n\n'
    'data: {"type":"action","name":"add_to_cart","args":{"product_id":"4903577695999","quantity":2},"result":{"ok":true}}\n\n'
    'data: {"type":"chips","items":["Proceed to checkout"]}\n\n'
    'data: {"type":"done","session_id":"s-abc"}\n\n'
).encode()


@pytest.fixture()
def fake_upstream(monkeypatch):
    """ADK_AGENT_URL set + httpx MockTransport standing in for the private Cloud Run agent."""
    import httpx
    from app import agent_proxy

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        if seen["body"]["message"] == "forbidden":
            return httpx.Response(403, text="Forbidden")
        async def chunks():  # SSE arrives in arbitrary chunks (split mid-event)
            for i in range(0, len(UPSTREAM_SSE), 37):
                yield UPSTREAM_SSE[i:i + 37]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())

    real = httpx.AsyncClient
    monkeypatch.setattr(agent_proxy.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("ADK_AGENT_URL", "https://cymbal-adk-agent-test.a.run.app/")
    calls = []

    def fake_fetch(req, audience):
        calls.append(audience)
        return "header.eyJleHAiOiA0MTAyNDQ0ODAwfQ.sig"  # exp = 2100-01-01
    import google.oauth2.id_token
    monkeypatch.setattr(google.oauth2.id_token, "fetch_id_token", fake_fetch)
    agent_proxy._token.clear()
    seen["token_calls"] = calls
    yield seen
    agent_proxy._token.clear()


def test_agent_stream_proxies_sse_with_id_token(client, fake_upstream):
    cfg = client.get("/config.js").text
    assert '"agentMode": "live"' in cfg
    client.cookies.set("cr_customer", "CR-0001")
    client.cookies.set("cr_visitor", "v-test123")
    page_state = {"cart": [{"product_id": "4903577695999", "title": "Eggs", "price": 267, "qty": 1}],
                  "checkout": {"state": "idle", "slot_id": None}, "orders": [],
                  "page_context": {"view": "home", "query": None, "product_id": None}}
    r = client.post("/api/agent/stream", json={"session_id": "s-1", "message": "Recommend dinner for tonight",
                                               "page_state": page_state, "customer_id": "SPOOF"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.content == UPSTREAM_SSE  # streamed through unchanged
    assert fake_upstream["url"] == "https://cymbal-adk-agent-test.a.run.app/api/chat/stream"
    assert fake_upstream["auth"] == "Bearer header.eyJleHAiOiA0MTAyNDQ0ODAwfQ.sig"
    assert fake_upstream["token_calls"] == ["https://cymbal-adk-agent-test.a.run.app"]  # audience = service URL
    b = fake_upstream["body"]
    assert b["customer_id"] == "CR-0001" and b["customer_name"] == "Sakura Tanaka" and b["visitor_id"] == "v-test123"
    assert b["session_id"] == "s-1" and b["message"] == "Recommend dinner for tonight" and b["page_state"] == page_state
    # token is cached for the next turn
    client.post("/api/agent/stream", json={"session_id": "s-1", "message": "Add the set to my cart"})
    assert len(fake_upstream["token_calls"]) == 1


def test_agent_stream_upstream_error_becomes_sse_error(client, fake_upstream):
    r = client.post("/api/agent/stream", json={"session_id": "s-2", "message": "forbidden"})
    assert r.status_code == 200
    events = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: ")]
    assert events[0]["type"] == "error" and "403" in events[0]["message"]
    assert events[-1] == {"type": "done", "session_id": "s-2"}
    assert client.post("/api/agent/stream", json={"message": "  "}).status_code == 400


# ---------- header cross links + backend selection ----------
ENRICH = "https://cymbal-data-enrichment.example.run.app"
STORE_APP = "https://cymbal-store-app.example.run.app"


def _cfg(client) -> dict:
    body = client.get("/config.js").text
    return json.loads(body[len("window.CR_CONFIG = "):].rstrip(";\n"))


@pytest.mark.parametrize("env,key,url", [("ENRICH_URL", "enrichUrl", ENRICH), ("STORE_APP_URL", "storeAppUrl", STORE_APP)])
def test_config_js_links_only_when_env_set(client, monkeypatch, env, key, url):
    monkeypatch.delenv(env, raising=False)
    assert key not in _cfg(client)
    monkeypatch.setenv(env, "  ")
    assert key not in _cfg(client)
    monkeypatch.setenv(env, "javascript:alert(1)")  # only http(s) URLs are exposed
    assert key not in _cfg(client)
    monkeypatch.setenv(env, url)
    assert _cfg(client)[key] == url


@pytest.mark.parametrize("env,backend,platform", [
    ({"AGENT_BACKEND": "adk", "ADK_AGENT_URL": "https://adk.example"}, "adk", "Agent Development Kit"),
    ({"AGENT_BACKEND": "ADK", "ADK_AGENT_URL": "https://adk.example"}, "adk", "Agent Development Kit"),
    ({"AGENT_BACKEND": "", "ADK_AGENT_URL": "https://adk.example"}, "adk", "Agent Development Kit"),
    ({"AGENT_BACKEND": "ces", "ADK_AGENT_URL": "https://adk.example"}, "adk", "Agent Development Kit"),  # CES removed
    ({"AGENT_BACKEND": "adk", "ADK_AGENT_URL": ""}, "mock", ""),
    ({"AGENT_BACKEND": "mock", "ADK_AGENT_URL": "https://adk.example"}, "mock", ""),
])
def test_backend_selection(client, monkeypatch, env, backend, platform):
    for k in ("AGENT_BACKEND", "ADK_AGENT_URL", "ENRICH_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app import main

    assert main.agent_backend() == backend
    cfg = _cfg(client)
    assert cfg["agentBackend"] == backend and cfg["agentPlatform"] == platform
    assert cfg["agentMode"] == ("mock" if backend == "mock" else "live")


def test_index_is_english_and_links_are_injected(client):
    html = client.get("/").text
    assert '<html lang="en"' in html and "Cymbal" in html and not JP.search(html)
    assert "AI Commerce Search" in html and "Search for Commerce" not in html  # product naming
    assert "cr-enrich-link" not in html
    js = client.get("/js/app.js").text
    assert "cr-enrich-link" in js and "cr-storeapp-link" in js and "Store App" in js


def test_static_js_has_no_japanese(client):
    import glob
    for f in sorted(glob.glob(str(SITE / "static" / "js" / "*.js"))) + [str(SITE / "static" / "css" / "theme.css")]:
        txt = Path(f).read_text(encoding="utf-8")
        assert not JP.search(txt), (f, JP.search(txt).group(0))


def test_site_python_has_no_japanese_or_old_project():
    import glob
    for f in glob.glob(str(SITE / "app" / "*.py")):
        txt = Path(f).read_text(encoding="utf-8")
        assert not JP.search(txt), f
        for bad in ("nov-11-11-retail-event-demo", "904006867101", "kcq3bvikaa"):
            assert bad not in txt, (f, bad)
