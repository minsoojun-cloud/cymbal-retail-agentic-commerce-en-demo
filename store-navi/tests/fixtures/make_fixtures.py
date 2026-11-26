"""Generate small, deterministic development fixtures in tests/fixtures/data (CONTRACT v8 file formats).

Used until the data worker's real files land in store-navi/data, and always by the unit tests (DATA_DIR pinned).
    python3 store-navi/tests/fixtures/make_fixtures.py
Products: real catalog ids (data/products_retail.jsonl) + ~22 fictional OTC items (products_extra.jsonl).
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data"
REPO = HERE.parents[2]
CATALOG = REPO / "seed" / "catalog" / "products_retail.jsonl"
BASE = (__import__("os").environ.get("SITE_BASE_URL") or "https://cymbal-site.example.test").rstrip("/") + "/img/products/"

OTC = [  # id, title, sub path, drug_class, symptoms, age_min, abuse, price, image
    ("4999100000011", "Cymbal ヘルスケア 総合かぜ薬 錠剤 30錠", "かぜ薬 > 総合かぜ薬", "指定第2類医薬品", ["発熱", "のどの痛み", "鼻水", "せき", "悪寒"], 15, False, 1078),
    ("4999100000028", "Cymbal ヘルスケア 総合かぜ薬 顆粒 12包", "かぜ薬 > 総合かぜ薬", "指定第2類医薬品", ["発熱", "のどの痛み", "せき", "頭痛"], 15, False, 1408),
    ("4999100000035", "Cymbal ヘルスケア こどもかぜシロップ いちご味 120ml", "かぜ薬 > 子ども用かぜ薬", "第2類医薬品", ["発熱", "鼻水", "せき"], 3, False, 968),
    ("4999100000042", "Cymbal ヘルスケア 葛根湯エキス顆粒 10包", "かぜ薬 > 漢方かぜ薬", "第2類医薬品", ["かぜのひきはじめ", "頭痛", "肩こり"], 7, False, 1298),
    ("4999100000059", "Cymbal メディカル 解熱鎮痛薬 A錠 24錠", "解熱鎮痛薬 > 解熱鎮痛薬", "第2類医薬品", ["発熱", "頭痛", "歯痛", "生理痛"], 15, False, 858),
    ("4999100000066", "Cymbal メディカル 解熱鎮痛薬 やさしい処方 20錠", "解熱鎮痛薬 > 解熱鎮痛薬", "第2類医薬品", ["発熱", "頭痛"], 7, False, 748),
    ("4999100000073", "Cymbal メディカル 解熱鎮痛薬 EX 12錠", "解熱鎮痛薬 > 解熱鎮痛薬", "第1類医薬品", ["頭痛", "発熱", "生理痛"], 15, False, 1298),
    ("4999100000080", "Cymbal ヘルスケア せき止めシロップ 120ml", "のど・せき > せき止め", "指定第2類医薬品", ["せき", "たん"], 12, True, 1188),
    ("4999100000097", "Cymbal ヘルスケア のどスプレー 25ml", "のど・せき > のどスプレー", "第3類医薬品", ["のどの痛み", "のどのはれ"], 5, False, 880),
    ("4999100000103", "Cymbal ヘルスケア のどトローチ はちみつレモン 24粒", "のど・せき > トローチ", "医薬部外品", ["のどのあれ", "声がれ"], 5, False, 528),
    ("4999100000110", "Cymbal メディカル 鼻炎カプセル 24カプセル", "鼻炎薬 > 鼻炎内服薬", "指定第2類医薬品", ["鼻水", "鼻づまり", "くしゃみ"], 15, True, 1408),
    ("4999100000127", "Cymbal メディカル 点鼻薬 30ml", "鼻炎薬 > 点鼻薬", "第2類医薬品", ["鼻づまり", "鼻水"], 7, False, 968),
    ("4999100000134", "Cymbal ヘルスケア 総合胃腸薬 顆粒 16包", "胃腸薬 > 総合胃腸薬", "第2類医薬品", ["胃もたれ", "胸やけ", "食べすぎ"], 15, False, 1078),
    ("4999100000141", "Cymbal ヘルスケア 整腸剤 120錠", "胃腸薬 > 整腸剤", "第3類医薬品", ["整腸", "便秘", "軟便"], 5, False, 1078),
    ("4999100000158", "Cymbal アイケア 疲れ目 目薬 12ml", "目薬 > 疲れ目", "第2類医薬品", ["目の疲れ", "かすみ目"], 7, False, 748),
    ("4999100000165", "Cymbal アイケア やさしい目薬 敏感 15ml", "目薬 > ドライアイ", "第3類医薬品", ["目の乾き", "目の疲れ"], 7, False, 638),
    ("4999100000172", "Cymbal メディカル 冷感湿布 24枚", "外用薬・湿布 > 冷湿布", "第3類医薬品", ["肩こり", "腰痛", "関節痛"], 15, False, 858),
    ("4999100000189", "Cymbal メディカル 温感湿布 24枚", "外用薬・湿布 > 温湿布", "第2類医薬品", ["肩こり", "腰痛"], 15, False, 968),
    ("4999100000196", "Cymbal メディカル 消炎鎮痛ゲル 50g", "外用薬・湿布 > 消炎鎮痛ゲル", "第2類医薬品", ["関節痛", "筋肉痛", "膝の痛み"], 15, False, 1298),
    ("4999100000202", "Cymbal ヘルスケア ビタミンB群 錠剤 90錠", "ビタミン剤・滋養強壮 > ビタミン剤", "第3類医薬品", ["肩こり", "目の疲れ"], 15, False, 1628),
    ("4999100000219", "Cymbal ヘルスケア 栄養ドリンク 100ml×3本", "ビタミン剤・滋養強壮 > 栄養ドリンク", "医薬部外品", ["滋養強壮", "疲労"], 15, False, 548),
    ("4999100000226", "Cymbal ヘルスケア かゆみ止めクリーム 30g", "皮膚薬・その他 > かゆみ止め", "第2類医薬品", ["かゆみ", "虫さされ", "湿疹"], 1, False, 858),
]

FEVER_SET = ["4999100000059", "4900025493107", "4921789214258", "4906832949998", "4953631900798"]
COLD_TABLET = "4999100000011"
BEAUTY_OUT = "4991674785415"   # ミナモリ 敏感肌用 泡洗顔 (out at S03, in stock at S01)


def otc_records() -> list[dict]:
    out = []
    for pid, title, sub, dc, sym, age, abuse, price in OTC:
        brand = " ".join(title.split()[:2])
        attrs = [{"key": "top_category", "value": {"text": ["医薬品・ヘルスケア"]}},
                 {"key": "drug_class", "value": {"text": [dc]}},
                 {"key": "symptoms", "value": {"text": sym}},
                 {"key": "age_min", "value": {"numbers": [float(age)]}},
                 {"key": "abuse_control", "value": {"text": ["true" if abuse else "false"]}},
                 {"key": "on_sale", "value": {"text": ["false"]}},
                 {"key": "size", "value": {"text": [title.split()[-1]]}}]
        out.append({"id": pid, "type": "PRIMARY", "title": title, "brands": [brand],
                    "categories": [f"医薬品・ヘルスケア > {sub}"],
                    "description": f"{'・'.join(sym)}の症状をやわらげます（デモ用の架空医薬品です）。パッケージ記載の用法・用量を守ってご使用ください。",
                    "languageCode": "ja", "attributes": attrs,
                    "priceInfo": {"currencyCode": "JPY", "price": price, "originalPrice": price},
                    "availability": "IN_STOCK", "images": [{"uri": BASE + f"v_otc_fixture_{pid[-3:]}_p.jpg"}]})
    return out


def gondolas_s01() -> list[dict]:
    otc = "医薬品・ヘルスケア"
    return [
        {"id": "A-01", "zone": "Z-OTC", "label": "かぜ薬・解熱鎮痛薬", "x": 0.3, "y": 3.0, "w": 1.0, "h": 4.0, "tiers": 5, "faces": ["E"],
         "categories": [f"{otc} > かぜ薬", f"{otc} > 解熱鎮痛薬", f"{otc} > のど・せき", f"{otc} > 鼻炎薬"]},
        {"id": "A-02", "zone": "Z-OTC", "label": "胃腸薬・目薬・湿布", "x": 0.3, "y": 7.6, "w": 1.0, "h": 4.0, "tiers": 5, "faces": ["E"],
         "categories": [f"{otc} > 胃腸薬", f"{otc} > 目薬", f"{otc} > 外用薬・湿布", f"{otc} > 皮膚薬・その他", f"{otc} > ビタミン剤・滋養強壮"]},
        {"id": "B-01", "zone": "Z-CARE", "label": "衛生・救急用品", "x": 4.0, "y": 3.4, "w": 1.2, "h": 3.6, "tiers": 5, "faces": ["W", "E"],
         "categories": ["ビューティー・パーソナルケア > 衛生・救急用品", "日用品・キッチン消耗品 > 衛生用品", "ビューティー・パーソナルケア > 健康グッズ"]},
        {"id": "B-02", "zone": "Z-DRINK", "label": "飲料・水分補給", "x": 4.0, "y": 8.0, "w": 1.2, "h": 3.6, "tiers": 5, "faces": ["W", "E"],
         "categories": ["飲料・水"]},
        {"id": "C-01", "zone": "Z-COS", "label": "スキンケア", "x": 8.5, "y": 5.0, "w": 3.2, "h": 1.2, "tiers": 4, "faces": ["N", "S"],
         "categories": ["ビューティー・パーソナルケア > スキンケア"]},
        {"id": "C-02", "zone": "Z-COS", "label": "ヘア・ボディケア", "x": 13.0, "y": 5.0, "w": 3.2, "h": 1.2, "tiers": 4, "faces": ["N", "S"],
         "categories": ["ビューティー・パーソナルケア > ヘアケア", "ビューティー・パーソナルケア > ボディケア", "ビューティー・パーソナルケア > ヘアスタイリング"]},
        {"id": "C-03", "zone": "Z-COS", "label": "オーラル・美容雑貨", "x": 8.5, "y": 8.4, "w": 3.2, "h": 1.2, "tiers": 4, "faces": ["N", "S"],
         "categories": ["ビューティー・パーソナルケア > オーラルケア", "ビューティー・パーソナルケア > 美容雑貨"]},
        {"id": "C-04", "zone": "Z-COS", "label": "身だしなみ", "x": 13.0, "y": 8.4, "w": 3.2, "h": 1.2, "tiers": 4, "faces": ["N", "S"],
         "categories": ["ビューティー・パーソナルケア"]},
        {"id": "D-01", "zone": "Z-DAILY", "label": "洗剤・掃除用品", "x": 19.0, "y": 1.5, "w": 1.2, "h": 4.2, "tiers": 5, "faces": ["W", "E"],
         "categories": ["日用品・キッチン消耗品 > 住居用洗剤", "日用品・キッチン消耗品 > 台所用洗剤", "日用品・キッチン消耗品 > 掃除用品", "日用品・キッチン消耗品 > 洗濯用品"]},
        {"id": "D-02", "zone": "Z-DAILY", "label": "紙製品・日用消耗品", "x": 19.0, "y": 6.6, "w": 1.2, "h": 4.2, "tiers": 5, "faces": ["W", "E"],
         "categories": ["日用品・キッチン消耗品"]},
        {"id": "E-01", "zone": "Z-BABY", "label": "ベビー用品", "x": 9.0, "y": 0.4, "w": 7.0, "h": 1.0, "tiers": 5, "faces": ["S"],
         "categories": ["ベビー・キッズ食品"]},
        {"id": "F-01", "zone": "Z-MAG", "label": "季節・特売", "x": 9.0, "y": 12.4, "w": 3.5, "h": 1.0, "tiers": 4, "faces": ["N", "S"],
         "categories": ["お菓子・製菓"]},
    ]


def layout_for(sid: str) -> dict:
    g = gondolas_s01()
    w, h = 24.0, 16.0
    if sid == "S02":
        w, h = 30.0, 18.0
        g.append({"id": "H-01", "zone": "Z-FOOD", "label": "食品・インスタント", "x": 24.0, "y": 2.0, "w": 1.2, "h": 6.0,
                  "tiers": 5, "faces": ["W", "E"], "categories": ["インスタント・レトルト", "調味料"]})
    if sid == "S03":
        w = 26.0
        g.append({"id": "C-05", "zone": "Z-COS", "label": "メイクアップ", "x": 22.0, "y": 5.0, "w": 3.0, "h": 1.2,
                  "tiers": 4, "faces": ["N", "S"], "categories": ["ビューティー・パーソナルケア > 美容雑貨"]})
    return {
        "store_id": sid, "version": 1, "width": w, "height": h,
        "entrance": {"x": 14.5, "y": h - 0.4}, "exit": {"x": 13.0, "y": h - 0.4},
        "register": {"x": 15.5, "y": h - 3.6, "w": 4.0, "h": 1.2},
        "counter": {"id": "RX", "label": "医薬品カウンター・調剤", "x": 0.5, "y": 0.4, "w": 6.0, "h": 1.2},
        "zones": [
            {"id": "Z-RX", "label": "調剤・医薬品カウンター", "color": "#e6f4ea", "rect": [0, 0, 8, 2.4]},
            {"id": "Z-OTC", "label": "医薬品", "color": "#e8f0fe", "rect": [0, 2.4, 2.8, 9.6]},
            {"id": "Z-CARE", "label": "衛生・救急", "color": "#fef7e0", "rect": [2.8, 2.4, 4.4, 5.0]},
            {"id": "Z-DRINK", "label": "飲料", "color": "#e4f7fb", "rect": [2.8, 7.4, 4.4, 4.6]},
            {"id": "Z-COS", "label": "コスメ・ビューティー", "color": "#fce8f3", "rect": [8, 4, 9, 6.4]},
            {"id": "Z-BABY", "label": "ベビー", "color": "#fff3e0", "rect": [8, 0, 9, 2.4]},
            {"id": "Z-DAILY", "label": "日用品", "color": "#f1f3f4", "rect": [17.6, 0, w - 17.6, 11.6]},
            {"id": "Z-MAG", "label": "季節・特売", "color": "#fde7e9", "rect": [8, 11.6, 5.5, 2.8]},
        ],
        "gondolas": g,
        "signs": [{"text": "医薬品", "x": 1.4, "y": 2.8}, {"text": "コスメ", "x": 12.5, "y": 4.4},
                  {"text": "日用品", "x": 21.0, "y": 0.9}],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "layouts").mkdir(exist_ok=True)
    rnd = random.Random(20261112)
    otc = otc_records()
    with open(OUT / "products_extra.jsonl", "w", encoding="utf-8") as f:
        for r in otc:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stores = [
        {"store_id": "S01", "name": "Cymbal Retail 川崎駅前店", "short_name": "川崎駅前店", "lat": 35.5313, "lng": 139.6977,
         "address": "神奈川県川崎市川崎区駅前本町", "phone": "044-000-0101", "hours": {"open": "09:00", "close": "22:00"},
         "pharmacy": {"open": "09:00", "close": "19:00", "closed": "日曜・祝日"}, "services": ["調剤", "Cymbal Pay", "免税"],
         "radius_m": 150, "layout_id": "S01", "tagline": "駅前すぐ・調剤併設のコンパクト店"},
        {"store_id": "S02", "name": "Cymbal Retail 武蔵小杉店", "short_name": "武蔵小杉店", "lat": 35.5763, "lng": 139.6596,
         "address": "神奈川県川崎市中原区小杉町", "phone": "044-000-0202", "hours": {"open": "10:00", "close": "23:00"},
         "pharmacy": {"open": "10:00", "close": "20:00", "closed": "日曜"}, "services": ["調剤", "食品", "駐車場"],
         "radius_m": 150, "layout_id": "S02", "tagline": "食品も揃う大型店"},
        {"store_id": "S03", "name": "Cymbal Retail 渋谷店", "short_name": "渋谷店", "lat": 35.6595, "lng": 139.7005,
         "address": "東京都渋谷区道玄坂", "phone": "03-0000-0303", "hours": {"open": "10:00", "close": "22:00"},
         "pharmacy": None, "services": ["コスメカウンセリング", "免税"], "radius_m": 150, "layout_id": "S03",
         "tagline": "ビューティー旗艦店"},
    ]
    (OUT / "stores.json").write_text(json.dumps(stores, ensure_ascii=False, indent=1), encoding="utf-8")
    layouts = {s["store_id"]: layout_for(s["store_id"]) for s in stores}
    for sid, lay in layouts.items():
        (OUT / "layouts" / f"{sid}.json").write_text(json.dumps(lay, ensure_ascii=False, indent=1), encoding="utf-8")

    # carried products = drugstore categories from the real catalog (+ OTC)
    tops = ("医薬品・ヘルスケア", "ビューティー・パーソナルケア", "日用品・キッチン消耗品", "飲料・水 > スポーツドリンク", "飲料・水 > お茶飲料",
            "ベビー・キッズ食品 > ベビー用品", "お菓子・製菓 > グミ・キャンディ")
    prods: list[tuple[str, str, bool]] = []
    for line in open(CATALOG, encoding="utf-8"):
        d = json.loads(line)
        cat = d["categories"][0]
        if cat.startswith(tops):
            abuse = any(a.get("key") == "abuse_control" and (a.get("value") or {}).get("text") == ["true"]
                        for a in d.get("attributes") or [])
            prods.append((d["id"], cat, abuse))
    prods.sort()
    for r in otc:
        abuse = any(a["key"] == "abuse_control" and a["value"]["text"] == ["true"] for a in r["attributes"])
        prods.append((r["id"], r["categories"][0], abuse))

    inv, shelf = [], []
    ts = "2026-11-12T00:00:00+00:00"
    for s in stores:
        sid = s["store_id"]
        gs = layouts[sid]["gondolas"]
        for pid, cat, abuse in prods:
            keep = 0.92 if sid != "S03" or "ビューティー" in cat else 0.6
            if rnd.random() > keep and pid not in FEVER_SET and pid != COLD_TABLET and pid != BEAUTY_OUT:
                continue
            q = max(0, int(rnd.gauss(14, 8)))
            if rnd.random() < 0.07:
                q = 0
            if pid == COLD_TABLET:
                q = {"S01": 0, "S02": 12, "S03": 5}[sid]
            if pid in FEVER_SET and sid == "S01":
                q = max(q, 8)
            if pid == BEAUTY_OUT:
                q = {"S01": 9, "S02": 4, "S03": 0}[sid]
            inv.append({"store_id": sid, "product_id": pid, "quantity": q,
                        "availability": "IN_STOCK" if q > 0 else "OUT_OF_STOCK", "updated_at": ts})
            if abuse:
                g = {"id": "RX", "faces": ["S"], "tiers": 0}
            else:
                cands = [(len(c), g) for g in gs for c in g["categories"] if cat.replace(" > ", ">").startswith(c.replace(" > ", ">"))]
                if not cands:
                    continue
                g = max(cands, key=lambda x: x[0])[1]
            shelf.append({"store_id": sid, "product_id": pid, "gondola_id": g["id"],
                          "side": g["faces"][rnd.randrange(len(g["faces"]))] if g["id"] != "RX" else None,
                          "tier": rnd.randint(1, g["tiers"]) if g["tiers"] else None, "position": rnd.randint(1, 6),
                          "photo_uri": None, "source": "seed", "registered_by": "seed", "registered_at": ts, "note": ""})
    with open(OUT / "store_inventory.jsonl", "w", encoding="utf-8") as f:
        for r in inv:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT / "shelf_locations.jsonl", "w", encoding="utf-8") as f:
        for r in shelf:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    personas = [
        {"customer_id": "CR-0001", "name": "さくら", "age": 38, "gender": "女性", "avatar": "👩",
         "concerns": ["子どものかぜ対策", "家族の健康管理"], "interest_categories": ["医薬品・ヘルスケア > かぜ薬", "ビューティー・パーソナルケア > 衛生・救急用品", "ベビー・キッズ食品"],
         "notes": "4人家族（小学生と幼児の子ども）。えびアレルギーの家族あり。"},
        {"customer_id": "CR-0002", "name": "ひろし", "age": 67, "gender": "男性", "avatar": "👴",
         "concerns": ["血圧が気になる", "膝の痛み"], "interest_categories": ["医薬品・ヘルスケア > 外用薬・湿布", "医薬品・ヘルスケア > ビタミン剤・滋養強壮"],
         "notes": "高血圧で通院・服薬中。湿布とサプリをよく購入。"},
        {"customer_id": "CR-0003", "name": "美咲", "age": 26, "gender": "女性", "avatar": "👩‍🦰",
         "concerns": ["敏感肌", "目の疲れ"], "interest_categories": ["ビューティー・パーソナルケア > スキンケア", "医薬品・ヘルスケア > 目薬"],
         "notes": "デスクワーク。低刺激コスメ・目薬をよく購入。"},
    ]
    (OUT / "store_personas.json").write_text(json.dumps(personas, ensure_ascii=False, indent=1), encoding="utf-8")
    hist = [{"customer_id": "CR-0002", "product_id": "4999100000172", "category": "医薬品・ヘルスケア > 外用薬・湿布 > 冷湿布", "quantity": 3, "order_date": "2026-10-20"},
            {"customer_id": "CR-0003", "product_id": "4999100000158", "category": "医薬品・ヘルスケア > 目薬 > 疲れ目", "quantity": 2, "order_date": "2026-10-28"},
            {"customer_id": "CR-0001", "product_id": "4947447231153", "category": "ビューティー・パーソナルケア > 衛生・救急用品 > 子ども用冷却シート", "quantity": 2, "order_date": "2026-10-02"}]
    with open(OUT / "store_purchase_history.jsonl", "w", encoding="utf-8") as f:
        for r in hist:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    coupons = [
        {"coupon_id": "CP-0001", "customer_id": "CR-0001", "store_id": None, "title": "お子さまのかぜ対策 200円引き", "description": "子ども用かぜ薬・冷却シート 1,000円以上で",
         "discount_type": "yen", "discount_value": 200, "target": {"categories": ["医薬品・ヘルスケア > かぜ薬", "ビューティー・パーソナルケア > 衛生・救急用品"], "product_ids": []},
         "valid_until": "2026-12-31", "reason": "よく購入される冷却シートに", "color": "#1a73e8"},
        {"coupon_id": "CP-0002", "customer_id": "CR-0002", "store_id": None, "title": "湿布・外用薬 15%OFF", "description": "外用薬・湿布が全品対象",
         "discount_type": "percent", "discount_value": 15, "target": {"categories": ["医薬品・ヘルスケア > 外用薬・湿布"], "product_ids": []},
         "valid_until": "2026-12-31", "reason": "よく購入される湿布に", "color": "#188038"},
        {"coupon_id": "CP-0003", "customer_id": "CR-0003", "store_id": None, "title": "敏感肌スキンケア ポイント5倍", "description": "低刺激スキンケアが対象",
         "discount_type": "points", "discount_value": 5, "target": {"categories": ["ビューティー・パーソナルケア > スキンケア"], "product_ids": []},
         "valid_until": "2026-12-31", "reason": "いつもの敏感肌ケアに", "color": "#c5221f"},
        {"coupon_id": "CP-0101", "customer_id": None, "store_id": "S01", "title": "川崎駅前店 冬のかぜ対策 10%OFF", "description": "マスク・のど飴・冷却シート",
         "discount_type": "percent", "discount_value": 10, "target": {"categories": ["日用品・キッチン消耗品 > 衛生用品"], "product_ids": []},
         "valid_until": "2026-12-15", "reason": "", "color": "#f29900"},
        {"coupon_id": "CP-0201", "customer_id": None, "store_id": None, "title": "Cymbal Pay 決済でポイント2倍", "description": "全店・全商品",
         "discount_type": "points", "discount_value": 2, "target": {"categories": [], "product_ids": []},
         "valid_until": "2026-12-31", "reason": "", "color": "#5f6368"},
    ]
    (OUT / "coupons.json").write_text(json.dumps(coupons, ensure_ascii=False, indent=1), encoding="utf-8")
    offers = [
        {"store_id": None, "product_id": "4900025493107", "sale_price": 398, "original_price": 498, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "特価", "theme": "冬のかぜ対策"},
        {"store_id": "S01", "product_id": "4921789214258", "sale_price": 158, "original_price": 181, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "店舗特価", "theme": "冬のかぜ対策"},
        {"store_id": None, "product_id": "4999100000172", "sale_price": 748, "original_price": 858, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "特価", "theme": "乾燥・冷え対策"},
        {"store_id": None, "product_id": "4999100000158", "sale_price": 658, "original_price": 748, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "特価", "theme": "目の疲れ"},
        {"store_id": None, "product_id": "4953631900798", "sale_price": 298, "original_price": 398, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "特価", "theme": "冬のかぜ対策"},
        {"store_id": "S03", "product_id": "4961626629296", "sale_price": 1848, "original_price": 2178, "start_date": "2026-11-01", "end_date": "2026-12-31", "badge": "旗艦店特価", "theme": "乾燥肌対策"},
    ]
    (OUT / "offers.json").write_text(json.dumps(offers, ensure_ascii=False, indent=1), encoding="utf-8")
    scen = {"cold_tablet_out_at_S01": {"product_id": COLD_TABLET, "in_stock_at": "S02"},
            "fever_set_S01": FEVER_SET,
            "beauty_out_at_S03": {"product_id": BEAUTY_OUT, "in_stock_at": "S01"},
            "staff_photo_products": []}
    (OUT / "scenarios.json").write_text(json.dumps(scen, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"fixtures: {len(otc)} OTC, {len(inv)} inventory rows, {len(shelf)} shelf rows → {OUT}")


if __name__ == "__main__":
    main()
