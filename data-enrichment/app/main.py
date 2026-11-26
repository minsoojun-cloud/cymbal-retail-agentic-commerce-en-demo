"""FastAPI app: Product Data Enrichment Studio (PoC)."""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import models as model_registry
from . import usage
from .config import settings
from .csv_ingest import SAMPLE_PRODUCTS, decode_csv_bytes, parse_csv_text, sample_csv_text
from .pipeline import (fallback_gmc, fill_rate_after, fill_rate_before, golden_record, process_gmc,
                       process_stage1, process_stage3, rebuild_gmc_strings)
from .prompts import source_text
from .repository import store
from .rules import (APPROVED, AUTO_APPROVED, EDITED, FINAL_OK, HUMAN_REJECTED, NEEDS_REVIEW, REJECTED,
                    composite_score, evidence_match)
from .schema import (ALLERGENS_28, ALLERGENS_MANDATORY, ATTR_BY_KEY, CATEGORY_GROUPS, FEATURE_TAGS, FOOD_GROUPS,
                     GROUP_SHORT, attrs_for_group, category_group)

os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("enrich")

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Cymbal Retail Data Enrichment Studio", version="2.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.add_middleware(model_registry.ModelMiddleware)   # X-Enrich-Model → models.current()

from .routes_docs import router as docs_router  # noqa: E402
from .routes_images import router as images_router  # noqa: E402

app.include_router(images_router)
app.include_router(docs_router)


@app.on_event("startup")
def _startup():
    model_registry.probe_async()          # cheap ping per candidate @ global, cached (TTL)
    if not settings.offline:
        try:
            store.load()
            log.info("loaded %d products, %d enriched, %d gmc", len(store.products), len(store.records), len(store.gmc))
        except Exception:  # pragma: no cover
            log.exception("initial load failed")
        usage.load()
        import threading
        threading.Thread(target=_batch_poller, daemon=True).start()


def _batch_poller():  # pragma: no cover - background
    import time
    from . import batch
    from .batch import TERMINAL
    while True:
        time.sleep(60)
        try:
            if any(j.get("state") not in TERMINAL or not j.get("ingested") for j in store.jobs
                   if j.get("state") not in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")):
                batch.refresh()
        except Exception:
            log.exception("batch poll failed")


NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/")
def index():
    """index.html with the cross link (STORE_URL) rendered server-side."""
    import html as _html
    from fastapi.responses import HTMLResponse
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    url = settings.store_url if settings.store_url.startswith(("https://", "http://")) else ""
    page = (
        page.replace("{{STORE_URL}}", _html.escape(url, quote=True))
        .replace("{{STORE_HIDDEN}}", "" if url else "hidden")
    )
    return HTMLResponse(page, headers=NO_CACHE)


@app.get("/api/health")
def health():
    return {"status": "ok", "products": len(store.products), "enriched": len(store.records), "gmc": len(store.gmc)}


# ── helpers ──────────────────────────────────────────────────────────────────

def _saved_images(sku: str) -> list[dict]:
    from .storage import proxy_url
    return [{"image_id": r["image_id"], "url": proxy_url(r["gcs_uri"]), "preset_label": r.get("preset_label"),
             "season": r.get("season"), "is_primary": bool(r.get("is_primary")), "width": r.get("width"),
             "height": r.get("height")}
            for r in store.images_of(sku) if r.get("status") == "saved"]


def _product_public(p: dict) -> dict:
    return {**{k: p.get(k) for k in ("sku", "title", "brand", "category", "description", "product_url",
                                     "image_url", "price", "group", "raw_attributes")},
            "group_label": CATEGORY_GROUPS.get(p["group"], p["group"]),
            "doc_sources": p.get("doc_sources") or [], "source": p.get("source") or "aics",
            "images": _saved_images(p["sku"])}


def _targets(group: str) -> list[dict]:
    return [{"key": a.key, "label": a.label, "unit": a.unit, "type": a.type, "raw_keys": list(a.raw_keys)}
            for a in attrs_for_group(group)]


def _detail(sku: str) -> dict:
    p = store.products.get(sku)
    if not p:
        raise HTTPException(404, "SKU not found")
    recs = store.records.get(sku, [])
    order = {a.key: i for i, a in enumerate(attrs_for_group(p["group"]))}
    recs = sorted(recs, key=lambda r: (r["kind"] != "attribute", order.get(r["attr_key"], 999), r["attr_key"]))
    return {
        "product": _product_public(p), "source_text": source_text(p), "targets": _targets(p["group"]),
        "records": recs, "extras": store.extras.get(sku, {"search_keywords": [], "ai_summary": ""}),
        "gmc": store.get_gmc(sku),
        "status": store.status_of(sku), "fill_before": fill_rate_before(p), "fill_after": fill_rate_after(p, recs),
    }


def _summary_item(p: dict) -> dict:
    recs = store.records.get(p["sku"], [])
    attrs = [r for r in recs if r["kind"] == "attribute"]
    return {
        "sku": p["sku"], "title": p["title"], "brand": p["brand"], "group": p["group"],
        "group_label": CATEGORY_GROUPS.get(p["group"]), "image_url": p["image_url"], "price": p["price"],
        "status": store.status_of(p["sku"]), "fill_before": fill_rate_before(p), "fill_after": fill_rate_after(p, recs),
        "attr_count": len(attrs),
        "image_count": sum(1 for r in store.images_of(p["sku"]) if r.get("status") == "saved"),
        "auto_approved": sum(1 for r in recs if r["gate_status"] == AUTO_APPROVED),
        "needs_review": sum(1 for r in recs if r["gate_status"] == NEEDS_REVIEW),
        "rejected": sum(1 for r in recs if r["gate_status"] == REJECTED),
        "tags": [r["value"] for r in recs if r["kind"] == "tag" and r["gate_status"] in FINAL_OK],
    }


def _all_recs() -> list[dict]:
    return [r for rs in store.records.values() for r in rs]


def _cost_stats() -> dict:
    """Dashboard KPI: list-price (standard online) cost per SKU for the default model, from measured tokens."""
    from .pricing import estimate, price_for
    m = model_registry.default_model()
    agg = usage.aggregate(m)
    if not agg:
        return {"basis": "none", "model": m, "per_sku_jpy": None, "per_sku_usd": None, "total_jpy": 0, "total_usd": 0,
                "input_tokens": 0,
                "output_tokens": 0, "thinking_tokens": 0, "skus_measured": 0}
    price = price_for(m)
    est = estimate(agg, 1, price, settings.usd_jpy)
    return {"basis": "List price (no promotions applied)", "model": m, "price_available": price is not None,
            "per_sku_jpy": est.get("per_sku_jpy"), "per_sku_usd": est.get("per_sku_usd"), "skus_measured": agg["skus"], "runs": agg["runs"],
            "avg_input_tokens": round(agg["input_tokens"]), "avg_output_tokens": round(agg["output_tokens"]),
            "avg_thinking_tokens": round(agg["thinking_tokens"]), "usd_jpy": settings.usd_jpy}


# ── API ──────────────────────────────────────────────────────────────────────

@app.get("/api/config")
def config():
    from .pricing import PRICE_RETRIEVED_AT, PRICING_URL
    model_registry.ensure_fresh()
    return {"threshold": settings.auto_approve_threshold, "model": model_registry.default_model(),
            "default_model": settings.gemini_model, "models": model_registry.public_list(),
            "pricing_url": PRICING_URL, "price_retrieved_at": PRICE_RETRIEVED_AT, "usd_jpy": settings.usd_jpy,
            "fallback_model": settings.gemini_fallback_model,
            "batch_model": settings.batch_model, "groups": CATEGORY_GROUPS, "group_short": GROUP_SHORT,
            "food_groups": list(FOOD_GROUPS), "allergens_28": ALLERGENS_28, "allergens_mandatory": ALLERGENS_MANDATORY,
            "attr_labels": {a.key: a.label for a in ATTR_BY_KEY.values()},
            "feature_tags": FEATURE_TAGS, "store_url": settings.store_url,
            "weights": {"evidence": 0.5, "self": 0.3, "token": 0.2},
            "weights_no_token": {"evidence": 0.6, "self": 0.4},
            "gmc_spec_url": "https://support.google.com/merchants/answer/17085370?hl=en",
            "image_models": [settings.image_model, settings.image_model_pro, settings.image_model_fallback],
            "image_bucket": settings.image_bucket, "samples_uri": f"gs://{settings.image_bucket}/_samples/",
            "dataset": settings.dataset_ref, "source_table": settings.bq_source_table}


@app.get("/api/dashboard")
def dashboard():
    prods = list(store.products.values())
    recs = _all_recs()
    attrs = [r for r in recs if r["kind"] == "attribute"]

    def cnt(rs, *st):
        return sum(1 for r in rs if r["gate_status"] in st)

    processed = [p for p in prods if store.records.get(p["sku"])]
    # over ALL PoC SKUs (unprocessed SKUs: after == before)
    fb = [fill_rate_before(p) for p in prods] or [0]
    fa = [fill_rate_after(p, store.records.get(p["sku"], [])) for p in prods] or [0]
    by_group = []
    for g, label in CATEGORY_GROUPS.items():
        gp = [p for p in prods if p["group"] == g]
        gproc = [p for p in gp if store.records.get(p["sku"])]
        grecs = [r for p in gproc for r in store.records.get(p["sku"], [])]
        gattrs = [r for r in grecs if r["kind"] == "attribute"]
        by_group.append({
            "group": g, "label": label, "skus": len(gp), "processed": len(gproc),
            "fill_before": round(sum(fill_rate_before(p) for p in gproc) / max(len(gproc), 1), 3),
            "fill_after": round(sum(fill_rate_after(p, store.records.get(p["sku"], [])) for p in gproc) / max(len(gproc), 1), 3),
            "auto_rate": round(cnt(gattrs, AUTO_APPROVED) / max(len(gattrs), 1), 3),
            "needs_review": cnt(grecs, NEEDS_REVIEW),
            "avg_tags_after": round(sum(1 for r in grecs if r["kind"] == "tag" and r["gate_status"] in FINAL_OK) / max(len(gproc), 1), 1),
        })
    normalized = sum(1 for r in attrs if not (r["gate"] == "gate1" and r["reason"].startswith("Unit normalization")))
    gate1_passed = sum(1 for r in attrs if r["gate"] != "gate1")
    golden_skus = sum(1 for p in processed if store.status_of(p["sku"]) == "completed")
    return {
        "totals": {"skus": len(prods), "processed": len(processed), "attributes": len(attrs),
                   "tags": sum(1 for r in recs if r["kind"] == "tag"),
                   "auto_approved": cnt(recs, AUTO_APPROVED), "needs_review": cnt(recs, NEEDS_REVIEW),
                   "rejected": cnt(recs, REJECTED), "human_approved": cnt(recs, APPROVED, EDITED),
                   "human_rejected": cnt(recs, HUMAN_REJECTED)},
        "fill_rate": {"before": round(sum(fb) / len(fb), 3), "after": round(sum(fa) / len(fa), 3)},
        "auto_approve_rate": round(cnt(attrs, AUTO_APPROVED) / max(len(attrs), 1), 3),
        "by_group": by_group,
        "funnel": {"source_skus": len(prods), "extracted": len(attrs), "normalized": normalized,
                   "gate1_passed": gate1_passed, "auto_approved": cnt(attrs, AUTO_APPROVED),
                   "needs_review": cnt(attrs, NEEDS_REVIEW), "rejected": cnt(attrs, REJECTED),
                   "golden_skus": golden_skus},
        "cost": _cost_stats(),
        "recent_reviews": list(reversed(store.reviews[-8:])),
    }


@app.get("/api/products")
def products(group: str | None = None, status: str | None = None, q: str | None = None):
    items = [_summary_item(p) for p in store.products.values()]
    if group:
        items = [i for i in items if i["group"] == group]
    if status:
        items = [i for i in items if i["status"] == status]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] + i["brand"] + i["sku"]).lower()]
    return {"items": items, "total": len(items)}


@app.get("/api/products/{sku}")
def product_detail(sku: str):
    return _detail(sku)


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def _enrich_single_product(p: dict, job_id: str, source: str = "live") -> tuple[list[dict], dict, dict, dict]:
    """Run stage1 + stage3 + GMC conversational attributes concurrently (or offline fallback).

    Uses the model selected for this request (models.current()); stats["model"] is the model actually used."""
    model = model_registry.current()
    if settings.offline:
        raw = p.get("raw_attributes") or {}
        s1_attrs = []
        for a in attrs_for_group(p["group"]):
            for rk in a.raw_keys:
                if rk in raw:
                    s1_attrs.append({"key": a.key, "raw_value": str(raw[rk]), "evidence": str(raw[rk])[:30],
                                     "source_field": "attributes", "confidence": 0.96})
                    break
        recs = process_stage1(p, {"attributes": s1_attrs, "not_found": []}, None, job_id, model)
        tags, extras = process_stage3(p, {"feature_tags": [], "search_keywords": [p.get("brand") or "", CATEGORY_GROUPS.get(p["group"], "")],
                                          "ai_summary": (p.get("description") or p.get("title") or "")[:75]}, None, job_id, model)
        all_recs = recs + tags
        gmc = fallback_gmc(p, all_recs, extras, list(store.products.values()))
        return all_recs, extras, gmc, {"input_tokens": 1200, "output_tokens": 550, "thinking_tokens": 0,
                                       "latency_ms": 120, "model": model,
                                       "s1_ms": 60, "s3_ms": 50, "gmc_ms": 60, "attr_count": len(recs), "tag_count": len(tags)}

    from . import gemini_client
    s1, s3, sg = await asyncio.gather(
        gemini_client.run_stage1(p, model),
        gemini_client.run_stage3(p, model),
        gemini_client.run_gmc(p, model),
    )
    recs = process_stage1(p, s1["data"], s1["avg_logprobs"], job_id, s1["model"])
    tags, extras = process_stage3(p, s3["data"], s3["avg_logprobs"], job_id, s3["model"])
    all_recs = recs + tags
    gmc = process_gmc(p, sg["data"], all_recs, sg["avg_logprobs"], job_id, sg["model"],
                      list(store.products.values()))
    usage.record(p, {"stage1": s1, "stage3": s3, "gmc": sg}, job_id, source)
    used = sorted({s1["model"], s3["model"], sg["model"]})
    stats = {
        "model": used[0] if len(used) == 1 else " / ".join(used),
        "requested_model": model,
        "input_tokens": s1["input_tokens"] + s3["input_tokens"] + sg["input_tokens"],
        "output_tokens": s1["output_tokens"] + s3["output_tokens"] + sg["output_tokens"],
        "thinking_tokens": s1["thinking_tokens"] + s3["thinking_tokens"] + sg["thinking_tokens"],
        "latency_ms": max(s1["latency_ms"], s3["latency_ms"], sg["latency_ms"]),
        "s1_ms": s1["latency_ms"],
        "s3_ms": s3["latency_ms"],
        "gmc_ms": sg["latency_ms"],
        "attr_count": len(recs),
        "tag_count": len(tags),
    }
    return all_recs, extras, gmc, stats


@app.post("/api/products/{sku}/enrich")
async def enrich_live(sku: str):
    p = store.products.get(sku)
    if not p:
        raise HTTPException(404, "SKU not found")
    from . import gemini_client

    model = model_registry.current()

    async def gen():
        job_id = f"live-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
        yield _sse({"event": "start", "sku": sku, "model": model})
        try:
            yield _sse({"event": "stage", "stage": "stage1", "status": "running"})
            yield _sse({"event": "stage", "stage": "stage3", "status": "running"})
            t1 = asyncio.create_task(gemini_client.run_stage1(p, model))
            t3 = asyncio.create_task(gemini_client.run_stage3(p, model))
            tg = asyncio.create_task(gemini_client.run_gmc(p, model))
            results: dict[str, dict] = {}
            for fut in asyncio.as_completed([t1, t3]):
                r = await fut
                stage = "stage1" if "attributes" in r["data"] else "stage3"
                results[stage] = r
                count = len(r["data"].get("attributes") or r["data"].get("feature_tags") or [])
                yield _sse({"event": "stage", "stage": stage, "status": "done", "latency_ms": r["latency_ms"],
                            "input_tokens": r["input_tokens"], "output_tokens": r["output_tokens"],
                            "thinking_tokens": r["thinking_tokens"], "model": r["model"], "count": count})
            sg = await tg
            s1, s3 = results["stage1"], results["stage3"]
            recs = process_stage1(p, s1["data"], s1["avg_logprobs"], job_id, s1["model"])
            failed = sum(1 for r in recs if r["reason"].startswith("Unit normalization"))
            yield _sse({"event": "stage", "stage": "normalize", "status": "done", "count": len(recs), "failed": failed})
            await asyncio.sleep(0.3)  # let the UI animate the step
            tags, extras = process_stage3(p, s3["data"], s3["avg_logprobs"], job_id, s3["model"])
            all_recs = recs + tags
            gmc = process_gmc(p, sg["data"], all_recs, sg["avg_logprobs"], job_id, sg["model"],
                              list(store.products.values()))
            summary = {st: sum(1 for r in all_recs if r["gate_status"] == st) for st in (AUTO_APPROVED, NEEDS_REVIEW, REJECTED)}
            yield _sse({"event": "stage", "stage": "gate", "status": "done", "summary": summary})
            store.save_enrichment(sku, all_recs, extras, job_id, gmc=gmc)
            usage.record(p, {"stage1": s1, "stage3": s3, "gmc": sg}, job_id, "studio")
            store.live_runs.append({"sku": sku, "model": s1["model"],
                                    "input_tokens": s1["input_tokens"] + s3["input_tokens"] + sg["input_tokens"],
                                    "output_tokens": s1["output_tokens"] + s3["output_tokens"] + sg["output_tokens"]})
            used = sorted({s1["model"], s3["model"], sg["model"]})
            tin = s1["input_tokens"] + s3["input_tokens"] + sg["input_tokens"]
            tout = s1["response_tokens"] + s3["response_tokens"] + sg["response_tokens"]
            tth = s1["thinking_tokens"] + s3["thinking_tokens"] + sg["thinking_tokens"]
            await asyncio.sleep(0.2)
            yield _sse({"event": "result", "detail": _detail(sku), "model": " / ".join(used), "requested_model": model,
                        "fallback": used != [model], "input_tokens": tin, "output_tokens": tout,
                        "thinking_tokens": tth, **cost_fields(tin, tout + tth, model)})
        except Exception as e:
            log.exception("live enrichment failed")
            yield _sse({"event": "error", "message": f"[{model}] {str(e)[:300]}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── CSV Live Test (Upload -> Real-time Enrichment -> Save to BigQuery) ───────

@app.get("/api/csv/sample.csv")
def csv_sample_download():
    text = sample_csv_text()
    return StreamingResponse(iter([text]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=aics_live_test_sample.csv"})


@app.get("/api/csv/sample")
def csv_sample_json():
    text = sample_csv_text()
    items, cols = parse_csv_text(text)
    enriched_preview = []
    for p in items:
        enriched_preview.append({
            **p,
            "group_label": CATEGORY_GROUPS.get(p["group"], p["group"]),
            "fill_before": fill_rate_before(p),
            "raw_attr_count": len(p.get("raw_attributes") or {}),
        })
    return {"items": enriched_preview, "count": len(enriched_preview), "columns": cols, "csv_text": text}


@app.post("/api/csv/parse")
def csv_parse(body: dict = Body(...)):
    csv_text = body.get("csv_text") or ""
    if not csv_text and body.get("csv_base64"):
        import base64
        csv_text = decode_csv_bytes(base64.b64decode(body["csv_base64"]))
    try:
        items, cols = parse_csv_text(csv_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out = []
    for p in items:
        out.append({
            **p,
            "group_label": CATEGORY_GROUPS.get(p["group"], p["group"]),
            "fill_before": fill_rate_before(p),
            "raw_attr_count": len(p.get("raw_attributes") or {}),
        })
    return {"items": out, "count": len(out), "columns": cols}


@app.post("/api/csv/enrich")
async def csv_enrich_live(body: dict = Body(...)):
    """Stream real-time enrichment for uploaded CSV products and save to BigQuery."""
    model = model_registry.current()
    raw_products = body.get("products")
    if not raw_products:
        csv_text = body.get("csv_text") or ""
        try:
            raw_products, _ = parse_csv_text(csv_text)
        except ValueError as e:
            raise HTTPException(400, str(e))
    save_to_bq = bool(body.get("save_to_bq", True))

    # Normalize each product dict
    norm_products: list[dict] = []
    for idx, rp in enumerate(raw_products[:50], start=1):
        sku = str(rp.get("sku") or f"CSV-{idx:04d}").strip()
        title = str(rp.get("title") or f"Product {sku}").strip()
        cat = str(rp.get("category") or "").strip()
        grp = rp.get("group") if rp.get("group") in CATEGORY_GROUPS else category_group(f"{cat} {title}")
        norm_products.append({
            "sku": sku,
            "title": title,
            "brand": str(rp.get("brand") or "").strip(),
            "category": cat or CATEGORY_GROUPS.get(grp, "Other"),
            "description": str(rp.get("description") or "").strip(),
            "price": rp.get("price"),
            "product_url": str(rp.get("product_url") or "").strip(),
            "image_url": str(rp.get("image_url") or "").strip(),
            "group": grp,
            "raw_attributes": rp.get("raw_attributes") or {},
        })

    async def gen():
        import time
        t0 = time.time()
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        job_id = f"csv-live-{stamp}"
        # Save products to in-memory store immediately; BQ sync happens per-SKU or at completion
        store.save_source_products(norm_products, persist_bq=False)
        yield _sse({
            "event": "start",
            "job_id": job_id,
            "model": model,
            "total": len(norm_products),
            "save_to_bq": save_to_bq,
            "dataset": settings.dataset_ref,
        })
        tin_total = tout_total = 0
        succeeded_skus: list[str] = []

        for i, p in enumerate(norm_products):
            sku = p["sku"]
            yield _sse({
                "event": "sku_start",
                "index": i,
                "total": len(norm_products),
                "sku": sku,
                "title": p["title"],
                "group": p["group"],
                "group_label": CATEGORY_GROUPS.get(p["group"], p["group"]),
            })
            try:
                all_recs, extras, gmc, st = await _enrich_single_product(p, job_id, "csv")
                store.save_enrichment(sku, all_recs, extras, job_id, gmc=gmc, persist_bq=False)
                tin_total += st["input_tokens"]
                tout_total += st["output_tokens"]
                succeeded_skus.append(sku)
                det = _detail(sku)
                yield _sse({
                    "event": "sku_done",
                    "index": i,
                    "total": len(norm_products),
                    "sku": sku,
                    "latency_ms": st["latency_ms"],
                    "input_tokens": st["input_tokens"],
                    "output_tokens": st["output_tokens"],
                    "thinking_tokens": st.get("thinking_tokens", 0),
                    "model": st.get("model", model),
                    "attr_count": st["attr_count"],
                    "tag_count": st["tag_count"],
                    "qa_count": len(gmc.get("questions_and_answers") or []),
                    "fill_before": det["fill_before"],
                    "fill_after": det["fill_after"],
                    "status": det["status"],
                    "detail": det,
                })
            except Exception as e:
                log.exception("CSV live enrichment failed for SKU %s", sku)
                yield _sse({
                    "event": "sku_error",
                    "index": i,
                    "sku": sku,
                    "message": str(e)[:250],
                })

        bq_result = None
        if save_to_bq and succeeded_skus:
            yield _sse({"event": "bq_saving", "skus": len(succeeded_skus), "dataset": settings.dataset_ref})
            try:
                bq_result = await asyncio.to_thread(store.persist_skus_to_bq, succeeded_skus)
                store.upsert_job({
                    "job_id": job_id,
                    "display_name": f"csv-live-{stamp}",
                    "model": model,
                    "state": "JOB_STATE_SUCCEEDED",
                    "sku_count": len(succeeded_skus),
                    "request_count": len(succeeded_skus) * 3,
                    "input_tokens": tin_total,
                    "output_tokens": tout_total,
                    **cost_fields(tin_total, tout_total, model),
                    "src": "CSV Live Upload",
                    "dest": f"{settings.dataset_ref}.enriched_attributes",
                    "ingested": True,
                    "ingested_skus": len(succeeded_skus),
                })
            except Exception as e:
                log.exception("BigQuery save failed")
                yield _sse({"event": "error", "message": f"BigQuery save error: {e}"})

        elapsed_ms = int((time.time() - t0) * 1000)
        yield _sse({
            "event": "complete",
            "job_id": job_id,
            "model": model,
            "processed": len(succeeded_skus),
            "total": len(norm_products),
            "save_to_bq": save_to_bq,
            "bq_result": bq_result,
            "input_tokens": tin_total,
            "output_tokens": tout_total,
            **cost_fields(tin_total, tout_total, model),
            "elapsed_ms": elapsed_ms,
        })

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/csv/save-bq")
def csv_save_bq(body: dict = Body(...)):
    skus = body.get("skus") or []
    if not skus:
        raise HTTPException(400, "No SKUs specified to save")
    res = store.persist_skus_to_bq([str(s) for s in skus])
    return {"ok": True, **res}


# ── Google Merchant Center Conversational Attributes API ─────────────────────

GMC_FEED_COLUMNS = [
    "id", "item_group_id", "title", "item_group_title", "variant_option",
    "question_and_answer", "product_highlight", "product_detail",
    "related_product", "popularity_rank", "document_link",
    "brand", "price", "link", "image_link", "additional_image_link",
]


def _gmc_feed_row(p: dict, g: dict) -> dict:
    price_val = p.get("price")
    price_str = f"{int(price_val)} JPY" if price_val else ""
    return {
        "id": p["sku"],
        "item_group_id": g.get("item_group_id") or "",
        "title": p.get("title") or "",
        "item_group_title": g.get("item_group_title") or "",
        "variant_option": g.get("variant_option") or "",
        "question_and_answer": g.get("question_and_answer") or "",
        "product_highlight": g.get("product_highlight") or "",
        "product_detail": g.get("product_detail") or "",
        "related_product": g.get("related_product") or "",
        "popularity_rank": g.get("popularity_rank") if g.get("popularity_rank") is not None else "",
        "document_link": g.get("document_link") or "",
        "brand": p.get("brand") or "",
        "price": price_str,
        "link": p.get("product_url") or "",
        "image_link": p.get("image_url") or "",
        "additional_image_link": ",".join(
            f"{settings.public_base_url.rstrip('/')}{i['url']}" for i in _saved_images(p["sku"])[:10]),
    }


def _gmc_summary_item(p: dict) -> dict:
    g = store.get_gmc(p["sku"])
    qas = g.get("questions_and_answers") or []
    vos = g.get("variant_options") or []
    hls = g.get("product_highlights") or []
    dts = g.get("product_details") or []
    rps = g.get("related_products") or []
    return {
        "sku": p["sku"],
        "title": p.get("title") or "",
        "brand": p.get("brand") or "",
        "group": p.get("group") or "",
        "group_label": CATEGORY_GROUPS.get(p.get("group"), p.get("group")),
        "image_url": p.get("image_url") or "",
        "price": p.get("price"),
        "item_group_id": g.get("item_group_id") or "",
        "item_group_title": g.get("item_group_title") or "",
        "variant_option": g.get("variant_option") or "",
        "variant_options": vos,
        "qa_count": len(qas),
        "qa_auto_approved": sum(1 for q in qas if q.get("status") == AUTO_APPROVED),
        "qa_needs_review": sum(1 for q in qas if q.get("status") == NEEDS_REVIEW),
        "highlight_count": len(hls),
        "detail_count": len(dts),
        "related_count": len(rps),
        "popularity_rank": g.get("popularity_rank"),
        "document_link": g.get("document_link") or "",
        "model": g.get("model") or "",
        "job_id": g.get("job_id") or "",
        "updated_at": g.get("updated_at") or "",
    }


@app.get("/api/gmc")
def gmc_list(group: str | None = None, q: str | None = None):
    prods = list(store.products.values())
    all_items = [_gmc_summary_item(p) for p in prods]
    total_skus = len(all_items)
    total_qas = sum(i["qa_count"] for i in all_items)
    stats = {
        "total_skus": total_skus,
        "total_qas": total_qas,
        "avg_qas_per_sku": round(total_qas / max(total_skus, 1), 1),
        "variant_option_skus": sum(1 for i in all_items if i["variant_options"]),
        "related_product_skus": sum(1 for i in all_items if i["related_count"] > 0),
        "highlight_skus": sum(1 for i in all_items if i["highlight_count"] > 0),
        "auto_approved_qas": sum(i["qa_auto_approved"] for i in all_items),
        "needs_review_qas": sum(i["qa_needs_review"] for i in all_items),
    }
    items = all_items
    if group:
        items = [i for i in items if i["group"] == group]
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i["title"] + i["brand"] + i["sku"] + i["item_group_title"]).lower()]
    return {
        "spec_url": "https://support.google.com/merchants/answer/17085370?hl=en",
        "spec_url_ja": "https://support.google.com/merchants/answer/17085370?hl=ja",
        "model": model_registry.current(),
        "stats": stats,
        "items": items,
        "total": len(items),
    }


@app.get("/api/gmc/export.tsv")
def gmc_export_tsv(group: str | None = None):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(GMC_FEED_COLUMNS)
    for p in store.products.values():
        if group and p.get("group") != group:
            continue
        row = _gmc_feed_row(p, store.get_gmc(p["sku"]))
        w.writerow([row.get(c, "") for c in GMC_FEED_COLUMNS])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/tab-separated-values; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=gmc_conversational_feed.tsv"})


@app.get("/api/gmc/export.csv")
def gmc_export_csv(group: str | None = None):
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(GMC_FEED_COLUMNS)
    for p in store.products.values():
        if group and p.get("group") != group:
            continue
        row = _gmc_feed_row(p, store.get_gmc(p["sku"]))
        w.writerow([row.get(c, "") for c in GMC_FEED_COLUMNS])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=gmc_conversational_feed.csv"})


@app.get("/api/gmc/{sku}")
def gmc_detail(sku: str):
    p = store.products.get(sku)
    if not p:
        raise HTTPException(404, "SKU not found")
    g = store.get_gmc(sku)
    return {
        "product": _product_public(p),
        "source_text": source_text(p),
        "gmc": g,
        "feed_row": _gmc_feed_row(p, g),
    }


@app.post("/api/gmc/{sku}/generate")
async def gmc_generate(sku: str):
    p = store.products.get(sku)
    if not p:
        raise HTTPException(404, "SKU not found")
    job_id = f"gmc-live-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
    recs = store.records.get(sku, [])
    model = model_registry.current()
    if settings.offline:
        gmc = fallback_gmc(p, recs, store.extras.get(sku, {}), list(store.products.values()))
        gmc["job_id"] = job_id
        gmc["model"] = model
        store.save_gmc(sku, gmc, persist_bq=True)
        return {**gmc_detail(sku), "latency_ms": 80, "input_tokens": 600, "output_tokens": 320, "model": model,
                "requested_model": model}
    from . import gemini_client
    try:
        sg = await gemini_client.run_gmc(p, model)
    except Exception as e:
        raise HTTPException(502, f"[{model}] GMC generation failed: {str(e)[:250]}")
    gmc = process_gmc(p, sg["data"], recs, sg["avg_logprobs"], job_id, sg["model"],
                      list(store.products.values()))
    store.save_gmc(sku, gmc, persist_bq=True)
    return {
        **gmc_detail(sku),
        "latency_ms": sg["latency_ms"],
        "input_tokens": sg["input_tokens"],
        "output_tokens": sg["output_tokens"],
        "thinking_tokens": sg["thinking_tokens"],
        "model": sg["model"],
        "requested_model": model,
    }


@app.put("/api/gmc/{sku}")
def gmc_update(sku: str, body: dict = Body(...)):
    p = store.products.get(sku)
    if not p:
        raise HTTPException(404, "SKU not found")
    cur = dict(store.get_gmc(sku))
    src = source_text(p)
    for k in ("item_group_id", "item_group_title", "document_link"):
        if k in body and body[k] is not None:
            cur[k] = str(body[k]).strip()
    if "popularity_rank" in body and body["popularity_rank"] is not None:
        try:
            cur["popularity_rank"] = round(float(body["popularity_rank"]), 1)
        except (TypeError, ValueError):
            pass
    if "variant_options" in body and isinstance(body["variant_options"], list):
        vos = []
        for vo in body["variant_options"]:
            name = str(vo.get("name") or "").strip()
            val = str(vo.get("value") or "").strip()
            ev_text = str(vo.get("evidence") or val).strip()
            if name and val:
                vos.append({
                    "name": name,
                    "value": val,
                    "evidence": ev_text,
                    "evidence_match": evidence_match(ev_text, src, val),
                })
        cur["variant_options"] = vos
    if "questions_and_answers" in body and isinstance(body["questions_and_answers"], list):
        qas = []
        for qa in body["questions_and_answers"]:
            q = str(qa.get("question") or "").strip()
            a = str(qa.get("answer") or "").strip()
            ev_text = str(qa.get("evidence") or "").strip()
            if not q or not a:
                continue
            ev = evidence_match(ev_text, src) if ev_text else 1.0
            conf = float(qa.get("confidence") or 0.95)
            sc = composite_score(ev, conf, None)
            qas.append({
                "question": q,
                "answer": a,
                "evidence": ev_text,
                "confidence": round(conf, 3),
                "evidence_match": ev,
                "score": sc,
                "status": qa.get("status") or (AUTO_APPROVED if sc >= settings.auto_approve_threshold else NEEDS_REVIEW),
            })
        cur["questions_and_answers"] = qas
    if "product_highlights" in body and isinstance(body["product_highlights"], list):
        cur["product_highlights"] = [str(h).strip() for h in body["product_highlights"] if str(h).strip()]
    if "related_products" in body and isinstance(body["related_products"], list):
        cur["related_products"] = [
            {
                "relationship_type": str(r.get("relationship_type") or "similar_to"),
                "identifier_type": str(r.get("identifier_type") or "id"),
                "identifier": str(r.get("identifier") or "").strip(),
                "title": str(r.get("title") or r.get("identifier") or "").strip(),
            }
            for r in body["related_products"] if str(r.get("identifier") or "").strip()
        ]
    store.save_gmc(sku, cur, persist_bq=True)
    return gmc_detail(sku)


@app.get("/api/reviews")
def reviews(group: str | None = None):
    items = []
    for sku, recs in store.records.items():
        p = store.products.get(sku)
        if not p or (group and p["group"] != group):
            continue
        for r in recs:
            if r["gate_status"] == NEEDS_REVIEW:
                items.append({"rec": r, "product": {"sku": sku, "title": p["title"], "image_url": p["image_url"],
                                                    "group_label": CATEGORY_GROUPS.get(p["group"])},
                              "source_text": source_text(p)})
    items.sort(key=lambda i: (i["rec"]["evidence_match"] > 0, i["rec"]["score"]))
    return {"items": items, "count": len(items)}


@app.post("/api/reviews/{sku}/{attr_key:path}")
def review(sku: str, attr_key: str, body: dict = Body(...)):
    action = body.get("action")
    if action not in ("approve", "edit", "reject"):
        raise HTTPException(400, "action must be approve|edit|reject")
    value: Any = body.get("value")
    if action == "edit":
        d = ATTR_BY_KEY.get(attr_key)
        if d is not None and d.type == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(400, "Please enter a number")
        if attr_key == "allergens":
            from .normalizer import normalize
            nr = normalize("allergens", str(value or ""))
            if not nr.ok:
                raise HTTPException(400, f"Enter allergens using the 28-allergen vocabulary (e.g. shrimp, wheat / none): {nr.note}")
            value = nr.value
        if d is not None and d.type == "bool" and isinstance(value, str):
            value = value.strip().lower() in ("true", "1", "yes", "y", "compatible", "supported")
    if action == "approve":
        cur = next((r for r in store.records.get(sku, []) if r["attr_key"] == attr_key), None)
        if cur is not None and cur.get("value") in (None, ""):
            raise HTTPException(400, "Cannot approve: no value was extracted. Check the package label and enter it with Edit (E), or reject")
    rec = store.apply_review(sku, attr_key, action, value, body.get("comment") or "")
    if rec is None:
        raise HTTPException(404, "record not found")
    remaining = sum(1 for r in _all_recs() if r["gate_status"] == NEEDS_REVIEW)
    return {"record": rec, "remaining": remaining}


@app.get("/api/batch/jobs")
def batch_jobs():
    return {"jobs": [_with_usd(j) for j in store.jobs]}


def _with_usd(job: dict) -> dict:
    """Older batch_jobs rows only carry cost_jpy: derive cost_usd for the (USD-only) UI."""
    if job.get("cost_usd") is None and job.get("cost_jpy") is not None:
        return {**job, "cost_usd": round(float(job["cost_jpy"]) / settings.usd_jpy, 6)}
    return job


@app.post("/api/batch/jobs")
def batch_submit(body: dict = Body(default={})):
    from . import batch
    model = model_registry.current()
    reason = model_registry.batch_reason(model)
    if reason:
        raise HTTPException(400, f"{model}: {reason}")
    try:
        job = batch.submit(body.get("group") or None, int(body.get("limit") or 10), model=model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job": job}


@app.post("/api/batch/jobs/refresh")
def batch_refresh():
    from . import batch
    return {"jobs": [_with_usd(j) for j in batch.refresh()]}


# ── Models & list-price cost (CONTRACT v6) ───────────────────────────────────

@app.get("/api/models")
def models_list(refresh: bool = False):
    if refresh:
        model_registry.probe_all(force=True)
    else:
        model_registry.ensure_fresh()
    return {"models": model_registry.public_list(), "default": model_registry.default_model(),
            "selected": model_registry.current()}


_catalog_count: dict = {}


def catalog_count() -> int | None:
    """Full catalog size from BigQuery `cymbal.products` (cached 1h)."""
    import time
    if settings.offline:
        return None
    if _catalog_count.get("t", 0) > time.time() - 3600:
        return _catalog_count.get("n")
    try:
        n = int(store.query(f"SELECT COUNT(*) AS n FROM `{settings.bq_source_table}`")[0]["n"])
    except Exception as e:  # pragma: no cover
        log.warning("catalog count failed: %s", e)
        n = _catalog_count.get("n")
    _catalog_count.update(t=time.time(), n=n)
    return n


def sample_product() -> dict | None:
    """Representative SKU for "Measure with 1 sample": median source-text length (same SKU for every model)."""
    prods = sorted(store.products.values(), key=lambda p: (len(source_text(p)), p["sku"]))
    return prods[len(prods) // 2] if prods else None


def cost_table(skus: int, usd_jpy: float, basis: str = "list") -> dict:
    from .pricing import PRICE_RETRIEVED_AT, PRICING_URL, estimate, norm_basis, price_for, price_public, today_jst
    basis = norm_basis(basis)
    rows = []
    for m in model_registry.public_list():
        if m["status"] == "unavailable":
            continue
        agg = usage.aggregate(m["id"])
        price = price_for(m["id"], basis=basis)
        rows.append({**m, "price": price_public(m["id"], basis), "measurement": agg,
                     "estimate": estimate(agg, skus, price, usd_jpy) if agg else None})
    priced = [r for r in rows if r["estimate"] and r["estimate"]["price_available"]]
    sp = sample_product()
    return {
        "skus": skus, "usd_jpy": usd_jpy, "basis": basis, "as_of": today_jst(),
        "pricing_url": PRICING_URL, "price_retrieved_at": PRICE_RETRIEVED_AT,
        "catalog_count": catalog_count(), "demo_skus": len(store.products),
        "sample_sku": {"sku": sp["sku"], "title": sp["title"], "chars": len(source_text(sp))} if sp else None,
        "unavailable": [m for m in model_registry.public_list() if m["status"] == "unavailable"],
        "models": rows,
        "cheapest": min(priced, key=lambda r: r["estimate"]["total_usd"])["id"] if priced else None,
    }


@app.get("/api/cost/models")
def cost_models(skus: int = Query(8807, ge=1, le=10_000_000), usd_jpy: float | None = Query(None, gt=0, le=10000),
                basis: str = Query("list", pattern="^(list|promo|regular|current)$")):
    return cost_table(skus, usd_jpy or settings.usd_jpy, basis)


@app.get("/api/cost/estimate")
def cost_estimate(skus: int = Query(8807, ge=1, le=10_000_000)):
    """Back-compat: list-price estimate for the selected model."""
    t = cost_table(skus, settings.usd_jpy)
    m = model_registry.current()
    row = next((r for r in t["models"] if r["id"] == m), None)
    est = (row or {}).get("estimate") or {}
    return {"skus": skus, "model": m, "total_jpy": est.get("total_jpy"), "per_sku_jpy": est.get("per_sku_jpy"),
            "total_usd": est.get("total_usd"), "per_sku_usd": est.get("per_sku_usd"), "usd_jpy": settings.usd_jpy,
            "price_available": est.get("price_available", False), "measurement": (row or {}).get("measurement"),
            "basis": "List price (no promotions applied)", "pricing_url": t["pricing_url"]}


@app.post("/api/cost/measure")
async def cost_measure(body: dict = Body(default={})):
    """"Measure with 1 sample": run the 3 calls for ONE representative SKU with the given model (no fallback),
    store the measured tokens in BigQuery `token_usage`. Enrichment results are NOT overwritten."""
    req = (body.get("model") or "").strip() or model_registry.current()
    if req not in model_registry.BY_ID or not model_registry.is_eligible(req):
        raise HTTPException(400, f"Unsupported model: {req}")
    if not model_registry.is_available(req):
        raise HTTPException(400, f"{req} is not available in this project")
    p = store.products.get(body.get("sku") or "") or sample_product()
    if not p:
        raise HTTPException(400, "No product available for measurement")
    from . import gemini_client

    async def gen():
        job_id = f"sample-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
        yield _sse({"event": "start", "model": req, "sku": p["sku"], "title": p["title"],
                    "chars": len(source_text(p))})
        results: dict[str, dict] = {}
        try:
            if settings.offline:
                for st, (i, o, t) in {"stage1": (700, 220, 90), "stage3": (520, 160, 60), "gmc": (800, 420, 120)}.items():
                    results[st] = {"model": req, "input_tokens": i, "output_tokens": o + t, "response_tokens": o,
                                   "thinking_tokens": t, "latency_ms": 5, "fallback": False}
                    yield _sse({"event": "stage", "stage": st, "status": "done", **{k: results[st][k] for k in
                               ("input_tokens", "response_tokens", "thinking_tokens", "latency_ms")}})
            else:
                tasks = {asyncio.ensure_future(fn(p, req, False)): st for st, fn in
                         (("stage1", gemini_client.run_stage1), ("stage3", gemini_client.run_stage3),
                          ("gmc", gemini_client.run_gmc))}
                for fut in asyncio.as_completed(list(tasks)):
                    r = await fut
                    st = "stage1" if "attributes" in r["data"] else ("stage3" if "feature_tags" in r["data"] else "gmc")
                    results[st] = r
                    yield _sse({"event": "stage", "stage": st, "status": "done", "input_tokens": r["input_tokens"],
                                "response_tokens": r["response_tokens"], "thinking_tokens": r["thinking_tokens"],
                                "latency_ms": r["latency_ms"]})
            row = await asyncio.to_thread(usage.record, p, results, job_id, "sample", True)
            if not row:
                raise RuntimeError("Could not get token counts for the 3 calls")
            yield _sse({"event": "result", "row": row, "measurement": usage.aggregate(req)})
        except Exception as e:
            log.exception("sample measure failed")
            yield _sse({"event": "error", "message": f"[{req}] {str(e)[:300]}"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def cost_fields(input_tokens: int, output_tokens: int, model: str) -> dict:
    """{"cost_usd", "cost_jpy"} at list price (standard online); output_tokens include thinking."""
    from .pricing import cost_fields as _cf
    return _cf(input_tokens, output_tokens, model, settings.usd_jpy)


def cost_jpy(input_tokens: int, output_tokens: int, model: str) -> float | None:
    """List-price (standard online) cost in JPY; output_tokens include thinking. None if not on the price page."""
    from .pricing import cost_usd, price_for
    usd = cost_usd(input_tokens, output_tokens, 0, price_for(model))
    return None if usd is None else round(usd * settings.usd_jpy, 4)


def _golden_items(group: str | None = None) -> list[dict]:
    out = []
    for sku, recs in store.records.items():
        p = store.products.get(sku)
        if not p or (group and p["group"] != group):
            continue
        out.append(golden_record(p, recs, store.extras.get(sku, {})))
    return out


@app.get("/api/golden")
def golden(group: str | None = None):
    return {"items": _golden_items(group)}


@app.get("/api/golden/export.csv")
def golden_csv(group: str | None = None):
    items = _golden_items(group)
    keys = [a.key for a in ATTR_BY_KEY.values()]
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM for Excel (Japanese)
    w = csv.writer(buf)
    w.writerow(["sku", "title", "category_group"] + keys + ["feature_tags", "search_keywords", "ai_summary",
                                                              "fill_rate_before", "fill_rate_after"])
    for g in items:
        row = [g["sku"], g["title"], g["group"]]
        row += [g["attributes"].get(k, {}).get("value", "") for k in keys]
        row += ["|".join(g["feature_tags"]), "|".join(g["search_keywords"]), g["ai_summary"],
                g["fill_rate_before"], g["fill_rate_after"]]
        w.writerow(row)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=golden_records.csv"})


@app.post("/api/admin/reload")
def reload_store():
    store.load()
    return {"products": len(store.products), "enriched": len(store.records), "gmc": len(store.gmc)}

