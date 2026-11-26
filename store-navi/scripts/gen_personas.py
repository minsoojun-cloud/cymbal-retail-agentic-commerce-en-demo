#!/usr/bin/env python3
"""Generate Store Navi personas, drugstore purchase history, coupons and store offers (deterministic).

Outputs (store-navi/data/):
  store_personas.json          CR-0001 (existing persona summary + 子どものかぜ対策), CR-0002 ひろし, CR-0003 美咲
  store_purchase_history.jsonl 6 months of drugstore purchases for CR-0002 / CR-0003 (cymbal.purchase_history schema)
  coupons.json                 common + store + personalized coupons (winter theme かぜ・乾燥対策)
  offers.json                  store-specific 特価 (8-15 per store; picked from in-stock assortment)

Run AFTER build_store_data.py inventory (offers are picked from store_inventory.jsonl). Does not modify seed/personas/.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store_layout_defs as defs  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "store-navi", "data")
CATALOG = os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl")
PERSONAS_SRC = os.path.join(ROOT, "seed", "personas", "personas.json")
SEED = 20261112
HIST_START = dt.date(2026, 5, 12)
HIST_END = dt.date(2026, 11, 11)
OTC, BE, DA, BA, DR = defs.OTC, defs.BE, defs.DA, defs.BA, defs.DR


def h01(*parts) -> float:
    d = hashlib.sha256(("|".join(map(str, parts)) + f"|{SEED}").encode()).digest()
    return int.from_bytes(d[:8], "big") / 2 ** 64


def attr1(p, key, default=None):
    for a in p.get("attributes", []):
        if a.get("key") == key:
            v = a.get("value", {})
            vals = v.get("text") or v.get("numbers")
            return vals[0] if vals else default
    return default


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ------------------------------------------------------------------------------------------------ personas
def build_personas() -> list[dict]:
    src = {p["customer_id"]: p for p in json.load(open(PERSONAS_SRC, encoding="utf-8"))}
    s = src.get("CR-0001", {})
    sakura = {
        "customer_id": "CR-0001", "name": s.get("name", "さくら"), "name_kana": s.get("name_kana", "サクラ"),
        "age": s.get("age", 38), "gender": s.get("gender", "女性"), "residence": s.get("residence", "神奈川県川崎市"),
        "household": s.get("household", []), "household_size": s.get("household_size", 4),
        "allergies": s.get("allergies", []),
        "concerns": ["子どものかぜ対策（蓮くん10歳・陽菜ちゃん7歳）", "家族の体調管理（発熱時の備え）", "冬の乾燥対策"],
        "interest_categories": [f"{OTC} > かぜ薬", f"{OTC} > 解熱鎮痛薬", f"{BE} > 衛生・救急用品", f"{DR} > スポーツドリンク",
                                f"{DA} > 衛生用品", f"{BA}"],
        "notes": "Cymbal Retail ネットスーパーの既存会員（週末まとめ買い）。川崎駅前店を通勤帰りに利用。"
                 "子どもが2人いるため、かぜ・発熱シーズンは子ども用のかぜ薬・冷却シート・経口補水液をまとめて準備したい。"
                 "長女はえびアレルギー。",
        "home_store_id": "S01", "avatar": "🌸",
        "loyalty": s.get("loyalty", {"tier": "ゴールド"}),
        "source": "seed/personas/personas.json (summary; full profile unchanged in cymbal.customers)",
    }
    hiroshi = {
        "customer_id": "CR-0002", "name": "ひろし", "name_kana": "ヒロシ", "age": 67, "gender": "男性",
        "residence": "神奈川県川崎市中原区",
        "household": [{"relation": "本人", "name": "ひろし", "age": 67}, {"relation": "妻", "name": "和子", "age": 64}],
        "household_size": 2, "allergies": [],
        "concerns": ["血圧が気になる（かかりつけ医で治療中）", "膝の痛み（階段の上り下り）", "肩こり"],
        "interest_categories": [f"{OTC} > 外用薬・湿布", f"{BE} > 健康グッズ", f"{OTC} > ビタミン剤・滋養強壮",
                                f"{DR} > 健康茶・青汁", f"{BE} > 大人用おむつ・介護用品"],
        "notes": "定年後、妻と二人暮らし。毎朝の散歩が日課。湿布（温湿布・冷湿布）とひざサポーター、健康茶を定期的に購入。"
                 "血圧の薬を処方されており、市販薬は薬剤師に相談してから選ぶ。調剤は川崎駅前店・武蔵小杉店を利用。",
        "home_store_id": "S02", "avatar": "🧓",
        "loyalty": {"tier": "シルバー", "member_since": "2019-06-01", "points_balance": 1520},
        "medication_note": "降圧薬を服用中（デモ用の架空設定）→ 市販薬は医師・薬剤師に相談",
    }
    misaki = {
        "customer_id": "CR-0003", "name": "美咲", "name_kana": "ミサキ", "age": 26, "gender": "女性",
        "residence": "東京都世田谷区",
        "household": [{"relation": "本人", "name": "美咲", "age": 26}], "household_size": 1, "allergies": [],
        "concerns": ["敏感肌（季節の変わり目に肌荒れしやすい）", "目の疲れ（PC作業が多い）", "冬の乾燥"],
        "interest_categories": [f"{BE} > スキンケア", f"{OTC} > 目薬", f"{BE} > ボディケア", f"{BE} > 美容雑貨",
                                f"{BE} > ヘアケア"],
        "notes": "渋谷のIT企業で働くデザイナー。仕事帰りに渋谷店に立ち寄る。低刺激・無香料のスキンケアを好み、"
                 "疲れ目用の目薬・ホットアイマスクをよく購入。新作コスメ・限定品のチェックが好き。",
        "home_store_id": "S03", "avatar": "💄",
        "loyalty": {"tier": "ゴールド", "member_since": "2023-04-01", "points_balance": 3260},
    }
    common = {"note": "デモ用の架空ペルソナです（実在の人物・会員情報ではありません）"}
    return [{**p, **common} for p in (sakura, hiroshi, misaki)]


# ------------------------------------------------------------------------------------------------ history
HIST_PLAN = {
    "CR-0002": {
        "interval": (9, 16), "items": (2, 5), "store": "S02", "exclude_title": ["女性", "ベビー", "キッズ", "子ども"],
        "staples": [  # (predicate, weight) recurring favourites
            (lambda p: f"{OTC} > 外用薬・湿布 > 温湿布" in cat(p), 0.55),
            (lambda p: f"{OTC} > 外用薬・湿布 > 冷湿布" in cat(p), 0.25),
            (lambda p: "健康茶・青汁" in cat(p), 0.5),
            (lambda p: "ひざサポーター" in cat(p), 0.08),
            (lambda p: f"{OTC} > ビタミン剤・滋養強壮" in cat(p) and attr1(p, "drug_class") != "第1類医薬品", 0.3),
            (lambda p: "血圧計" in cat(p), 0.03),
        ],
        "fillers": [f"{BE} > オーラルケア > 入れ歯洗浄剤", f"{BE} > シェービング", f"{DA} > 紙製品", f"{DR} > お茶飲料",
                    f"{BE} > 健康グッズ", f"{DA} > 衛生用品", f"{BE} > ボディケア > 薬用入浴剤", f"{BE} > 大人用おむつ・介護用品 > 尿もれパッド"],
    },
    "CR-0003": {
        "interval": (6, 12), "items": (2, 5), "store": "S03", "exclude_title": ["メンズ", "ベビー", "キッズ", "子ども", "入れ歯"],
        "staples": [
            (lambda p: "敏感肌" in p["title"] and f"{BE} > スキンケア" in cat(p), 0.6),
            (lambda p: f"{OTC} > 目薬 > 疲れ目" in cat(p) or f"{OTC} > 目薬 > ドライアイ" in cat(p), 0.45),
            (lambda p: "ホットアイマスク" in cat(p), 0.25),
            (lambda p: "フェイスマスク" in cat(p), 0.35),
            (lambda p: "リップケア" in cat(p) or "ハンドクリーム" in cat(p), 0.3),
        ],
        "fillers": [f"{BE} > スキンケア > 日焼け止め", f"{BE} > ヘアケア", f"{BE} > 美容雑貨", f"{BE} > 生理用品",
                    f"{DR} > 水・炭酸水", f"{BE} > ボディケア > 入浴剤", f"{BE} > スキンケア > クレンジングジェル",
                    f"{defs.SW} > チョコレート"],
    },
}


def cat(p):
    return p["categories"][0]


def build_history(products) -> list[dict]:
    rows = []
    sellable = [p for p in products if attr1(p, "abuse_control", "false") != "true"]
    for cid, plan in HIST_PLAN.items():
        rng = random.Random(f"{SEED}-{cid}")
        ex = plan.get("exclude_title", [])
        pool_all = [p for p in sellable if not any(w in p["title"] for w in ex)]
        staple_pools = []
        for pred, w in plan["staples"]:
            pool = sorted([p for p in pool_all if pred(p)], key=lambda p: p["id"])
            if pool:
                # each customer has 1-2 favourite SKUs per staple
                fav = sorted(pool, key=lambda p: h01("fav", cid, p["id"]))[:2]
                staple_pools.append((fav, w))
        filler_pools = [sorted([p for p in pool_all if cat(p).startswith(f)], key=lambda p: p["id"])
                        for f in plan["fillers"]]
        filler_pools = [fp for fp in filler_pools if fp]
        d = HIST_START + dt.timedelta(days=rng.randint(0, 5))
        seq = 0
        while d <= HIST_END:
            seq += 1
            order_id = f"{cid.replace('-', '')}-{d:%Y%m%d}-{seq:04d}"
            items = {}
            for fav, w in staple_pools:
                if rng.random() < w:
                    p = rng.choice(fav)
                    items[p["id"]] = p
            n_target = rng.randint(*plan["items"])
            guard = 0
            while len(items) < n_target and guard < 20:
                guard += 1
                p = rng.choice(rng.choice(filler_pools))
                items[p["id"]] = p
            for p in items.values():
                q = 2 if ("湿布" in cat(p) and rng.random() < 0.3) else 1
                price = int(round(p["priceInfo"]["price"]))
                rows.append({"order_id": order_id, "customer_id": cid, "order_date": d.isoformat(),
                             "product_id": p["id"], "product_title": p["title"], "category": cat(p), "quantity": q,
                             "unit_price_tax_included": price, "amount": price * q})
            d += dt.timedelta(days=rng.randint(*plan["interval"]))
    return rows


# ------------------------------------------------------------------------------------------------ coupons
def build_coupons(products) -> list[dict]:
    by_title = {}
    for p in products:
        by_title.setdefault(p["title"], p["id"])
    sensitive = sorted([p["id"] for p in products if "敏感肌" in p["title"] and f"{BE} > スキンケア" in cat(p)])[:6]
    c = [
        # common
        {"coupon_id": "CP-WINTER-01", "customer_id": None, "store_id": None, "title": "かぜ・乾燥対策 応援クーポン",
         "description": "冷却シート・マスク・のどケア用品が10%OFF", "discount_type": "percent", "discount_value": 10,
         "target": {"categories": [f"{BE} > 衛生・救急用品 > 冷却シート", f"{BE} > 衛生・救急用品 > 子ども用冷却シート",
                                   f"{DA} > 衛生用品 > マスク", f"{DA} > 衛生用品 > マスク（子ども用）",
                                   f"{OTC} > のど・せき > 薬用のどドロップ"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "冬のかぜ・乾燥対策シーズン", "color": "#1a73e8"},
        {"coupon_id": "CP-WINTER-02", "customer_id": None, "store_id": None, "title": "保湿ケア 2点以上で15%OFF",
         "description": "ハンドクリーム・リップ・ボディクリームを2点以上お買い上げで15%OFF", "discount_type": "percent",
         "discount_value": 15,
         "target": {"categories": [f"{BE} > ボディケア > ハンドクリーム", f"{BE} > スキンケア > リップケア",
                                   f"{BE} > ボディケア > ボディクリーム", f"{BE} > ボディケア > かかとクリーム"],
                    "product_ids": []},
         "valid_until": "2026-12-15", "reason": "空気が乾燥する季節に", "color": "#e91e63"},
        {"coupon_id": "CP-DRINK-01", "customer_id": None, "store_id": None, "title": "経口補水液・スポーツドリンク 50円引き",
         "description": "対象の経口補水液・スポーツドリンク1本につき50円引き", "discount_type": "yen", "discount_value": 50,
         "target": {"categories": [f"{DR} > スポーツドリンク"], "product_ids": []},
         "valid_until": "2026-11-30", "reason": "発熱時の水分補給の備えに", "color": "#00acc1"},
        # store
        {"coupon_id": "CP-S01-01", "customer_id": None, "store_id": "S01", "title": "川崎駅前店 夜トクポイント",
         "description": "19時以降のお買い物でポイント2倍", "discount_type": "points", "discount_value": 2,
         "target": {"categories": [], "product_ids": []}, "valid_until": "2026-12-15",
         "reason": "通勤帰りのお買い物に", "color": "#f9ab00"},
        {"coupon_id": "CP-S02-01", "customer_id": None, "store_id": "S02", "title": "武蔵小杉店 冷凍食品 20%OFF",
         "description": "冷凍食品・アイスが全品20%OFF（武蔵小杉店限定）", "discount_type": "percent", "discount_value": 20,
         "target": {"categories": [defs.FZ], "product_ids": []}, "valid_until": "2026-11-30",
         "reason": "食品もそろう武蔵小杉店限定", "color": "#34a853"},
        {"coupon_id": "CP-S03-01", "customer_id": None, "store_id": "S03", "title": "渋谷店 スキンケア 10%OFF",
         "description": "スキンケア全品10%OFF（渋谷店限定）", "discount_type": "percent", "discount_value": 10,
         "target": {"categories": [f"{BE} > スキンケア"], "product_ids": []}, "valid_until": "2026-12-15",
         "reason": "ビューティー旗艦店限定", "color": "#ea4335"},
        # personalized CR-0001
        {"coupon_id": "CP-CR0001-01", "customer_id": "CR-0001", "store_id": None, "title": "お子さまのかぜ対策 100円引き",
         "description": "子ども用冷却シート・子ども用マスク・キッズ飲料が100円引き（1,000円以上）", "discount_type": "yen",
         "discount_value": 100,
         "target": {"categories": [f"{BE} > 衛生・救急用品 > 子ども用冷却シート", f"{DA} > 衛生用品 > マスク（子ども用）",
                                   f"{BA} > キッズ飲料"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "お子さま（蓮くん・陽菜ちゃん）のかぜ・発熱シーズンの備えに", "color": "#ff7043"},
        {"coupon_id": "CP-CR0001-02", "customer_id": "CR-0001", "store_id": None, "title": "体温計・救急用品 ポイント3倍",
         "description": "体温計・絆創膏など衛生・救急用品がポイント3倍", "discount_type": "points", "discount_value": 3,
         "target": {"categories": [f"{BE} > 衛生・救急用品"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "ご家族の体調管理に", "color": "#7e57c2"},
        # personalized CR-0002
        {"coupon_id": "CP-CR0002-01", "customer_id": "CR-0002", "store_id": None, "title": "湿布・外用薬 15%OFF",
         "description": "温湿布・冷湿布・消炎鎮痛テープが15%OFF", "discount_type": "percent", "discount_value": 15,
         "target": {"categories": [f"{OTC} > 外用薬・湿布"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "よく購入される湿布・外用薬", "color": "#1e88e5"},
        {"coupon_id": "CP-CR0002-02", "customer_id": "CR-0002", "store_id": None, "title": "サポーター・健康グッズ 200円引き",
         "description": "ひざ・腰サポーター、血圧計など健康グッズが200円引き（1,500円以上）", "discount_type": "yen",
         "discount_value": 200, "target": {"categories": [f"{BE} > 健康グッズ"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "毎日の散歩とひざのケアに", "color": "#43a047"},
        {"coupon_id": "CP-CR0002-03", "customer_id": "CR-0002", "store_id": None, "title": "健康茶・青汁 ポイント3倍",
         "description": "健康茶・青汁がポイント3倍", "discount_type": "points", "discount_value": 3,
         "target": {"categories": [f"{DR} > 健康茶・青汁"], "product_ids": []},
         "valid_until": "2026-11-30", "reason": "定期的にご購入の健康茶", "color": "#8d6e63"},
        # personalized CR-0003
        {"coupon_id": "CP-CR0003-01", "customer_id": "CR-0003", "store_id": None, "title": "敏感肌スキンケア 15%OFF",
         "description": "敏感肌用の化粧水・乳液・クリーム・洗顔が15%OFF", "discount_type": "percent", "discount_value": 15,
         "target": {"categories": [], "product_ids": sensitive},
         "valid_until": "2026-12-15", "reason": "よく購入される低刺激スキンケア", "color": "#d81b60"},
        {"coupon_id": "CP-CR0003-02", "customer_id": "CR-0003", "store_id": None, "title": "目薬・アイケア 100円引き",
         "description": "目薬・ホットアイマスクが100円引き", "discount_type": "yen", "discount_value": 100,
         "target": {"categories": [f"{OTC} > 目薬", f"{BE} > 健康グッズ > ホットアイマスク"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "よく購入されるアイケア用品", "color": "#3949ab"},
        {"coupon_id": "CP-CR0003-03", "customer_id": "CR-0003", "store_id": "S03", "title": "渋谷店 フェイスマスク 2点で20%OFF",
         "description": "フェイスマスクを2点以上で20%OFF（渋谷店）", "discount_type": "percent", "discount_value": 20,
         "target": {"categories": [f"{BE} > スキンケア > フェイスマスク"], "product_ids": []},
         "valid_until": "2026-11-30", "reason": "乾燥する季節のスペシャルケアに", "color": "#ab47bc"},
    ]
    return c


# ------------------------------------------------------------------------------------------------ offers
OFFER_THEMES = {
    "S01": [("かぜ・乾燥対策", [f"{BE} > 衛生・救急用品 > 冷却シート", f"{DA} > 衛生用品 > マスク", f"{DR} > スポーツドリンク",
                         f"{OTC} > のど・せき > トローチ", f"{OTC} > のど・せき > 薬用のどドロップ", f"{BE} > ボディケア > ハンドクリーム",
                         f"{BE} > スキンケア > リップケア", f"{OTC} > 外用薬・湿布"], 7),
            ("駅前 日用品特価", [f"{DA} > 紙製品", f"{DA} > 洗濯用品", f"{BE} > オーラルケア > 歯磨き粉"], 4)],
    "S02": [("かぜ・乾燥対策", [f"{BE} > 衛生・救急用品 > 冷却シート", f"{DA} > 衛生用品 > マスク", f"{DR} > スポーツドリンク",
                         f"{OTC} > のど・せき > トローチ", f"{OTC} > 外用薬・湿布", f"{BE} > ボディケア > ハンドクリーム"], 5),
            ("まとめ買い 日用品", [f"{DA} > 紙製品", f"{DA} > 洗濯用品", f"{BA} > ベビー用品 > 紙おむつ"], 4),
            ("冷凍食品・食品", [defs.FZ, f"{defs.IN} > カップ麺"], 4)],
    "S03": [("冬の保湿ケア", [f"{BE} > スキンケア > 化粧水", f"{BE} > スキンケア > 乳液", f"{BE} > スキンケア > 保湿クリーム",
                        f"{BE} > スキンケア > フェイスマスク", f"{BE} > スキンケア > リップケア", f"{BE} > ボディケア > ハンドクリーム"], 7),
            ("かぜ・乾燥対策", [f"{DA} > 衛生用品 > マスク", f"{OTC} > 目薬 > 疲れ目", f"{BE} > 健康グッズ > ホットアイマスク"], 3)],
}
COMMON_OFFER_CATS = [f"{BE} > 衛生・救急用品 > 冷却シート", f"{DR} > スポーツドリンク > 経口補水液"]


def build_offers(products, scenarios=None) -> list[dict]:
    inv_path = os.path.join(DATA, "store_inventory.jsonl")
    stock = {}
    if os.path.exists(inv_path):
        for line in open(inv_path, encoding="utf-8"):
            r = json.loads(line)
            stock[(r["store_id"], r["product_id"])] = r["quantity"]
    by_id = {p["id"]: p for p in products}
    excluded = set()
    if scenarios and scenarios.get("cold_tablet_out_at_S01", {}).get("product_id"):
        excluded.add(scenarios["cold_tablet_out_at_S01"]["product_id"])

    def eligible(p):
        return (attr1(p, "abuse_control", "false") != "true" and attr1(p, "drug_class", "") != "第1類医薬品"
                and p["id"] not in excluded)

    def mk(store_id, p, theme, badge):
        orig = int(round(p["priceInfo"].get("originalPrice") or p["priceInfo"]["price"]))
        rate = 0.10 + 0.15 * h01("rate", store_id, p["id"])
        sale = int(orig * (1 - rate)) // 10 * 10 + 8
        sale = min(sale, orig - 10)
        return {"store_id": store_id, "product_id": p["id"], "sale_price": sale, "original_price": orig,
                "start_date": "2026-11-12", "end_date": "2026-11-30" if h01("end", store_id, p["id"]) < 0.5 else "2026-12-15",
                "badge": badge, "theme": theme, "title": p["title"]}

    offers = []
    # common (all stores): one per common category, must be carried & in stock everywhere
    for c in COMMON_OFFER_CATS:
        cands = sorted([p for p in products if cat(p).startswith(c) and eligible(p)
                        and all(stock.get((s, p["id"]), 0) > 0 for s in ("S01", "S02", "S03"))],
                       key=lambda p: h01("common", p["id"]))
        if cands:
            offers.append({**mk(None, cands[0], "かぜ・乾燥対策", "全店特価")})
    used = {o["product_id"] for o in offers}
    for sid, themes in OFFER_THEMES.items():
        for theme, prefixes, n in themes:
            picked = 0
            for i in range(200):
                if picked >= n:
                    break
                pre = prefixes[i % len(prefixes)]
                cands = sorted([p for p in products if cat(p).startswith(pre) and eligible(p) and p["id"] not in used
                                and stock.get((sid, p["id"]), 0) > 0], key=lambda p: h01("offer", sid, p["id"]))
                if not cands:
                    continue
                p = cands[0]
                used.add(p["id"])
                badge = "店舗限定特価" if i % 3 else "今週の特価"
                offers.append(mk(sid, p, theme, badge))
                picked += 1
    return offers


def main():
    products = load_catalog()
    personas = build_personas()
    write_json(os.path.join(DATA, "store_personas.json"), personas)
    hist = build_history(products)
    with open(os.path.join(DATA, "store_purchase_history.jsonl"), "w", encoding="utf-8") as f:
        for r in hist:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    coupons = build_coupons(products)
    write_json(os.path.join(DATA, "coupons.json"), coupons)
    sc_path = os.path.join(DATA, "scenarios.json")
    scen = json.load(open(sc_path, encoding="utf-8")) if os.path.exists(sc_path) else None
    offers = build_offers(products, scen)
    write_json(os.path.join(DATA, "offers.json"), offers)
    per_store = {}
    for o in offers:
        per_store[o["store_id"] or "ALL"] = per_store.get(o["store_id"] or "ALL", 0) + 1
    print("personas", len(personas), "history rows", len(hist),
          {c: len({r["order_id"] for r in hist if r["customer_id"] == c}) for c in HIST_PLAN},
          "coupons", len(coupons), "offers", len(offers), per_store)


if __name__ == "__main__":
    main()
