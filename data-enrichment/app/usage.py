"""Measured token usage per SKU and model (all 3 calls: attributes / tags / GMC) → BigQuery `token_usage`.

One row per (SKU run, model). Rows are written for every complete live run (studio, CSV live test, docs re-enrich)
and for "Measure with 1 sample". Only runs where all three calls were answered by the requested model (no fallback)
are recorded, so the averages are attributable to that model.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from .config import settings

log = logging.getLogger(__name__)
TABLE = "token_usage"
STAGES = ("stage1", "stage3", "gmc")

DDL = """CREATE TABLE IF NOT EXISTS `{ds}.token_usage` (
  sku STRING, title STRING, model STRING, job_id STRING, source STRING,
  input_tokens INT64, output_tokens INT64, thinking_tokens INT64, calls INT64,
  stage_tokens_json STRING, source_chars INT64, created_at TIMESTAMP)"""

_lock = threading.Lock()
rows: list[dict] = []
_table_ready = False


def _ensure_table():
    global _table_ready
    if _table_ready or settings.offline:
        return
    from .repository import store
    store.bq.query(DDL.format(ds=settings.dataset_ref)).result()
    _table_ready = True


def load():
    """Load all rows from BigQuery (creates the table on first use)."""
    if settings.offline:
        return
    from .repository import store
    try:
        _ensure_table()
        got = store.query(f"SELECT * FROM `{settings.dataset_ref}.{TABLE}` ORDER BY created_at")
        for r in got:
            if isinstance(r.get("created_at"), dt.datetime):
                r["created_at"] = r["created_at"].isoformat()
        with _lock:
            rows[:] = got
        log.info("token_usage: %d rows", len(got))
    except Exception:  # pragma: no cover
        log.exception("load token_usage failed")


def make_row(p: dict, results: dict[str, dict], job_id: str, source: str) -> dict | None:
    """Build a row from the three stage results; None if incomplete or answered by a fallback model."""
    import json

    from .prompts import source_text
    if any(s not in results or not results[s] for s in STAGES):
        return None
    models = {results[s].get("model") for s in STAGES}
    if len(models) != 1 or any(results[s].get("fallback") for s in STAGES):
        return None
    model = models.pop()
    per = {s: {"input": int(results[s].get("input_tokens") or 0),
               "output": int(results[s].get("response_tokens", results[s].get("output_tokens", 0)) or 0),
               "thinking": int(results[s].get("thinking_tokens") or 0)} for s in STAGES}
    return {
        "sku": p["sku"], "title": p.get("title") or "", "model": model, "job_id": job_id, "source": source,
        "input_tokens": sum(v["input"] for v in per.values()),
        "output_tokens": sum(v["output"] for v in per.values()),
        "thinking_tokens": sum(v["thinking"] for v in per.values()),
        "calls": len(STAGES), "stage_tokens_json": json.dumps(per), "source_chars": len(source_text(p)),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def record(p: dict, results: dict[str, dict], job_id: str, source: str, sync: bool = False) -> dict | None:
    row = make_row(p, results, job_id, source)
    if row is None:
        return None
    with _lock:
        rows.append(row)
    if not settings.offline:
        from .repository import store
        try:
            _ensure_table()
        except Exception:  # pragma: no cover
            log.exception("token_usage table check failed")
        store._append(TABLE, [row], sync=sync)
    return row


def aggregate(model: str, data: list[dict] | None = None) -> dict | None:
    """Average tokens per SKU for a model (None if never measured)."""
    with _lock:
        rs = [r for r in (data if data is not None else rows) if r.get("model") == model]
    if not rs:
        return None
    n = len(rs)
    skus = list(dict.fromkeys(r["sku"] for r in rs))
    last = rs[-1]
    return {
        "runs": n, "skus": len(skus),
        "input_tokens": sum(int(r["input_tokens"] or 0) for r in rs) / n,
        "output_tokens": sum(int(r["output_tokens"] or 0) for r in rs) / n,
        "thinking_tokens": sum(int(r["thinking_tokens"] or 0) for r in rs) / n,
        "source_chars": sum(int(r.get("source_chars") or 0) for r in rs) / n,
        "only_sample": all(r.get("source") == "sample" for r in rs),
        "sample_sku": last["sku"], "sample_title": last.get("title") or "",
        "sources": sorted({r.get("source") or "" for r in rs}),
        "last_at": last.get("created_at"),
    }
