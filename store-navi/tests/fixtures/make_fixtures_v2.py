"""Generate CONTRACT v9 (layout v2, slot-level) dev fixtures → tests/fixtures/data_v2.

    python3 store-navi/tests/fixtures/make_fixtures_v2.py

Supermarket + drug hybrid (44×30 m): perimeter multidecks (精肉・鮮魚・惣菜・日配), wall OTC (A), RX counter, centre
double-sided gondolas = two fixtures per body (west face / east face), produce flat tables, register front.
Fixture ids = zone letter + 2 digits; slot id = <fixture>-<tier><NN> (NN left→right as seen facing the fixture).
Reuses the v1 fixture generator for stores / personas / coupons / offers / OTC products / scenarios.
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path

import make_fixtures as v1

HERE = Path(__file__).resolve().parent
OUT = HERE / "data_v2"

ZONES = {  # letter: (label, colour, category prefixes)
    "A": ("医薬品", "#e8f0fe", ["医薬品・ヘルスケア"]),
    "B": ("ビューティー", "#fce8f3", ["ビューティー・パーソナルケア > スキンケア", "ビューティー・パーソナルケア > ヘアケア",
                                  "ビューティー・パーソナルケア > ボディケア", "ビューティー・パーソナルケア > 美容雑貨",
                                  "ビューティー・パーソナルケア > オーラルケア"]),
    "C": ("ヘルスケア・衛生", "#fef7e0", ["ビューティー・パーソナルケア", "日用品・キッチン消耗品 > 衛生用品"]),
    "D": ("日用品", "#f1f3f4", ["日用品・キッチン消耗品"]),
    "E": ("ベビー", "#fff3e0", ["ベビー・キッズ食品"]),
    "F": ("お菓子", "#fdf3e7", ["お菓子・製菓"]),
    "G": ("飲料・お酒", "#e4f7fb", ["飲料・水"]),
    "H": ("グロッサリー", "#eef5e9", ["インスタント・レトルト", "調味料"]),
    "K": ("日配", "#e8f5fd", ["卵・乳製品・豆乳・デザート"]),
    "L": ("精肉", "#fde7e9", ["お肉"]),
    "M": ("鮮魚", "#e3f2fd", ["お魚・水産加工品"]),
    "N": ("惣菜・ベーカリー", "#fff8e1", ["惣菜"]),
    "P": ("青果", "#e6f4ea", ["野菜・果物"]),
    "R": ("レジ前", "#f3e8fd", []),
}


def build_layout(sid: str) -> dict:
    W, H = 44.0, 30.0
    fx: list[dict] = []
    counters: dict[str, int] = {}

    def nid(z: str) -> str:
        counters[z] = counters.get(z, 0) + 1
        return f"{z}{counters[z]:02d}"

    def add(z, typ, x, y, w, h, face, tiers, label=None, body=None, slot_w=None):
        length = w if face in "NS" else h
        sw = slot_w or (60 if typ in ("flat_table", "chest", "deli_table", "flower") else 30)
        f = {"id": nid(z), "body_id": body, "type": typ, "zone": f"Z-{z}", "label": label or ZONES[z][0], "x": round(x, 2),
             "y": round(y, 2), "w": round(w, 2), "h": round(h, 2), "face": face, "length_cm": int(round(length * 100)),
             "tiers": tiers, "slot_width_cm": sw, "slots_per_tier": max(1, int(round(length * 100 / sw))),
             "categories": ZONES[z][2]}
        f["body_id"] = body or f["id"]
        fx.append(f)
        return f

    # back wall multidecks (face S): 精肉 / 鮮魚 / 惣菜
    x = 8.0
    for z, n in (("L", 3), ("M", 3), ("N", 3)):
        for _ in range(n):
            add(z, "multideck", x, 0.2, 3.6, 0.9, "S", 5)
            x += 3.7
    # right wall multidecks (face W): 日配
    y = 2.5
    for _ in range(5):
        add("K", "multideck", W - 1.1, y, 0.9, 3.6, "W", 5)
        y += 3.7
    # left wall OTC (face E)
    y = 3.0
    for _ in range(5):
        add("A", "wall", 0.2, y, 0.6, 2.7, "E", 5)
        y += 2.8
    # centre double-sided gondolas: body 0.9 m deep → west half (face W) + east half (face E)
    cols = ["B", "C", "D", "E", "F", "G", "H", "G", "F"]
    gx = 4.5
    for ci, z in enumerate(cols):
        for (y0, y1) in ((4.0, 10.6), (12.4, 19.0)):
            body = f"{sid}-body-{ci}-{int(y0)}"
            add(z, "gondola", gx, y0, 0.45, y1 - y0, "W", 5, body=body)
            add(z, "gondola", gx + 0.45, y0, 0.45, y1 - y0, "E", 5, body=body)
        gx += 3.3
    # produce flat tables (face S) front-right
    for i in range(4):
        add("P", "flat_table", 31.0 + i * 2.4, 22.0, 1.8, 0.9, "S", 1)
    # impulse shelves at the register (face N)
    add("R", "gondola", 17.0, 24.6, 2.7, 0.45, "N", 4, label="レジ前")
    zones = []
    for z in ZONES:
        fz = [f for f in fx if f["zone"] == f"Z-{z}"]
        if not fz:
            continue
        x0, y0 = min(f["x"] for f in fz) - 0.3, min(f["y"] for f in fz) - 0.3
        x1, y1 = max(f["x"] + f["w"] for f in fz) + 0.3, max(f["y"] + f["h"] for f in fz) + 0.3
        zones.append({"id": f"Z-{z}", "label": ZONES[z][0], "color": ZONES[z][1],
                      "rect": [round(max(0, x0), 2), round(max(0, y0), 2), round(min(W, x1) - max(0, x0), 2), round(min(H, y1) - max(0, y0), 2)]})
    return {"store_id": sid, "version": 2, "width": W, "height": H, "entrance": {"x": 26.0, "y": H - 0.3},
            "exit": {"x": 23.0, "y": H - 0.3}, "register": {"x": 4.0, "y": 25.8, "w": 12.0, "h": 1.2},
            "counter": {"id": "RX", "label": "医薬品カウンター・調剤", "x": 0.4, "y": 0.3, "w": 6.0, "h": 1.2},
            "zones": zones, "signs": [], "fixtures": fx}


def main() -> None:
    v1.main()  # refresh v1 fixtures (stores, personas, coupons, offers, OTC products, scenarios)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    (OUT / "layouts").mkdir()
    for name in ("stores.json", "store_personas.json", "store_purchase_history.jsonl", "coupons.json", "offers.json",
                 "scenarios.json", "products_extra.jsonl", "store_inventory.jsonl"):
        shutil.copy(v1.OUT / name, OUT / name)
    rnd = random.Random(909)
    cat_of: dict[str, str] = {}
    abuse: set[str] = set()
    for line in open(v1.CATALOG, encoding="utf-8"):
        d = json.loads(line)
        cat_of[d["id"]] = d["categories"][0]
        if any(a.get("key") == "abuse_control" and (a.get("value") or {}).get("text") == ["true"] for a in d.get("attributes") or []):
            abuse.add(d["id"])
    for line in open(v1.OUT / "products_extra.jsonl", encoding="utf-8"):
        d = json.loads(line)
        cat_of[d["id"]] = d["categories"][0]
        if any(a.get("key") == "abuse_control" and (a.get("value") or {}).get("text") == ["true"] for a in d["attributes"]):
            abuse.add(d["id"])
    inv = [json.loads(l) for l in open(OUT / "store_inventory.jsonl", encoding="utf-8")]
    layouts = {sid: build_layout(sid) for sid in ("S01", "S02", "S03")}
    for sid, lay in layouts.items():
        (OUT / "layouts" / f"{sid}.json").write_text(json.dumps(lay, ensure_ascii=False, indent=1), encoding="utf-8")
    ts = "2026-11-12T00:00:00+00:00"
    rows, slots_out = [], []
    for sid, lay in layouts.items():
        for f in lay["fixtures"]:
            for t in range(1, f["tiers"] + 1):
                for i in range(1, f["slots_per_tier"] + 1):
                    slots_out.append({"store_id": sid, "slot_id": f"{f['id']}-{t}{i:02d}", "fixture_id": f["id"], "tier": t,
                                      "index": i, "width_cm": f["slot_width_cm"], "fixture_type": f["type"]})
    for r in inv:
        sid, pid = r["store_id"], r["product_id"]
        if pid in abuse:
            rows.append({"store_id": sid, "product_id": pid, "fixture_id": "RX", "slot_ids": ["RX-101"], "tier": 1, "facings": 1,
                         "gondola_id": "RX", "side": "S", "position": 1, "photo_uri": None, "source": "seed",
                         "registered_by": "seed", "registered_at": ts, "note": ""})
            continue
        cat = cat_of.get(pid, "").replace(" > ", ">")
        best, blen = [], -1
        for f in layouts[sid]["fixtures"]:
            for c in f["categories"]:
                cc = c.replace(" > ", ">")
                if cat.startswith(cc):
                    if len(cc) > blen:
                        best, blen = [f], len(cc)
                    elif len(cc) == blen:
                        best.append(f)
        if not best:
            continue
        f = best[rnd.randrange(len(best))]
        tier = rnd.randint(1, f["tiers"])
        facings = rnd.choice([1, 1, 1, 2, 2, 3]) if f["slots_per_tier"] >= 3 else 1
        start = rnd.randint(1, max(1, f["slots_per_tier"] - facings + 1))
        sids = [f"{f['id']}-{tier}{start + k:02d}" for k in range(facings)]
        rows.append({"store_id": sid, "product_id": pid, "fixture_id": f["id"], "slot_ids": sids, "tier": tier, "facings": facings,
                     "gondola_id": f["id"], "side": f["face"], "position": start, "photo_uri": None, "source": "seed",
                     "registered_by": "seed", "registered_at": ts, "note": ""})
    with open(OUT / "shelf_locations.jsonl", "w", encoding="utf-8") as fo:
        for r in rows:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT / "shelf_slots.jsonl", "w", encoding="utf-8") as fo:
        for r in slots_out:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"v2 fixtures: {sum(len(l['fixtures']) for l in layouts.values())} fixtures, {len(rows)} shelf rows, "
          f"{len(slots_out)} slots → {OUT}")


if __name__ == "__main__":
    main()
