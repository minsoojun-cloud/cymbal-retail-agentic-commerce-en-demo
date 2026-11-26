"""HTTP API (DATA_MODE=local, AGENT_MODE=mock) — CONTRACT v8 Store Navi BFF."""
from __future__ import annotations

import json

from conftest import sse_events


def test_config_js(client):
    r = client.get("/config.js")
    assert r.status_code == 200 and r.text.startswith("window.SN_CONFIG = ")
    cfg = json.loads(r.text[len("window.SN_CONFIG = "):].rstrip(";\n"))
    assert cfg["appMode"] == "customer" and cfg["agentMode"] in ("live", "mock")
    assert cfg["onlineStoreUrl"].startswith("https://") and "904006867101" not in r.text
    assert {c["customer_id"] for c in cfg["customers"]} >= {"CR-0001", "CR-0002", "CR-0003"}
    assert "staffUrl" not in cfg  # only when STAFF_URL is set


def test_stores_nearest(client):
    r = client.get("/api/stores", params={"lat": 35.5314, "lng": 139.6978}).json()
    assert r["nearest_id"] == "S01" and r["stores"][0]["in_store"] is True
    assert {"distance_m", "open_now", "in_store"} <= set(r["stores"][0])


def test_store_home_personalized(client):
    client.post("/api/login", json={"customer_id": "CR-0001"})
    a = client.get("/api/stores/S01/home").json()
    client.post("/api/login", json={"customer_id": "CR-0003"})
    b = client.get("/api/stores/S01/home").json()
    client.post("/api/login", json={"customer_id": "CR-0001"})
    assert a["customer"]["customer_id"] == "CR-0001" and b["customer"]["customer_id"] == "CR-0003"
    pa = [c for c in a["coupons"] if c["personal"]]
    pb = [c for c in b["coupons"] if c["personal"]]
    assert pa and pb and all(c["customer_id"] == "CR-0001" for c in pa) and all(c["customer_id"] == "CR-0003" for c in pb)
    assert a["coupons"][0]["personal"] and a["coupons"][0]["badge"] == "Just for Sakura"
    assert all(o["product"]["id"] for o in a["offers"])
    assert [o["product_id"] for o in a["offers"]] != [o["product_id"] for o in b["offers"]]
    assert client.get("/api/stores/S99/home").status_code == 404


def test_login_rejects_unknown(client):
    assert client.post("/api/login", json={"customer_id": "CR-9999"}).status_code == 400


def test_autocomplete(client):
    r = client.get("/api/autocomplete", params={"q": "cu"}).json()
    assert r["suggestions"] and r["source"] in ("retail", "local")
    assert client.get("/api/autocomplete", params={"q": ""}).json()["suggestions"] == []


def test_search_availability_current_first(client, scenarios):
    r = client.get("/api/search", params={"q": "cold medicine tablets", "store_id": "S01"}).json()
    assert r["results"]
    for p in r["results"]:
        av = p["availability"]
        assert av["current"]["store_id"] == "S01"
        assert "drug_class" in p and "abuse_control" in p and "age_min" in p
    pid = scenarios["cold_tablet_out_at_S01"]["product_id"]
    hit = [p for p in r["results"] if p["id"] == pid]
    assert hit and hit[0]["availability"]["current"]["status"] == "out_of_stock"


def test_search_symptom_fallback(client):
    """Symptom-only queries (AICS may return 0) fall back to local symptom / synonym matching."""
    for q in ("fever", "cough", "runny nose", "cold"):
        r = client.get("/api/search", params={"q": q, "store_id": "S01", "page_size": 10}).json()
        assert r["results"], q
        assert any(p.get("drug_class") for p in r["results"][:5]), q
    from app import deps

    ids = deps.symptom_search("fever", 10)
    assert ids and any("Fever" in (deps.to_product(i).get("category_path") or "")
                       or any("fever" in s.lower() for s in deps.to_product(i).get("symptoms", [])) for i in ids[:5])


def test_search_in_store_only(client):
    r = client.get("/api/search", params={"q": "cooling", "store_id": "S01", "in_store_only": 1}).json()
    assert r["results"]
    assert all(p["availability"]["current"]["status"] in ("in_stock", "low_stock") for p in r["results"])


def test_product_availability_and_layout_and_route(client, scenarios):
    pid = scenarios["fever_set_S01"][0]
    av = client.get(f"/api/products/{pid}/availability", params={"store_id": "S01"}).json()
    assert av["current"]["status"] in ("in_stock", "low_stock") and av["product"]["id"] == pid
    assert av["source"] in ("retail_local_inventory", "bigquery", "local") and av["channel"] in ("omni", "store_only", "online_only")
    assert av["online"]["url"] == f"https://cymbal-site.example.test/?product={pid}"
    assert client.get("/api/products/0000000000000/availability").status_code == 404
    lay = client.get("/api/stores/S01/layout").json()
    assert lay["store_id"] == "S01" and (lay.get("fixtures") or lay.get("gondolas"))
    route = client.post("/api/route", json={"store_id": "S01", "product_ids": scenarios["fever_set_S01"][:4]}).json()
    assert [s["no"] for s in route["stops"]] == list(range(1, len(route["stops"]) + 1)) and route["polyline"]


def test_photos_and_static(client):
    assert client.get("/api/photos/shelf_photos/S01/nope.jpg").status_code == 404
    assert client.get("/api/photos/../../etc/passwd").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/static/shared/img/logo.svg").status_code == 200
    assert client.get("/static/shared/css/tokens.css").status_code == 200


def test_staff_routes_absent_in_customer_mode(client):
    assert client.get("/api/staff/demo-photos").status_code in (404, 405)


def _stream(client, msg, sid="pytest-s1"):
    r = client.post("/api/agent/stream", json={"session_id": sid, "message": msg, "store_id": "S01"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return sse_events(r.text)


def test_agent_stream_fever_mock(client):
    ev = _stream(client, "I've had a fever since yesterday and feel like I'm getting a cold")
    types = [e["type"] for e in ev]
    assert types[0] == "status" and types[-1] == "done" and "chips" in types
    act = next(e for e in ev if e["type"] == "action")
    assert act["name"] == "show_store_items"
    args = act["args"]
    nos = [it["no"] for it in args["items"] if it["no"]]
    assert nos == list(range(1, len(nos) + 1)) and len(nos) >= 2
    assert args["route"]["stops"] and args["disclaimer"].startswith("This service provides general information")
    from app.agent.safety import MEDICINE_CLASSES

    meds = [it for it in args["items"] if it["product"].get("drug_class") in MEDICINE_CLASSES]
    assert len(meds) <= 1
    for it in args["items"]:
        assert it["availability"]["current"]["store_id"] == "S01"
    done = ev[-1]
    assert done["trace"] and {r["tool"] for r in done["trace"]} >= {"red_flag_check", "show_store_items"}
    text = "".join(e.get("text", "") for e in ev if e["type"] == "text")
    assert "Sakura" in text and not __import__("re").search(r"[\u3040-\u30ff\u4e00-\u9fff]", text.replace(args["store_name"] or "", ""))


def test_agent_stream_red_flag_first(client):
    from app.agent.safety import MEDICINE_CLASSES

    ev = _stream(client, "I've had a fever of 39C for 3 days and it won't go down", sid="pytest-rf")
    first_text = next(e for e in ev if e["type"] == "text")
    assert "doctor" in first_text["text"][:60]
    for e in ev:
        if e["type"] == "action":
            assert all(it["product"].get("drug_class", "") not in MEDICINE_CLASSES for it in e["args"]["items"])
    assert next(e for e in ev if e["type"] == "done")["red_flags"]


def test_agent_stream_rejects_empty(client):
    assert client.post("/api/agent/stream", json={"message": " "}).status_code == 400


def test_health(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["data_mode"] == "local" and h["catalog"] > 1000


def test_fixture_elevation_endpoint(client, scenarios):
    pid = scenarios["fever_set_S01"][1]
    av = client.get(f"/api/products/{pid}/availability", params={"store_id": "S01"}).json()
    sh = av["current"]["shelf"]
    g = client.get(f"/api/stores/S01/fixtures/{sh['fixture_id']}").json()
    assert g["fixture"]["id"] == sh["fixture_id"] and g["tiers"][0]["tier"] == g["fixture"]["tiers"]
    assert all(len(t["slots"]) == g["fixture"]["slots_per_tier"] for t in g["tiers"])
    cells = {s["slot_id"]: s for t in g["tiers"] for s in t["slots"]}
    assert all(any(p["id"] == pid for p in cells[x]["products"]) for x in sh["slot_ids"])
    assert client.get("/api/stores/S01/fixtures/ZZ99").status_code == 404


def test_agent_stream_meal_ideas_show_recipes(client):
    ev = _stream(client, "Dinner ideas the kids will like", sid="pytest-meal")
    act = next(e for e in ev if e["type"] == "action")
    assert act["name"] == "show_recipes" and act["args"]["query"]
    assert "shrimp" in "".join(e.get("text", "") for e in ev if e["type"] == "text")


def test_channel_rules_in_availability(client):
    """CONTRACT v14: online_only → no store rows in stock; store_only → online not available."""
    from app import deps, inventory

    cat = deps.get_catalog()
    by = {}
    for pid in list(cat.items)[:4000]:
        p = deps.to_product(pid)
        by.setdefault(p.get("channel") or "omni", pid)
    if "online_only" in by:
        av = inventory.get_availability(by["online_only"], "S01")
        assert av["channel"] == "online_only" and av["current"]["status"] == "not_carried" and av["online"]["available"]
    if "store_only" in by:
        av = inventory.get_availability(by["store_only"], "S01")
        assert av["channel"] == "store_only" and av["online"]["available"] is False
