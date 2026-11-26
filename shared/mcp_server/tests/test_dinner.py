"""Unit tests for recommend_dinner_set (dinner.py + server wiring). No network: catalog / profile / history are fakes.

Run:
  cd shared/mcp_server && uv run --no-project --with-requirements requirements.txt --with pytest pytest -q tests
"""
import asyncio
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dinner  # noqa: E402
import server  # noqa: E402

TODAY = dt.date(2026, 11, 4)
JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# English persona fixture (shape of seed/personas/personas.json CR-0001, contract v14).
PERSONA = {
    "customer_id": "CR-0001", "name": "Sakura Tanaka", "age": 38, "household_size": 4,
    "household": [{"relation": "Self", "name": "Sakura", "age": 38}, {"relation": "Husband", "name": "Ken", "age": 40},
                  {"relation": "Daughter", "name": "Yui", "age": 9}, {"relation": "Son", "name": "Haru", "age": 6}],
    "family_preferences": [
        {"relation": "Self", "name": "Sakura", "call_name": "Sakura", "age": 38, "likes": ["Fish", "Japanese food", "Lots of vegetables"],
         "ng": [], "like_keywords": [{"label": "fish", "keywords": ["salmon", "mackerel", "yellowtail", "saury", "cod"]},
                                     {"label": "vegetables", "keywords": ["spinach", "komatsuna", "broccoli", "shimeji", "maitake", "napa cabbage", "cabbage"]}]},
        {"relation": "Husband", "name": "Ken", "call_name": "Ken", "age": 40, "likes": ["Meat dishes", "Grilled fish"], "ng": [],
         "like_keywords": [{"label": "meat dishes", "keywords": ["sliced pork", "pork loin", "pork belly", "chicken thigh", "ginger pork", "tonkatsu"]},
                           {"label": "grilled fish", "keywords": ["salted mackerel", "hokke", "salted salmon"]}]},
        {"relation": "Daughter", "name": "Yui", "call_name": "Yui", "age": 9, "likes": ["Hamburg steak", "Karaage", "Medium-hot curry"],
         "ng": ["shrimp (allergy)"], "allergies": ["shrimp"],
         "like_keywords": [{"label": "hamburg steak", "keywords": ["hamburg"]}, {"label": "karaage", "keywords": ["karaage", "fried chicken"]},
                           {"label": "medium-hot curry", "keywords": ["medium hot", "medium-hot"]}]},
        {"relation": "Son", "name": "Haru", "call_name": "Haru", "age": 6, "likes": ["Sweet omelet", "Corn", "Fruit"],
         "ng": ["shrimp (allergy)"], "allergies": ["shrimp"],
         "like_keywords": [{"label": "sweet omelet", "keywords": ["sweet omelet", "tamagoyaki", "omelet"]}, {"label": "corn", "keywords": ["corn"]},
                           {"label": "fruit", "keywords": ["mandarin", "apple", "persimmon", "grape", "pear", "banana", "kiwi"]}]},
    ],
    "allergies": [{"member": "Daughter", "allergen": "shrimp", "severity": "must avoid"},
                  {"member": "Son", "allergen": "shrimp", "severity": "must avoid"}],
    "exclude_keywords": ["shrimp", "prawn"],
    "taste_preferences": {"curry_spiciness": "medium-hot"},
    "cooking_style": "Quick meals under 20 min on weekdays (pre-cut vegetables, marinated meat, meal kits)",
}


def row(pid, title, cat, price, original=None, sale=False, seasonal=False, size="", allergens=(), avail="IN_STOCK",
        months=None, desc="", channel="omni"):
    attrs = [{"key": "on_sale", "value": {"text": ["true" if sale else "false"]}},
             {"key": "discount_rate", "value": {"numbers": [20.0 if sale else 0.0]}},
             {"key": "is_seasonal", "value": {"text": ["true" if seasonal else "false"]}},
             {"key": "size", "value": {"text": [size]}},
             {"key": "allergens", "value": {"text": list(allergens) or ["none"]}},
             {"key": "is_food", "value": {"text": ["true"]}},
             {"key": "channel", "value": {"text": [channel]}}]
    if months:
        attrs.append({"key": "season_months", "value": {"numbers": [float(m) for m in months]}})
    return {"id": pid, "title": title, "categories": [cat], "attributes": attrs, "description": desc,
            "priceInfo": {"price": float(price), "originalPrice": float(original or price)}, "availability": avail,
            "images": [{"uri": f"https://img/{pid}.jpg"}], "rating": {"averageRating": 4.2, "ratingCount": 50}}


CATALOG_ROWS = [
    row("F1", "Hokkaido Fresh Autumn Salmon Fillets 4 pcs (approx. 320g)", "Seafood > Salmon > Fillets", 640, 800, sale=True, seasonal=True, size="4 pcs (approx. 320g)"),
    row("F2", "Norwegian Salted Mackerel Fillets 4 pcs", "Seafood > Salted Fish > Salted Mackerel", 473, size="4 pcs"),
    row("X1", "Shrimp Tempura 4 pcs", "Deli & Meal Kits > Fried Foods > Tempura", 500, sale=True, allergens=["shrimp", "wheat"]),
    row("X2", "Peeled Prawns for Hot Pot 200g", "Seafood > Shrimp & Crab > Peeled Shrimp", 600, sale=True, seasonal=True),
    row("X3", "Seafood Shumai 8 pcs", "Deli & Meal Kits > Chinese Dishes > Shumai", 300, allergens=["shrimp"]),
    row("X4", "Domestic Chicken Thigh for Karaage 2kg Bulk Pack", "Meat > Chicken > Thigh", 1800, sale=True),
    row("X5", "Hokkaido Fresh Autumn Salmon Fillets 8 pcs", "Seafood > Salmon > Fillets", 900, sale=True, seasonal=True, avail="OUT_OF_STOCK"),
    row("X6", "Cymbal Kids' Miso Soup from 12 Months 5 servings", "Baby > Toddler Food > Soups", 321),
    row("X7", "Extra Hot Mala Pork Stir-fry Meal Kit 2 servings", "Deli & Meal Kits > Meal Kits > Chinese Meal Kits", 700, sale=True),
    row("X8", "Cooked In-store Salmon Shioyaki 1 serving", "Deli & Meal Kits > Grilled Dishes > Grilled Fish", 350, channel="store_only"),
    row("M1", "Domestic Chicken Karaage (Thigh) approx. 400g", "Deli & Meal Kits > Fried Foods > Karaage", 645, size="approx. 400g"),
    row("M2", "Cymbal Beef & Pork Hamburg Steak 4 pcs", "Frozen > Frozen Meat Dishes > Hamburg Steak", 538, sale=True, size="4 pcs"),
    row("M3", "Canadian Sliced Pork 400g", "Meat > Pork > Sliced Pork", 516, size="400g"),
    row("S1", "Cymbal Sweet Omelet Bites 8 pcs (thaw and serve)", "Frozen > Frozen Bento Sides > Omelet", 267, size="8 pcs", allergens=["egg"]),
    row("S2", "Cymbal Sweet Corn Whole Kernel 190g", "Pantry > Canned Goods > Canned Corn", 98, sale=True),
    row("S3", "Domestic Spinach 1 bunch", "Produce > Leafy Greens > Spinach", 198, seasonal=True),
    row("S4", "Nagano Shimeji Mushrooms 1 pack", "Produce > Mushrooms > Shimeji", 108, months=[9, 10, 11]),
    row("S5", "Domestic Napa Cabbage 1/2 cut", "Produce > Leafy Greens > Napa Cabbage", 170, sale=True, seasonal=True),
    row("S6", "Potato Salad 200g", "Deli & Meal Kits > Salads > Potato Salad", 300),
    row("P1", "Cymbal Fresh Miso Soup Tofu 10 servings", "Rice & Noodles > Instant Soup & Miso Soup > Instant Miso Soup", 278, sale=True),
    row("P2", "Domestic Soybean Silken Tofu 300g", "Tofu & Natto > Tofu > Silken Tofu", 98),
    row("N1", "Cymbal Yosenabe Hot Pot Soup Base 3-4 servings 750g", "Seasonings & Sauces > Hot Pot Soup Bases > Yosenabe", 213, seasonal=True),
    row("N2", "Domestic Chicken Meatballs for Hot Pot 300g", "Meat > Chicken > Hot Pot & Seasoned", 494, seasonal=True, size="300g"),
    row("N3", "Cymbal Boiled Udon 180g x 3 servings", "Rice & Noodles > Noodles > Udon", 98),
    row("C1", "Cymbal Curry Roux Medium Hot 8 servings", "Seasonings & Sauces > Curry & Stew > Curry Roux", 138),
    row("C2", "Domestic Pork Shoulder Loin Cubes for Curry 350g", "Meat > Pork > Cubes & Blocks", 516, size="350g"),
    row("R1", "Cymbal Packaged Rice 200g x 3", "Rice & Noodles > Packaged Rice > White Rice", 298),
    row("D1", "Aomori Apples (Fuji) 3 pcs", "Produce > Fruit > Apples", 498, 598, sale=True, seasonal=True),
    row("D2", "Aichi Jiro Persimmons 4 pcs", "Produce > Fruit > Persimmons", 473, seasonal=True),
]
CATALOG = [dinner.normalize_product(r) for r in CATALOG_ROWS]
HISTORY = [
    {"product_id": "M3", "product_title": "Canadian Sliced Pork 400g", "category": "Meat > Pork > Sliced Pork", "times": 9},
    {"product_id": "S4", "product_title": "Nagano Shimeji Mushrooms 1 pack", "category": "Produce > Mushrooms > Shimeji", "times": 6},
    {"product_id": "Z9", "product_title": "Domestic Cabbage 1 head", "category": "Produce > Vegetables > Leafy Greens", "times": 5},
]


def rec(request="", **kw):
    return dinner.recommend(PERSONA, HISTORY, CATALOG, request=request, customer_id="CR-0001", today=kw.pop("today", TODAY), **kw)


def all_items(res):
    return [i for s in res["sets"] for i in s["items"]]


# ── composition ──────────────────────────────────────────────────────────────

def test_primary_set_is_balanced():
    res = rec("Recommend something for dinner tonight")
    assert 1 <= len(res["sets"]) <= 3
    p = res["sets"][0]
    roles = [i["role"] for i in p["items"]]
    assert 4 <= len(p["items"]) <= 5
    assert roles[0] == "Main" and len(set(roles)) >= 3
    assert p["kind"] == "fish"  # default: Japanese set meal for Sakura
    assert p["total_tax_included"] == sum(i["subtotal"] for i in p["items"])
    for s in res["sets"]:
        assert 4 <= len(s["items"]) <= 5 and s["name"] and s["concept"]
    assert res["servings"] == 4 and res["theme"].startswith("Tonight's dinner for Sakura's family")


def test_roles_and_tags_vocabulary():
    assert dinner.ROLE_ORDER == ["Main", "Side", "Soup", "Staple", "Dessert & fruit"]
    assert dinner.TAG_ORDER == ["Frequently bought", "Family favorite", "On sale", "In season", "Quick"]


def test_output_is_english():
    for req in ("", "meat please", "hot pot", "curry", "fish"):
        assert not JA_RE.search(json.dumps(rec(req), ensure_ascii=False))


def test_allergen_bulk_oos_dislikes_store_only_excluded():
    ids = {i["product_id"] for i in all_items(rec("What should we have for dinner tonight?"))}
    ids |= {i["product_id"] for i in all_items(rec("I want hot pot"))}
    ids |= {i["product_id"] for i in all_items(rec("Meat would be nice"))}
    ids |= {i["product_id"] for i in all_items(rec("fish please"))}
    assert not ids & {"X1", "X2", "X3", "X4", "X5", "X6", "X8"}
    for i in all_items(rec("")):
        assert not re.search(r"shrimp|prawn|bulk", i["title"], re.I)


def test_dislike_extra_hot_excluded():
    persona = {**PERSONA, "dislikes": ["Extra hot food"]}
    res = dinner.recommend(persona, HISTORY, CATALOG, request="meat, quick", customer_id="CR-0001", today=TODAY)
    assert "X7" not in {i["product_id"] for i in all_items(res)}


def test_tags_and_reasons_are_grounded():
    items = {i["product_id"]: i for i in all_items(rec("")) + all_items(rec("Meat would be nice"))}
    for i in items.values():
        assert i["reason"] and set(i["tags"]) <= set(dinner.TAG_ORDER)
    if "F1" in items:
        assert {"On sale", "In season"} <= set(items["F1"]["tags"])
    kid = [i for i in items.values() if i["product_id"] in ("S1", "S2")]
    assert kid and all("Family favorite" in i["tags"] and "Haru" in i["reason"] for i in kid)
    if "M3" in items:
        assert "Frequently bought" in items["M3"]["tags"] and "often" in items["M3"]["reason"]


def test_fish_set_has_kid_friendly_side():
    p = rec("")["sets"][0]
    assert any(i["product_id"] in ("S1", "S2") for i in p["items"] if i["role"] == "Side")


@pytest.mark.parametrize("req,kind", [("Meat would be nice", "meat"), ("I want hot pot", "nabe"),
                                      ("Let's have curry", "curry"), ("Fish please", "fish")])
def test_request_changes_primary(req, kind):
    res = rec(req)
    assert res["sets"][0]["kind"] == kind


def test_curry_set_has_staple_and_medium_hot():
    p = rec("Let's have curry")["sets"][0]
    ids = [i["product_id"] for i in p["items"]]
    assert "C1" in ids and "R1" in ids
    assert p["name"] == "Medium-Hot Curry Family Dinner"


def test_nabe_set_has_soup_base_and_udon():
    p = rec("hot pot tonight")["sets"][0]
    ids = [i["product_id"] for i in p["items"]]
    assert "N1" in ids and "N3" in ids and p["name"].endswith("Hot Pot Night")


def test_budget_respected():
    for b in (1500, 2000):
        res = rec("", budget_jpy=b)
        assert res["budget_jpy"] == b
        for s in res["sets"]:
            assert s["total_tax_included"] <= b
    assert rec("Keep it under ¥1,500")["budget_jpy"] == 1500
    assert rec("budget 2000 yen")["budget_jpy"] == 2000


def test_servings_quantity():
    two = {i["product_id"]: i for i in rec("", servings=2)["sets"][0]["items"]}
    six = {i["product_id"]: i for i in rec("", servings=6)["sets"][0]["items"]}
    main2 = next(i for i in two.values() if i["role"] == "Main")
    assert main2["quantity"] == 1
    main6 = next(i for i in six.values() if i["role"] == "Main")
    assert main6["quantity"] >= 1 and main6["subtotal"] == main6["price"] * main6["quantity"]
    assert rec("Dinner for 2 people")["servings"] == 2
    assert rec("dinner for two")["servings"] == 2


def test_quick_request_prefers_quick_items():
    res = rec("Something quick and easy")
    assert sum("Quick" in i["tags"] for i in res["sets"][0]["items"]) >= 1


def test_deterministic_per_day():
    a, b = rec("dinner tonight"), rec("dinner tonight")
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)
    assert dinner.jitter("CR-0001", "2026-11-04", "", "F1") != dinner.jitter("CR-0001", "2026-11-05", "", "F1")


def test_show_args_shape():
    res = rec("")
    a = res["show_products_on_page_args"]
    flat = [pid for s in a["sections"] for pid in s["product_ids"]]
    assert a["product_ids"] == flat and len(a["reasons"]) == len(flat)
    for s in a["sections"]:
        assert s["label"] in dinner.ROLE_ORDER and len(s["reasons"]) == len(s["product_ids"]) == len(s["tags"]) == len(s["quantities"])
    qmap = {i["product_id"]: i["quantity"] for i in res["sets"][0]["items"]}
    assert a["quantities"] == [qmap[x] for x in a["product_ids"]]
    assert [q for s in a["sections"] for q in s["quantities"]] == a["quantities"]
    assert a["theme"] == res["theme"] and a["message"].endswith("incl. tax)") and "¥" in a["message"]
    for alt in res["sets"][1:]:
        aa = alt["show_products_on_page_args"]
        assert aa["product_ids"] == [i["product_id"] for i in alt["items"]] and alt["name"] in aa["theme"]
        assert aa["theme"].startswith("Tonight's dinner for Sakura's family: ")


def test_signals():
    sig = rec("")["signals"]
    assert sig["frequent"][0] == "Sliced Pork"
    assert any(f.startswith("Haru") and "shrimp" in f for f in sig["family"])
    assert sig["sale_count"] > 0 and "shrimp" in sig["excluded"]


def test_exclusion_words_variants():
    words = dinner.exclusion_words(PERSONA)
    for w in ("shrimp", "prawn", "ebi"):
        assert w in words
    assert "egg" not in words


def test_egg_allergy_does_not_block_eggplant():
    p = dinner.normalize_product(row("E1", "Domestic Eggplant 3 pcs", "Produce > Vegetables > Eggplant", 198))
    assert not dinner.blocked(p, ["egg", "omelet"], [])
    p2 = dinner.normalize_product(row("E2", "Sweet Omelet 1 pc", "Deli & Meal Kits > Egg Dishes > Omelet", 198))
    assert dinner.blocked(p2, ["egg", "omelet"], [])


@pytest.mark.parametrize("title,exp", [("Canadian Sliced Pork 300g", "Sliced Pork"), ("Cymbal Milk 200ml x 3", "Milk"),
                                       ("[Fresh+] Hokkaido Fresh Autumn Salmon Fillets 2 pcs (approx. 160g)", "Autumn Salmon Fillets")])
def test_short_name(title, exp):
    assert dinner.short_name(title) == exp


def test_parse_request():
    r = dinner.parse_request("Something quick with meat, budget ¥3,000, for 3 people")
    assert r["meat"] and r["quick"] and r["budget"] == 3000 and r["servings"] == 3
    r = dinner.parse_request("hot pot for 4")
    assert r["nabe"] and r["servings"] == 4 and r["budget"] == 0


def test_bundled_persona_if_english():
    """The bundled persona (owned by `data`) is used when BigQuery is down; once translated it must work end to end."""
    data = json.loads((Path(server.__file__).parent / "data" / "personas.json").read_text(encoding="utf-8"))
    bundled = next(p for p in data if p["customer_id"] == "CR-0001")
    if JA_RE.search(json.dumps(bundled, ensure_ascii=False)):
        pytest.skip("bundled persona not yet translated by data")
    res = dinner.recommend(bundled, HISTORY, CATALOG, customer_id="CR-0001", today=TODAY)
    assert res["sets"] and "shrimp" in json.dumps(res["signals"]).lower()


# ── server wiring ────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(server, "get_customer_profile_sync", lambda cid: {**PERSONA, "_source": "fake"})
    monkeypatch.setattr(server, "dinner_history_rows", lambda cid: (HISTORY, "fake"))
    monkeypatch.setattr(server, "dinner_catalog", lambda: (CATALOG, "fake"))


def test_tool_via_mcp(wired):
    out = run(server.mcp.call_tool("recommend_dinner_set", {"customer_id": "CR-0001", "request": "", "budget_jpy": "0",
                                                            "servings": 4.0}))
    blocks = out[0] if isinstance(out, tuple) else out
    res = json.loads(blocks[0].text)
    assert res["sets"] and res["_source"]["catalog"] == "fake" and "show_products_on_page_args" in res


def test_tool_schema():
    tools = {t.name: t for t in run(server.mcp.list_tools())}
    s = tools["recommend_dinner_set"].inputSchema
    assert s["required"] == ["customer_id"]
    assert s["properties"]["budget_jpy"]["type"] == "integer" and s["properties"]["request"]["type"] == "string"
    assert "default" not in json.dumps(s) and "title" not in s


def test_catalog_fallback_to_file(monkeypatch, tmp_path):
    f = tmp_path / "cat.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in CATALOG_ROWS), encoding="utf-8")

    def boom():
        raise RuntimeError("bq down")

    monkeypatch.setattr(server, "_dinner_catalog_bq", boom)
    monkeypatch.setattr(server, "DINNER_CATALOG_FILE", str(f))
    monkeypatch.setattr(server, "_dinner_cache", {"at": 0.0, "items": None, "source": ""})
    items, src = server.dinner_catalog()
    assert src == "file" and len(items) == len(CATALOG_ROWS)
    items2, src2 = server.dinner_catalog()  # cached
    assert src2 == "file" and items2 is items


def test_profile_json_merge(monkeypatch):
    pj = json.dumps(PERSONA, ensure_ascii=False)
    monkeypatch.setattr(server, "_bq_query", lambda sql, params: [{"customer_id": "CR-0001", "name": "Sakura Tanaka",
                                                                   "family_preferences": None, "profile_json": pj}])
    prof = server.get_customer_profile_sync("CR-0001")
    assert prof["_source"] == "bigquery" and "profile_json" not in prof
    assert prof["family_preferences"][3]["call_name"] == "Haru"


def _p(title, size, top, price=100):
    return {"title": title, "size": size, "top": top, "price": price}


@pytest.mark.parametrize("title,size,role,top,exp", [
    ("[Meal Kit] Autumn Salmon & Mushroom Chanchan-yaki 3-4 servings", "3-4 servings (approx. 900g)", "Main", "Deli & Meal Kits", 1),
    ("Cymbal Sweet Corn Whole Kernel 190g", "190g", "Side", "Pantry", 1),
    ("Cymbal Freeze-dried Miso Soup Tofu 5-pack", "5-pack", "Soup", "Rice & Noodles", 1),
    ("Philippine Bananas 1 bunch (4-5 pcs)", "1 bunch", "Dessert & fruit", "Produce", 1),
    ("Domestic Shimeji Mushrooms 3 packs (approx. 300g)", "3 packs", "Side", "Produce", 1),
    ("Nikkori Pear 1 pc (large)", "1 pc", "Dessert & fruit", "Produce", 1),
    ("Custard Pudding 3-pack", "3-pack", "Dessert & fruit", "Eggs & Dairy", 1),
    ("Retort Curry 170g x 5 servings", "850g (170g x 5)", "Main", "Seasonings & Sauces", 1),
    ("Ginger Pork 1 serving", "1 serving", "Main", "Deli & Meal Kits", 4),
    ("Instant Miso Soup 1 serving", "1 serving", "Soup", "Rice & Noodles", 4),
    ("Silky Pudding 1 pc", "1 pc", "Dessert & fruit", "Eggs & Dairy", 4),
    ("Fresh Autumn Salmon Fillets 2 pcs (approx. 160g)", "2 pcs (approx. 160g)", "Main", "Seafood", 2),
])
def test_portion_qty(title, size, role, top, exp):
    assert dinner.portion_qty(_p(title, size, top), role, 4) == exp


def test_totals_use_quantity_and_reason_mentions_it():
    extra = CATALOG + [dinner.normalize_product(row("Q1", "Grilled Salted Salmon 1 serving", "Deli & Meal Kits > Grilled Dishes > Grilled Fish", 350,
                                                     seasonal=True, size="1 serving"))]
    for req in ("", "meat", "hot pot", "curry", "fish please"):
        res = dinner.recommend(PERSONA, HISTORY, extra, request=req, customer_id="CR-0001", today=TODAY)
        for s in res["sets"]:
            assert s["total_tax_included"] == sum(i["price"] * i["quantity"] for i in s["items"])
            for i in s["items"]:
                assert i["subtotal"] == i["price"] * i["quantity"]
                if i["quantity"] > 1:
                    assert f"{i['quantity']} " in i["reason"] and "for 4 people" in i["reason"]
    q = dinner._item(dinner.normalize_product(row("Q2", "Grilled Salted Salmon 1 serving", "Deli & Meal Kits > Grilled Dishes > Grilled Fish", 350,
                                                   size="1 serving")), "Main",
                     dinner.Ctx(profile=PERSONA, hist=dinner.history_signals([]), req=dinner.parse_request(""), today=TODAY,
                                customer_id="CR-0001", servings=4, rules=[]))
    assert q["quantity"] == 4 and q["subtotal"] == 1400 and q["reason"].startswith("4 servings for 4 people")


def test_non_main_packs_scale_to_household_when_servings_stated():
    """verifier #132(b): a 2-serving soup for a family of 4 → 2 packs; family/bulk packs stay 1."""
    P = lambda t: {"id": "x", "title": t, "size": ""}  # noqa: E731
    assert dinner.portion_qty(P("Hearty Tonjiru, approx. 400 g (2 servings)"), dinner.ROLE_SOUP, 4) == 2
    assert dinner.portion_qty(P("Steamed rice for 2"), dinner.ROLE_STAPLE, 4) == 2
    assert dinner.portion_qty(P("Instant Miso Soup 10 servings"), dinner.ROLE_SOUP, 4) == 1
    assert dinner.portion_qty(P("Spinach, 1 bunch"), dinner.ROLE_SIDE, 4) == 1
    assert dinner.portion_qty(P("Rice 5 kg"), dinner.ROLE_STAPLE, 4) == 1
    assert dinner.portion_qty(P("Miso soup 3 servings x 2"), dinner.ROLE_SOUP, 4) == 1
    assert dinner.portion_qty(P("Hearty Tonjiru, approx. 400 g (2 servings)"), dinner.ROLE_SOUP, 2) == 1
