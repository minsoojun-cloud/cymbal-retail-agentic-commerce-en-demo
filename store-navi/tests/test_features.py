"""Shopper features router (app/features.py): recipes, forgot-check, product detail, voice."""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import features

JA = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


@pytest.fixture(scope="module")
def fc():
    app = FastAPI()
    app.include_router(features.router)
    with TestClient(app) as c:
        yield c


def test_suggest_returns_three_english_dishes(fc):
    r = fc.get("/api/recipes/suggest", params={"q": "quick dinner kids like", "store_id": "S01"})
    assert r.status_code == 200
    dishes = r.json()["dishes"]
    assert len(dishes) == 3
    for d in dishes:
        assert d["id"] and d["title"] and not JA.search(d["title"])
        assert isinstance(d["minutes"], int) and isinstance(d["kid_friendly"], bool)
        assert not JA.search(d["reason"])


def test_suggest_keyword_ranks_match_first(fc):
    dishes = fc.get("/api/recipes/suggest", params={"q": "curry"}).json()["dishes"]
    assert "curry" in (dishes[0]["title"] + " ".join(dishes[0]["tags"])).lower()


def test_recipe_card_scales_and_customizes(fc):
    rid = fc.get("/api/recipes/suggest", params={"q": "curry"}).json()["dishes"][0]["id"]
    base = fc.get(f"/api/recipes/{rid}", params={"store_id": "S01"}).json()
    big = fc.get(f"/api/recipes/{rid}", params={"servings": base["base_servings"] * 2, "kid_friendly": 1,
                                                 "quick": 1, "store_id": "S01"}).json()
    assert big["servings"] == base["base_servings"] * 2
    assert big["steps"][0]["text"].startswith("Shortcut:")
    assert any(n.startswith("Kid-friendly") for n in big["notes"])
    assert big["minutes"] <= base["minutes"]
    assert len(big["ingredients"]) == len(base["ingredients"]) > 0
    for ing in big["ingredients"]:
        assert set(ing) >= {"name", "amount", "product", "availability"}
        assert not JA.search(ing["name"] + ing["amount"])


def test_recipe_404(fc):
    assert fc.get("/api/recipes/NOPE-999").status_code == 404


def test_scale_amount():
    assert features.scale_amount("300 g", 2) == "600 g"
    assert features.scale_amount("1/4 head", 2) == "1/2 head"
    assert features.scale_amount("3", 0.5) == "1 1/2"
    assert features.scale_amount("to taste", 3) == "to taste"


def test_cycle_and_date_text():
    import datetime as dt
    assert features._cycle_text(14) == "every 2 weeks"
    assert features._cycle_text(7) == "every week"
    assert features._cycle_text(30) == "every month"
    assert features._md(dt.date(2026, 11, 2)) == "Nov 2"


def test_forgot_check_shape_and_excludes_cart(fc):
    rid = fc.get("/api/recipes/suggest", params={"q": "curry"}).json()["dishes"][0]["id"]
    r = fc.post("/api/forgot-check", json={"store_id": "S01", "cart_ids": [], "recipe_id": rid})
    assert r.status_code == 200
    items = r.json()["items"]
    for it in items:
        assert it["kind"] in ("replenish", "recipe")
        assert it["product"]["id"]
        assert it["reason"].startswith(("Usually", "Needed for"))
    if items:
        first = items[0]["product"]["id"]
        again = fc.post("/api/forgot-check", json={"store_id": "S01", "cart_ids": [first], "recipe_id": rid}).json()
        assert first not in [i["product"]["id"] for i in again["items"]]


def test_forgot_check_history_customer(fc):
    # CR-0002 has real purchase rows in the store data
    fc.cookies.set("cr_customer", "CR-0002")
    try:
        r = fc.post("/api/forgot-check", json={"store_id": "S01", "cart_ids": []})
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["kind"] == "replenish" and " \u2014 last bought " in it["reason"]
    finally:
        fc.cookies.clear()


def test_product_detail(fc):
    from app import deps
    pid = next(iter(deps.get_catalog().items))
    r = fc.get(f"/api/products/{pid}/detail", params={"store_id": "S01"})
    assert r.status_code == 200
    body = r.json()
    assert body["product"]["id"] == pid
    assert "availability" in body and "history" in body
    assert fc.get("/api/products/does-not-exist-xyz/detail").status_code == 404


def test_product_detail_history_label(fc):
    rows = features._history_rows("CR-0002")
    pid = str(rows[0]["product_id"]) if rows else None
    if not pid:
        pytest.skip("no purchase history in data")
    h = features.product_history("CR-0002", pid)
    assert h and h["label"].startswith("Last bought ")


def test_voice_empty_upload_rejected(fc):
    r = fc.post("/api/voice/transcribe", content=b"", headers={"content-type": "audio/webm"})
    assert r.status_code == 400 and r.json()["error"]


def test_voice_unavailable_returns_503(fc, monkeypatch):
    def boom(data, mime):
        raise RuntimeError("no gemini")
    monkeypatch.setattr(features, "_transcribe", boom)
    r = fc.post("/api/voice/transcribe", content=b"\x1aE\xdf\xa3fake", headers={"content-type": "audio/webm"})
    assert r.status_code == 503 and r.json()["error"]


def test_voice_ok(fc, monkeypatch):
    monkeypatch.setattr(features, "_transcribe", lambda data, mime: "quick dinner kids like")
    r = fc.post("/api/voice/transcribe", content=b"fake", headers={"content-type": "audio/ogg; codecs=opus"})
    assert r.status_code == 200 and r.json()["text"] == "quick dinner kids like"


def test_forgot_check_default_persona_uses_history(fc):
    # CR-0001 (default cookie) -> online purchase history (seed/personas) or demo staples; active habits only
    items = fc.post("/api/forgot-check", json={"store_id": "S01", "cart_ids": []}).json()["items"]
    rep = [i for i in items if i["kind"] == "replenish"]
    assert rep, "expected replenish suggestions for the default persona"
    for it in rep:
        h = it["history"]
        assert h["count"] >= 3 and h["cycle_days"] and h["days_since"] <= 3 * h["cycle_days"]


def test_suggest_ignores_stopwords():
    for d in features.suggest("Dinner ideas the kids will like", 3, "CR-0001"):
        assert "Matches" not in d["reason"], d["reason"]
    assert any(d["kid_friendly"] for d in features.suggest("Dinner ideas the kids will like", 3, "CR-0001"))
