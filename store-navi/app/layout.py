"""Store layouts + walkable grid (0.5 m) + A* + nearest-neighbour multi-stop routes (entrance → stops → register).

Layout schema: metres, origin top-left, y grows downward.
- v2 (CONTRACT v9): `fixtures:[{id, body_id, type, zone, label, x, y, w, h, face, length_cm, tiers, slot_width_cm,
  slots_per_tier, categories}]` — one face per fixture, slot id `<fixture>-<tier><NN>` (NN left→right as seen by a
  customer facing the fixture).
- v1 (CONTRACT v8): `gondolas:[{id, x, y, w, h, tiers, faces:[…], categories}]` — normalized to fixtures (face = faces[0]).
Walkable = inside the store bounds and not inside any fixture / register / counter rect.
"""
from __future__ import annotations

import heapq
import math
import re
import threading
from typing import Any

from . import repository, stores

RES = 0.5            # routing grid resolution (m)
INFLATE = 0.2        # obstacle clearance for routing cells
FINE = 0.1           # line-of-sight raster resolution (m)
LOS_INFLATE = 0.15   # clearance used when smoothing (line of sight)
FACE_OFFSET = 0.45   # distance of the access point from the fixture face
V1_POSITIONS = 6     # v1: nominal facings per gondola face
FLAT_TYPES = {"flat_table", "chest", "deli_table", "flower"}

_lock = threading.Lock()
_layouts: dict[str, dict] | None = None
_grids: dict[str, "Grid"] = {}
_fixtures: dict[str, dict] = {}
_slots: dict[str, dict[str, dict]] | None = None


def _load() -> dict[str, dict]:
    global _layouts
    with _lock:
        if _layouts is None:
            _layouts = repository.load_layouts([s["store_id"] for s in stores.load_stores()])
        return _layouts


def reload() -> None:
    global _layouts, _slots
    with _lock:
        _layouts = None
        _slots = None
        _grids.clear()
        _fixtures.clear()


def get_layout(store_id: str) -> dict | None:
    return _load().get(store_id)


# ---------------------------------------------------------------------------------------------- fixtures / slots
_SLOT_RE = re.compile(r"^(.+)-(\d)(\d{2})$")


def parse_slot(sid: str) -> tuple[str, int, int] | None:
    """'A01-203' → ('A01', 2, 3); 'A-01-305' (v1 compat) → ('A-01', 3, 5)."""
    m = _SLOT_RE.match(str(sid or ""))
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


def slot_id(fixture_id: str, tier: int, index: int) -> str:
    return f"{fixture_id}-{int(tier)}{int(index):02d}"


def _normalize(f: dict, v1: bool) -> dict:
    x, y, w, h = float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"])
    ftype = f.get("type") or "gondola"
    if v1:
        faces = [c for c in (f.get("faces") or []) if c in "NSEW"] or ["S"]
        face = faces[0]
    else:
        face = f.get("face") if f.get("face") in ("N", "S", "E", "W") else "S"
        faces = [face]
    length_m = w if face in "NS" else h
    width_cm = int(f.get("slot_width_cm") or (60 if ftype in FLAT_TYPES else 30))
    spt = int(f.get("slots_per_tier") or (V1_POSITIONS if v1 else max(1, round(length_m * 100 / width_cm))))
    tiers = int(f.get("tiers") or (1 if ftype in FLAT_TYPES else 5))
    return {**f, "id": f["id"], "type": ftype, "x": x, "y": y, "w": w, "h": h, "face": face, "faces": faces,
            "tiers": tiers, "slots_per_tier": spt, "slot_width_cm": width_cm,
            "length_cm": int(f.get("length_cm") or round(length_m * 100)), "label": f.get("label") or "",
            "zone": f.get("zone") or "", "categories": f.get("categories") or [], "body_id": f.get("body_id") or f["id"],
            "uid": f.get("uid") or f["id"], "tier_slots": f.get("tier_slots") or {},
            "disabled_slots": f.get("disabled_slots") or []}


def _fixture_index(store_id: str) -> dict[str, dict]:
    lay = get_layout(store_id)
    if not lay:
        return {}
    ent = _fixtures.get(store_id)
    if ent is None or ent["lay"] is not lay:
        v1 = "fixtures" not in lay
        rows = lay.get("gondolas") if v1 else lay.get("fixtures")
        idx: dict[str, dict] = {}
        rx_row = None
        for f in rows or []:
            if f.get("id") == "RX":
                rx_row = f
                continue
            n = _normalize(f, v1)
            idx[n["id"]] = n
        c = lay.get("counter")
        if c or rx_row:
            src = {**(c or {}), **(rx_row or {})}
            spt = int(src.get("slots_per_tier") or (c or {}).get("slots_per_tier") or 1)
            idx["RX"] = {"id": "RX", "type": "counter", "label": src.get("label") or "Pharmacy counter",
                         "x": float(src["x"]), "y": float(src["y"]), "w": float(src["w"]), "h": float(src["h"]),
                         "face": src.get("face") if src.get("face") in ("N", "S", "E", "W") else "S",
                         "faces": ["S", "N", "E", "W"], "tiers": int(src.get("tiers") or 1), "slots_per_tier": spt,
                         "slot_width_cm": int(src.get("slot_width_cm") or 60),
                         "length_cm": int(src.get("length_cm") or float(src["w"]) * 100), "zone": "RX",
                         "categories": [], "body_id": "RX", "is_counter": True}
        ent = _fixtures[store_id] = {"lay": lay, "idx": idx}
    return ent["idx"]


def fixtures(store_id: str) -> list[dict]:
    return list(_fixture_index(store_id).values())


def fixture(store_id: str, fixture_id: str) -> dict | None:
    return _fixture_index(store_id).get(fixture_id)


gondola = fixture  # v8 name (kept for the staff app)


def _slot_rows() -> dict[str, dict[str, dict]]:
    global _slots
    if _slots is None:
        out: dict[str, dict[str, dict]] = {}
        try:
            for r in repository.load_shelf_slots():
                out.setdefault(str(r.get("store_id")), {})[str(r.get("slot_id"))] = r
        except Exception:  # noqa: BLE001
            out = {}
        _slots = out
    return _slots


def _along(f: dict, frac: float) -> tuple[float, float]:
    """Point on the fixture face at fraction `frac` left→right as seen by a customer facing it."""
    x, y, w, h, face = f["x"], f["y"], f["w"], f["h"], f["face"]
    if face == "S":   # customer south of it, looking north → left = west
        return x + w * frac, y + h
    if face == "N":   # customer north, looking south → left = east
        return x + w * (1 - frac), y
    if face == "E":   # customer east, looking west → left = south
        return x + w, y + h * (1 - frac)
    return x, y + h * frac  # W: customer west, looking east → left = north


def _outward(face: str, px: float, py: float, d: float) -> tuple[float, float]:
    return {"N": (px, py - d), "S": (px, py + d), "E": (px + d, py), "W": (px - d, py)}[face]


def slot_point(store_id: str, sid: str) -> tuple[float, float] | None:
    """Plan point of a slot on the fixture face (shelf_slots.jsonl x,y when present)."""
    p = parse_slot(sid)
    if not p:
        return None
    f = fixture(store_id, p[0])
    if not f:
        return None
    row = _slot_rows().get(store_id, {}).get(sid)
    if row and row.get("x") is not None and row.get("y") is not None:
        return float(row["x"]), float(row["y"])
    t_spt = int((f.get("tier_slots") or {}).get(str(p[1])) or f.get("slots_per_tier", 1))
    frac = (min(p[2], t_spt) - 0.5) / max(1, t_spt)
    return _along(f, min(1.0, max(0.0, frac)))


def access_point(store_id: str, fixture_id: str, slot: str | None = None, side: str | None = None,
                 position: int | None = None) -> tuple[float, float, str, tuple[float, float]] | None:
    """Walk-to point in front of a slot (or a fixture/position) → (ax, ay, face, pin_xy on the face edge)."""
    f = fixture(store_id, fixture_id)
    if not f:
        return None
    face = f["face"]
    if side and side in (f.get("faces") or []) and not f.get("is_counter"):
        face = side
    pin = None
    if slot and parse_slot(slot) and face == f["face"]:
        pin = slot_point(store_id, slot)
    if pin is None:
        frac = 0.5
        if position:
            frac = min(0.95, max(0.05, (int(position) - 0.5) / max(1, f["slots_per_tier"])))
        pin = _along({**f, "face": face}, frac)
    x, y, w, h = f["x"], f["y"], f["w"], f["h"]
    px, py = pin
    px, py = min(max(px, x), x + w), min(max(py, y), y + h)
    if face == "N":
        py = y
    elif face == "S":
        py = y + h
    elif face == "E":
        px = x + w
    else:
        px = x
    ax, ay = _outward(face, px, py, FACE_OFFSET)
    return ax, ay, face, (px, py)


def obstacles(lay: dict) -> list[tuple[float, float, float, float]]:
    rows = lay.get("fixtures") if "fixtures" in lay else lay.get("gondolas")
    rects = [(float(g["x"]), float(g["y"]), float(g["w"]), float(g["h"])) for g in rows or []]
    for k in ("register", "counter"):
        r = lay.get(k)
        if r and all(n in r for n in ("x", "y", "w", "h")):
            rects.append((float(r["x"]), float(r["y"]), float(r["w"]), float(r["h"])))
    return rects


def _in_rect(px: float, py: float, r: tuple, pad: float) -> bool:
    x, y, w, h = r
    return x - pad < px < x + w + pad and y - pad < py < y + h + pad


class Grid:
    """Routing grid (0.5 m) + fine line-of-sight raster (0.1 m), both rasterized from obstacle rects."""

    def __init__(self, lay: dict):
        self.lay = lay
        self.w, self.h = float(lay["width"]), float(lay["height"])
        self.nx, self.ny = int(math.ceil(self.w / RES)), int(math.ceil(self.h / RES))
        self.rects = obstacles(lay)
        self.free = [[True] * self.nx for _ in range(self.ny)]
        for j in range(self.ny):
            for i in range(self.nx):
                cx, cy = self.center(i, j)
                if cx < 0.2 or cy < 0.2 or cx > self.w - 0.2 or cy > self.h - 0.2:
                    self.free[j][i] = False
        for (x, y, w, h) in self.rects:
            i0, i1 = max(0, int((x - INFLATE) / RES) - 1), min(self.nx - 1, int((x + w + INFLATE) / RES) + 1)
            j0, j1 = max(0, int((y - INFLATE) / RES) - 1), min(self.ny - 1, int((y + h + INFLATE) / RES) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    cx, cy = self.center(i, j)
                    if x - INFLATE < cx < x + w + INFLATE and y - INFLATE < cy < y + h + INFLATE:
                        self.free[j][i] = False
        self.fx, self.fy = int(math.ceil(self.w / FINE)) + 2, int(math.ceil(self.h / FINE)) + 2
        self.block = bytearray(self.fx * self.fy)
        for (x, y, w, h) in self.rects:
            i0, i1 = max(0, int((x - LOS_INFLATE) / FINE)), min(self.fx - 1, int(math.ceil((x + w + LOS_INFLATE) / FINE)))
            j0, j1 = max(0, int((y - LOS_INFLATE) / FINE)), min(self.fy - 1, int(math.ceil((y + h + LOS_INFLATE) / FINE)))
            for j in range(j0, j1 + 1):
                row = j * self.fx
                self.block[row + i0: row + i1 + 1] = b"\x01" * (i1 - i0 + 1)

    def center(self, i: int, j: int) -> tuple[float, float]:
        return ((i + 0.5) * RES, (j + 0.5) * RES)

    def point_free(self, x: float, y: float, pad: float = LOS_INFLATE) -> bool:
        if x < 0 or y < 0 or x > self.w or y > self.h:
            return False
        if pad != LOS_INFLATE:
            return not any(_in_rect(x, y, r, pad) for r in self.rects)
        i, j = int(x / FINE), int(y / FINE)   # conservative: the 4 surrounding fine cells must be free
        b, fx = self.block, self.fx
        return not (b[j * fx + i] or b[j * fx + i + 1] or b[(j + 1) * fx + i] or b[(j + 1) * fx + i + 1])

    def snap(self, x: float, y: float) -> tuple[int, int]:
        ci, cj = min(self.nx - 1, max(0, int(x / RES))), min(self.ny - 1, max(0, int(y / RES)))
        best, bd = (0, 0), float("inf")
        for rad in range(0, max(self.nx, self.ny)):
            for j in range(max(0, cj - rad), min(self.ny, cj + rad + 1)):
                for i in range(max(0, ci - rad), min(self.nx, ci + rad + 1)):
                    if max(abs(i - ci), abs(j - cj)) != rad or not self.free[j][i]:
                        continue
                    cx, cy = self.center(i, j)
                    d = (cx - x) ** 2 + (cy - y) ** 2
                    if d < bd:
                        bd, best = d, (i, j)
            if bd < float("inf") and (rad * RES) ** 2 > bd + RES:
                break
        return best

    def astar(self, a: tuple[int, int], b: tuple[int, int]) -> tuple[float, list[tuple[int, int]]]:
        if a == b:
            return 0.0, [a]
        openq: list = [(0.0, 0.0, a)]
        g = {a: 0.0}
        came: dict = {}
        bx, by = b
        free = self.free
        while openq:
            _, gc, cur = heapq.heappop(openq)
            if cur == b:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                return gc * RES, path[::-1]
            if gc > g.get(cur, float("inf")):
                continue
            ci, cj = cur
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    if not di and not dj:
                        continue
                    ni, nj = ci + di, cj + dj
                    if not (0 <= ni < self.nx and 0 <= nj < self.ny) or not free[nj][ni]:
                        continue
                    if di and dj and not (free[cj][ni] and free[nj][ci]):
                        continue  # no corner cutting
                    ng = gc + (1.41421356 if di and dj else 1.0)
                    if ng < g.get((ni, nj), float("inf")):
                        g[(ni, nj)] = ng
                        came[(ni, nj)] = cur
                        heapq.heappush(openq, (ng + math.hypot(ni - bx, nj - by), ng, (ni, nj)))
        return float("inf"), []

    def los(self, p: tuple[float, float], q: tuple[float, float]) -> bool:
        d = math.hypot(q[0] - p[0], q[1] - p[1])
        n = max(1, int(d / 0.08))
        for k in range(n + 1):
            t = k / n
            if not self.point_free(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t):
                return False
        return True

    def smooth(self, pts: list[tuple[float, float]], window: int = 80) -> list[tuple[float, float]]:
        if len(pts) <= 2:
            return pts
        out = [pts[0]]
        i = 0
        while i < len(pts) - 1:
            j = min(len(pts) - 1, i + window)
            while j > i + 1 and not self.los(pts[i], pts[j]):
                j -= 1
            out.append(pts[j])
            i = j
        return out


def get_grid(store_id: str) -> Grid | None:
    lay = get_layout(store_id)
    if not lay:
        return None
    g = _grids.get(store_id)
    if g is None or g.lay is not lay:
        g = _grids[store_id] = Grid(lay)
    return g


def face_point(g: dict, side: str | None, position: int | None = None) -> tuple[float, float, str]:
    """Access point in front of a fixture face at a nominal position (v8 helper, used by tests/staff)."""
    f = g if "slots_per_tier" in g else _normalize(g, "faces" in g)
    faces = f.get("faces") or [f.get("face") or "S"]
    s = side if side in faces else faces[0]
    frac = 0.5
    if position:
        frac = min(0.95, max(0.05, (int(position) - 0.5) / max(1, f.get("slots_per_tier") or V1_POSITIONS)))
    px, py = _along({**f, "face": s}, frac)
    ax, ay = _outward(s, px, py, FACE_OFFSET)
    return ax, ay, s


def register_point(lay: dict) -> tuple[float, float]:
    r = lay.get("register") or {}
    if r:
        return float(r["x"]) + float(r["w"]) / 2, float(r["y"]) - FACE_OFFSET
    e = lay.get("exit") or lay.get("entrance") or {"x": 0, "y": 0}
    return float(e["x"]), float(e["y"])


def plan_route(store_id: str, stops: list[dict]) -> dict[str, Any]:
    """stops: [{product_id, fixture_id|gondola_id, slot_ids?, primary_slot?, side?, tier?, position?}] →
    {stops:[{no, product_id, fixture_id, gondola_id, slot_ids, primary_slot, tier, side, x, y, pin}], polyline, length_m}."""
    lay = get_layout(store_id)
    grid = get_grid(store_id)
    if not lay or not grid:
        return {"stops": [], "polyline": [], "length_m": 0}
    ent = lay.get("entrance") or {"x": 0, "y": 0}
    start = grid.snap(float(ent["x"]), float(ent["y"]))
    cand = []
    for s in stops:
        fid = s.get("fixture_id") or s.get("gondola_id") or ""
        primary = s.get("primary_slot") or ((s.get("slot_ids") or [None])[0])
        ap = access_point(store_id, fid, primary, s.get("side"), s.get("position"))
        if not ap:
            continue
        ax, ay, face, (px, py) = ap
        cand.append({**s, "fixture_id": fid, "primary_slot": primary, "side": face, "_cell": grid.snap(ax, ay),
                     "pin": {"x": round(px, 2), "y": round(py, 2)}})
    order: list[dict] = []
    cells: list[tuple[int, int]] = [start]
    cur = start
    remaining = list(cand)
    cache: dict = {}

    def dist(a, b):
        k = (a, b)
        if k not in cache:
            cache[k] = grid.astar(a, b)
        return cache[k]

    while remaining:
        nxt = min(remaining, key=lambda s: (dist(cur, s["_cell"])[0], s.get("product_id", "")))
        remaining.remove(nxt)
        order.append(nxt)
        cur = nxt["_cell"]
        cells.append(cur)
    rx, ry = register_point(lay)
    cells.append(grid.snap(rx, ry))
    poly: list[tuple[float, float]] = []
    for a, b in zip(cells, cells[1:]):
        _, path = dist(a, b)
        pts = [grid.center(i, j) for i, j in path]
        sm = grid.smooth(pts) if len(pts) > 1 else pts
        if poly and sm and poly[-1] == sm[0]:
            sm = sm[1:]
        poly.extend(sm)
    if not poly:
        poly = [grid.center(*start)]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))
    out_stops = []
    for no, s in enumerate(order, 1):
        cx, cy = grid.center(*s["_cell"])
        out_stops.append({"no": no, "product_id": s.get("product_id"), "fixture_id": s["fixture_id"],
                          "gondola_id": s["fixture_id"], "slot_ids": s.get("slot_ids") or [],
                          "primary_slot": s.get("primary_slot"), "side": s.get("side"), "tier": s.get("tier"),
                          "position": s.get("position"), "x": round(cx, 2), "y": round(cy, 2), "pin": s["pin"]})
    return {"stops": out_stops, "polyline": [[round(x, 2), round(y, 2)] for x, y in poly],
            "length_m": round(length, 1)}


def route_for_products(store_id: str, product_ids: list[str]) -> dict[str, Any]:
    from . import inventory  # lazy (inventory imports layout)

    stops, seen = [], set()
    for pid in product_ids:
        if pid in seen:
            continue
        seen.add(pid)
        sh = inventory.shelf_for(store_id, pid)
        if sh:
            stops.append({"product_id": pid, "fixture_id": sh["fixture_id"], "slot_ids": sh.get("slot_ids") or [],
                          "primary_slot": sh.get("primary_slot"), "side": sh.get("side"), "tier": sh.get("tier"),
                          "position": sh.get("position")})
    return plan_route(store_id, stops)


def elevation(store_id: str, fixture_id: str) -> dict | None:
    from . import inventory

    return inventory.fixture_grid(store_id, fixture_id)
