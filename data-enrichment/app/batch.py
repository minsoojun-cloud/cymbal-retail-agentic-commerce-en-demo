"""Vertex AI Batch Prediction: build request table -> submit -> poll -> ingest results."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import uuid

from .config import settings
from .pipeline import build_requests, parse_json_text, process_gmc, process_stage1, process_stage3
from .repository import store

os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
log = logging.getLogger(__name__)
DS = settings.dataset_ref

TERMINAL = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED",
            "JOB_STATE_PARTIALLY_SUCCEEDED"}


def cost_jpy(input_tokens: int, output_tokens: int, model: str | None = None) -> float | None:
    """LIST-price (standard online) cost in JPY for `model` (output incl. thinking). No batch discount is applied
    in the studio's estimates; None when the model's price is not on the official page."""
    from .pricing import cost_usd, price_for
    usd = cost_usd(input_tokens, output_tokens, 0, price_for(model or settings.batch_model))
    return None if usd is None else round(usd * settings.usd_jpy, 4)


def cost_fields(input_tokens: int, output_tokens: int, model: str | None = None) -> dict:
    """{"cost_usd", "cost_jpy"} at LIST price for `model` (output incl. thinking)."""
    from .pricing import cost_fields as _cf
    return _cf(input_tokens, output_tokens, model or settings.batch_model, settings.usd_jpy)


_CLIENT = None


def _client():
    # keep a module-level reference: a temporary genai.Client closes its HTTP client when garbage-collected
    global _CLIENT
    if _CLIENT is None:
        from google import genai
        _CLIENT = genai.Client(vertexai=True, project=settings.project_id, location=settings.batch_location)
    return _CLIENT


def select_skus(group: str | None = None, limit: int | None = None, skus: list[str] | None = None) -> list[dict]:
    items = [p for p in store.products.values() if (not group or p["group"] == group)]
    if skus:
        wanted = set(skus)
        items = [p for p in items if p["sku"] in wanted]
    return items[:limit] if limit else items


def write_request_table(products: list[dict], table: str) -> int:
    from google.cloud import bigquery
    rows = []
    for p in products:
        for stage, req in build_requests(p).items():
            rows.append({"sku": p["sku"], "stage": stage, "request": json.dumps(req, ensure_ascii=False)})
    schema = [bigquery.SchemaField("sku", "STRING"), bigquery.SchemaField("stage", "STRING"),
              bigquery.SchemaField("request", "STRING")]
    cfg = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    store.bq.load_table_from_json(rows, f"{DS}.{table}", job_config=cfg).result()
    return len(rows)


def submit(group: str | None = None, limit: int | None = None, skus: list[str] | None = None,
           model: str | None = None) -> dict:
    from google.genai import types
    model = model or settings.batch_model
    products = select_skus(group, limit, skus)
    if not products:
        raise ValueError("No target SKUs")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    req_table = f"batch_requests_{stamp}_{suffix}"
    out_table = f"batch_predictions_{stamp}_{suffix}"
    n = write_request_table(products, req_table)
    try:
        job = _client().batches.create(
            model=model,
            src=f"bq://{DS}.{req_table}",
            config=types.CreateBatchJobConfig(dest=f"bq://{DS}.{out_table}", display_name=f"enrich-{stamp}"),
        )
    except Exception as e:  # degrade gracefully: keep the request table, tell the user what to do instead
        log.exception("batch submit failed")
        raise ValueError(
            f"Could not start Vertex AI Batch Prediction ({model} @ {settings.batch_location}): "
            f"{str(e)[:220]} - requests are saved in {DS}.{req_table}. For small runs use live enrichment "
            f"(Enrichment Studio / CSV) or scripts/run_online.py.") from e
    rec = {"job_id": job.name, "display_name": f"enrich-{stamp}", "model": model,
           "state": str(job.state.value if hasattr(job.state, "value") else job.state),
           "sku_count": len(products), "request_count": n, "input_tokens": 0, "output_tokens": 0,
           "cost_jpy": 0.0, "cost_usd": 0.0, "src": f"{DS}.{req_table}", "dest": f"{DS}.{out_table}"}
    store.upsert_job(rec)
    return rec


def _state(job) -> str:
    s = job.state
    return str(s.value if hasattr(s, "value") else s)


def refresh() -> list[dict]:
    """Poll non-terminal jobs; ingest newly finished ones."""
    cli = _client()
    for j in list(store.jobs):
        if j.get("state") in TERMINAL and j.get("ingested"):
            continue
        try:
            job = cli.batches.get(name=j["job_id"])
            state = _state(job)
        except Exception as e:  # pragma: no cover
            log.warning("batch get failed: %s", e)
            continue
        upd = {**j, "state": state}
        err = getattr(job, "error", None)
        if err is not None and getattr(err, "message", None):
            upd["error"] = str(err.message)[:500]
        if state in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED") and not j.get("ingested"):
            stats = ingest(j["dest"], j["job_id"], j.get("model") or settings.batch_model)
            upd.update(stats, ingested=True)
        if upd != j:
            store.upsert_job(upd)
    return store.jobs


def _parse_response(resp: dict) -> tuple[dict | None, float | None, int, int]:
    try:
        cand = (resp.get("candidates") or [])[0]
        text = "".join(pt.get("text", "") for pt in cand["content"]["parts"] if not pt.get("thought"))
        usage = resp.get("usageMetadata") or {}
        out = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
        return parse_json_text(text), cand.get("avgLogprobs"), int(usage.get("promptTokenCount", 0)), out
    except Exception as e:
        log.warning("parse batch response failed: %s", e)
        return None, None, 0, 0


def ingest(dest_table: str, job_id: str, model: str | None = None) -> dict:
    model = model or settings.batch_model
    rows = store.query(f"SELECT sku, stage, TO_JSON_STRING(response) AS response, status FROM `{dest_table}`")
    by_sku: dict[str, dict] = {}
    tin = tout = 0
    for r in rows:
        resp = json.loads(r["response"]) if r.get("response") and r["response"] != "null" else {}
        if isinstance(resp, str):
            resp = json.loads(resp)
        data, alp, i, o = _parse_response(resp)
        tin += i
        tout += o
        by_sku.setdefault(r["sku"], {})[r["stage"]] = (data, alp)
    items = []
    for sku, st in by_sku.items():
        p = store.products.get(sku)
        if not p:
            continue
        recs = []
        extras = {"search_keywords": [], "ai_summary": ""}
        if st.get("stage1") and st["stage1"][0]:
            recs += process_stage1(p, st["stage1"][0], st["stage1"][1], job_id, model)
        if st.get("stage3") and st["stage3"][0]:
            tags, extras = process_stage3(p, st["stage3"][0], st["stage3"][1], job_id, model)
            recs += tags
        gmc = None
        if st.get("gmc") and st["gmc"][0]:
            gmc = process_gmc(p, st["gmc"][0], recs, st["gmc"][1], job_id, model,
                              list(store.products.values()))
        items.append((sku, recs, extras, gmc) if gmc else (sku, recs, extras))
    store.save_many(items, job_id)
    return {"input_tokens": tin, "output_tokens": tout, **cost_fields(tin, tout, model),
            "ingested_skus": len(items)}
