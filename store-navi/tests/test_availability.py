"""Availability (CONTRACT v8): current store ALWAYS first/fixed, nearby sorted (in-stock first, distance), online link."""
from __future__ import annotations

from app import config, deps, inventory, stores


def test_status_thresholds():
    assert inventory.status_of(0, "IN_STOCK") == "out_of_stock"
    assert inventory.status_of(5, "OUT_OF_STOCK") == "out_of_stock"
    assert inventory.status_of(1, "IN_STOCK") == "low_stock"
    assert inventory.status_of(3, "IN_STOCK") == "low_stock"
    assert inventory.status_of(4, "IN_STOCK") == "in_stock"
    assert inventory.status_of(None, None) == "not_carried"


def test_cold_tablet_scenario(scenarios):
    sc = scenarios["cold_tablet_out_at_S01"]
    av = inventory.get_availability(sc["product_id"], "S01")
    assert av["current"]["store_id"] == "S01"
    assert av["current"]["status"] == "out_of_stock"
    assert av["nearby"][0]["store_id"] == sc["in_stock_at"]
    assert av["nearby"][0]["status"] in ("in_stock", "low_stock")
    assert all("distance_m" in n and "open_now" in n for n in av["nearby"])
    assert all(n["store_id"] != "S01" for n in av["nearby"])
    assert av["online"]["url"] == f"{config.ONLINE_STORE_URL}/?product={sc['product_id']}"
    assert av["online"]["price"] > 0


def test_current_row_present_even_when_not_carried():
    carried = {pid for (sid, pid) in inventory._inv if sid == "S01"}
    pid = next(p for p in deps.get_catalog().items if p not in carried)
    av = inventory.get_availability(pid, "S01")
    assert av["current"] is not None and av["current"]["status"] == "not_carried" and av["current"]["shelf"] is None


def test_nearby_sorted_in_stock_first():
    s01 = stores.get_store("S01")
    for (sid, pid) in list(inventory._inv)[:300]:
        if sid != "S01":
            continue
        av = inventory.get_availability(pid, "S01")
        keys = [(n["status"] not in inventory.IN_STOCK, n["distance_m"]) for n in av["nearby"]]
        assert keys == sorted(keys)
        assert s01


def test_abuse_control_goes_to_counter():
    cat = deps.get_catalog()
    pid = next(p for p in cat.items if deps.to_product(p).get("abuse_control") and inventory.inventory_for("S01", p))
    sh = inventory.shelf_for("S01", pid)
    assert sh["gondola_id"] == "RX" and sh["counter"] is True
    assert "pharmacy counter" in sh["note"]


def test_fever_set_in_stock_at_s01(scenarios):
    for pid in scenarios.get("fever_set_S01") or []:
        av = inventory.get_availability(pid, "S01")
        assert av["current"]["status"] in inventory.IN_STOCK, pid
        assert av["current"]["shelf"], pid


def test_set_quantity_and_shelf_registration_visible_immediately(scenarios):
    pid = (scenarios.get("fever_set_S01") or [None])[0]
    before = inventory.inventory_for("S01", pid)
    r = inventory.set_quantity("S01", pid, 2, by="pytest")
    assert r == {"quantity": 2, "status": "low_stock"}
    assert inventory.get_availability(pid, "S01")["current"]["status"] == "low_stock"
    from app import repository

    from app import layout

    old_sh = inventory.shelf_for("S01", pid)
    fid = old_sh["fixture_id"]
    f = layout.fixture("S01", fid)
    new_slots = [layout.slot_id(fid, 2, 1), layout.slot_id(fid, 2, 2)] if f["slots_per_tier"] >= 2 else [layout.slot_id(fid, 2, 1)]
    row = repository.append_shelf_location({"store_id": "S01", "product_id": pid, "fixture_id": fid, "slot_ids": new_slots,
                                            "tier": 2, "facings": len(new_slots), "gondola_id": fid, "side": f["face"],
                                            "position": 1, "photo_uri": "demo/x.jpg", "source": "staff",
                                            "registered_by": "pytest", "note": ""})
    inventory.record_shelf(row)
    sh = inventory.shelf_for("S01", pid)
    assert sh["fixture_id"] == fid and sh["tier"] == 2 and sh["photo_url"] == "/api/photos/demo/x.jpg"
    assert sh["slot_ids"] == new_slots and sh["primary_slot"] == new_slots[0] and sh["slot_label"].startswith(new_slots[0])
    grid = inventory.fixture_grid("S01", fid)
    cells = {s["slot_id"]: [p["id"] for p in s["products"]] for t in grid["tiers"] for s in t["slots"]}
    assert all(pid in cells[x] for x in new_slots)
    assert all(pid not in cells.get(x, []) for x in old_sh["slot_ids"] if x not in new_slots)  # moved away
    # a fresh full reload (other instance) also sees the staff row + event (latest registered_at wins)
    inventory.invalidate(full=True)
    assert inventory.shelf_for("S01", pid)["tier"] == 2
    assert inventory.inventory_for("S01", pid)["quantity"] == 2
    inventory.set_quantity("S01", pid, before["quantity"], by="pytest")


def test_shelf_object_v9_fields(scenarios):
    pid = scenarios["fever_set_S01"][1]
    sh = inventory.shelf_for("S01", pid)
    for k in ("fixture_id", "gondola_id", "slot_ids", "primary_slot", "tier", "position", "facings", "side", "label",
              "slot_label", "fixture_type", "slot_width_cm", "photo_url"):
        assert k in sh, k
    assert sh["gondola_id"] == sh["fixture_id"] and sh["slot_ids"] and "shelf " in sh["slot_label"]


def test_slot_label_format():
    assert inventory.slot_label(["A01-203"]) == "A01-203 (shelf 2, 3rd from left)"
    assert inventory.slot_label(["A01-203", "A01-204"]) == "A01-203–204 (shelf 2, 3rd–4th from left)"
    assert inventory.slot_label(["A01-211"]) == "A01-211 (shelf 2, 11th from left)"
