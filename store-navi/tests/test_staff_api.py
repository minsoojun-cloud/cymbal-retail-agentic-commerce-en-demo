"""Staff API tests (CONTRACT v9 slot ids; DATA_MODE=local, Gemini mocked).

Run:  cd store-navi && DATA_MODE=local python -m pytest -q tests/test_staff_api.py
"""
from __future__ import annotations

import base64
import importlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

SN = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    state = tmp_path_factory.mktemp("sn_state")
    os.environ.update({"DATA_MODE": "local", "SEARCH_MODE": "local", "APP_MODE": "staff", "SN_STATE_DIR": str(state),
                       "RETAIL_LOCAL_INVENTORY": "0", "STAFF_PREWARM": "0"})
    os.environ.pop("STAFF_VISION_MOCK", None)
    if str(SN) not in sys.path:
        sys.path.insert(0, str(SN))
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    yield state


@pytest.fixture(scope="module")
def mods(env):
    return importlib.import_module("app.staff"), importlib.import_module("app.inventory")


@pytest.fixture(scope="module")
def client(mods):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    staff = mods[0]
    try:
        app = importlib.import_module("app.main").app
        if not any(getattr(r, "path", "").startswith("/api/staff") for r in app.routes):
            app.include_router(staff.router)
    except Exception:  # main.py not there → staff router only
        app = FastAPI()
        app.include_router(staff.router)
    return TestClient(app)


def _jpeg(color=(200, 220, 240)) -> bytes:
    try:
        from PIL import Image
    except ImportError:  # Pillow optional: use any catalog packshot
        return sorted((SN.parent / "online-store" / "static" / "img" / "products").glob("*_p.jpg"))[0].read_bytes()
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def scenario(mods):
    """Target: first staff photo product at S01, moved to a slot range different from its seed."""
    staff = mods[0]
    sc = json.loads((staff._data_dir() / "scenarios.json").read_text(encoding="utf-8"))
    cands = list(sc.get("staff_photo_products") or []) + list(sc.get("fever_set_S01") or [])
    if not cands:
        rows = [json.loads(l) for l in (staff._data_dir() / "shelf_locations.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        cands = [r["product_id"] for r in rows if r.get("store_id") == "S01"]
    pid = cands[0]
    pid = pid if isinstance(pid, str) else pid["product_id"]
    fx = staff.fixture_index("S01")
    seed = staff.store_status("S01", pid).get("shelf") or {}
    fid = seed.get("fixture_id") or seed.get("gondola_id")
    f = fx.get(fid) or next(x for x in fx.values() if x.get("type") in ("gondola", "wall") and x["tiers"] >= 3)
    seed_tier = seed.get("tier")
    tier = next(t for t in (3, 2, 4, 1, 5) if t <= f["tiers"] and t != seed_tier)
    start = 2 if f["slots_per_tier"] >= 3 else 1
    slots = [staff.slot_id(f["id"], tier, start)] + ([staff.slot_id(f["id"], tier, start + 1)] if f["slots_per_tier"] >= 3 else [])
    return {"product_id": pid, "store_id": "S01", "fixture": f, "tier": tier, "slots": slots, "seed": seed}


@pytest.fixture(scope="module")
def demo(mods, tmp_path_factory, scenario):
    staff = mods[0]
    d = tmp_path_factory.mktemp("demo")
    (d / "t01.jpg").write_bytes(_jpeg())
    (d / "demo_photos.json").write_text(json.dumps({"photos": [{
        "id": "t01", "file": "t01.jpg", "store_id": "S01", "fixture_id": scenario["fixture"]["id"],
        "slot_ids": scenario["slots"], "tier": scenario["tier"], "product_id": scenario["product_id"], "hint": "test"}]},
        ensure_ascii=False), encoding="utf-8")
    staff.DEMO_DIR, staff.DEMO_MANIFEST = d, d / "demo_photos.json"
    return d


@pytest.fixture()
def fake_vision(mods, monkeypatch, scenario):
    staff = mods[0]
    calls = []

    def fake(image, mime="image/jpeg"):
        calls.append(len(image))
        return fake.result

    first = scenario["slots"][0]
    fw = "".join(chr(ord(c) + 0xFEE0) for c in first)  # full-width (NFKC-normalised by the parser)
    fake.result = {"slot_label_text": fw, "other_slot_labels": [], "plate_text": scenario["fixture"]["id"],
                   "tier": scenario["tier"], "tier_count": 5, "facings_slots": len(scenario["slots"]),
                   "package_text": "test package", "product_type": "facial toner", "brand": "Cymbal",
                   "search_query": "facial toner", "confidence": 0.86}
    fake.calls = calls
    monkeypatch.setattr(staff, "gemini_analyze", fake)
    staff._analysis_cache.clear()
    return fake


# ------------------------------------------------------------------------------------------------ unit
def test_parse_and_labels(mods):
    staff = mods[0]
    assert staff.parse_slot("A01-203") == ("A01", 2, 3)
    assert staff.parse_slot("\uff21\uff10\uff11\uff0d\uff12\uff10\uff13") == ("A01", 2, 3)  # full-width A01-203
    assert staff.parse_slot("shelf h12 - 5 07") == ("H12", 5, 7)
    assert staff.parse_slot("A-01") is None
    assert staff.parse_slot("RX-105") == ("RX", 1, 5)
    assert staff.slot_id("A01", 2, 3) == "A01-203"
    assert staff.normalize_fixture("A-1", {"A01": {}}) == "A01"
    assert staff.normalize_fixture("A01-203", {"A01": {}}) == "A01"
    assert staff.normalize_fixture("Z99", {"A01": {}}) is None
    assert staff.slot_label(["A01-204", "A01-203"]) == "A01-203–204 (tier 2, positions 3–4 from left)"
    assert staff.slot_label(["A01-501"]) == "A01-501 (tier 5, position 1 from left)"


def test_layout_v2_fixtures(mods):
    staff = mods[0]
    fx = staff.fixture_index("S01")
    assert fx and all(f["tiers"] >= 1 and f["slots_per_tier"] >= 1 for f in fx.values())
    assert any(f.get("type") in ("gondola", "wall") for f in fx.values())


def test_validate_slots(mods, scenario):
    staff = mods[0]
    from fastapi import HTTPException
    f, t = scenario["fixture"], scenario["tier"]
    ok = staff.validate_slots("S01", scenario["slots"])
    assert ok[0]["id"] == f["id"] and ok[1] == t
    bad = [
        [staff.slot_id(f["id"], t, 1), staff.slot_id(f["id"], t, 3)],                       # gap
        [staff.slot_id(f["id"], t, 1), staff.slot_id(f["id"], max(1, t - 1) if t > 1 else t + 1, 2)],  # tiers differ
        [staff.slot_id(f["id"], t, i) for i in range(1, 5)],                                 # > 3
        [staff.slot_id(f["id"], f["tiers"] + 1, 1)],                                         # tier out of range
        [staff.slot_id(f["id"], t, f["slots_per_tier"] + 1)],                                # index out of range
        ["Z98-101"], ["bogus"], [],
    ]
    for b in bad:
        with pytest.raises(HTTPException):
            staff.validate_slots("S01", b)


# ------------------------------------------------------------------------------------------------ API
def test_demo_photos(client, demo, scenario):
    r = client.get("/api/staff/demo-photos")
    assert r.status_code == 200
    ph = r.json()["photos"][0]
    assert ph["id"] == "t01" and ph["fixture_id"] == scenario["fixture"]["id"] and ph["slot_ids"] == scenario["slots"]
    assert client.get(ph["url"]).status_code == 200
    assert client.get("/api/staff/demo-photos/../../etc/passwd").status_code == 404


def test_analyze_reads_slot_label(client, demo, fake_vision, scenario):
    r = client.post("/api/staff/analyze-photo", json={"photo_id": "t01", "store_id": "S01"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["fixture_id"] == scenario["fixture"]["id"] and a["detect_source"] == "slot_label"
    assert a["slot_ids"] == scenario["slots"] and a["slot_id"] == scenario["slots"][0]
    assert a["tier_guess"] == scenario["tier"] and a["slot_label"].startswith(scenario["slots"][0])
    assert a["fixture"]["slots_per_tier"] == scenario["fixture"]["slots_per_tier"]
    assert a["candidates"][0]["id"] == scenario["product_id"] and len(a["candidates"]) >= 2
    assert all("drug_class" in c for c in a["candidates"])
    assert isinstance(a["existing_products"], list)
    n = len(fake_vision.calls)
    assert client.post("/api/staff/analyze-photo", json={"photo_id": "t01", "store_id": "S01"}).json()["cached"]
    assert len(fake_vision.calls) == n


def test_analyze_plate_only_and_unknown(client, fake_vision, scenario):
    b64 = "data:image/jpeg;base64," + base64.b64encode(_jpeg((10, 10, 10))).decode()
    fake_vision.result = {**fake_vision.result, "slot_label_text": "", "plate_text": scenario["fixture"]["id"]}
    a = client.post("/api/staff/analyze-photo", json={"image_base64": b64, "store_id": "S01"}).json()
    assert a["fixture_id"] == scenario["fixture"]["id"] and a["slot_ids"] == [] and a["detect_source"] == "plate"
    assert a["confidence"] <= 0.6
    fake_vision.result = {**fake_vision.result, "slot_label_text": "Z98-999", "plate_text": "Z98"}
    a = client.post("/api/staff/analyze-photo", json={"image_base64": b64, "store_id": "S01"}).json()
    assert a["fixture_id"] is None and a["slot_ids"] == [] and a["confidence"] <= 0.3
    assert a["candidates"], "OCR search should still return candidates"


def test_analyze_vision_failure_falls_back(client, demo, mods, monkeypatch, scenario):
    staff = mods[0]
    staff._analysis_cache.clear()
    monkeypatch.setattr(staff, "gemini_analyze", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    a = client.post("/api/staff/analyze-photo", json={"photo_id": "t01", "store_id": "S01"}).json()
    assert a["vision_error"] and a["fixture_id"] == scenario["fixture"]["id"] and a["slot_ids"] == scenario["slots"]
    assert a["candidates"][0]["id"] == scenario["product_id"]


def test_register_slots_updates_customer(client, demo, mods, scenario):
    staff, inventory = mods
    pid, slots = scenario["product_id"], scenario["slots"]
    before = (inventory.get_availability(pid, "S01")["current"].get("shelf") or {}).get("slot_ids")
    assert before != slots, "target must differ from the seed slots"
    body = {"store_id": "S01", "product_id": pid, "slot_ids": [s.lower() for s in reversed(slots)], "note": "pytest",
            "photo_id": "t01", "staff_name": "Tester"}
    r = client.post("/api/staff/shelf-locations", json=body)
    assert r.status_code == 200, r.text
    loc = r.json()["location"]
    assert loc["slot_ids"] == slots and loc["fixture_id"] == scenario["fixture"]["id"] and loc["tier"] == scenario["tier"]
    assert loc["facings"] == len(slots) and loc["gondola_id"] == loc["fixture_id"] and loc["source"] == "staff"
    assert loc["position"] == int(slots[0][-2:]) and loc["slot_label"].startswith(slots[0])
    assert loc["photo_url"] and loc["photo_url"].startswith("/api/")
    sh = inventory.get_availability(pid, "S01")["current"]["shelf"]
    assert sh["slot_ids"] == slots and sh["fixture_id"] == scenario["fixture"]["id"] and sh["tier"] == scenario["tier"]
    assert sh["photo_url"] == loc["photo_url"]
    ra = client.get(f"/api/products/{pid}/availability?store_id=S01")
    if ra.status_code == 200:
        assert ra.json()["current"]["shelf"]["slot_ids"] == slots
        assert client.get(loc["photo_url"]).status_code == 200
    rg = client.get(f"/api/stores/S01/fixtures/{scenario['fixture']['id']}")
    if rg.status_code == 200:  # front-elevation grid reflects the move
        txt = json.dumps(rg.json())
        cell = next(s for t in rg.json()["tiers"] for s in t["slots"] if s["slot_id"] == slots[0])
        assert pid in json.dumps(cell), txt[:300]
    regs = client.get("/api/staff/registrations?store_id=S01").json()["registrations"]
    assert regs and regs[0]["product_id"] == pid and regs[0]["slot_ids"] == slots and regs[0]["registered_by"] == "Tester"
    li = r.json()["local_inventory"]  # AI Commerce Search Local Inventory push status (local mode → skipped)
    assert li["place_id"] == "S01" and li["product_id"] == pid and li["status"] in ("skipped", "synced", "submitted", "failed")


def test_push_local_inventory_status(mods, monkeypatch):
    staff, _ = mods
    assert staff.push_retail_local_inventory("S01", "p1", "A01", 2, ["A01-203"])["status"] == "skipped"  # local mode
    monkeypatch.setattr(staff, "push_retail_local_inventory", lambda *a: {"status": "synced", "place_id": a[0], "product_id": a[1]})
    assert staff._push_with_timeout("S02", "p2", "A01", 1, None) == {"status": "synced", "place_id": "S02", "product_id": "p2"}
    monkeypatch.setattr(staff, "push_retail_local_inventory", lambda *a: __import__("time").sleep(0.5))
    assert staff._push_with_timeout("S02", "p2", "A01", 1, None, timeout=0.05)["status"] == "submitted"


def test_no_hard_coded_project(mods):
    staff, _ = mods
    src = open(staff.__file__, encoding="utf-8").read()
    assert "nov-11-11-retail-event-demo" not in src and "904006867101" not in src


def test_register_v8_compat_body(client, scenario):
    f, t = scenario["fixture"], scenario["tier"]
    r = client.post("/api/staff/shelf-locations", json={"store_id": "S01", "product_id": scenario["product_id"],
                                                         "gondola_id": f["id"], "tier": t, "position": 1, "staff_name": "t"})
    assert r.status_code == 200, r.text
    assert r.json()["location"]["slot_ids"] == [f"{f['id']}-{t}01"]
    # restore the v9 target for later tests
    client.post("/api/staff/shelf-locations", json={"store_id": "S01", "product_id": scenario["product_id"],
                                                    "slot_ids": scenario["slots"], "staff_name": "t"})


def test_register_validation(client, scenario):
    f, t = scenario["fixture"], scenario["tier"]
    base = {"store_id": "S01", "product_id": scenario["product_id"], "staff_name": "t"}
    assert client.post("/api/staff/shelf-locations", json={**base, "slot_ids": ["Z98-101"]}).status_code == 400
    assert client.post("/api/staff/shelf-locations", json={**base, "slot_ids": [f"{f['id']}-{t}01", f"{f['id']}-{t}03"]}).status_code == 400
    assert client.post("/api/staff/shelf-locations", json={**base, "product_id": "0000", "slot_ids": scenario["slots"]}).status_code == 404
    assert client.post("/api/staff/shelf-locations", json={**base, "store_id": "S99", "slot_ids": scenario["slots"]}).status_code == 400
    assert client.post("/api/staff/shelf-locations", json={**base}).status_code == 400


def test_stock_search_and_inventory_patch(client, scenario):
    pid = scenario["product_id"]
    res = client.get(f"/api/staff/stock-search?q={pid}").json()["results"]
    assert res and res[0]["product"]["id"] == pid
    stores = res[0]["stores"]
    assert [s["store_id"] for s in stores] == ["S01", "S02", "S03"]
    assert all(s["status"] in ("in_stock", "low_stock", "out_of_stock", "not_carried") for s in stores)
    s01 = stores[0]
    assert s01["shelf"]["slot_ids"] == scenario["slots"] and s01["shelf"]["slot_label"].startswith(scenario["slots"][0])
    r = client.patch("/api/staff/inventory", json={"store_id": "S01", "product_id": pid, "quantity": 0, "staff_name": "t"})
    assert r.status_code == 200 and r.json() == {"ok": True, "quantity": 0, "status": "out_of_stock"}
    s01 = client.get(f"/api/staff/stock-search?q={pid}").json()["results"][0]["stores"][0]
    assert s01["quantity"] == 0 and s01["status"] == "out_of_stock"
    assert client.patch("/api/staff/inventory", json={"store_id": "S01", "product_id": pid, "quantity": 2}).json()["status"] == "low_stock"
    assert client.patch("/api/staff/inventory", json={"store_id": "S01", "product_id": pid, "quantity": 12}).json()["status"] == "in_stock"


def test_stock_search_text(client):
    res = client.get("/api/staff/stock-search?q=Cymbal").json()["results"]
    assert res and all(len(x["stores"]) == 3 for x in res)
    assert client.get("/api/staff/stock-search?q=").json() == {"results": []}
