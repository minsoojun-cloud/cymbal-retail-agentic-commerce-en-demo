"""CONTRACT v6: model registry filtering, list-price cost math, extrapolation, missing-price handling, model plumbing."""
import json
import os

os.environ["OFFLINE"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, models, pricing, usage  # noqa: E402
from app.config import settings  # noqa: E402
from app.repository import store  # noqa: E402

settings.offline = True
client = TestClient(main.app)

P = {"sku": "S1", "title": "Test product", "brand": "", "category": "x", "group": "grocery", "price": 100,
     "description": "Net content: 500g / Storage: Ambient", "product_url": "", "image_url": "", "raw_attributes": {}}


def _res(model, i, o, t, fallback=False):
    return {"model": model, "input_tokens": i, "output_tokens": o + t, "response_tokens": o, "thinking_tokens": t,
            "fallback": fallback}


@pytest.fixture(autouse=True)
def _clean():
    usage.rows.clear()
    models._cache.clear()
    models.probe_all()          # offline → every eligible candidate "available"
    store.products.clear()
    store.load([dict(P), {**P, "sku": "S2", "description": "Long description text " * 40}, {**P, "sku": "S3", "description": "Short"}])
    yield
    usage.rows.clear()


# ── registry ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mid,ok", [
    ("gemini-3.8-flash", True), ("gemini-3.5-flash-lite", True), ("gemini-3-flash-preview", True),
    ("gemini-3.1-pro-preview", True), ("gemini-4-carbon", True),
    ("gemini-2.5-flash", False), ("gemini-2.0-flash-001", False), ("gemini-1.5-pro", False),
    ("gemini-3.1-flash-image", False), ("gemini-3.8-flash-tts", False), ("gemini-3.8-live", False),
    ("text-embedding-005", False), ("claude-x", False),
])
def test_registry_eligibility(mid, ok):
    assert models.is_eligible(mid) is ok


def test_candidates_are_3x_plus_and_default_first_choice():
    ids = [c.id for c in models.eligible_candidates()]
    assert ids and all(models.major_version(i) >= 3 for i in ids)
    assert not any(i.startswith("gemini-2") for i in ids)
    assert models.default_model() == "gemini-3.8-flash" == settings.gemini_model


def test_unavailable_models_excluded_and_resolve_falls_back():
    models._set("gemini-3.7-flash", available=False, reason="Not available in this project", latency_ms=None)
    lst = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert lst["gemini-3.7-flash"]["status"] == "unavailable"
    assert models.resolve("gemini-3.7-flash") == "gemini-3.8-flash"     # not selectable
    assert models.resolve("gemini-2.5-flash") == "gemini-3.8-flash"     # 2.x never
    assert models.resolve("gemini-3.5-flash") == "gemini-3.5-flash"
    cost = client.get("/api/cost/models?skus=10").json()
    assert "gemini-3.7-flash" not in [r["id"] for r in cost["models"]]
    assert "gemini-3.7-flash" in [m["id"] for m in cost["unavailable"]]


def test_batch_support_reason():
    assert models.batch_reason("gemini-3.8-flash") == ""
    assert all(c.batch is True for c in models.CANDIDATES)      # all verified 2026-11-11
    for c in models.CANDIDATES:
        if c.batch is not True:
            assert models.batch_reason(c.id)          # disabled with a reason
    assert models.batch_reason("nope")


# ── pricing / cost math ─────────────────────────────────────────────────────

def test_default_is_list_price_without_promotion():
    # default basis = regular list price (no promotion / credit-back discount), whatever the date
    for on in ("2026-11-11", "2027-03-01"):
        p = pricing.price_for("gemini-3.8-flash", on=on)
        assert (p.input_usd, p.output_usd) == (1.50, 7.50) and not p.promo
    assert (pricing.price_for("gemini-3.7-flash").input_usd, pricing.price_for("gemini-3.6-flash").output_usd) == (1.50, 7.50)
    g4 = pricing.price_for("gemini-4-carbon")
    assert (g4.input_usd, g4.output_usd) == (4.00, 20.00) and not g4.promo
    assert pricing.price_for("gemini-3.5-flash").output_usd == 9.00
    assert pricing.price_for("gemini-3.5-flash-lite").input_usd == 0.30
    assert pricing.price_for("gemini-3.1-flash-lite").output_usd == 1.50
    assert pricing.price_for("gemini-3.1-pro-preview").output_usd == 12.00
    pub = pricing.price_public("gemini-3.8-flash")
    assert pub["promo"] is False and pub["note"] == "List price (no promotion applied)" and pub["has_promo"]
    assert pricing.PRICE_RETRIEVED_AT == "2026-11-11"
    assert pricing.PRICING_URL == "https://cloud.google.com/vertex-ai/generative-ai/pricing"


def test_promo_only_as_reference_basis():
    p = pricing.price_for("gemini-3.8-flash", on="2026-11-11", basis="promo")
    assert (p.input_usd, p.output_usd, p.promo) == (0.75, 3.75, True)
    assert pricing.price_for("gemini-3.8-flash", on="2027-01-01", basis="promo").input_usd == 1.50
    assert pricing.price_for("gemini-4-argon", on="2026-11-11", basis="promo").input_usd == 2.00
    assert pricing.norm_basis(None) == "list" and pricing.norm_basis("bogus") == "list"
    assert pricing.price_public("gemini-3.8-flash", "promo")["promo"] is True


def test_cost_math_thinking_billed_as_output():
    p = pricing.PricePeriod(0.5, 4.0)
    usd = pricing.cost_usd(1_000_000, 500_000, 250_000, p)
    assert usd == pytest.approx(0.5 + 0.75 * 4.0)
    assert pricing.cost_usd(1, 1, 1, None) is None


def test_extrapolation_and_breakdown():
    per = {"input_tokens": 3000, "output_tokens": 800, "thinking_tokens": 200}
    p = pricing.PricePeriod(1.0, 10.0)
    e = pricing.estimate(per, 1000, p, 150.0)
    assert e["tokens"] == {"input": 3_000_000, "output": 800_000, "thinking": 200_000}
    assert e["breakdown"]["input"]["usd"] == pytest.approx(3.0)
    assert e["breakdown"]["output"]["usd"] == pytest.approx(8.0)
    assert e["breakdown"]["thinking"]["usd"] == pytest.approx(2.0)
    assert e["total_usd"] == pytest.approx(13.0)
    assert e["total_jpy"] == pytest.approx(1950.0)
    assert e["per_sku_jpy"] == pytest.approx(1.95)
    # linear in N
    assert pricing.estimate(per, 2000, p, 150.0)["total_jpy"] == pytest.approx(3900.0)


def test_missing_price_is_not_invented():
    assert pricing.price_for("gemini-3-flash-preview") is None          # output price not on the official page
    assert pricing.price_for("unknown-model") is None
    e = pricing.estimate({"input_tokens": 10, "output_tokens": 5, "thinking_tokens": 1}, 100, None, 150)
    assert e["price_available"] is False and e["total_jpy"] is None and e["tokens"]["input"] == 1000
    pub = pricing.price_public("gemini-3-flash-preview")
    assert pub["available"] is False and pub["note"] == "See the official pricing page" and pub["url"] == pricing.PRICING_URL


# ── measured tokens → cost table ────────────────────────────────────────────

def test_usage_record_requires_three_calls_same_model_no_fallback():
    r = {"stage1": _res("gemini-3.5-flash", 1000, 200, 50), "stage3": _res("gemini-3.5-flash", 800, 100, 20),
         "gmc": _res("gemini-3.5-flash", 1200, 400, 30)}
    row = usage.record(P, r, "j1", "live")
    assert row and row["input_tokens"] == 3000 and row["output_tokens"] == 700 and row["thinking_tokens"] == 100
    assert json.loads(row["stage_tokens_json"])["gmc"] == {"input": 1200, "output": 400, "thinking": 30}
    assert usage.record(P, {"stage1": r["stage1"], "stage3": r["stage3"]}, "j2", "live") is None
    assert usage.record(P, {**r, "gmc": _res("gemini-3.5-flash", 1, 1, 1, fallback=True)}, "j3", "live") is None
    assert usage.record(P, {**r, "gmc": _res("gemini-3.8-flash", 1, 1, 1)}, "j4", "live") is None
    agg = usage.aggregate("gemini-3.5-flash")
    assert agg["runs"] == 1 and agg["input_tokens"] == 3000 and agg["only_sample"] is False
    assert usage.aggregate("gemini-3.8-flash") is None


def test_cost_models_endpoint_per_model_and_missing_price():
    usage.rows.extend([
        {"sku": "S1", "title": "a", "model": "gemini-3.5-flash", "source": "live", "input_tokens": 3000,
         "output_tokens": 600, "thinking_tokens": 400, "source_chars": 100},
        {"sku": "S2", "title": "b", "model": "gemini-3.5-flash", "source": "csv", "input_tokens": 5000,
         "output_tokens": 1000, "thinking_tokens": 0, "source_chars": 300},
        {"sku": "S1", "title": "a", "model": "gemini-3-flash-preview", "source": "sample", "input_tokens": 3000,
         "output_tokens": 600, "thinking_tokens": 100, "source_chars": 100},
    ])
    d = client.get("/api/cost/models?skus=1000&usd_jpy=100").json()
    assert d["basis"] == "list"
    assert d["price_retrieved_at"] == "2026-11-11" and d["pricing_url"].startswith("https://cloud.google.com/vertex-ai")
    rows = {r["id"]: r for r in d["models"]}
    r = rows["gemini-3.5-flash"]
    assert r["measurement"]["runs"] == 2 and r["measurement"]["input_tokens"] == 4000
    # avg/SKU 4000 in, 800 out, 200 think → 1000 SKU: 4M*1.5 + 1M*9 = 6 + 9 = $15 → ¥1,500 @100
    assert r["estimate"]["total_usd"] == pytest.approx(15.0) and r["estimate"]["total_jpy"] == pytest.approx(1500.0)
    assert r["price"]["url"] == pricing.PRICING_URL
    m = rows["gemini-3-flash-preview"]
    assert m["measurement"]["only_sample"] is True
    assert m["estimate"]["price_available"] is False and m["estimate"]["total_jpy"] is None
    assert m["price"]["note"] == "See the official pricing page"
    assert d["cheapest"] == "gemini-3.5-flash"            # missing-price rows never compete
    assert rows["gemini-3.8-flash"]["measurement"] is None and rows["gemini-3.8-flash"]["estimate"] is None
    assert d["sample_sku"]["sku"] == "S1"                  # median source length of S1/S2/S3


def test_sample_measure_stream_stores_tokens():
    with client.stream("POST", "/api/cost/measure", json={"model": "gemini-3.5-flash-lite"}) as resp:
        evs = [json.loads(l[5:]) for l in resp.iter_lines() if l.startswith("data:")]
    assert evs[0]["event"] == "start" and evs[0]["model"] == "gemini-3.5-flash-lite"
    assert [e["stage"] for e in evs if e["event"] == "stage"] and evs[-1]["event"] == "result"
    agg = usage.aggregate("gemini-3.5-flash-lite")
    assert agg and agg["only_sample"] and agg["sample_sku"] == evs[0]["sku"]
    assert client.post("/api/cost/measure", json={"model": "gemini-2.5-flash"}).status_code == 400


# ── model plumbing (header → live run) ──────────────────────────────────────

def test_header_selects_model_for_live_runs(monkeypatch):
    seen = {}
    # live path (offline=False) but no GCP: stub BigQuery writes
    monkeypatch.setattr(store, "_append", lambda *a, **k: None)
    monkeypatch.setattr(usage, "_ensure_table", lambda: None)

    async def fake(prompt, schema, model=None, allow_fallback=True):
        from app import models as mr
        m = model or mr.current()
        seen.setdefault("models", set()).add(m)
        data = {"attributes": [], "not_found": []} if "attributes" in json.dumps(schema) and "feature_tags" not in json.dumps(schema) else (
            {"feature_tags": [], "search_keywords": [], "ai_summary": ""} if "feature_tags" in json.dumps(schema) else {})
        return {"data": data, "model": m, "avg_logprobs": None, "input_tokens": 100, "output_tokens": 30,
                "response_tokens": 20, "thinking_tokens": 10, "latency_ms": 1, "fallback": False, "requested_model": m}

    from app import gemini_client
    orig, settings.offline = gemini_client._call, False
    gemini_client._call = fake
    try:
        with client.stream("POST", "/api/products/S1/enrich", headers={"X-Enrich-Model": "gemini-3.5-flash"}) as resp:
            evs = [json.loads(l[5:]) for l in resp.iter_lines() if l.startswith("data:")]
    finally:
        gemini_client._call, settings.offline = orig, True
    assert evs[0]["model"] == "gemini-3.5-flash"
    res = [e for e in evs if e["event"] == "result"]
    assert res and res[0]["model"] == "gemini-3.5-flash" and seen["models"] == {"gemini-3.5-flash"}
    assert usage.aggregate("gemini-3.5-flash")["runs"] == 1        # measured tokens stored for that model


def test_gmc_generate_reports_model_and_batch_guard(monkeypatch):
    r = client.post("/api/gmc/S1/generate", headers={"X-Enrich-Model": "gemini-3.1-flash-lite"}).json()
    assert r["model"] == "gemini-3.1-flash-lite" and r["gmc"]["model"] == "gemini-3.1-flash-lite"
    bad = "gemini-3.6-flash"     # simulate a candidate whose batch support is not verified
    monkeypatch.setitem(models.BY_ID, bad, models.Candidate(bad, "Gemini 3.6 Flash", "flash", None))
    resp = client.post("/api/batch/jobs", json={"limit": 1}, headers={"X-Enrich-Model": bad})
    assert resp.status_code == 400 and bad in resp.json()["detail"]


def test_config_exposes_models_and_price_date():
    c = client.get("/api/config").json()
    assert c["model"] == "gemini-3.8-flash" and c["price_retrieved_at"] == "2026-11-11"
    assert {m["id"] for m in c["models"]} >= {"gemini-3.8-flash", "gemini-3.5-flash"}
    assert all(models.is_eligible(m["id"]) for m in c["models"])


def test_cost_table_default_list_vs_promo_reference():
    usage.rows.append({"sku": "S1", "title": "a", "model": "gemini-3.8-flash", "source": "sample", "input_tokens": 4000,
                       "output_tokens": 1000, "thinking_tokens": 0, "source_chars": 100})
    # 1000 SKU: 4M in + 1M out → list $1.5*4 + $7.5*1 = $13.5 ; promo $0.75*4 + $3.75 = $6.75
    lst = {r["id"]: r for r in client.get("/api/cost/models?skus=1000&usd_jpy=100").json()["models"]}
    assert lst["gemini-3.8-flash"]["estimate"]["total_usd"] == pytest.approx(13.5)
    assert lst["gemini-3.8-flash"]["price"]["promo"] is False
    d = client.get("/api/cost/models?skus=1000&usd_jpy=100&basis=promo").json()
    pro = {r["id"]: r for r in d["models"]}
    assert d["basis"] == "promo"
    if pricing.today_jst() <= "2026-12-31":
        assert pro["gemini-3.8-flash"]["estimate"]["total_usd"] == pytest.approx(6.75)
        assert pro["gemini-3.8-flash"]["price"]["promo"] is True
    # live/batch cost helpers use the list price too
    assert main.cost_jpy(1_000_000, 1_000_000, "gemini-3.8-flash") == pytest.approx((1.5 + 7.5) * settings.usd_jpy)


def test_cost_fields_usd_and_jpy():
    # 1M in + 1M out (incl. thinking) on 3.8 Flash list price → $1.5 + $7.5 = $9 ; ¥900 @100
    assert pricing.cost_fields(1_000_000, 1_000_000, "gemini-3.8-flash", 100) == {"cost_usd": 9.0, "cost_jpy": 900.0}
    assert pricing.cost_fields(1000, 1000, "gemini-3-flash-preview", 100) == {"cost_usd": None, "cost_jpy": None}
    est = pricing.estimate({"input_tokens": 1000, "output_tokens": 100, "thinking_tokens": 0}, 10,
                           pricing.price_for("gemini-3.8-flash"), 150)
    assert est["usd_jpy"] == 150 and est["total_usd"] > 0 and est["per_sku_usd"] > 0
    m = {x["id"]: x for x in models.public_list()}["gemini-3.8-flash"]
    assert (m["price_in_usd_per_mtok"], m["price_out_usd_per_mtok"]) == (1.50, 7.50)
