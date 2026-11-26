import json
import os

os.environ["OFFLINE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import settings  # noqa: E402
from app.pipeline import fill_rate_after, fill_rate_before, process_stage1, process_stage3  # noqa: E402
from app.repository import store  # noqa: E402

settings.offline = True

FOOD = {
    "sku": "4909270706758", "title": "Shizuoka Swordfish Fillets 3 Fillets (approx. 240 g)", "brand": "",
    "category": "Seafood > Swordfish & Spanish Mackerel > Swordfish", "group": "fresh", "price": 646,
    "description": "Boneless swordfish fillets that are easy for kids to eat. Great for teriyaki, meuniere or tatsuta-age, "
                   "for Japanese or Western dishes, and handy for bento.\n"
                   "Net weight: 3 fillets (approx. 240 g) / Origin: Shizuoka, Japan / Storage: Chilled",
    "product_url": "https://example.com/?product=4909270706758", "image_url": "",
    "raw_attributes": {"size": "3 fillets (approx. 240 g)", "origin": "Shizuoka, Japan", "storage_type": "Chilled",
                       "allergens": "none", "base_id": "x_fish_swordfish_fillet", "dishes": "teriyaki / meuniere / tatsuta-age",
                       "season_months": "10 / 11 / 12 / 1 / 2", "labels": "Chilled / In season", "on_sale": "false"},
}
GM = {
    "sku": "4946311911337", "title": "Cymbal Select Deep Frying Pan with Glass Lid 28 cm Navy", "brand": "Cymbal Select",
    "category": "Kitchen > Pots & Pans > Frying Pans", "group": "kitchen", "price": 3980,
    "description": "A deep frying pan for everything from stir-fries to stews. Induction compatible and dishwasher safe.\n"
                   "Size: 28 cm / Material: aluminum alloy, fluororesin coating / Color: Navy / Country of origin: China",
    "product_url": "", "image_url": "",
    "raw_attributes": {"size": "28 cm", "material": "aluminum alloy, fluororesin coating", "color": "Navy", "origin": "China",
                       "labels": "Induction compatible / Dishwasher safe", "on_sale": "true", "promo_text": "Flash sale"},
}

S1_FOOD = {"attributes": [
    {"key": "net_weight_g", "raw_value": "3 fillets (approx. 240 g)", "evidence": "Net weight: 3 fillets (approx. 240 g)",
     "source_field": "description", "confidence": 0.98},
    {"key": "pack_count", "raw_value": "3 fillets (approx. 240 g)", "evidence": "Net weight: 3 fillets (approx. 240 g)",
     "source_field": "description", "confidence": 0.97},
    {"key": "origin", "raw_value": "Shizuoka, Japan", "evidence": "Origin: Shizuoka, Japan", "source_field": "description",
     "confidence": 0.99},
    {"key": "storage_method", "raw_value": "Chilled", "evidence": "Storage: Chilled", "source_field": "description",
     "confidence": 0.99},
    {"key": "allergens", "raw_value": "none", "evidence": "allergens: none", "source_field": "attributes", "confidence": 0.9},
    {"key": "season", "raw_value": "10 / 11 / 12 / 1 / 2", "evidence": "season_months: 10 / 11 / 12 / 1 / 2",
     "source_field": "attributes", "confidence": 0.95},
    {"key": "dishes", "raw_value": "teriyaki, meuniere, tatsuta-age", "evidence": "Great for teriyaki, meuniere or tatsuta-age",
     "source_field": "description", "confidence": 0.93},
], "not_found": ["cook_time_min"]}
S3_FOOD = {"feature_tags": [{"tag": "Boneless / easy to eat", "evidence": "Boneless swordfish fillets that are easy for kids to eat",
                             "confidence": 0.96},
                            {"tag": "In season", "evidence": "labels: Chilled / In season", "confidence": 0.95},
                            {"tag": "Organic", "evidence": "Organically farmed", "confidence": 0.6}],
           "search_keywords": ["swordfish", "fillets"], "ai_summary": "Boneless, easy-to-eat swordfish fillets from Shizuoka."}
S1_GM = {"attributes": [
    {"key": "diameter_cm", "raw_value": "28 cm", "evidence": "Size: 28 cm", "source_field": "description", "confidence": 0.95},
    {"key": "material", "raw_value": "aluminum alloy, fluororesin coating", "evidence": "Material: aluminum alloy, fluororesin coating",
     "source_field": "description", "confidence": 0.98},
    {"key": "ih_compatible", "raw_value": "Induction compatible", "evidence": "Induction compatible and dishwasher safe",
     "source_field": "description", "confidence": 0.97},
    {"key": "dishwasher_safe", "raw_value": "dishwasher safe", "evidence": "Induction compatible and dishwasher safe",
     "source_field": "description", "confidence": 0.95},
    {"key": "width_cm", "raw_value": "900 cm", "evidence": "Size: 28 cm", "source_field": "description", "confidence": 0.9},
], "not_found": []}


def setup_module(_):
    store.products.clear(); store.records.clear(); store.extras.clear(); store.reviews.clear(); store.gmc.clear()
    store.load([dict(FOOD), dict(GM)])
    recs = process_stage1(FOOD, S1_FOOD, None, "job-test", "m")
    tags, extras = process_stage3(FOOD, S3_FOOD, None, "job-test", "m")
    store.save_enrichment(FOOD["sku"], recs + tags, extras, "job-test")
    recs = process_stage1(GM, S1_GM, None, "job-test", "m")
    store.save_enrichment(GM["sku"], recs, {"search_keywords": [], "ai_summary": ""}, "job-test")


client = TestClient(main.app)


def test_pipeline_gates_food():
    recs = {r["attr_key"]: r for r in store.records[FOOD["sku"]]}
    assert recs["net_weight_g"]["value"] == 240 and recs["net_weight_g"]["gate_status"] == "auto_approved"
    assert recs["pack_count"]["value"] == 3
    assert recs["storage_method"]["value"] == "Chilled"
    assert recs["season"]["value"] == "Oct-Feb"
    # structured "none" only -> food-safety review
    assert recs["allergens"]["gate_status"] == "needs_review" and "structured attribute" in recs["allergens"]["reason"]
    assert recs["tag:Organic"]["gate_status"] == "needs_review"       # evidence not in source
    assert recs["tag:Boneless / easy to eat"]["gate_status"] == "auto_approved"


def test_pipeline_gates_gm():
    recs = {r["attr_key"]: r for r in store.records[GM["sku"]]}
    assert recs["diameter_cm"]["value"] == 28 and recs["diameter_cm"]["gate_status"] == "auto_approved"
    assert recs["ih_compatible"]["value"] is True and recs["dishwasher_safe"]["value"] is True
    assert recs["width_cm"]["gate_status"] == "rejected"                    # physical limit
    assert "allergens" not in recs                                          # GM: no food-safety placeholder


def test_fill_rates():
    p = store.products[FOOD["sku"]]
    b, a = fill_rate_before(p), fill_rate_after(p, store.records[p["sku"]])
    assert 0 < b < a <= 1


def test_config_and_dashboard():
    cfg = client.get("/api/config").json()
    assert cfg["threshold"] == 0.9 and cfg["model"] == "gemini-3.8-flash"
    assert "fresh" in cfg["groups"] and len(cfg["allergens_28"]) == 28
    d = client.get("/api/dashboard").json()
    assert d["totals"]["skus"] == 2 and d["totals"]["needs_review"] >= 2 and d["totals"]["rejected"] >= 1
    assert 0 <= d["fill_rate"]["before"] <= d["fill_rate"]["after"] <= 1
    assert {g["group"] for g in d["by_group"]} >= {"fresh", "kitchen"}


def test_products_and_detail():
    items = client.get("/api/products").json()["items"]
    assert {i["sku"] for i in items} == {FOOD["sku"], GM["sku"]}
    d = client.get(f"/api/products/{FOOD['sku']}").json()
    assert d["product"]["group_label"].startswith("Fresh")
    assert any(t["key"] == "allergens" for t in d["targets"])
    assert "Net weight: 3 fillets (approx. 240 g)" in d["source_text"]
    assert d["gmc"]["item_group_id"] == "x_fish_swordfish_fillet"
    assert client.get("/api/products/NOPE").status_code == 404


def test_review_flow_allergen():
    q = client.get("/api/reviews").json()
    assert any(i["rec"]["attr_key"] == "allergens" for i in q["items"])
    bad = client.post(f"/api/reviews/{FOOD['sku']}/allergens", json={"action": "edit", "value": "nutmeg"})
    assert bad.status_code == 400
    r = client.post(f"/api/reviews/{FOOD['sku']}/allergens", json={"action": "edit", "value": "Allergens: none", "comment": "Checked package"})
    assert r.status_code == 200 and r.json()["record"]["gate_status"] == "edited" and r.json()["record"]["value"] == "none"
    r = client.post(f"/api/reviews/{FOOD['sku']}/tag%3AOrganic", json={"action": "reject"})
    assert r.status_code == 200 and r.json()["record"]["gate_status"] == "human_rejected"
    assert r.json()["remaining"] == client.get("/api/reviews").json()["count"]
    assert client.post(f"/api/reviews/{FOOD['sku']}/origin", json={"action": "bogus"}).status_code == 400


def test_review_cannot_approve_empty_placeholder():
    from app.pipeline import missing_allergen_record
    store.records[GM["sku"]].append({**missing_allergen_record(store.products[GM["sku"]], "j", "m"), "attr_key": "allergens"})
    r = client.post(f"/api/reviews/{GM['sku']}/allergens", json={"action": "approve"})
    assert r.status_code == 400
    store.records[GM["sku"]] = [x for x in store.records[GM["sku"]] if x["attr_key"] != "allergens"]


def test_golden_and_csv():
    items = {g["sku"]: g for g in client.get("/api/golden").json()["items"]}
    g = items[FOOD["sku"]]
    assert g["attributes"]["net_weight_g"]["value"] == 240
    assert g["attributes"]["allergens"]["value"] == "none"          # after human edit
    assert "Boneless / easy to eat" in g["feature_tags"] and "Organic" not in g["feature_tags"]
    csv_text = client.get("/api/golden/export.csv").text
    assert "net_weight_g" in csv_text.splitlines()[0]


def test_csv_live_test_endpoints():
    samp = client.get("/api/csv/sample").json()
    assert samp["count"] == 3 and samp["items"][0]["sku"].startswith("CSV-")
    dl = client.get("/api/csv/sample.csv")
    assert dl.status_code == 200 and "CSV-FOOD-001" in dl.text
    parsed = client.post("/api/csv/parse", json={"csv_text": samp["csv_text"]}).json()
    assert parsed["count"] == 3
    assert [i["group"] for i in parsed["items"]] == ["prepared", "fresh", "kitchen"]
    res = client.post("/api/csv/enrich", json={"products": [parsed["items"][0]], "save_to_bq": True})
    events = [json.loads(line[5:].strip()) for line in res.text.splitlines() if line.startswith("data:")]
    names = [e["event"] for e in events]
    assert "start" in names and "sku_done" in names and "complete" in names
    assert [e for e in events if e["event"] == "complete"][0]["processed"] == 1


def test_gmc_conversational_endpoints():
    gl = client.get("/api/gmc").json()
    assert "17085370" in gl["spec_url"] and gl["total"] >= 2
    sku = FOOD["sku"]
    det = client.get(f"/api/gmc/{sku}").json()
    assert det["gmc"]["sku"] == sku and det["feed_row"]["id"] == sku
    assert det["gmc"]["document_link"] == ""                          # never invent a document URL
    gen = client.post(f"/api/gmc/{sku}/generate").json()
    assert gen["gmc"]["model"] == "gemini-3.8-flash"
    upd = client.put(f"/api/gmc/{sku}", json={
        "item_group_title": "Shizuoka Swordfish Fillets",
        "variant_options": [{"name": "Net weight", "value": "3 fillets (approx. 240 g)",
                             "evidence": "Net weight: 3 fillets (approx. 240 g)"}],
        "questions_and_answers": [
            {"question": "How many fillets are in the pack?", "answer": "3 fillets (approx. 240 g).",
             "evidence": "Net weight: 3 fillets (approx. 240 g)", "confidence": 0.98},
            {"question": "Is it safe for a shrimp allergy?", "answer": "It does not contain shrimp.", "evidence": "Boneless",
             "confidence": 0.99},
        ],
    }).json()
    assert "Net weight:3 fillets (approx. 240 g)" in upd["gmc"]["variant_option"]
    qa = {q["question"]: q for q in upd["gmc"]["questions_and_answers"]}
    assert qa["How many fillets are in the pack?"]["status"] == "auto_approved"
    tsv = client.get("/api/gmc/export.tsv")
    assert tsv.status_code == 200 and "question_and_answer\tproduct_highlight" in tsv.text.splitlines()[0]


def test_allergen_qa_requires_statement():
    from app.pipeline import process_gmc
    g = process_gmc(FOOD, {"questions_and_answers": [
        {"question": "Does it contain any allergens?", "answer": "None.", "evidence": "easy for kids to eat", "confidence": 0.99}]},
        [], None, "j", "m")
    assert g["questions_and_answers"][0]["status"] == "needs_review"


def test_cost_estimate_and_index_store_link():
    c = client.get("/api/cost/estimate?skus=8807").json()
    assert c["basis"].startswith("List price") and "total_jpy_slide" not in c     # no batch / planning-value estimates
    assert c["total_usd"] is None or c["total_usd"] >= 0
    assert "per_sku_usd" in c and "total_jpy" in c
    settings.store_url = "https://store.example.com"
    html = client.get("/").text
    assert 'href="https://store.example.com"' in html
    assert "FLYER" not in html
    settings.store_url = ""
    assert 'id="store-link"' in client.get("/").text and "hidden" in client.get("/").text.split('id="store-link"')[1][:200]


def test_response_schema_shapes():
    from app.schema import CATEGORY_GROUPS, FEATURE_TAGS, gmc_schema, stage1_schema, stage3_schema
    for g in CATEGORY_GROUPS:
        s1 = stage1_schema(g)
        item = s1["properties"]["attributes"]["items"]
        assert set(item["required"]) == {"key", "raw_value", "evidence", "source_field", "confidence"}
        assert item["properties"]["key"]["enum"]
        assert stage3_schema(g)["properties"]["feature_tags"]["items"]["properties"]["tag"]["enum"] == FEATURE_TAGS[g]
        json.dumps(s1, ensure_ascii=False)
    assert "questions_and_answers" in gmc_schema()["properties"]


def test_category_group_mapping():
    from app.schema import category_group
    assert category_group("Seafood > Swordfish & Spanish Mackerel > Swordfish") == "fresh"
    assert category_group("Frozen > Frozen Meat") == "prepared"
    assert category_group("Kitchen > Frying Pans") == "kitchen"
    assert category_group("Pet > Beds") == "pet"
    assert category_group("Dog food for seniors") == "pet"
    assert category_group("Beauty & Personal Care > Shampoo") == "beauty"
    assert category_group("Home & Living > Storage") == "living"
