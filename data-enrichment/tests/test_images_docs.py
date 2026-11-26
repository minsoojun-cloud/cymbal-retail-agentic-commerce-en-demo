"""Tests for (1) Seasonal Image Studio presets and (2) document extraction (offline: Gemini & GCS are stubbed)."""
import io
import json
import os
import tempfile

os.environ["OFFLINE"] = "true"
os.environ.setdefault("LOCAL_GCS_DIR", tempfile.mkdtemp(prefix="gcs_test_"))

from fastapi.testclient import TestClient  # noqa: E402

from app import doc_extract, image_presets, main  # noqa: E402
from app.config import settings  # noqa: E402
from app.repository import product_to_row, store  # noqa: E402

settings.offline = True

SANMA = {
    "sku": "4925319234365", "title": "Hokkaido Fresh Pacific Saury, 2 fish", "brand": "",
    "category": "Seafood > Saury > Fresh saury", "group": "fresh", "price": 398,
    "description": "Rich, oily flavor of the season. Best salt-grilled.\nNet contents: 2 fish / Origin: Hokkaido, Japan / Storage: Chilled",
    "product_url": "", "image_url": "",
    "raw_attributes": {"season_months": "9 / 10 / 11", "on_sale": "true", "promo_text": "This week's great value pick!",
                       "dishes": "Salt-grilled / Kabayaki"},
}
HAKUSAI = {
    "sku": "4913667472959", "title": "Ibaraki Napa Cabbage, 1/2 cut", "brand": "", "category": "Produce > Leafy greens > Napa cabbage",
    "group": "fresh", "price": 198, "description": "Great for hot pots and pickles.\nNet contents: 1/2 cut / Origin: Ibaraki, Japan / Storage: Chilled",
    "product_url": "", "image_url": "", "raw_attributes": {"season_months": "11 / 12 / 1 / 2", "on_sale": "false"},
}
SPONGE = {
    "sku": "4925863331336", "title": "Cymbal Kitchen Sponge, 3 pcs", "brand": "Cymbal", "category": "Household > Kitchen supplies > Sponges",
    "group": "living", "price": 198, "description": "A kitchen sponge that lathers well and drains quickly.", "product_url": "", "image_url": "",
    "raw_attributes": {"size": "3 pcs", "on_sale": "false"},
}
PAN = {
    "sku": "4954950777214", "title": "Cymbal Basic Non-Stick Frying Pan 20cm", "brand": "Cymbal Basic",
    "category": "Kitchen > Pots & pans > Frying pans", "group": "kitchen", "price": 1280,
    "description": "An easy-to-use 20cm frying pan for every day.", "product_url": "", "image_url": "", "raw_attributes": {"base_id": "z_kitchen_pan_20"},
}

client = TestClient(main.app)


def setup_module(_):
    store.products.clear(); store.records.clear(); store.extras.clear(); store.images.clear()
    store.load([dict(SANMA), dict(HAKUSAI), dict(SPONGE), dict(PAN)])


def _ids(presets):
    return {p["id"]: p for p in presets}


def test_presets_gating_requires_evidence():
    s = _ids(image_presets.evaluate(SANMA, []))
    assert s["autumn-taste"]["available"] and "Sep" in s["autumn-taste"]["evidence"]
    assert s["sale"]["available"] and "on_sale" in s["sale"]["evidence"]
    assert not s["winter-nabe"]["available"] and "hot pot" in s["winter-nabe"]["reason"]
    assert not s["summer-cool"]["available"]
    h = _ids(image_presets.evaluate(HAKUSAI, []))
    assert h["winter-nabe"]["available"] and "hot pot" in h["winter-nabe"]["evidence"]
    assert not h["sale"]["available"]                      # on_sale=false, no promo -> no sale banner
    assert h["ec-white"]["available"] and h["lifestyle"]["available"]


def test_presets_category_filter():
    ids = set(_ids(image_presets.evaluate(SPONGE, [])))
    assert "autumn-taste" not in ids and "winter-nabe" not in ids and "ec-white" in ids and "newlife" in ids


def test_presets_tag_match_is_case_insensitive():
    recs = [{"kind": "tag", "value": "great for HOT POT", "gate_status": "auto_approved"}]
    w = _ids(image_presets.evaluate(SANMA, recs))["winter-nabe"]
    assert w["available"] and "great for HOT POT" in w["evidence"]


def test_presets_word_boundaries():
    # "price" must not count as "ice", "ideal" not as "deal", category path is not evidence
    p = {**SPONGE, "description": "Ideal price for families.", "category": "Summer > Ice > Sale"}
    s = _ids(image_presets.evaluate(p, []))
    assert not s["sale"]["available"]
    assert all(x["label"] and x["season_label"] for x in image_presets.evaluate(HAKUSAI, []))


def test_presets_use_approved_attributes():
    recs = [{"kind": "attribute", "attr_key": "cook_time_min", "value": 3, "unit": "min", "gate_status": "auto_approved"}]
    q = _ids(image_presets.evaluate(SANMA, recs))
    assert q["quick-dinner"]["available"] and "3 min" in q["quick-dinner"]["evidence"]
    recs[0]["gate_status"] = "needs_review"               # not approved -> not evidence
    assert not _ids(image_presets.evaluate(SANMA, recs))["quick-dinner"]["available"]


def test_generate_save_delete_flow():
    r = client.get("/api/images/presets", params={"sku": SANMA["sku"]})
    assert r.status_code == 200 and r.json()["options"]["default_size"] == 600
    bad = client.post(f"/api/products/{SANMA['sku']}/images/generate", json={"presets": ["winter-nabe"], "base": {"type": "none"}})
    assert bad.status_code == 400
    with client.stream("POST", f"/api/products/{SANMA['sku']}/images/generate",
                       json={"presets": ["autumn-taste"], "base": {"type": "none"}, "n": 3, "size": 300}) as resp:
        evs = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    imgs = [e["image"] for e in evs if e["event"] == "image"]
    assert len(imgs) == 3 and evs[-1]["event"] == "complete"
    for im in imgs:
        assert im["gcs_uri"].startswith(f"gs://{settings.image_bucket}/{SANMA['sku']}/{SANMA['sku']}_autumn-taste_")
        assert im["gcs_uri"].endswith(".jpg") and (im["width"], im["height"]) == (300, 300)
    g = client.get(imgs[0]["url"])
    assert g.status_code == 200 and g.headers["content-type"] == "image/jpeg"
    s = client.post(f"/api/products/{SANMA['sku']}/images/save",
                    json={"image_ids": [imgs[0]["image_id"], imgs[1]["image_id"]], "primary_id": imgs[1]["image_id"],
                          "update_aics": False})
    assert s.status_code == 200 and len(s.json()["saved"]) == 2 and s.json()["aics"] is None
    assert client.get(f"/api/products/{SANMA['sku']}").json()["product"]["images"][0]["is_primary"]
    d = client.delete(f"/api/products/{SANMA['sku']}/images/{imgs[2]['image_id']}")
    assert d.status_code == 200 and d.json()["gcs_deleted"]
    assert client.get(imgs[2]["url"]).status_code == 404


def test_size_option_non_square():
    with client.stream("POST", f"/api/products/{SPONGE['sku']}/images/generate",
                       json={"presets": ["ec-white"], "base": {"type": "none"}, "n": 2, "size": 600, "aspect": "16:9"}) as resp:
        evs = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    im = next(e["image"] for e in evs if e["event"] == "image")
    assert max(im["width"], im["height"]) == 600


def test_xlsx_to_text():
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Price list"
    ws.append(["JAN", "Product name", "Price"]); ws.append(["4913667472959", "Napa Cabbage 1/2 cut", 198])
    buf = io.BytesIO(); wb.save(buf)
    t = doc_extract.xlsx_to_text(buf.getvalue())
    assert "### Sheet: Price list" in t and "4913667472959\tNapa Cabbage 1/2 cut\t198" in t


def test_match_sku_by_jan_and_title():
    m = doc_extract.match_sku({"jan": "4954950777214", "title": ""}, list(store.products.values()))
    assert m and m[0]["sku"] == PAN["sku"] and m[0]["why"] == "JAN match"
    m = doc_extract.match_sku({"title": "Cymbal Basic Non-Stick Frying Pan 20 cm"}, list(store.products.values()))
    assert m and m[0]["sku"] == PAN["sku"]


EX = {"idx": 0, "model_number": "", "jan": "4954950777214", "title": "Cymbal Basic Non-Stick Frying Pan 20 cm",
      "description": "", "price": 1280, "attributes": [
          {"key": "material", "label": "Material", "value": "Aluminum alloy (PTFE non-stick interior)", "evidence": "Body: aluminum alloy", "page": 1},
          {"key": "ih_compatible", "label": "Induction compatible", "value": "Induction compatible (100 V / 200 V)",
           "evidence": "Induction compatible (100 V / 200 V)", "page": 1},
          {"key": "dimensions", "label": "Dimensions", "value": "Diameter 20 cm x depth 5.0 cm", "evidence": "Diameter 20 cm x depth 5.0 cm", "page": 1}]}


def test_merge_fill_vs_overwrite():
    base = {**PAN, "raw_attributes": {"base_id": "z_kitchen_pan_20", "material": "Aluminum"}}
    p, diff = doc_extract.merge(dict(base), EX, "fill", PAN["sku"], "spec.pdf", "gs://b/spec.pdf")
    acts = {d["field"]: d["action"] for d in diff}
    assert acts["attr:material"] == "keep" and p["raw_attributes"]["material"] == "Aluminum"
    assert acts["attr:ih_compatible"] == "add" and p["raw_attributes"]["ih_compatible"] == "Induction compatible (100 V / 200 V)"
    assert any(d["key"] == "material" for d in p["doc_sources"])          # kept values stay as document evidence
    p2, _ = doc_extract.merge(dict(base), EX, "overwrite", PAN["sku"], "spec.pdf", "gs://b/spec.pdf")
    assert p2["raw_attributes"]["material"].startswith("Aluminum alloy")
    new, d3 = doc_extract.merge(None, EX, "fill", "DOC-PAN", "spec.pdf", "gs://b/spec.pdf")
    assert new["group"] == "kitchen" and all(d["action"] == "new" for d in d3)


def test_docs_preview_and_apply():
    r = client.post("/api/docs/preview", json={"sku": PAN["sku"], "product": EX, "mode": "fill"})
    assert r.status_code == 200 and r.json()["changes"] >= 1
    with client.stream("POST", "/api/docs/apply", json={"items": [{"sku": PAN["sku"], "product": EX, "mode": "fill",
                                                                   "file_uri": "gs://b/spec.pdf", "file_name": "spec.pdf"}],
                                                        "update_aics": False}) as resp:
        evs = [json.loads(l[6:]) for l in resp.iter_lines() if l.startswith("data: ")]
    kinds = [e["event"] for e in evs]
    assert "merged" in kinds and "enriched" in kinds and kinds[-1] == "complete" and "aics" not in kinds
    det = client.get(f"/api/products/{PAN['sku']}").json()
    assert "[Documents]" in det["source_text"] and "Induction compatible (100 V / 200 V)" in det["source_text"]


def test_product_row_has_latest_columns():
    row = product_to_row({**PAN, "source": "doc", "doc_sources": [{"file_name": "x"}]})
    assert row["updated_at"] and row["source"] == "doc" and json.loads(row["doc_sources_json"])[0]["file_name"] == "x"


def test_overwrite_keeps_identity_fields():
    p, diff = doc_extract.merge(dict(PAN), {**EX, "title": "Another Name Frying Pan", "price": 1280.0}, "overwrite", PAN["sku"], "x.pdf", "gs://b/x.pdf")
    acts = {d["field"]: d["action"] for d in diff}
    assert acts["title"] == "keep" and p["title"] == PAN["title"]


def test_aics_product_row_never_rewrites_numeric_attributes():
    from app import aics
    existing = {"id": "1", "title": "x", "attributes": [{"key": "price_tax_excluded", "value": {"numbers": [248.0], "text": []}},
                                                        {"key": "origin", "value": {"text": ["Japan"], "numbers": []}}]}
    row, n = aics.product_row({"sku": "1", "raw_attributes": {"price_tax_excluded": "248", "origin": "Japan", "allergens": "egg"}}, existing)
    attrs = {a["key"]: a for a in row["attributes"]}
    assert attrs["price_tax_excluded"]["value"]["numbers"] == [248.0] and n == 1 and attrs["allergens"]["value"]["text"] == ["egg"]
