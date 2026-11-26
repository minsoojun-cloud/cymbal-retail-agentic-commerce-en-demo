#!/usr/bin/env python3
"""Build Cymbal Store Navi store data v2 (CONTRACT v9) — deterministic.

  python3 build_store_data.py all      # stores.json, layouts/S0x.json (v2 fixtures), store_inventory.jsonl,
                                       # shelf_slots.jsonl, shelf_locations.jsonl, scenarios.json
Then run gen_personas.py (offers are picked from the in-stock assortment) and setup_bq.py.

Pipeline per store: assortment (carry rate per planogram group) -> demand in slot units (size class x facings) ->
pour groups into the store's ordered fixture positions (store_layout_defs.store_geometry) -> pack products into
tiers (heavy low, best sellers tiers 3-4) and 30/60 cm slots (many-to-many, contiguous on one tier).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store_layout_defs as defs  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "store-navi", "data")
CATALOG = os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl")
IMG_DIR = os.path.join(ROOT, "online-store", "static", "img", "products")
SEED = 20261112
UPDATED_AT = "2026-11-12T00:00:00+09:00"
SEED_REGISTERED_AT = "2026-11-01T09:00:00+09:00"
STORE_IDS = ["S01", "S02", "S03"]
OOS_RATE = 0.04
FILL = 0.82          # target shelf fill (units used / capacity) before scaling to the available positions
MAX_SCALE = 1.6

MUST_INCLUDE = ["4997989851807", "4934187704559", "4950519592175", "4913031854046", "4922498218728", "4973103820157",
                "4940363008748", "4918785204963", "4905727212964", "4913451622324", "4939504329545", "4964839943357",
                "4984256270751", "4967748296101"]
STAFF_PHOTO_PREFERRED = ["4939504329545", "4905727212964", "4918785204963", "4964839943357"]
SCENARIO_IDS = {"cold": "4997989851807", "kids_syrup": "4950519592175", "antipyretic": "4913031854046"}

# carry rate per group (share of the group's catalog SKUs carried)
CARRY = {
    "S01": {"default": 0.88, "A_otc": 1.0, "RX": 1.0, "B_beauty": 0.92, "C_health": 0.95, "D_daily": 0.92,
            "E_baby": 0.9, "P_flower": 0.6, "N_bake": 0.8, "T_living": 0.82, "G_sake": 0.86},
    "S02": {"default": 0.96, "A_otc": 1.0, "RX": 1.0, "B_beauty": 0.97, "C_health": 1.0, "D_daily": 0.98,
            "E_baby": 0.98},
    "S03": {"default": 0.90, "A_otc": 1.0, "RX": 1.0, "B_beauty": 1.0, "C_health": 1.0, "D_daily": 0.92,
            "E_baby": 0.85, "P_flower": 0.7, "N_bake": 0.75, "L_meat": 0.8, "M_fish": 0.8, "P_flat": 0.82,
            "T_living": 0.8},
}
ZONE_ORDER = ["otc_wall", "drug", "grocery", "chill", "flat", "flower", "deli", "frozen", "chest", "endcap", "register"]
SINGLE_FLOW = {"endcap": "S_season", "register": "R_impulse", "otc_wall": "A_otc", "flat": "P_flat",
               "flower": "P_flower", "frozen": "J_frozen", "chest": "J_ice"}


def h01(*parts) -> float:
    d = hashlib.sha256(("|".join(map(str, parts)) + f"|{SEED}").encode()).digest()
    return int.from_bytes(d[:8], "big") / 2 ** 64


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")


def write_jsonl(path, rows) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def has_local_image(p) -> bool:
    imgs = p.get("images") or []
    return bool(imgs) and os.path.exists(os.path.join(IMG_DIR, imgs[0]["uri"].rsplit("/", 1)[-1]))


def flow_of(group: str) -> str:
    return defs.GROUPS[group][1]


def slot_cap(slot_w: int) -> int:
    return defs.SLOT_UNITS[slot_w]


# ------------------------------------------------------------------------------------------- scenarios
def pick_scenario_products(products):
    by_id = {p["id"]: p for p in products}
    t = lambda p: p["title"]  # noqa: E731
    c = defs.cat_of

    def find(pred, prefer):
        cands = sorted([p for p in products if pred(p) and not defs.is_rx(p)], key=prefer)
        return cands[0] if cands else None

    pref = lambda p: (0 if t(p).startswith("Cymbal") else 1, len(t(p)))  # noqa: E731
    fever = []
    for role, pred in [
        ("cooling_sheet", lambda p: "冷却ジェルシート" in t(p) and "大人用" in t(p)),
        ("ors", lambda p: "経口補水液" in t(p) and "500ml" in t(p) and "×" not in t(p)),
        ("thermometer", lambda p: "デジタル体温計" in t(p) and "ベビー" not in t(p) and "体温計" in c(p)),
        ("mask", lambda p: "不織布" in t(p) and "マスク" in t(p) and "子ども" not in t(p)),
        ("kids_syrup", None), ("antipyretic", None)]:
        p = by_id.get(SCENARIO_IDS.get(role, "")) or (find(pred, pref) if pred else None)
        if p:
            fever.append((role, p))
    beauty = find(lambda p: "敏感肌" in t(p) and "化粧水" in c(p), pref) or \
        find(lambda p: "敏感肌" in t(p) and "スキンケア" in c(p),
             lambda p: (0 if t(p).startswith("Cymbal Beauty") else 1, len(t(p))))
    return by_id[SCENARIO_IDS["cold"]], fever, beauty


# ------------------------------------------------------------------------------------------- store build
class StoreBuild:
    def __init__(self, sid, products, meta, forced):
        self.sid = sid
        self.products = products
        self.meta = meta          # pid -> {group, units, facings}
        self.forced = forced      # pid -> qty or None
        self.by_id = {p["id"]: p for p in products}

    def assortment(self):
        rates = CARRY[self.sid]
        self.carried = []
        for p in self.products:
            g = self.meta[p["id"]]["group"]
            r = rates.get(g, rates["default"])
            if p["id"] in self.forced or h01("carry", self.sid, p["id"]) < r:
                self.carried.append(p["id"])
        return self.carried

    def consume(self, pid, slot_w):
        m = self.meta[pid]
        u = m["units"]
        if slot_w == 60:
            u = max(1, math.ceil(u / 2)) if u < 6 else u  # flat tables are deep: small items pack denser
        return max(slot_cap(slot_w) // 6, u * m["facings"])  # at most 6 products share one slot

    def plan(self):
        base, geo = defs.store_geometry(self.sid)
        self.base = base
        by_group = defaultdict(list)
        rx = []
        for pid in self.carried:
            g = self.meta[pid]["group"]
            (rx if g == "RX" else by_group[g]).append(pid)
        for g in by_group:
            by_group[g].sort(key=lambda pid: (defs.cat_of(self.by_id[pid]), self.by_id[pid]["title"], pid))
        self.rx = sorted(rx)
        fixtures = []    # dicts with positions + group
        report = {}
        for flow in ZONE_ORDER:
            pos = geo.positions.get(flow, [])
            groups = defs.FLOW_GROUP_ORDER.get(flow, [SINGLE_FLOW.get(flow)])
            groups = [g for g in groups if g and by_group.get(g)]
            if not groups:
                continue
            sw = pos[0]["slot_width_cm"]
            cap_of = lambda p: p["tiers"] * p["slots_per_tier"] * slot_cap(p["slot_width_cm"])  # noqa: E731
            demand = {g: sum(self.consume(pid, sw) for pid in by_group[g]) for g in groups}
            need = {g: demand[g] / FILL for g in groups}
            total_cap = sum(cap_of(p) for p in pos)
            total_need = sum(need.values())
            if total_need > total_cap:
                raise SystemExit(f"{self.sid} flow {flow}: need {total_need:.0f} units > capacity {total_cap} "
                                 f"({ {g: round(need[g]) for g in groups} })")
            scale = total_cap / total_need if flow in ("otc_wall",) else min(MAX_SCALE, total_cap / total_need)
            i = 0
            for gi, g in enumerate(groups):
                target = need[g] * scale
                got = []
                acc = 0
                while i < len(pos) and (acc < target or not got):
                    got.append(pos[i])
                    acc += cap_of(pos[i])
                    i += 1
                if acc * 0.95 < demand[g]:
                    raise SystemExit(f"{self.sid} flow {flow}: group {g} short ({acc} < {demand[g]})")
                for p in got:
                    fixtures.append({**p, "group": g})
                report[g] = {"skus": len(by_group[g]), "fixtures": len(got), "capacity": acc, "demand": demand[g],
                             "fill": round(demand[g] / acc, 2)}
            report[f"_{flow}_unused_positions"] = len(pos) - i
        # ids per zone letter in creation order
        counters = Counter()
        for f in fixtures:
            z = defs.GROUPS[f["group"]][0]
            counters[z] += 1
            f["id"] = f"{z}{counters[z]:02d}"
            f["zone_letter"] = z
        self.fixtures = fixtures
        self.by_group = by_group
        self.report = report
        return report

    def pack(self):
        """Assign products to fixtures, tiers and slots."""
        self.loc = {}       # pid -> location dict
        self.slot_products = defaultdict(list)
        fx_by_group = defaultdict(list)
        for f in self.fixtures:
            fx_by_group[f["group"]].append(f)
        for g, fxs in fx_by_group.items():
            pids = self.by_group[g]
            caps = [f["tiers"] * f["slots_per_tier"] * slot_cap(f["slot_width_cm"]) for f in fxs]
            total = sum(self.consume(pid, fxs[0]["slot_width_cm"]) for pid in pids)
            frac = total / sum(caps)
            k, cum, assign = 0, 0.0, defaultdict(list)
            bound = caps[0] * frac
            for pid in pids:  # cumulative targets keep the fill even (no remainder dumped on the last fixture)
                c = self.consume(pid, fxs[k]["slot_width_cm"])
                while cum + c / 2 > bound and k < len(fxs) - 1:
                    k += 1
                    bound += caps[k] * frac
                assign[k].append(pid)
                cum += c
            for k, f in enumerate(fxs):
                self._pack_fixture(f, assign.get(k, []))
            for f in fxs:
                cats = Counter()
                for pid in f["products"]:
                    parts = defs.cat_of(self.by_id[pid]).split(" > ")
                    cats[" > ".join(parts[:2])] += 1
                f["categories"] = sorted(cats)
                subs = [c.split(" > ")[-1] for c, _ in cats.most_common(2)]
                f["label"] = "・".join(subs) if subs else defs.GROUPS[g][2]
        # RX counter
        for n, pid in enumerate(self.rx, 1):
            sid = f"RX-1{n:02d}"
            self.loc[pid] = {"fixture_id": "RX", "slot_ids": [sid], "tier": 1, "facings": 1, "side": "E",
                             "position": n}
            self.slot_products[sid].append(pid)

    def _pack_fixture(self, f, pids):
        f["products"] = list(pids)
        T, S, sw = f["tiers"], f["slots_per_tier"], f["slot_width_cm"]
        cap = slot_cap(sw)
        tier_cap = S * cap
        rem = {t: tier_cap for t in range(1, T + 1)}
        tiers = defaultdict(list)
        for pid in pids:
            m = self.meta[pid]
            c = min(self.consume(pid, sw), tier_cap)
            if T == 1:
                pref = [1]
            elif T == 2:
                pref = [1, 2] if m["units"] >= 6 else [2, 1]
            elif m["units"] >= 6:
                pref = [1, 2, 3, 4, 5]
            elif m["facings"] >= 2:
                pref = [3, 4, 2, 5, 1]
            else:
                pref = [4, 3, 5, 2, 1]
            pref = [t for t in pref if t <= T]
            t = next((t for t in pref if rem[t] >= c), max(rem, key=lambda t: rem[t]))
            rem[t] -= c
            tiers[t].append(pid)
        for t, lst in tiers.items():
            lst.sort(key=lambda pid: (defs.cat_of(self.by_id[pid]), self.by_id[pid]["title"], pid))
            cons = [max(cap / 6, min(self.consume(pid, sw), tier_cap)) for pid in lst]  # <= 6 items per slot
            used = sum(cons)
            scale = min(3.0, tier_cap * 0.92 / used) if used else 1.0
            for _attempt in range(12):
                placed, pos = [], 0.0
                for c in cons:
                    w = c * scale
                    s0 = int(pos // cap + 1e-9)
                    if w <= cap + 1e-9 and pos + w > (s0 + 1) * cap + 1e-9:
                        pos = (s0 + 1) * cap  # small items never straddle a slot boundary
                    placed.append((pos, pos + w))
                    pos += w
                if pos <= tier_cap + 1e-6:
                    break
                scale *= 0.9
            for pid, (a, b) in zip(lst, placed):
                s0 = min(int(a // cap + 1e-9), S - 1)
                s1 = min(max(s0, int(math.ceil(b / cap - 1e-9)) - 1), S - 1)
                slot_ids = [f"{f['id']}-{t}{k + 1:02d}" for k in range(s0, s1 + 1)]
                for sl in slot_ids:
                    self.slot_products[sl].append(pid)
                self.loc[pid] = {"fixture_id": f["id"], "slot_ids": slot_ids, "tier": t,
                                 "facings": self.meta[pid]["facings"], "side": f["face"], "position": s0 + 1}

    def layout(self):
        fixtures_out = []
        for f in self.fixtures:
            fixtures_out.append({k: f[k] for k in ("id", "body_id", "type")} | {
                "zone": None, "label": f["label"], "x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"],
                "face": f["face"], "length_cm": f["length_cm"], "tiers": f["tiers"],
                "slot_width_cm": f["slot_width_cm"], "slots_per_tier": f["slots_per_tier"],
                "categories": f["categories"], "group": f["group"]})
        # zones = clusters of same-letter fixtures that touch / are within one aisle
        zones = []
        letters = defaultdict(list)
        for f in fixtures_out:
            letters[f["id"][0]].append(f)
        for z, fs in sorted(letters.items()):
            parent = list(range(len(fs)))

            def find(i):
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i
            for i in range(len(fs)):
                for j in range(i + 1, len(fs)):
                    a, b = fs[i], fs[j]
                    if defs.rect_gap(("", a["x"], a["y"], a["w"], a["h"]), ("", b["x"], b["y"], b["w"], b["h"])) <= 2.7:
                        parent[find(i)] = find(j)
            groups = defaultdict(list)
            for i, f in enumerate(fs):
                groups[find(i)].append(f)
            for n, (_, members) in enumerate(sorted(groups.items(), key=lambda kv: min(m["id"] for m in kv[1]))):
                zid = f"Z-{z}{n + 1}"
                x0 = min(m["x"] for m in members) - 0.3
                y0 = min(m["y"] for m in members) - 0.3
                x1 = max(m["x"] + m["w"] for m in members) + 0.3
                y1 = max(m["y"] + m["h"] for m in members) + 0.3
                x0, y0 = max(0, x0), max(0, y0)
                x1, y1 = min(self.base["width"], x1), min(self.base["height"], y1)
                for m in members:
                    m["zone"] = zid
                label = defs.ZONE_META[z][0]
                gl = {defs.GROUPS[m["group"]][2] for m in members}
                if len(gl) == 1:
                    label = gl.pop()
                zones.append({"id": zid, "label": label, "color": defs.ZONE_META[z][1],
                              "rect": [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]})
        c = self.base["counter"]
        zones.append({"id": "Z-X1", "label": defs.ZONE_META["X"][0], "color": defs.ZONE_META["X"][1],
                      "rect": [0, round(c["y"] - 0.6, 2), 3.4, round(c["h"] + 1.2, 2)]})
        signs = [{"text": z["label"], "x": round(z["rect"][0] + z["rect"][2] / 2, 2),
                  "y": round(z["rect"][1] + z["rect"][3] / 2, 2)} for z in zones]
        signs += [{"text": "入口", "x": self.base["entrance"]["x"], "y": self.base["height"] - 0.6},
                  {"text": "レジ", "x": round(self.base["register"]["x"] + self.base["register"]["w"] / 2, 2),
                   "y": round(self.base["register"]["y"] + 2.0, 2)}]
        n_rx = max(1, len(self.rx))
        fixtures_out.append({"id": "RX", "body_id": f"{self.sid}-RX", "type": "counter", "zone": "Z-X1",
                             "label": c["label"], "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"], "face": "E",
                             "length_cm": 30 * n_rx, "tiers": 1, "slot_width_cm": 30, "slots_per_tier": n_rx,
                             "categories": sorted({" > ".join(defs.cat_of(self.by_id[p]).split(" > ")[:2])
                                                   for p in self.rx}), "group": "RX"})
        lay = dict(self.base)
        lay["counter"] = {**c, "slots_per_tier": n_rx, "tiers": 1, "slot_width_cm": 30, "face": "E"}
        lay["zones"] = zones
        lay["signs"] = signs
        lay["fixtures"] = fixtures_out
        return lay

    def slots(self):
        rows = []
        for f in self.fixtures:
            pts = defs.slot_points(f)
            for t in range(1, f["tiers"] + 1):
                for k, (x, y) in enumerate(pts):
                    rows.append({"store_id": self.sid, "slot_id": f"{f['id']}-{t}{k + 1:02d}", "fixture_id": f["id"],
                                 "tier": t, "index": k + 1, "width_cm": f["slot_width_cm"], "fixture_type": f["type"],
                                 "x": x, "y": y})
        c = self.base["counter"]
        for n in range(1, len(self.rx) + 1):
            rows.append({"store_id": self.sid, "slot_id": f"RX-1{n:02d}", "fixture_id": "RX", "tier": 1, "index": n,
                         "width_cm": 30, "fixture_type": "counter", "x": round(c["x"] + c["w"] / 2, 2),
                         "y": round(c["y"] + min(c["h"] - 0.1, 0.15 + 0.3 * (n - 1)), 2)})
        return rows


def compute_meta(products):
    meta = {}
    by_group = defaultdict(list)
    for p in products:
        g = defs.route(p)
        meta[p["id"]] = {"group": g, "units": defs.size_units(p), "facings": 1}
        by_group[g].append(p)
    for g, ps in by_group.items():
        ps = sorted(ps, key=lambda p: (-(p.get("rating") or {}).get("ratingCount", 0), p["id"]))
        n = len(ps)
        for i, p in enumerate(ps):
            if meta[p["id"]]["units"] >= 12 or g in ("RX",):
                continue
            if i < max(1, int(n * 0.04)):
                meta[p["id"]]["facings"] = 3
            elif i < int(n * 0.12):
                meta[p["id"]]["facings"] = 2
    return meta


def build_all():
    products = load_catalog()
    by_id = {p["id"]: p for p in products}
    meta = compute_meta(products)
    cold, fever, beauty = pick_scenario_products(products)
    forced = {s: {} for s in STORE_IDS}
    for s in STORE_IDS:
        for pid in MUST_INCLUDE + STAFF_PHOTO_PREFERRED:
            if pid in by_id:
                forced[s][pid] = None
    forced["S01"][cold["id"]] = 0
    forced["S02"][cold["id"]] = 12
    forced["S03"][cold["id"]] = 6
    for _, p in fever:
        forced["S01"][p["id"]] = 8 + int(h01("fq", p["id"]) * 15)
    if beauty:
        forced["S03"][beauty["id"]] = 0
        forced["S01"][beauty["id"]] = 9
        forced["S02"][beauty["id"]] = 4

    rng = random.Random(SEED)
    inv_rows, slot_rows, shelf_rows, builds = [], [], [], {}
    write_json(os.path.join(DATA, "stores.json"), defs.STORES)
    for sid in STORE_IDS:
        b = StoreBuild(sid, products, meta, forced[sid])
        b.assortment()
        b.plan()
        b.pack()
        lay = b.layout()
        errs = defs.validate_layout(lay)
        if errs:
            raise SystemExit(f"{sid} layout invalid: {errs[:10]}")
        write_json(os.path.join(DATA, "layouts", f"{sid}.json"), lay)
        builds[sid] = b
        slot_rows += b.slots()
        qty = {}
        for pid in sorted(b.carried):
            fq = forced[sid].get(pid, "x")
            if fq is not None and fq != "x":
                q = fq
            elif fq is None:
                q = 6 + int(h01("mq", sid, pid) * 20)
            elif h01("oos", sid, pid) < OOS_RATE:
                q = 0
            else:
                q = max(1, min(40, int(round(rng.gauss(16, 8)))))
            qty[pid] = q
        b.qty = qty
    # staff photo products (S01): preferred OTC + varied categories, distinct fixtures, local packshot, in stock
    b1 = builds["S01"]
    used_fx, photos = set(), []
    fever_ids = {p["id"] for _, p in fever}
    excl = fever_ids | {cold["id"], beauty["id"] if beauty else ""}

    def ok_photo(pid):
        loc = b1.loc.get(pid)
        return (loc and loc["fixture_id"] != "RX" and loc["fixture_id"] not in used_fx and pid not in excl
                and has_local_image(by_id[pid]) and b1.qty.get(pid, 0) > 0)
    for pid in STAFF_PHOTO_PREFERRED:
        if ok_photo(pid):
            photos.append(pid)
            used_fx.add(b1.loc[pid]["fixture_id"])
    otc_c = sorted([pid for pid in b1.carried if defs.cat_of(by_id[pid]).startswith(defs.OTC + " > ")],
                   key=lambda pid: (0 if by_id[pid]["title"].startswith("Cymbal") else 1, h01("otcphoto", pid)))
    for pid in otc_c:
        if sum(1 for x in photos if defs.cat_of(by_id[x]).startswith(defs.OTC)) >= 3:
            break
        if ok_photo(pid):
            photos.append(pid)
            used_fx.add(b1.loc[pid]["fixture_id"])
    pref_cats = [f"{defs.BE} > スキンケア > 化粧水", f"{defs.BE} > オーラルケア > 歯磨き粉", f"{defs.DA} > 洗濯用品",
                 f"{defs.SE} > 醤油", f"{defs.IN} > カップ麺", f"{defs.SW} > チョコレート", f"{defs.DR} > お茶飲料",
                 f"{defs.BE} > ヘアケア > シャンプー"]
    for pc in pref_cats:
        if len(photos) >= 10:
            break
        cands = sorted([pid for pid in b1.carried if defs.cat_of(by_id[pid]).startswith(pc) and ok_photo(pid)],
                       key=lambda pid: (0 if by_id[pid]["title"].startswith("Cymbal") else 1, h01("photo", pid)))
        if cands:
            photos.append(cands[0])
            used_fx.add(b1.loc[cands[0]]["fixture_id"])
    for sid, b in builds.items():
        for pid in sorted(b.carried):
            q = b.qty[pid]
            inv_rows.append({"store_id": sid, "product_id": pid, "quantity": q,
                             "availability": "IN_STOCK" if q > 0 else "OUT_OF_STOCK", "updated_at": UPDATED_AT})
            loc = b.loc[pid]
            note = ""
            if loc["fixture_id"] == "RX":
                note = ("指定濫用防止医薬品（架空）: 医薬品カウンターでお渡し"
                        if defs.attr1(by_id[pid], "abuse_control", "false") == "true"
                        else "第1類医薬品: 医薬品カウンター内に陳列（薬剤師が説明のうえ販売）")
            shelf_rows.append({"store_id": sid, "product_id": pid, "fixture_id": loc["fixture_id"],
                               "slot_ids": loc["slot_ids"], "tier": loc["tier"], "facings": loc["facings"],
                               "gondola_id": loc["fixture_id"], "side": loc["side"], "position": loc["position"],
                               "photo_uri": None, "source": "seed", "registered_by": "seed-planogram",
                               "registered_at": SEED_REGISTERED_AT, "note": note})
    n_inv = write_jsonl(os.path.join(DATA, "store_inventory.jsonl"), inv_rows)
    n_slot = write_jsonl(os.path.join(DATA, "shelf_slots.jsonl"), slot_rows)
    n_shelf = write_jsonl(os.path.join(DATA, "shelf_locations.jsonl"), shelf_rows)

    def det(pid, sid="S01"):
        b = builds[sid]
        loc = b.loc[pid]
        return {"product_id": pid, "title": by_id[pid]["title"], "fixture_id": loc["fixture_id"],
                "gondola_id": loc["fixture_id"], "slot_ids": loc["slot_ids"], "side": loc["side"], "tier": loc["tier"],
                "quantity": b.qty[pid], "image": (by_id[pid].get("images") or [{}])[0].get("uri")}
    scen = {
        "cold_tablet_out_at_S01": {"product_id": cold["id"], "title": cold["title"], "in_stock_at": "S02",
                                   "quantity_S01": 0, "quantity_S02": 12, "online_available": True},
        "fever_set_S01": [p["id"] for _, p in fever],
        "fever_set_S01_detail": [{"role": r, **det(p["id"])} for r, p in fever],
        "beauty_out_at_S03": {"product_id": beauty["id"], "title": beauty["title"], "in_stock_at": "S01"},
        "staff_photo_products": photos,
        "staff_photo_products_detail": [det(pid) for pid in photos],
    }
    write_json(os.path.join(DATA, "scenarios.json"), scen)
    # summary
    N = len(products)
    summary = {}
    for sid, b in builds.items():
        instock = sum(1 for q in b.qty.values() if q > 0)
        nslots = sum(1 for r in slot_rows if r["store_id"] == sid)
        used = len([s for s in b.slot_products if not s.startswith("RX")])
        summary[sid] = {"fixtures": len(b.fixtures), "slots": nslots, "slots_with_products": used,
                        "carried": len(b.carried), "carried_pct": round(100 * len(b.carried) / N, 1),
                        "in_stock": instock, "in_stock_pct_catalog": round(100 * instock / N, 1),
                        "oos_pct_carried": round(100 * (len(b.carried) - instock) / len(b.carried), 1),
                        "rx_items": len(b.rx), "size_m": f"{b.base['width']}x{b.base['height']}"}
    print(json.dumps({"catalog": N, "rows": {"inventory": n_inv, "slots": n_slot, "shelf_locations": n_shelf},
                      "stores": summary}, ensure_ascii=False, indent=1))
    for sid, b in builds.items():
        print(sid, json.dumps(b.report, ensure_ascii=False))
    print("scenario", json.dumps({k: v for k, v in scen.items() if not k.endswith("detail")}, ensure_ascii=False))
    return builds, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?", default="all", choices=["all", "layouts", "inventory"])
    ap.parse_args()
    build_all()


if __name__ == "__main__":
    main()
