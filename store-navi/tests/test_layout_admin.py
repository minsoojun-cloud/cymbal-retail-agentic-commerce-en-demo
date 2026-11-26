import json
import pytest
from app import layout_admin


@pytest.fixture
def sample_layout():
    return {
        "store_id": "S01",
        "version": 2,
        "width": 70.0,
        "height": 42.0,
        "zones": [
            {"id": "Z-A1", "label": "Medicine", "color": "#e8f0fe", "auto_rect": True, "rect": [0, 0, 10, 10]},
            {"id": "Z-H1", "label": "Food", "color": "#f1f8e9", "auto_rect": True, "rect": [10, 0, 10, 10]},
        ],
        "counter": {
            "id": "RX",
            "label": "Pharmacy",
            "x": 0, "y": 20, "w": 1.2, "h": 3.6,
            "tiers": 1, "slots_per_tier": 5, "slot_width_cm": 30, "face": "E"
        },
        "fixtures": [
            {
                "id": "A01",
                "uid": "S01-F0001",
                "type": "wall",
                "zone": "Z-A1",
                "label": "Cold & Flu",
                "x": 1.0, "y": 2.0, "w": 0.6, "h": 3.0,
                "face": "E",
                "tiers": 3,
                "slot_width_cm": 30,
                "slots_per_tier": 10,
                "tier_slots": {"2": 8},
                "disabled_slots": ["A01-101", "A01-102"],
            },
            {
                "id": "H03",
                "uid": "S01-F0002",
                "type": "gondola",
                "zone": "Z-H1",
                "label": "Rice",
                "x": 12.0, "y": 5.0, "w": 0.6, "h": 3.6,
                "face": "W",
                "tiers": 2,
                "slot_width_cm": 30,
                "slots_per_tier": 12,
                "tier_slots": {},
                "disabled_slots": [],
            },
        ],
    }


def test_slot_universe(sample_layout):
    univ = layout_admin.slot_universe(sample_layout)
    # Tier 1 of A01 has 10 slots minus 2 disabled = 8 slots (A01-103 .. A01-110)
    assert "A01-101" not in univ
    assert "A01-102" not in univ
    assert "A01-103" in univ
    assert "A01-110" in univ

    # Tier 2 of A01 has tier_slots = 8 slots (A01-201 .. A01-208)
    assert "A01-208" in univ
    assert "A01-209" not in univ

    # Tier 3 of A01 has default 10 slots
    assert "A01-310" in univ
    assert "A01-311" not in univ

    # Counter RX slots (5 slots)
    assert "RX-101" in univ
    assert "RX-105" in univ
    assert "RX-106" not in univ


def test_auto_compute_zones(sample_layout):
    updated = layout_admin.auto_compute_zones(sample_layout)
    z_a1 = next(z for z in updated if z["id"] == "Z-A1")
    # A01 is at x=1.0, y=2.0, w=0.6, h=3.0 -> min_x=1.0, min_y=2.0, max_x=1.6, max_y=5.0
    # with 0.2 pad: x1=0.8, y1=1.8, w=1.0, h=3.4
    assert z_a1["rect"] == [0.8, 1.8, 1.0, 3.4]


def test_diff_and_impact(sample_layout):
    import copy

    new_lay = copy.deepcopy(sample_layout)
    # 1. Rename H03 -> H09 (keeps same uid S01-F0002)
    new_lay["fixtures"][1]["id"] = "H09"
    # 2. Disable slot A01-103
    new_lay["fixtures"][0]["disabled_slots"].append("A01-103")
    # 3. Add new fixture B01
    new_lay["fixtures"].append({
        "id": "B01",
        "uid": "S01-F0003",
        "type": "gondola",
        "zone": "Z-A1",
        "label": "Cosmetics",
        "x": 20.0, "y": 10.0, "w": 0.6, "h": 2.4,
        "face": "E", "tiers": 1, "slots_per_tier": 8,
        "slot_width_cm": 30,
    })

    diff_res = layout_admin.diff(sample_layout, new_lay)
    assert len(diff_res["added_fixtures"]) == 1
    assert diff_res["added_fixtures"][0]["id"] == "B01"
    assert len(diff_res["renamed_fixtures"]) == 1
    assert diff_res["renamed_fixtures"][0]["old"]["id"] == "H03"
    assert diff_res["renamed_fixtures"][0]["new"]["id"] == "H09"

    # Slot map checks
    assert diff_res["slot_map"]["H03-101"] == "H09-101"
    assert diff_res["slot_map"]["A01-103"] is None

    # Impact checks with synthetic shelf rows
    shelf_rows = [
        # Product 1: in A01-103 -> should become unassigned
        {
            "store_id": "S01", "product_id": "P01", "fixture_id": "A01",
            "slot_ids": ["A01-103"], "tier": 1, "registered_at": "2026-11-12T00:00:00Z"
        },
        # Product 2: in H03-101 -> should be reassigned to H09-101
        {
            "store_id": "S01", "product_id": "P02", "fixture_id": "H03",
            "slot_ids": ["H03-101"], "tier": 1, "registered_at": "2026-11-12T00:00:00Z"
        },
        # Product 3: multi-facing in A01-104 and A01-105 -> unaffected
        {
            "store_id": "S01", "product_id": "P03", "fixture_id": "A01",
            "slot_ids": ["A01-104", "A01-105"], "tier": 1, "registered_at": "2026-11-12T00:00:00Z"
        },
    ]

    imp = layout_admin.impact("S01", diff_res, shelf_rows)
    assert imp["unassigned_count"] == 1
    assert imp["unassigned_products"][0]["product_id"] == "P01"
    assert imp["reassigned_count"] == 1
    assert imp["reassigned_products"][0]["product_id"] == "P02"
    assert imp["reassigned_products"][0]["new_slots"] == ["H09-101"]
    assert imp["unaffected_count"] == 1


def test_validate():
    bad_lay = {
        "width": 50, "height": 30,
        "fixtures": [
            {"id": "bad_id_123", "x": 0, "y": 0, "w": 1, "h": 1, "tiers": 2},
            {"id": "A01", "x": -5, "y": 0, "w": 1, "h": 1, "tiers": 2},  # out of bounds
            {"id": "A02", "x": 10, "y": 10, "w": 2, "h": 2, "tiers": 10}, # bad tiers
            {"id": "A03", "x": 20, "y": 20, "w": 2, "h": 2, "tiers": 3},
            {"id": "A03", "x": 20, "y": 20, "w": 2, "h": 2, "tiers": 3}, # duplicate
        ]
    }
    errs = layout_admin.validate(bad_lay)
    assert any("Duplicate" in e for e in errs)
    assert any("bad_id_123" in e for e in errs)
    assert any("outside the store bounds" in e for e in errs)
    assert any("tiers" in e for e in errs)
