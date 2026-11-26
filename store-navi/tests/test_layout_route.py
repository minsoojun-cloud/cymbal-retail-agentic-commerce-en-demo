"""Walkable grid + A* + nearest-neighbour multi-stop route: never crosses gondolas, entrance → stops → register."""
from __future__ import annotations

import math
import random

import pytest

from app import inventory, layout, stores


def _inside_strict(x, y, r, tol=0.02):
    rx, ry, rw, rh = r
    return rx + tol < x < rx + rw - tol and ry + tol < y < ry + rh - tol


def _assert_no_crossing(lay, poly):
    rects = layout.obstacles(lay)
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / 0.05))
        for k in range(n + 1):
            t = k / n
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            assert 0 <= x <= lay["width"] and 0 <= y <= lay["height"]
            for r in rects:
                assert not _inside_strict(x, y, r), f"route crosses obstacle {r} at ({x:.2f},{y:.2f})"


STORE_IDS = [s["store_id"] for s in stores.load_stores()]
inventory.ensure_loaded()   # tests below read inventory._inv directly


@pytest.mark.parametrize("sid", STORE_IDS)
def test_every_gondola_face_reachable(sid):
    lay = layout.get_layout(sid)
    grid = layout.get_grid(sid)
    ent = grid.snap(lay["entrance"]["x"], lay["entrance"]["y"])
    for g in layout.fixtures(sid):
        for f in g.get("faces") or [g["face"]]:
            ax, ay, _ = layout.face_point(g, f)
            d, path = grid.astar(ent, grid.snap(ax, ay))
            assert path and d < float("inf"), (sid, g["id"], f)


@pytest.mark.parametrize("sid", STORE_IDS)
def test_route_never_crosses_gondolas_and_order(sid):
    rnd = random.Random(7)
    carried = [pid for (s, pid) in inventory._inv if s == sid and inventory.shelf_for(sid, pid)]
    for _ in range(4):
        pids = rnd.sample(carried, 5)
        r = layout.route_for_products(sid, pids)
        lay = layout.get_layout(sid)
        assert [s["no"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1))
        assert len(r["stops"]) == 5
        _assert_no_crossing(lay, r["polyline"])
        # starts at the entrance, ends at the register
        grid = layout.get_grid(sid)
        ex, ey = grid.center(*grid.snap(lay["entrance"]["x"], lay["entrance"]["y"]))
        assert math.hypot(r["polyline"][0][0] - ex, r["polyline"][0][1] - ey) < 0.01
        rx, ry = grid.center(*grid.snap(*layout.register_point(lay)))
        assert math.hypot(r["polyline"][-1][0] - rx, r["polyline"][-1][1] - ry) < 0.01
        # polyline passes every stop, in order
        idx = 0
        for s in r["stops"]:
            while idx < len(r["polyline"]) and math.hypot(r["polyline"][idx][0] - s["x"], r["polyline"][idx][1] - s["y"]) > 0.01:
                idx += 1
            assert idx < len(r["polyline"]), f"stop {s['no']} not on polyline"
        # nearest-neighbour: each stop is the A*-closest of the remaining ones
        cur = grid.snap(lay["entrance"]["x"], lay["entrance"]["y"])
        remaining = [(grid.snap(s["x"], s["y"]), s) for s in r["stops"]]
        for s in r["stops"]:
            dists = {id(t): grid.astar(cur, c)[0] for c, t in remaining}
            best = min(dists.values())
            mine = grid.astar(cur, grid.snap(s["x"], s["y"]))[0]
            assert mine <= best + 1e-6
            remaining = [(c, t) for c, t in remaining if t is not s]
            cur = grid.snap(s["x"], s["y"])
        assert r["length_m"] > 0


def test_counter_stop_for_abuse_control():
    from app import deps

    pid = next(p for (s, p) in inventory._inv if s == "S01" and (deps.to_product(p) or {}).get("abuse_control"))
    r = layout.route_for_products("S01", [pid])
    assert r["stops"][0]["gondola_id"] == "RX" and r["stops"][0]["fixture_id"] == "RX"


def test_empty_route():
    assert layout.route_for_products("S01", []) == {"stops": [], "polyline": layout.route_for_products("S01", [])["polyline"],
                                                  "length_m": layout.route_for_products("S01", [])["length_m"]}


def test_route_stop_fields_v9():
    carried = [pid for (s, pid) in inventory._inv if s == "S01" and inventory.shelf_for("S01", pid)][:3]
    r = layout.route_for_products("S01", carried)
    for st in r["stops"]:
        assert {"no", "product_id", "fixture_id", "slot_ids", "primary_slot", "tier", "x", "y", "pin"} <= set(st)
        sh = inventory.shelf_for("S01", st["product_id"])
        # (staff tests earlier in the session may re-register these shelves in another module instance → compare fixture)
        assert st["fixture_id"] == sh["fixture_id"] and str(st["primary_slot"]).startswith(sh["fixture_id"])


def test_slot_geometry_left_to_right():
    """NN increases left→right as seen by a customer facing the fixture."""
    for f in layout.fixtures("S01"):
        if f.get("is_counter") or f["slots_per_tier"] < 2:
            continue
        p1 = layout.slot_point("S01", layout.slot_id(f["id"], 1, 1))
        p2 = layout.slot_point("S01", layout.slot_id(f["id"], 1, f["slots_per_tier"]))
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        expect = {"S": dx > 0, "N": dx < 0, "E": dy < 0, "W": dy > 0}[f["face"]]
        assert expect, (f["id"], f["face"], p1, p2)


def test_parse_slot():
    assert layout.parse_slot("A01-203") == ("A01", 2, 3)
    assert layout.parse_slot("A-01-512") == ("A-01", 5, 12)
    assert layout.parse_slot("nope") is None
    assert layout.slot_id("H03", 4, 7) == "H03-407"


def test_large_store_route_performance():
    import time

    sid = max(STORE_IDS, key=lambda s: layout.get_layout(s)["width"] * layout.get_layout(s)["height"])
    carried = [pid for (s, pid) in inventory._inv if s == sid and inventory.shelf_for(sid, pid)]
    t = time.time()
    r = layout.route_for_products(sid, random.Random(3).sample(carried, 8))
    assert time.time() - t < 3.0 and len(r["stops"]) == 8
