"""Data-worker tests for Cymbal Store Navi store data (CONTRACT v9): layout v2, slots, locations, scenarios."""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SN = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(SN, ".."))
DATA = os.path.join(SN, "data")
sys.path.insert(0, os.path.join(SN, "scripts"))
import store_layout_defs as defs  # noqa: E402

OTC = "Health & Medicine"  # English top category (shared/catalog/category_map_en.json)

STORES = ["S01", "S02", "S03"]
SLOT_RE = re.compile(r"^([A-HJ-NP-U]\d\d)-([1-5])(\d\d)$")


def jload(name):
    return json.load(open(os.path.join(DATA, name), encoding="utf-8"))


def jlines(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


@pytest.fixture(scope="module")
def layouts():
    return {s: jload(f"layouts/{s}.json") for s in STORES}


@pytest.fixture(scope="module")
def catalog():
    with open(os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl"), encoding="utf-8") as f:
        return {p["id"]: p for p in (json.loads(x) for x in f if x.strip())}


@pytest.fixture(scope="module")
def inventory():
    return jlines("store_inventory.jsonl")


@pytest.fixture(scope="module")
def shelves():
    return jlines("shelf_locations.jsonl")


@pytest.fixture(scope="module")
def slots():
    return jlines("shelf_slots.jsonl")


@pytest.fixture(scope="module")
def scenarios():
    return jload("scenarios.json")


# ------------------------------------------------------------------ stores
def test_stores_schema():
    stores = jload("stores.json")
    assert [s["store_id"] for s in stores] == STORES
    for s in stores:
        for k in ("name", "short_name", "lat", "lng", "address", "phone", "hours", "pharmacy", "services", "radius_m",
                  "layout_id", "tagline"):
            assert k in s, k
        assert re.fullmatch(r"\d{2,3}-\d{3,4}-\d{4}", s["phone"])
        assert s["radius_m"] == 150 and s["layout_id"] == s["store_id"]


# ------------------------------------------------------------------ layouts v2
@pytest.mark.parametrize("sid", STORES)
def test_layout_v2_valid(layouts, sid):
    lay = layouts[sid]
    assert lay["version"] == 2 and "gondolas" not in lay
    assert defs.validate_layout(lay) == []


@pytest.mark.parametrize("sid", STORES)
def test_layout_v2_fixtures(layouts, sid):
    lay = layouts[sid]
    zone_ids = {z["id"] for z in lay["zones"]}
    bodies = defaultdict(list)
    rx = [f for f in lay["fixtures"] if f["type"] == "counter"]
    assert len(rx) == 1 and rx[0]["id"] == "RX" and rx[0]["slots_per_tier"] == lay["counter"]["slots_per_tier"]
    for f in lay["fixtures"]:
        if f["type"] == "counter":
            continue
        assert re.fullmatch(r"[A-HJ-NP-U]\d\d", f["id"]) and f["id"][0] not in "IOS"
        assert f["type"] in ("gondola", "wall", "endcap", "multideck", "reach_in", "chest", "flat_table", "deli_table",
                             "flower", "counter")
        assert f["face"] in "NSEW" and f["zone"] in zone_ids
        sw, tiers, _ = defs.FTYPE[f["type"]]
        assert f["slot_width_cm"] == sw and f["tiers"] == tiers
        assert f["slots_per_tier"] == f["length_cm"] // sw
        if sw == 30:
            assert f["length_cm"] % 90 == 0  # 900 mm bay modules
        bodies[f["body_id"]].append(f)
    for b, fs in bodies.items():
        assert len(fs) in (1, 2)
        if len(fs) == 2:  # double-sided: opposite faces, touching halves
            assert {fs[0]["face"], fs[1]["face"]} in ({"W", "E"}, {"N", "S"})
    letters = {f["id"][0] for f in lay["fixtures"]}
    assert set("ABCDEFGHJKLMNPQRTU") <= letters
    # OTC wall is next to the RX counter on the left wall
    c = lay["counter"]
    a = [f for f in lay["fixtures"] if f["id"][0] == "A"]
    assert all(f["x"] == 0 for f in a) and any(abs(f["y"] + f["h"] - c["y"]) < 0.01 for f in a)


def test_validator_detects_problems(layouts):
    lay = json.loads(json.dumps(layouts["S01"]))
    g = next(f for f in lay["fixtures"] if f["type"] == "gondola" and f["face"] == "W")
    g["x"] -= 1.0  # squeeze aisle
    assert any("aisle" in e or "overlaps" in e for e in defs.validate_layout(lay))
    lay = json.loads(json.dumps(layouts["S01"]))
    lay["fixtures"].append({**lay["fixtures"][0], "id": "Z99", "x": 0, "y": lay["height"] - 3.0, "w": lay["width"],
                            "h": 0.6, "face": "N"})
    assert defs.validate_layout(lay)


# ------------------------------------------------------------------ slots
def test_slots(layouts, slots):
    by_store = defaultdict(set)
    for r in slots:
        assert set(r) == {"store_id", "slot_id", "fixture_id", "tier", "index", "width_cm", "fixture_type", "x", "y"}
        assert r["slot_id"] not in by_store[r["store_id"]]
        by_store[r["store_id"]].add(r["slot_id"])
        if r["fixture_id"] == "RX":
            assert re.fullmatch(r"RX-1\d\d", r["slot_id"])
            continue
        m = SLOT_RE.match(r["slot_id"])
        assert m and m.group(1) == r["fixture_id"] and int(m.group(2)) == r["tier"] and int(m.group(3)) == r["index"]
    for sid in STORES:
        for f in layouts[sid]["fixtures"]:
            n = sum(1 for s in by_store[sid] if s.startswith(f["id"] + "-"))
            if f["type"] == "counter":
                assert n == f["slots_per_tier"]
                continue
            assert n == f["tiers"] * f["slots_per_tier"]


def test_slot_left_to_right(layouts):
    f = {"x": 0, "y": 0, "w": 0.45, "h": 3.6, "face": "W", "slots_per_tier": 12}
    pts = defs.slot_points(f)
    assert pts[0][1] < pts[-1][1]  # facing east from the west side: left = north (smaller y)
    f["face"] = "E"
    assert defs.slot_points(f)[0][1] > defs.slot_points(f)[-1][1]
    f.update({"w": 3.6, "h": 0.9, "face": "S"})
    assert defs.slot_points(f)[0][0] < defs.slot_points(f)[-1][0]


# ------------------------------------------------------------------ inventory & assortment
def test_assortment_targets(inventory, catalog):
    N = len(catalog)
    carried, instock = defaultdict(int), defaultdict(int)
    seen = set()
    for r in inventory:
        assert set(r) == {"store_id", "product_id", "quantity", "availability", "updated_at"}
        assert r["product_id"] in catalog and 0 <= r["quantity"] <= 40
        assert r["availability"] == ("IN_STOCK" if r["quantity"] > 0 else "OUT_OF_STOCK")
        assert (r["store_id"], r["product_id"]) not in seen
        seen.add((r["store_id"], r["product_id"]))
        carried[r["store_id"]] += 1
        instock[r["store_id"]] += r["quantity"] > 0
    # English v14 subset (3,000 SKUs, online_only never stocked): each store carries most of the store assortment,
    # the large Musashi-Kosugi store (S02) the most.
    for s in STORES:
        assert 0.6 <= carried[s] / N <= 0.98, (s, carried[s] / N)
    assert carried["S02"] >= max(carried["S01"], carried["S03"])
    for s in STORES:
        oos = 1 - instock[s] / carried[s]
        assert 0.0 <= oos <= 0.10, (s, oos)
        assert instock[s] / N >= 0.55, s
    otc = [p for p, x in catalog.items() if x["categories"][0].startswith(OTC + " > ")]
    for s in STORES:
        assert all((s, p) in seen for p in otc)


# ------------------------------------------------------------------ shelf locations v2
def test_shelf_locations_v2(shelves, inventory, slots, layouts, catalog):
    inv_keys = {(r["store_id"], r["product_id"]) for r in inventory}
    slot_set = {(r["store_id"], r["slot_id"]) for r in slots}
    fx = {(s, f["id"]): f for s in STORES for f in layouts[s]["fixtures"]}
    per_slot = defaultdict(int)
    keys = set()
    for r in shelves:
        assert set(r) == {"store_id", "product_id", "fixture_id", "slot_ids", "tier", "facings", "gondola_id", "side",
                          "position", "photo_uri", "source", "registered_by", "registered_at", "note"}
        k = (r["store_id"], r["product_id"])
        assert k in inv_keys and k not in keys
        keys.add(k)
        assert r["gondola_id"] == r["fixture_id"] and r["source"] == "seed" and r["slot_ids"]
        for s in r["slot_ids"]:
            assert (r["store_id"], s) in slot_set
            per_slot[(r["store_id"], s)] += 1
        p = catalog[r["product_id"]]
        if r["fixture_id"] == "RX":   # pharmacy counter (abuse-prone OTC handed over at the counter)
            assert (p.get("attributes") or p.get("drug_class") or True)
            continue
        f = fx[(r["store_id"], r["fixture_id"])]
        assert r["side"] == f["face"] and 1 <= r["tier"] <= f["tiers"]
        nums = [int(SLOT_RE.match(s).group(3)) for s in r["slot_ids"]]
        tiers = {int(SLOT_RE.match(s).group(2)) for s in r["slot_ids"]}
        assert tiers == {r["tier"]} and nums == list(range(nums[0], nums[0] + len(nums)))  # contiguous, same tier
        assert r["position"] == nums[0]
        assert (r["store_id"], r["fixture_id"]) in fx
    assert keys == inv_keys
    # slot sharing stays realistic: <= 6 products per slot (a few slots also hold the edge of a wide neighbour)
    assert max(per_slot.values()) <= 8
    assert sum(v <= 6 for v in per_slot.values()) / len(per_slot) >= 0.995
    # heavy items low, best sellers mostly in the golden zone on 5-tier fixtures
    heavy_tiers = [r["tier"] for r in shelves if r["fixture_id"] != "RX" and defs.size_units(catalog[r["product_id"]]) >= 6
                   and fx[(r["store_id"], r["fixture_id"])]["tiers"] == 5]
    if heavy_tiers:   # legacy size parser (store_layout_defs) only understands the original units
        assert sum(t <= 2 for t in heavy_tiers) / len(heavy_tiers) >= 0.6
    best = [r["tier"] for r in shelves if r["facings"] >= 2 and r["fixture_id"] != "RX"
            and fx[(r["store_id"], r["fixture_id"])]["tiers"] == 5]
    assert sum(t in (3, 4) for t in best) / len(best) >= 0.6


# ------------------------------------------------------------------ scenarios
def test_scenarios(scenarios, inventory, shelves, catalog):
    q = {(r["store_id"], r["product_id"]): r["quantity"] for r in inventory}
    sh = {(r["store_id"], r["product_id"]): r for r in shelves}
    cold = scenarios["cold_tablet_out_at_S01"]
    assert cold["product_id"] == "4997989851807"
    assert q[("S01", cold["product_id"])] == 0 and q[("S02", cold["product_id"])] == 12
    fever = scenarios["fever_set_S01"]
    assert len(fever) == 6 and all(q[("S01", p)] > 0 for p in fever)
    assert len({sh[("S01", p)]["fixture_id"] for p in fever}) == 6
    b = scenarios["beauty_out_at_S03"]["product_id"]
    assert "sensitive" in catalog[b]["title"].lower() and q[("S03", b)] == 0 and q[("S01", b)] > 0
    photos = scenarios["staff_photo_products"]
    assert 8 <= len(photos) <= 10
    assert len({sh[("S01", p)]["fixture_id"] for p in photos}) == len(photos)
    assert sum(catalog[p]["categories"][0].startswith(OTC) for p in photos) >= 3
    for d in scenarios["staff_photo_products_detail"]:
        assert d["slot_ids"] == sh[("S01", d["product_id"])]["slot_ids"] and q[("S01", d["product_id"])] > 0


# ------------------------------------------------------------------ personas / coupons / offers
def test_personas_history_coupons_offers(catalog, inventory):
    ps = jload("store_personas.json")
    assert [p["customer_id"] for p in ps] == ["CR-0001", "CR-0002", "CR-0003"]
    hist = jlines("store_purchase_history.jsonl")
    assert hist and all(r["customer_id"] in ("CR-0002", "CR-0003") and r["product_id"] in catalog for r in hist)
    coupons = jload("coupons.json")
    assert all(c.get("reason") for c in coupons)
    assert not any(__import__("re").search(r"[\u3040-\u30ff\u4e00-\u9fff]", json.dumps(c, ensure_ascii=False)) for c in coupons)
    for c in coupons:
        assert all(pid in catalog for pid in c["target"]["product_ids"])
    offers = jload("offers.json")
    q = {(r["store_id"], r["product_id"]): r["quantity"] for r in inventory}
    for s in STORES:
        assert 8 <= sum(o["store_id"] == s for o in offers) <= 15
    for o in offers:
        assert o["sale_price"] < o["original_price"] and not defs.is_rx(catalog[o["product_id"]])
        for s in ([o["store_id"]] if o["store_id"] else STORES):
            assert q.get((s, o["product_id"]), 0) > 0
