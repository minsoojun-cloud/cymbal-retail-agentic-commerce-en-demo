"""CONTRACT v12 — Shelf Manager screen (floor plan → shelves → shelf numbers): API-level scenarios in local mode.

Covers: /admin page served, shelf-number validation (format / duplicate / unnumbered), renaming a shelf carries its
products to the new number, deleting a shelf makes its products unassigned (confirmation required), drawn shelves get a
permanent uid + slots, staff re-registration clears the error, and rollback restores products.
Never touches the seed data dir (all writes go to a temp SN_STATE_DIR)."""
import copy
import hashlib
import importlib
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SN = Path(__file__).resolve().parents[1]
SEED_FILES = ["layouts/S01.json", "shelf_slots.jsonl", "shelf_locations.jsonl"]


def _digest(data_dir: Path) -> dict:
    return {f: hashlib.md5((data_dir / f).read_bytes()).hexdigest() for f in SEED_FILES if (data_dir / f).exists()}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    state = tmp_path_factory.mktemp("sn_v12_state")
    old = {k: os.environ.get(k) for k in ["DATA_MODE", "SEARCH_MODE", "APP_MODE", "SN_STATE_DIR", "RETAIL_LOCAL_INVENTORY", "STAFF_PREWARM"]}
    os.environ.update({"DATA_MODE": "local", "SEARCH_MODE": "local", "APP_MODE": "staff", "SN_STATE_DIR": str(state),
                       "RETAIL_LOCAL_INVENTORY": "0", "STAFF_PREWARM": "0"})
    if str(SN) not in sys.path:
        sys.path.insert(0, str(SN))
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]
    yield state
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[m]


@pytest.fixture(scope="module")
def client(env):
    main = importlib.import_module("app.main")
    c = TestClient(main.app)
    c.cookies.set("sn_role", "manager")
    return c


@pytest.fixture(scope="module")
def seed_digest(env):
    from app import config
    return config.data_dir(), _digest(config.data_dir())


def _editor(client, sid="S01"):
    r = client.get(f"/api/admin/stores/{sid}/editor")
    assert r.status_code == 200, r.text
    return r.json()


def _shelf_with_products(ed):
    counts = ed["fixture_product_counts"]
    for f in ed["layout"]["fixtures"]:
        if f.get("type") != "counter" and counts.get(f["id"], 0) >= 2:
            return f, counts[f["id"]]
    pytest.skip("no shelf with registered products in seed data")


def _publish(client, draft, confirm=False, sid="S01"):
    assert client.put(f"/api/admin/stores/{sid}/draft", json={"draft": draft}).status_code == 200
    ver = _editor(client, sid)["current_version"]
    return client.post(f"/api/admin/stores/{sid}/publish", json={"base_version": ver, "by": "Test Manager",
                                                                 "confirm_unassigned": confirm})


def test_admin_page_served(client, seed_digest):
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "Shelf Manager" in r.text and 'lang="en"' in r.text and "/static/admin/js/admin.js" in r.text
    assert client.get("/static/admin/js/floorplan.js").status_code == 200
    # the v11 tab is gone from the staff app, replaced by a link
    staff = client.get("/").text
    assert 'id="tab-admin"' not in staff and 'href="/admin/"' in staff


def test_editor_has_counts_and_unassigned(client):
    ed = _editor(client)
    assert isinstance(ed["fixture_product_counts"], dict) and ed["fixture_product_counts"]
    assert "unassigned_items" in ed
    assert ed["layout"]["fixtures"] and all(f.get("uid") for f in ed["layout"]["fixtures"])


def test_validation_unnumbered_duplicate_format(client):
    lay = copy.deepcopy(_editor(client)["layout"])
    fx = [f for f in lay["fixtures"] if f.get("type") != "counter"]
    fx[0]["id"] = ""
    fx[1]["id"] = fx[2]["id"]
    fx[3]["id"] = "a-1"
    r = client.post("/api/admin/stores/S01/draft/preview", json={"draft": lay}).json()
    assert r["valid"] is False
    joined = " ".join(r["errors"])
    assert "no shelf number" in joined and "Duplicate" in joined and "a-1" in joined
    client.delete("/api/admin/stores/S01/draft")


def test_rename_carries_products(client, seed_digest):
    ed = _editor(client)
    lay = copy.deepcopy(ed["layout"])
    f, n = _shelf_with_products(ed)
    old_id = f["id"]
    used = {x["id"] for x in lay["fixtures"]}
    new_id = next(f"Q{i:02d}" for i in range(1, 100) if f"Q{i:02d}" not in used)
    next(x for x in lay["fixtures"] if x["uid"] == f["uid"])["id"] = new_id

    pv = client.post("/api/admin/stores/S01/draft/preview", json={"draft": lay}).json()
    assert pv["valid"], pv["errors"]
    assert [(x["old"]["id"], x["new"]["id"]) for x in pv["diff"]["renamed_fixtures"]] == [(old_id, new_id)]
    assert pv["impact"]["new_unassigned_count"] == 0
    assert pv["impact"]["reassigned_count"] >= n

    r = _publish(client, lay)
    assert r.status_code == 200, r.text
    ed2 = _editor(client)
    assert ed2["fixture_product_counts"].get(new_id, 0) >= n
    assert ed2["fixture_product_counts"].get(old_id, 0) == 0
    assert ed2["unassigned_count"] == ed["unassigned_count"]
    # rename back for the next tests
    lay2 = copy.deepcopy(ed2["layout"])
    next(x for x in lay2["fixtures"] if x["uid"] == f["uid"])["id"] = old_id
    assert _publish(client, lay2).status_code == 200
    assert _editor(client)["fixture_product_counts"].get(old_id, 0) >= n


def test_delete_shelf_makes_products_unassigned_then_staff_reassigns(client):
    ed = _editor(client)
    f, n = _shelf_with_products(ed)
    lay = copy.deepcopy(ed["layout"])
    lay["fixtures"] = [x for x in lay["fixtures"] if x["uid"] != f["uid"]]

    pv = client.post("/api/admin/stores/S01/draft/preview", json={"draft": lay}).json()
    assert pv["valid"], pv["errors"]
    assert pv["impact"]["new_unassigned_count"] == n
    # confirmation is mandatory
    r = _publish(client, lay, confirm=False)
    assert r.status_code == 400 and "unassigned" in r.json()["detail"]
    r = _publish(client, lay, confirm=True)
    assert r.status_code == 200, r.text
    assert r.json()["unassigned_count"] == n

    un = client.get("/api/staff/unassigned?store_id=S01").json()
    assert un["count"] >= n
    item = next(i for i in un["items"] if i["previous_fixture_id"] == f["id"])
    assert item["previous_slot_ids"]
    # customer-facing shelf says the location is being checked (ask staff)
    from app import inventory
    sh = inventory.shelf_for("S01", item["product_id"])
    assert sh["unassigned"] is True and sh["label"] and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", sh["label"])

    # staff re-registers it on another shelf (no photo) → error clears for that product
    target = next(x for x in lay["fixtures"] if x.get("type") == "gondola")
    rr = client.post("/api/staff/shelf-locations", json={"store_id": "S01", "product_id": item["product_id"],
                                                "slot_ids": [f"{target['id']}-101"], "staff_name": "Sato",
                                                "reassign": True})
    assert rr.status_code == 200, rr.text
    un2 = client.get("/api/staff/unassigned?store_id=S01").json()
    assert all(i["product_id"] != item["product_id"] for i in un2["items"])
    assert un2["count"] == un["count"] - 1

    # rollback to the version before the deletion restores the remaining products
    vers = client.get("/api/admin/stores/S01/versions").json()["versions"]
    before = sorted(v["version"] for v in vers)[-2]
    rb = client.post("/api/admin/stores/S01/rollback", json={"to_version": before, "by": "Test Manager"})
    assert rb.status_code == 200, rb.text
    assert rb.json()["restored_products_count"] == n - 1
    assert client.get("/api/staff/unassigned?store_id=S01").json()["count"] == un2["count"] - (n - 1)
    assert any(x["id"] == f["id"] for x in _editor(client)["layout"]["fixtures"])


def test_drawn_shelf_gets_uid_and_slots(client):
    ed = _editor(client)
    lay = copy.deepcopy(ed["layout"])
    used = {x["id"] for x in lay["fixtures"]}
    nid = next(f"W{i:02d}" for i in range(1, 100) if f"W{i:02d}" not in used)
    # an empty 2.7 m x 0.6 m spot near the top wall is not guaranteed; place it on the floor and allow overlap
    lay["fixtures"].append({"uid": "NEW-test", "id": "", "type": "gondola", "zone": "", "label": "", "x": 30.0,
                            "y": 1.2, "w": 2.7, "h": 0.6, "face": "S", "length_cm": 270, "tiers": 4,
                            "slot_width_cm": 30, "slots_per_tier": 9, "tier_slots": {}, "disabled_slots": []})
    pv = client.post("/api/admin/stores/S01/draft/preview", json={"draft": lay}).json()
    assert not pv["valid"] and any("no shelf number" in e for e in pv["errors"])
    lay["fixtures"][-1]["id"] = nid
    pv = client.post("/api/admin/stores/S01/draft/preview", json={"draft": lay}).json()
    assert pv["valid"], pv["errors"]
    assert [x["id"] for x in pv["diff"]["added_fixtures"]] == [nid]
    assert _publish(client, lay).status_code == 200
    ed2 = _editor(client)
    nf = next(x for x in ed2["layout"]["fixtures"] if x["id"] == nid)
    assert nf["uid"].startswith("S01-F")
    g = client.get(f"/api/stores/S01/fixtures/{nid}").json()
    assert len(g["tiers"]) == 4 and len(g["tiers"][0]["slots"]) == 9
    assert g["tiers"][0]["slots"][0]["slot_id"].startswith(f"{nid}-")
    # zone naming
    lay3 = copy.deepcopy(ed2["layout"])
    lay3["zones"].append({"id": "Z-U01", "label": "Cosmetics", "color": "#fce4ec", "rect": [0, 0, 0, 0], "auto_rect": True})
    next(x for x in lay3["fixtures"] if x["id"] == nid)["zone"] = "Z-U01"
    assert _publish(client, lay3).status_code == 200
    z = next(z for z in _editor(client)["layout"]["zones"] if z["id"] == "Z-U01")
    assert z["label"] == "Cosmetics" and z["rect"][2] > 2.5


def test_seed_data_untouched(client, seed_digest):
    data_dir, before = seed_digest
    assert _digest(data_dir) == before


def test_layout_version_endpoint_tracks_publish(client):
    """CONTRACT v13: the staff app polls this to pick up Shelf Manager changes immediately."""
    v = client.get("/api/stores/S01/layout/version").json()
    ed = _editor(client)
    assert v["version"] == ed["current_version"] == client.get("/api/stores/S01/layout").json()["version"]
    lay = copy.deepcopy(ed["layout"])
    lay["fixtures"][0]["label"] = (lay["fixtures"][0].get("label") or "") + " (v13)"
    assert _publish(client, lay).status_code == 200
    v2 = client.get("/api/stores/S01/layout/version").json()
    assert v2["version"] == v["version"] + 1
    assert client.get("/api/stores/S01/layout").json()["fixtures"][0]["label"].endswith(" (v13)")
    assert client.get("/api/stores/S99/layout/version").status_code == 404
    staff = client.get("/").text
    assert 'id="confirm-map"' in staff and 'id="map-sheet"' in staff and 'id="reg-store-banner"' in staff
