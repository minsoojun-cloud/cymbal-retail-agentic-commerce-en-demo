"""English catalog checks (CONTRACT_EN.md, owner: data).

Run: python -m pytest -q tests/test_catalog_en.py
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "seed", "catalog", "products_retail.jsonl")
JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef\u3000-\u303f]")
TOPS = ["Produce", "Seafood", "Meat", "Deli & Meal Kits", "Frozen", "Rice & Noodles", "Eggs & Dairy", "Pantry",
        "Seasonings & Sauces", "Snacks & Sweets", "Tofu & Natto", "Bakery", "Beverages", "Alcohol", "Household",
        "Beauty & Personal Care", "Baby", "Pet", "Home & Living", "Kitchen", "Health & Medicine", "Flowers"]
DRUG_CLASSES = {"Pharmacist-only (Class 1)", "Designated Class 2", "Class 2", "Class 3", "Quasi-drug"}


def attrs(p):
    return {a["key"]: a["value"] for a in p.get("attributes", [])}


@pytest.fixture(scope="module")
def products():
    return [json.loads(l) for l in open(CATALOG, encoding="utf-8") if l.strip()]


@pytest.fixture(scope="module")
def ids(products):
    return {p["id"] for p in products}


def test_count_and_unique(products, ids):
    assert 2800 <= len(products) <= 3300
    assert len(ids) == len(products)


def test_language_currency_type(products):
    for p in products:
        assert p["languageCode"] == "en", p["id"]
        assert p["priceInfo"]["currencyCode"] == "JPY", p["id"]
        assert p["type"] == "PRIMARY"


def test_no_japanese_except_title_ja(products):
    bad = []
    for p in products:
        q = dict(p)
        q["attributes"] = [a for a in p["attributes"] if a["key"] != "title_ja"]
        if JP.search(json.dumps(q, ensure_ascii=False)):
            bad.append(p["id"])
    assert not bad, bad[:20]


def test_title_ja_present(products):
    assert all(attrs(p).get("title_ja", {}).get("text") for p in products)


def test_all_top_categories(products):
    tops = {attrs(p)["top_category"]["text"][0] for p in products}
    assert set(TOPS) == tops
    for p in products:
        assert p["categories"][0].split(" > ")[0] in TOPS, p["id"]


def test_channel_on_all(products):
    seen = set()
    for p in products:
        ch = attrs(p).get("channel", {}).get("text", [None])[0]
        assert ch in ("omni", "store_only", "online_only"), p["id"]
        seen.add(ch)
    assert seen == {"omni", "store_only", "online_only"}


def test_ids_no_kfm(ids):
    assert not [i for i in ids if i.upper().startswith("KFM")]


def test_uris_point_to_site(products):
    for p in products:
        assert p["uri"].endswith(f"/?product={p['id']}")
        assert "904006867101" not in p["uri"]
        for im in p.get("images", []):
            assert "/img/products/" in im["uri"] and "904006867101" not in im["uri"]


def test_otc(products):
    otc = [p for p in products if "drug_class" in attrs(p)]
    assert len(otc) >= 149
    for p in otc:
        assert attrs(p)["drug_class"]["text"][0] in DRUG_CLASSES
        ch = attrs(p)["channel"]["text"][0]
        if attrs(p)["drug_class"]["text"][0].startswith("Pharmacist-only"):
            assert ch == "store_only", p["id"]


def test_allergens_and_storage_english(products):
    for p in products:
        a = attrs(p)
        for v in a.get("allergens", {}).get("text", []):
            assert v == v.lower() and not JP.search(v), (p["id"], v)
        for v in a.get("storage_type", {}).get("text", []):
            assert v in ("Ambient", "Chilled", "Frozen"), (p["id"], v)


def test_recipe_ingredients_exist(ids):
    recipes = json.load(open(os.path.join(ROOT, "seed", "catalog", "recipes_en.json"), encoding="utf-8"))
    assert len(recipes) == 60
    missing = [(r["recipe_id"], i["product_id"]) for r in recipes for i in r["ingredients"] if i["product_id"] not in ids]
    assert not missing, missing[:10]
    assert not JP.search(json.dumps(recipes, ensure_ascii=False))


def test_purchase_history_in_subset(ids):
    rows = [json.loads(l) for l in open(os.path.join(ROOT, "seed", "personas", "purchase_history.jsonl"), encoding="utf-8")]
    assert rows and all(r["product_id"] in ids for r in rows)


def test_store_inventory_channel_consistent(products, ids):
    inv_path = os.path.join(ROOT, "store-navi", "data", "store_inventory.jsonl")
    inv = [json.loads(l) for l in open(inv_path, encoding="utf-8")]
    ch = {p["id"]: attrs(p)["channel"]["text"][0] for p in products}
    stocked = {}
    for r in inv:
        assert r["product_id"] in ids
        assert r["store_id"] in ("S01", "S02", "S03")
        assert ch[r["product_id"]] != "online_only", r
        if r["quantity"] > 0:
            stocked.setdefault(r["product_id"], set()).add(r["store_id"])
    store_only = [i for i, c in ch.items() if c == "store_only"]
    assert all(i in stocked for i in store_only), [i for i in store_only if i not in stocked][:10]


def test_stores_english():
    stores = json.load(open(os.path.join(ROOT, "store-navi", "data", "stores.json"), encoding="utf-8"))
    assert [s["store_id"] for s in stores] == ["S01", "S02", "S03"]
    assert {s["name"] for s in stores} == {"Cymbal Retail Kawasaki Station", "Cymbal Retail Musashi-Kosugi",
                                          "Cymbal Retail Shibuya"}
    assert not JP.search(json.dumps(stores, ensure_ascii=False))
