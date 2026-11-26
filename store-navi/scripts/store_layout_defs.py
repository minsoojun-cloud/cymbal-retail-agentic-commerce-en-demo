"""Cymbal Store Navi — layout v2 (CONTRACT v9): supermarket (Kansai Food Market style) + drugstore corner.

This module holds
  * STORES master,
  * product routing (category/storage -> planogram group, size-class units),
  * per-store floor geometry as ordered "positions" (candidate fixture faces) per flow,
  * the layout validator (aisles >= 1.2 m, main aisles >= 1.5 m, face reachability).
build_store_data.py pours each store's assortment into the positions (fixtures are created only where needed) and
packs products into 30 cm / 60 cm slots.

Units: metres, origin top-left, y grows towards the front (entrance wall at y = height).
"""
from __future__ import annotations

import math
import re
from collections import deque

OTC = "医薬品・ヘルスケア"
BE = "ビューティー・パーソナルケア"
DA = "日用品・キッチン消耗品"
BA = "ベビー・キッズ食品"
DR = "飲料・水"
AL = "お酒・ノンアルコール"
SW = "お菓子・製菓"
IN = "お米・麺・インスタント"
FZ = "冷凍食品・アイス"
CN = "缶詰・粉類・乾物"
BR = "パン・シリアル・ジャム・はちみつ"
LV = "生活雑貨・インテリア"
KT = "キッチン・調理器具"
PET = "ペット用品"
SE = "調味料"
VEG = "野菜・果物"
MEAT = "お肉・お肉加工品"
FISH = "お魚・水産加工品"
DELI = "お惣菜・ミールキット"
DAIRY = "卵・乳製品・豆乳・デザート"
TOFU = "豆腐・納豆・練り物"

MIN_AISLE_M = 1.2
MAIN_AISLE_M = 1.5
GRID_M = 0.1
SLOT_UNITS = {30: 6, 60: 12}  # capacity units per slot (XS=1, S=2, M=3, L=6, XL=12)

ZONE_META = {
    "A": ("医薬品", "#e8f0fe"), "B": ("ビューティー", "#fce4ec"), "C": ("ヘルスケア・衛生", "#e3f2fd"),
    "D": ("日用品・紙・洗剤", "#fff8e1"), "E": ("ベビー", "#f3e5f5"), "F": ("お菓子", "#fff3e0"),
    "G": ("飲料・お酒", "#e0f7fa"), "H": ("グロッサリー", "#f1f8e9"), "J": ("冷凍食品・アイス", "#e1f5fe"),
    "K": ("日配", "#fffde7"), "L": ("精肉", "#ffebee"), "M": ("鮮魚・寿司・刺身・塩干", "#e0f2f1"),
    "N": ("惣菜・ベーカリー", "#fbe9e7"), "P": ("青果・地場野菜・果物・花", "#e8f5e9"), "Q": ("パン・シリアル", "#efebe9"),
    "T": ("ペット・キッチン・生活雑貨", "#ede7f6"), "U": ("季節・特売", "#fde7e9"), "R": ("レジ前", "#eceff1"),
    "X": ("医薬品カウンター・調剤", "#e6f4ea"),
}

# planogram groups: key -> (zone letter, flow, label)
GROUPS = {
    "S_season": ("U", "endcap", "かぜ・乾燥対策（季節特集）"),
    "R_impulse": ("R", "register", "レジ前（マスク・電池・ガム）"),
    "A_otc": ("A", "otc_wall", "医薬品"),
    "B_beauty": ("B", "drug", "ビューティー"),
    "C_health": ("C", "drug", "ヘルスケア・衛生"),
    "D_daily": ("D", "drug", "日用品・紙・洗剤"),
    "E_baby": ("E", "drug", "ベビー"),
    "P_chill": ("P", "chill", "青果（カット野菜・きのこ・サラダ）"),
    "L_meat": ("L", "chill", "精肉"),
    "M_fish": ("M", "chill", "鮮魚・寿司・刺身・塩干"),
    "N_deli": ("N", "chill", "惣菜・お弁当"),
    "K_daily": ("K", "chill", "日配"),
    "P_flat": ("P", "flat", "青果・果物・地場野菜"),
    "P_flower": ("P", "flower", "花"),
    "N_bake": ("N", "deli", "ベーカリー"),
    "N_hot": ("N", "deli", "惣菜平台"),
    "J_frozen": ("J", "frozen", "冷凍食品"),
    "J_ice": ("J", "chest", "アイス・冷凍食品（平台）"),
    "G_drink": ("G", "grocery", "飲料"),
    "G_sake": ("G", "grocery", "お酒"),
    "H_grocery": ("H", "grocery", "グロッサリー"),
    "Q_bread": ("Q", "grocery", "パン・シリアル・ジャム"),
    "F_snack": ("F", "grocery", "お菓子"),
    "T_living": ("T", "grocery", "ペット・キッチン・生活雑貨"),
}
FLOW_GROUP_ORDER = {
    "chill": ["P_chill", "L_meat", "M_fish", "N_deli", "K_daily"],
    "grocery": ["G_drink", "G_sake", "H_grocery", "Q_bread", "F_snack", "T_living"],
    "drug": ["B_beauty", "C_health", "E_baby", "D_daily"],
    "deli": ["N_bake", "N_hot"],
}

SEASON = [f"{BE} > 衛生・救急用品 > 冷却シート", f"{BE} > 衛生・救急用品 > 子ども用冷却シート",
          f"{BE} > 衛生・救急用品 > 保冷まくら", f"{BE} > ボディケア > ハンドクリーム", f"{BE} > ボディケア > かかとクリーム",
          f"{DR} > その他飲料 > しょうが湯・葛湯", f"{BE} > 健康グッズ > ホットアイマスク"]
IMPULSE = [f"{DA} > 衛生用品 > マスク", f"{DA} > 衛生用品 > マスク（小さめサイズ）", f"{DA} > 衛生用品 > マスク（子ども用）",
           f"{DA} > 衛生用品 > マスク（立体・カラー）", f"{DA} > 電池・電球 > アルカリ乾電池（単3・単4）", f"{SW} > ガム・ラムネ"]
VEG_FLAT_SUBS = {"果物", "葉物野菜", "果菜・花菜", "香味野菜", "いも類", "洋野菜", "根菜", "トマト", "野菜", "京野菜・伝統野菜",
                 "有機・特別栽培野菜", "地場野菜", "地元やさい市場"}
DELI_TABLE_SUBS = {"焼き物", "餃子・焼売", "粉もん", "串カツ・揚げ物", "関西惣菜"}
BREAD_SUBS = {"菓子パン", "パン", "惣菜パン", "食パン", "フランスパン・ハード系", "ベーグル・マフィン", "ナン・ピタ・トルティーヤ",
              "ホットドッグ・バーガー用パン", "焼きたてパン", "店内焼きたてパン", "ベーカリー", "店内焼きたてベーカリー"}
HEALTH_SUBS = {"健康グッズ", "衛生・救急用品", "大人用おむつ・介護用品"}


def attr1(p: dict, key: str, default=None):
    for a in p.get("attributes", []):
        if a.get("key") == key:
            v = a.get("value", {})
            vals = v.get("text") or v.get("numbers")
            return vals[0] if vals else default
    return default


def cat_of(p: dict) -> str:
    return p["categories"][0]


def starts(cat: str, prefixes) -> bool:
    return any(cat == x or cat.startswith(x + " > ") for x in prefixes)


def is_rx(p: dict) -> bool:
    return attr1(p, "abuse_control", "false") == "true" or attr1(p, "drug_class", "") == "第1類医薬品"


def route(p: dict) -> str:
    """Planogram group key ('RX' = handed over at the 医薬品カウンター)."""
    c = cat_of(p)
    parts = c.split(" > ")
    top, sub = parts[0], (parts[1] if len(parts) > 1 else "")
    storage = attr1(p, "storage_type", "")
    title = p["title"]
    if top == OTC:
        return "RX" if is_rx(p) else "A_otc"
    if starts(c, SEASON):
        return "S_season"
    if starts(c, IMPULSE):
        return "R_impulse"
    if ("花" in top and "花菜" not in top) or sub in ("花", "花束", "仏花", "鉢花", "切り花", "花・グリーン"):
        return "P_flower"
    if storage == "冷凍食品" or top == FZ:
        return "J_ice" if "アイス" in sub else "J_frozen"
    if top == BE:
        return "C_health" if sub in HEALTH_SUBS else "B_beauty"
    if top == DA:
        return "E_baby" if sub == "ベビー用品" else "D_daily"
    if top == BA:
        return "K_daily" if storage == "冷蔵食品" else "E_baby"
    if top == VEG or "地元やさい" in title or "地場" in sub:
        return "P_flat" if (sub in VEG_FLAT_SUBS or "地元やさい" in title or "地場" in sub) else "P_chill"
    if top == MEAT:
        return "L_meat" if storage != "常温食品" else "H_grocery"
    if top == FISH:
        return "M_fish" if storage != "常温食品" else "H_grocery"
    if top == DELI:
        return "N_hot" if sub in DELI_TABLE_SUBS else "N_deli"
    if top in (DAIRY, TOFU):
        return "K_daily" if storage != "常温食品" else "H_grocery"
    if top == BR:
        if sub in BREAD_SUBS and storage != "冷蔵食品":
            return "N_bake"
        return "K_daily" if storage == "冷蔵食品" else "Q_bread"
    if storage == "冷蔵食品":
        return "K_daily"
    if top == DR:
        return "G_drink"
    if top == AL:
        return "G_sake"
    if top == SW:
        return "F_snack"
    if top in (PET, KT, LV):
        return "T_living"
    return "H_grocery"


_XL = re.compile(r"ケース|×24|×12本|×6本|10kg|5kg|トイレットペーパー|紙おむつ|おむつ|パンツ .*枚|猫砂|ペットシーツ|まとめ買い|"
                 r"箱買い|フライパン|両手鍋|片手鍋|布団|収納ケース")
_L = re.compile(r"2L|1\.8L|1\.5L|2kg|3kg|4kg|大容量|徳用|業務用|ティッシュ.*(箱|×)|1000ml|900ml|一升|"
                r"ドッグフード|キャットフード|食パン|洗剤|柔軟剤|ゴミ袋|キッチンペーパー|丸ごと|1玉|すいか|白菜|大根|キャベツ|メロン|"
                r"花束|鉢|ホールケーキ|オードブル|鍋セット")
_XS_CAT = (OTC, f"{SE} > スパイス・ハーブ", f"{SE} > 塩・こしょう・スパイス", f"{SE} > わさび・からし・薬味チューブ",
           f"{SE} > 薬味・チューブ", f"{SW} > グミ・キャンディ", f"{SW} > ガム・ラムネ", f"{BA} > 離乳食", f"{BA} > ベビーおやつ",
           f"{BE} > スキンケア > リップケア", f"{BE} > 衛生・救急用品", f"{BE} > 身だしなみ用品", f"{BE} > 美容雑貨",
           f"{BE} > オーラルケア > 歯ブラシ", f"{BE} > オーラルケア > 子ども用歯ブラシ", f"{DA} > 電池・電球",
           f"{IN} > お茶漬け・ふりかけ・ごはんの素", f"{IN} > ふりかけ・お茶漬け", f"{SW} > 製菓材料", f"{DR} > お茶・ティーバッグ")


def size_units(p: dict) -> int:
    """Shelf-space units of one facing (30 cm slot = 6 units): XS 1, S 2, M 3, L 6, XL 12."""
    t, c = p["title"], cat_of(p)
    if _XL.search(t):
        return 12
    if _L.search(t):
        return 6
    if c.startswith(OTC + " > "):
        return 2
    if starts(c, _XS_CAT):
        return 1
    top = c.split(" > ")[0]
    if top in (VEG, MEAT, FISH, DELI, BR, FZ, DA, PET, KT, LV):
        return 3
    return 2


def is_heavy(p: dict) -> bool:
    return size_units(p) >= 6


# ------------------------------------------------------------------------------------------- stores
STORES = [
    {"store_id": "S01", "name": "Cymbal Retail 川崎駅前店", "short_name": "川崎駅前店", "lat": 35.5313, "lng": 139.6977,
     "address": "神奈川県川崎市川崎区駅前本町", "phone": "044-000-0101",
     "hours": {"open": "09:00", "close": "22:00"},
     "pharmacy": {"open": "09:00", "close": "19:00", "closed": "日曜・祝日"},
     "services": ["生鮮食品・お惣菜", "調剤薬局", "医薬品カウンター（薬剤師・登録販売者）", "ネット注文 店舗受け取り",
                  "キャッシュレス決済", "Cymbal ポイント"],
     "radius_m": 150, "layout_id": "S01", "tagline": "駅前の食品スーパー＆ドラッグ。夜22時まで営業"},
    {"store_id": "S02", "name": "Cymbal Retail 武蔵小杉店", "short_name": "武蔵小杉店", "lat": 35.5763, "lng": 139.6596,
     "address": "神奈川県川崎市中原区新丸子東", "phone": "044-000-0202",
     "hours": {"open": "09:00", "close": "23:00"},
     "pharmacy": {"open": "10:00", "close": "20:00", "closed": "日曜"},
     "services": ["生鮮食品・お惣菜", "店内焼きたてベーカリー", "地元やさい市場", "花", "調剤薬局",
                  "医薬品カウンター（薬剤師・登録販売者）", "駐車場 120台", "ネット注文 店舗受け取り", "Cymbal ポイント"],
     "radius_m": 150, "layout_id": "S02", "tagline": "生鮮・惣菜・ベーカリーまでそろう旗艦店"},
    {"store_id": "S03", "name": "Cymbal Retail 渋谷店", "short_name": "渋谷店", "lat": 35.6595, "lng": 139.7005,
     "address": "東京都渋谷区道玄坂", "phone": "03-0000-0303",
     "hours": {"open": "09:00", "close": "23:00"},
     "pharmacy": {"open": "10:00", "close": "19:00", "closed": "土日・祝日"},
     "services": ["生鮮食品・お惣菜", "ビューティーカウンセリング", "医薬品カウンター（薬剤師・登録販売者）", "調剤薬局",
                  "免税カウンター", "キャッシュレス決済", "Cymbal ポイント"],
     "radius_m": 150, "layout_id": "S03", "tagline": "コスメ充実の都市型スーパー＆ドラッグ"},
]

# fixture type -> (slot width cm, tiers, depth m of one face)
FTYPE = {
    "gondola": (30, 5, 0.45), "wall": (30, 5, 0.6), "endcap": (30, 5, 0.6), "multideck": (30, 5, 0.9),
    "reach_in": (30, 5, 0.9), "chest": (60, 1, 0.9), "flat_table": (60, 2, 0.9), "deli_table": (60, 2, 0.9),
    "flower": (60, 2, 0.6),
}

PARAMS = {
    "S01": dict(W=70.0, H=42.0, n_drug=4, n_groc=6, n_chill=4, n_frozen=2, n_chest=1, n_flat=3, n_deli=2),
    "S02": dict(W=72.0, H=46.0, n_drug=4, n_groc=6, n_chill=4, n_frozen=2, n_chest=1, n_flat=3, n_deli=2),
    "S03": dict(W=70.0, H=42.0, n_drug=4, n_groc=6, n_chill=4, n_frozen=2, n_chest=1, n_flat=3, n_deli=2),
}
MODULE = 3.6
PITCH_GONDOLA = 0.9 + 1.6
PITCH_ISLAND = 1.8 + 2.0


def _split(length: float, slot_w: int) -> list[float]:
    out = [MODULE] * int(length // MODULE + 1e-9)
    rem = length - len(out) * MODULE
    unit = 0.9 if slot_w == 30 else 1.2
    tail = math.floor(rem / unit + 1e-9) * unit
    if tail >= (1.8 if slot_w == 30 else 1.2) - 1e-9:
        out.append(round(tail, 2))
    return out


class Geo:
    def __init__(self, sid: str):
        self.sid = sid
        self.positions: dict[str, list[dict]] = {}
        self.body_n = 0

    def body(self) -> str:
        self.body_n += 1
        return f"{self.sid}-BD{self.body_n:03d}"

    def add(self, flow, ftype, x, y, w, h, face, body, length, run):
        sw, tiers, _ = FTYPE[ftype]
        self.positions.setdefault(flow, []).append(
            {"type": ftype, "x": round(x, 2), "y": round(y, 2), "w": round(w, 2), "h": round(h, 2), "face": face,
             "body_id": body, "length_cm": int(round(length * 100)), "tiers": tiers, "slot_width_cm": sw,
             "slots_per_tier": int(round(length * 100)) // sw, "run": run})

    def wall(self, flow, ftype, side, a0, a1, W, H, run, order=+1, align="start"):
        d = FTYPE[ftype][2]
        lens = _split(abs(a1 - a0), FTYPE[ftype][0])
        segs, pos = [], (min(a0, a1) if align == "start" else max(a0, a1) - sum(lens))
        for L in lens:
            segs.append((pos, L))
            pos += L
        if order < 0:
            segs.reverse()
        for s, L in segs:
            b = self.body()
            if side == "left":
                self.add(flow, ftype, 0, s, d, L, "E", b, L, run)
            elif side == "right":
                self.add(flow, ftype, W - d, s, d, L, "W", b, L, run)
            elif side == "back":
                self.add(flow, ftype, s, 0, L, d, "S", b, L, run)
            else:
                self.add(flow, ftype, s, H - d, L, d, "N", b, L, run)

    def island(self, flow, ftype, x, y0, y1, run, reverse=False, align="start"):
        """Vertical double-sided island: per module a W face and an E face sharing one body_id."""
        d = FTYPE[ftype][2]
        lens = _split(y1 - y0, FTYPE[ftype][0])
        segs, pos = [], (y0 if align == "start" else y1 - sum(lens))
        for L in lens:
            segs.append((pos, L))
            pos += L
        if reverse:
            segs.reverse()
        for s, L in segs:
            b = self.body()
            self.add(flow, ftype, x, s, d, L, "W", b, L, run)
            self.add(flow, ftype, x + d, s, d, L, "E", b, L, run)


def store_geometry(sid: str):
    P = PARAMS[sid]
    W, H = P["W"], P["H"]
    g = Geo(sid)
    y0 = 0.9 + 3.0                       # back aisle 3.0 m
    y1 = H - 8.4                         # front end of runs (endcap, promenade 3.0 m, registers)
    ya = round((y0 + y1) / 2 - 1.0, 1)   # cross aisle 2.0 m
    yb = ya + 2.0
    x = 0.9 + 2.4
    drug_x = [x + i * PITCH_GONDOLA for i in range(P["n_drug"])]
    x = drug_x[-1] + 0.9 + 2.0
    groc_x = [x + i * PITCH_GONDOLA for i in range(P["n_groc"])]
    x = groc_x[-1] + 0.9 + 2.0
    bands = {}
    for key in ("n_chill", "n_frozen", "n_chest", "n_flat"):
        bands[key] = [x + i * PITCH_ISLAND for i in range(P[key])]
        if P[key]:
            x = bands[key][-1] + PITCH_ISLAND
    right_aisle_x = W - 0.9 - 2.6
    if x - 2.0 > right_aisle_x + 1e-6:
        raise ValueError(f"{sid}: bands need {x - 2.0:.1f} m > {right_aisle_x:.1f} m")
    rx_y = round(y1 - 4.0, 2)
    # chill flow: right wall front->back, back wall right->left, left wall back->front, then islands
    g.wall("chill", "multideck", "right", 0.0, H - 9.0, W, H, "right wall", order=-1)
    g.wall("chill", "multideck", "back", 6.0, W - 0.9, W, H, "back wall", order=-1, align="end")
    g.wall("chill", "multideck", "left", 0.0, yb - 1.4, W, H, "left wall")
    for i, cx in enumerate(bands["n_chill"]):
        g.island("chill", "multideck", cx, y0, ya, f"chill {i + 1}a")
        g.island("chill", "multideck", cx, yb, y1, f"chill {i + 1}b")
    for i, fx in enumerate(bands["n_frozen"]):
        g.island("frozen", "reach_in", fx, y0, ya, f"frozen {i + 1}a")
        g.island("frozen", "reach_in", fx, yb, y1, f"frozen {i + 1}b")
    for i, cx in enumerate(bands["n_chest"]):
        g.island("chest", "chest", cx, yb, y1, f"chest {i + 1}b")
        g.island("chest", "chest", cx, y0, ya, f"chest {i + 1}a")
    for i, fx in enumerate(bands["n_flat"]):
        g.island("flat", "flat_table", fx, yb, y1, f"flat {i + 1}b", reverse=True)
    for i, fx in enumerate(bands["n_flat"]):
        g.island("flat", "flat_table", fx, y0, ya, f"flat {i + 1}a", reverse=True)
    for i, gx in enumerate(groc_x):
        g.island("grocery", "gondola", gx, y0, ya, f"groc {i + 1}a")
        g.island("grocery", "gondola", gx, yb, y1 - 0.6, f"groc {i + 1}b", align="end")
        g.add("endcap", "endcap", gx, y1 - 0.6, 0.9, 0.6, "S", g.body(), 0.9, f"endcap groc {i + 1}")
    for i, dx in enumerate(drug_x):
        g.island("drug", "gondola", dx, yb, y1 - 0.6, f"drug {i + 1}", align="end")
        g.add("endcap", "endcap", dx, y1 - 0.6, 0.9, 0.6, "S", g.body(), 0.9, f"endcap drug {i + 1}")
    g.wall("otc_wall", "wall", "left", yb, rx_y, W, H, "OTC wall", align="end")
    deli_w = drug_x[-1] + 0.9 - drug_x[0]
    n_deli = max(1, min(P["n_deli"], int((deli_w + 2.0) // PITCH_ISLAND)))
    for i in range(n_deli):
        g.island("deli", "deli_table", drug_x[0] + i * PITCH_ISLAND, y0, ya, f"deli {i + 1}")
    ent_x = round(right_aisle_x - 3.0, 2)
    reg_w = groc_x[min(len(groc_x) - 1, 4)] + 0.9 - drug_x[0]
    reg = {"x": round(drug_x[0], 2), "y": round(H - 4.6, 2), "w": round(reg_w, 2), "h": 1.4}
    rx0 = reg["x"] + reg["w"] + 1.6
    # flower stands along the front wall between the exit and the entrance (customers pass them on the way in)
    g.wall("flower", "flower", "front", rx0 + 5.0, ent_x - 1.8, W, H, "flowers", order=-1)
    g.add("register", "gondola", rx0, reg["y"] - 0.65, 0.45, 2.7, "E", g.body(), 2.7, "register impulse")
    counter = {"id": "RX", "label": "医薬品カウンター・調剤", "x": 0, "y": rx_y, "w": 1.2, "h": 3.6}
    main_aisles = [
        [1.0, 1.0, round(W - 2.0, 2), 2.8],                                          # back aisle
        [round(drug_x[0], 2), round(y1 + 0.1, 2), round(right_aisle_x - drug_x[0], 2), 2.8],  # front promenade
        [round(right_aisle_x + 0.05, 2), 1.0, 2.5, round(H - 10.2, 2)],               # right perimeter
        [round(drug_x[0], 2), round(ya + 0.05, 2), round(right_aisle_x - drug_x[0], 2), 1.9],  # cross aisle
    ]
    base = {"store_id": sid, "version": 2, "width": W, "height": H,
            "entrance": {"x": ent_x, "y": H}, "exit": {"x": round(rx0 + 3.0, 2), "y": H},
            "register": reg, "counter": counter, "main_aisles": main_aisles}
    return base, g


# ------------------------------------------------------------------------------------------- validation
def obstacles(layout: dict):
    obs = [(f["id"], f["x"], f["y"], f["w"], f["h"]) for f in layout["fixtures"] if f["type"] != "counter"]
    r, c = layout["register"], layout["counter"]
    obs.append(("REGISTER", r["x"], r["y"], r["w"], r["h"]))
    obs.append((c["id"], c["x"], c["y"], c["w"], c["h"]))
    return obs


def rect_gap(a, b) -> float:
    dx = max(0.0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    dy = max(0.0, max(a[2], b[2]) - min(a[2] + a[4], b[2] + b[4]))
    return math.hypot(dx, dy)


def rect_overlap(a, b) -> bool:
    return (min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]) > 1e-6 and
            min(a[2] + a[4], b[2] + b[4]) - max(a[2], b[2]) > 1e-6)


class Grid:
    def __init__(self, layout: dict, res: float = GRID_M):
        self.res = res
        self.W, self.H = layout["width"], layout["height"]
        self.nx, self.ny = int(round(self.W / res)), int(round(self.H / res))
        self.blocked = bytearray(self.nx * self.ny)
        for _, x, y, w, h in obstacles(layout):
            i0, i1 = int(math.floor(x / res + 1e-9)), int(math.ceil((x + w) / res - 1e-9))
            j0, j1 = int(math.floor(y / res + 1e-9)), int(math.ceil((y + h) / res - 1e-9))
            i0, i1 = max(0, i0), min(self.nx, i1)
            for j in range(max(0, j0), min(self.ny, j1)):
                b = j * self.nx
                self.blocked[b + i0:b + i1] = b"\x01" * (i1 - i0)

    def cell(self, x, y):
        return (min(self.nx - 1, max(0, int(x / self.res))), min(self.ny - 1, max(0, int(y / self.res))))

    def walkable(self, x, y) -> bool:
        if not (0 <= x <= self.W and 0 <= y <= self.H):
            return False
        i, j = self.cell(x, y)
        return not self.blocked[j * self.nx + i]

    def reach_from(self, x, y) -> bytearray:
        seen = bytearray(self.nx * self.ny)
        i, j = self.cell(min(max(x, 0.05), self.W - 0.05), min(max(y, 0.05), self.H - 0.05))
        s = j * self.nx + i
        if self.blocked[s]:
            return seen
        seen[s] = 1
        q = deque([s])
        nx, n, bl = self.nx, self.nx * self.ny, self.blocked
        while q:
            c = q.popleft()
            ci = c % nx
            if ci < nx - 1 and not seen[c + 1] and not bl[c + 1]:
                seen[c + 1] = 1
                q.append(c + 1)
            if ci > 0 and not seen[c - 1] and not bl[c - 1]:
                seen[c - 1] = 1
                q.append(c - 1)
            if c + nx < n and not seen[c + nx] and not bl[c + nx]:
                seen[c + nx] = 1
                q.append(c + nx)
            if c - nx >= 0 and not seen[c - nx] and not bl[c - nx]:
                seen[c - nx] = 1
                q.append(c - nx)
        return seen


def face_probe(f: dict, frac: float = 0.5, off: float = 0.3):
    x, y, w, h, face = f["x"], f["y"], f["w"], f["h"], f["face"]
    if face == "N":
        return (x + w * frac, y - off)
    if face == "S":
        return (x + w * frac, y + h + off)
    if face == "E":
        return (x + w + off, y + h * frac)
    return (x - off, y + h * frac)


def slot_points(f: dict):
    """Slot centres (plan metres) left->right as seen by a customer standing in front of the face."""
    n = f["slots_per_tier"]
    x, y, w, h, face = f["x"], f["y"], f["w"], f["h"], f["face"]
    pts = []
    for k in range(n):
        t = (k + 0.5) / n
        if face == "S":      # customer south, looking north: left = west
            pts.append((x + w * t, y + h / 2))
        elif face == "N":    # customer north, looking south: left = east
            pts.append((x + w * (1 - t), y + h / 2))
        elif face == "W":    # customer west, looking east: left = north
            pts.append((x + w / 2, y + h * t))
        else:                # E: customer east, looking west: left = south
            pts.append((x + w / 2, y + h * (1 - t)))
    return [(round(a, 2), round(b, 2)) for a, b in pts]


def validate_layout(layout: dict) -> list[str]:
    errs: list[str] = []
    W, H = layout["width"], layout["height"]
    obs = obstacles(layout)
    ids = [f["id"] for f in layout["fixtures"]]
    if len(ids) != len(set(ids)):
        errs.append("duplicate fixture ids")
    for f in layout["fixtures"]:
        if f["type"] == "counter":
            if f["id"] != "RX":
                errs.append(f"bad counter id {f['id']}")
            continue
        if not re.fullmatch(r"[A-HJ-NP-U]\d\d", f["id"]):
            errs.append(f"bad fixture id {f['id']}")
        if f["slots_per_tier"] < 1:
            errs.append(f"{f['id']} has no slots")
    for o in obs:
        _, x, y, w, h = o
        if x < -1e-9 or y < -1e-9 or x + w > W + 1e-9 or y + h > H + 1e-9:
            errs.append(f"{o[0]} out of bounds")
        strips = {"left": (0, y, x, h), "top": (x, 0, w, y), "right": (x + w, y, W - x - w, h),
                  "bottom": (x, y + h, w, H - y - h)}
        for name, d in (("left", x), ("top", y), ("right", W - x - w), ("bottom", H - y - h)):
            if 1e-6 < d < MIN_AISLE_M - 1e-6:
                sx, sy, sw, sh = strips[name]
                pts = [(sx + sw * (a + 0.5) / 4, sy + sh * (b + 0.5) / 4) for a in range(4) for b in range(4)]
                others = [p for p in obs if p[0] != o[0]]
                if not all(any(p[1] <= px <= p[1] + p[3] and p[2] <= py <= p[2] + p[4] for p in others)
                           for px, py in pts):
                    errs.append(f"{o[0]} leaves a {d:.2f} m gap to the {name} wall")
    srt = sorted(obs, key=lambda o: o[1])
    for i in range(len(srt)):
        a = srt[i]
        for j in range(i + 1, len(srt)):
            b = srt[j]
            if b[1] > a[1] + a[3] + MIN_AISLE_M:
                break
            if rect_overlap(a, b):
                errs.append(f"{a[0]} overlaps {b[0]}")
                continue
            d = rect_gap(a, b)
            if 1e-6 < d < MIN_AISLE_M - 1e-6:
                errs.append(f"aisle {a[0]}-{b[0]} only {d:.2f} m")
    for r in layout.get("main_aisles", []):
        if min(r[2], r[3]) < MAIN_AISLE_M - 1e-6:
            errs.append(f"main aisle {r} narrower than {MAIN_AISLE_M}")
        for o in obs:
            if rect_overlap(("m", *r), o):
                errs.append(f"main aisle {r} blocked by {o[0]}")
    grid = Grid(layout)
    ent = layout["entrance"]
    reach = grid.reach_from(ent["x"], ent["y"] - 0.3 if ent["y"] >= H - 1e-6 else ent["y"])
    if not any(reach):
        return errs + ["entrance not walkable"]

    def ok(px, py):
        if not grid.walkable(px, py):
            return False
        i, j = grid.cell(px, py)
        return bool(reach[j * grid.nx + i])

    for f in layout["fixtures"]:
        for frac in (0.25, 0.5, 0.75):
            if not ok(*face_probe(f, frac)):
                errs.append(f"{f['id']} face {f['face']} not reachable at {frac:.2f}")
                break
    c = layout["counter"]
    if not ok(c["x"] + c["w"] + 0.3, c["y"] + c["h"] / 2):
        errs.append("RX counter not reachable")
    r = layout["register"]
    if not any(ok(*p) for p in ((r["x"] + r["w"] / 2, r["y"] - 0.3), (r["x"] + r["w"] / 2, r["y"] + r["h"] + 0.3))):
        errs.append("register not reachable")
    ex = layout["exit"]
    if not ok(ex["x"], ex["y"] - 0.3 if ex["y"] >= H - 1e-6 else ex["y"]):
        errs.append("exit not reachable")
    return errs
