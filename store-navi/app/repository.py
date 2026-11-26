"""Data access: BigQuery `cymbal.*` store tables / GCS photos (DATA_MODE=bq) or JSON files (DATA_MODE=local).

Static data (stores, layouts, coupons, offers, personas, scenarios) is read from BigQuery when available and falls
back to the bundled DATA_DIR files (deploy.sh stages store-navi/data). Dynamic data (store_inventory,
shelf_locations, store_inventory_events) is loaded at startup and refreshed by delta queries (inventory.py).
Appends (staff registrations / stock adjustments) go to BigQuery streaming inserts or SN_STATE_DIR jsonl files.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import mimetypes
import re
import threading
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("store-navi.repo")

_bq = None
_gcs = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------------------------- files
def _path(name: str) -> Path:
    return config.data_dir() / name


def read_json(name: str, default: Any) -> Any:
    p = _path(name)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("read %s failed: %s", p, e)
        return default


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------------------------- BigQuery
def use_bq() -> bool:
    return config.data_mode() == "bq"


def bq():
    global _bq
    if _bq is None:
        from google.cloud import bigquery  # lazy

        _bq = bigquery.Client(project=config.PROJECT_ID)
    return _bq


def table(name: str) -> str:
    return f"`{config.PROJECT_ID}.{config.BQ_DATASET}.{name}`"


def _decode(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, list):
        return [_decode(x) for x in v]
    if isinstance(v, dict):
        return {k: _decode(x) for k, x in v.items()}
    return v


def bq_rows(sql: str, params: list | None = None, timeout: float = 30) -> list[dict]:
    from google.cloud import bigquery

    job = bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params or []))
    return [{k: _decode(v) for k, v in dict(r).items()} for r in job.result(timeout=timeout)]


def _bq_or_file(table_name: str, file_name: str, default: Any, sql: str | None = None) -> Any:
    if use_bq():
        try:
            rows = bq_rows(sql or f"SELECT * FROM {table(table_name)}")
            if rows:
                return rows
        except Exception as e:  # noqa: BLE001
            log.warning("BQ %s unavailable (%s) → %s", table_name, str(e)[:200], file_name)
    return read_json(file_name, default)


# ---------------------------------------------------------------------------------------------- static data
def load_stores() -> list[dict]:
    rows = _bq_or_file("stores", "stores.json", [])
    return sorted(rows, key=lambda s: s.get("store_id", ""))


def load_layouts(store_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if use_bq():
        try:
            for r in bq_rows(f"SELECT store_id, version, layout FROM {table('store_layouts')} "
                             f"QUALIFY ROW_NUMBER() OVER (PARTITION BY store_id ORDER BY version DESC) = 1"):
                lay = r.get("layout")
                if isinstance(lay, dict):
                    out[r["store_id"]] = lay
        except Exception as e:  # noqa: BLE001
            log.warning("BQ store_layouts unavailable: %s", str(e)[:200])
    for sid in store_ids:
        if sid not in out:
            p_state = config.state_dir() / f"layouts/{sid}.json"   # published locally (CONTRACT v11/v12)
            if p_state.exists():
                try:
                    out[sid] = json.loads(p_state.read_text(encoding="utf-8"))
                    continue
                except Exception:  # noqa: BLE001
                    pass
            lay = read_json(f"layouts/{sid}.json", None)
            if lay:
                out[sid] = lay
    return out


def load_coupons() -> list[dict]:
    return _bq_or_file("store_coupons", "coupons.json", [])


def load_offers() -> list[dict]:
    return _bq_or_file("store_offers", "offers.json", [])


def load_personas() -> list[dict]:
    """Store Navi personas (CR-0001..3). Always from the bundled store_personas.json (BQ cymbal.customers holds the
    richer CR-0001 storefront row which the store app does not need)."""
    rows = read_json("store_personas.json", [])
    if isinstance(rows, dict):
        rows = rows.get("personas") or list(rows.values())
    return rows


def load_purchase_history() -> list[dict]:
    rows = read_jsonl(_path("store_purchase_history.jsonl"))
    return rows


def load_scenarios() -> dict:
    return read_json("scenarios.json", {})


def load_shelf_slots() -> list[dict]:
    """CONTRACT v9 cymbal.shelf_slots / shelf_slots.jsonl: {store_id, slot_id, fixture_id, tier, index, width_cm, fixture_type, x, y}."""
    if use_bq():
        try:
            rows = bq_rows(f"SELECT store_id, slot_id, fixture_id, tier, `index`, width_cm, fixture_type, x, y "
                           f"FROM {table('shelf_slots')}", timeout=60)
            if rows:
                return rows
        except Exception as e:  # noqa: BLE001
            log.warning("BQ shelf_slots unavailable (%s) → file", str(e)[:200])
    p_state = config.state_dir() / "shelf_slots.jsonl"
    return read_jsonl(p_state if p_state.exists() else _path("shelf_slots.jsonl"))


# ---------------------------------------------------------------------------------------------- dynamic data
def load_inventory_rows() -> list[dict]:
    if use_bq():
        try:
            return bq_rows(f"SELECT store_id, product_id, quantity, availability, updated_at "
                           f"FROM {table('store_inventory')}", timeout=60)
        except Exception as e:  # noqa: BLE001
            log.warning("BQ store_inventory unavailable (%s) → file", str(e)[:200])
    return read_jsonl(_path("store_inventory.jsonl"))


def load_shelf_rows(since: str | None = None) -> list[dict]:
    if use_bq():
        try:
            from google.cloud import bigquery

            if since:
                return bq_rows(f"SELECT * FROM {table('shelf_locations')} WHERE registered_at > @since",
                               [bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)])
            return bq_rows(f"SELECT * FROM {table('shelf_locations')}", timeout=60)
        except Exception as e:  # noqa: BLE001
            log.warning("BQ shelf_locations unavailable (%s) → file", str(e)[:200])
            if since:
                return []
    rows = read_jsonl(_path("shelf_locations.jsonl")) if not since else []
    rows += [r for r in read_jsonl(config.state_dir() / "shelf_locations.jsonl")
             if not since or str(r.get("registered_at", "")) > since]
    return rows


def load_inventory_events(since: str | None = None) -> list[dict]:
    if use_bq():
        try:
            from google.cloud import bigquery

            if since:
                return bq_rows(f"SELECT * FROM {table('store_inventory_events')} WHERE `at` > @since ORDER BY `at`",
                               [bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)])
            return bq_rows(f"SELECT * FROM {table('store_inventory_events')} ORDER BY `at`")
        except Exception as e:  # noqa: BLE001
            log.warning("BQ store_inventory_events unavailable: %s", str(e)[:200])
            return []
    rows = read_jsonl(config.state_dir() / "inventory_events.jsonl")
    return [r for r in rows if not since or str(r.get("at", "")) > since]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _bq_insert(table_name: str, row: dict) -> None:
    def enc(v):
        if isinstance(v, dict) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
            return json.dumps(v, ensure_ascii=False)
        return v  # scalars and lists of scalars (REPEATED columns such as slot_ids)

    clean = {k: enc(v) for k, v in row.items()}
    errors = bq().insert_rows_json(f"{config.PROJECT_ID}.{config.BQ_DATASET}.{table_name}", [clean])
    if errors:
        raise RuntimeError(f"BQ insert {table_name} failed: {errors}")


def append_shelf_location(row: dict) -> dict:
    row = dict(row)
    row.setdefault("registered_at", now_iso())
    row.setdefault("source", "staff")
    if use_bq():
        _bq_insert("shelf_locations", row)
    else:
        _append_jsonl(config.state_dir() / "shelf_locations.jsonl", row)
    return row


def append_shelf_locations(rows: list[dict]) -> list[dict]:
    """CONTRACT v12: batch append (one BigQuery streaming insert for a whole layout change)."""
    out = []
    for r in rows:
        r = dict(r)
        r.setdefault("registered_at", now_iso())
        r.setdefault("source", "staff")
        out.append(r)
    if not out:
        return out
    if use_bq():
        def enc(v):
            if isinstance(v, dict) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
                return json.dumps(v, ensure_ascii=False)
            return v

        for i in range(0, len(out), 500):
            chunk = [{k: enc(v) for k, v in r.items()} for r in out[i:i + 500]]
            errors = bq().insert_rows_json(f"{config.PROJECT_ID}.{config.BQ_DATASET}.shelf_locations", chunk)
            if errors:
                raise RuntimeError(f"BQ insert shelf_locations failed: {errors[:3]}")
    else:
        for r in out:
            _append_jsonl(config.state_dir() / "shelf_locations.jsonl", r)
    return out


def append_inventory_event(row: dict) -> dict:
    row = dict(row)
    row.setdefault("at", now_iso())
    if use_bq():
        _bq_insert("store_inventory_events", row)
    else:
        _append_jsonl(config.state_dir() / "inventory_events.jsonl", row)
    return row


def staff_registrations(store_id: str = "", limit: int = 30) -> list[dict]:
    rows = [r for r in load_shelf_rows() if r.get("source") == "staff" and (not store_id or r.get("store_id") == store_id)]
    rows.sort(key=lambda r: str(r.get("registered_at", "")), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------------------------- photos
_SAFE = re.compile(r"^[A-Za-z0-9_\-./]+$")


def _safe_rel(path: str) -> str | None:
    path = (path or "").lstrip("/")
    if not path or ".." in path.split("/") or not _SAFE.match(path):
        return None
    return path


def upload_photo(store_id: str, filename: str, data: bytes, content_type: str = "image/jpeg") -> str:
    fname = re.sub(r"[^A-Za-z0-9_.\-]", "_", filename or "photo.jpg")
    sid = re.sub(r"[^A-Za-z0-9_\-]", "_", store_id or "S00")
    rel = f"shelf_photos/{sid}/{fname}"
    if use_bq():
        global _gcs
        if _gcs is None:
            from google.cloud import storage  # lazy

            _gcs = storage.Client(project=config.PROJECT_ID)
        blob = _gcs.bucket(config.PHOTO_BUCKET).blob(rel)
        blob.upload_from_string(data, content_type=content_type or "image/jpeg")
        return f"gs://{config.PHOTO_BUCKET}/{rel}"
    p = config.state_dir() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return f"local://{rel}"


def photo_url(photo_uri: str | None) -> str | None:
    """gs://bucket/shelf_photos/S01/x.jpg | local://shelf_photos/… | demo/x.jpg | /static/… → browser URL."""
    if not photo_uri:
        return None
    u = str(photo_uri)
    if u.startswith(("http://", "https://", "/")):
        return u
    if u.startswith("gs://"):
        rest = u[5:].split("/", 1)
        return "/api/photos/" + (rest[1] if len(rest) > 1 else "")
    if u.startswith("local://"):
        return "/api/photos/" + u[len("local://"):]
    return "/api/photos/" + u.lstrip("/")


_photo_cache: dict[str, tuple[bytes, str]] = {}


def read_photo(path: str) -> tuple[bytes, str] | None:
    rel = _safe_rel(path)
    if not rel:
        return None
    ctype = mimetypes.guess_type(rel)[0] or "image/jpeg"
    if rel.startswith("demo/"):
        p = config.STATIC_DIR / "staff" / "demo_photos" / rel[len("demo/"):]
        return (p.read_bytes(), ctype) if p.is_file() else None
    local = config.state_dir() / rel
    if local.is_file():
        return local.read_bytes(), ctype
    bundled = config.data_dir() / "photos" / rel          # seed photos shipped with the data files
    if bundled.is_file():
        return bundled.read_bytes(), ctype
    if rel in _photo_cache:
        return _photo_cache[rel]
    if use_bq() and rel.startswith("shelf_photos/"):
        try:
            global _gcs
            if _gcs is None:
                from google.cloud import storage

                _gcs = storage.Client(project=config.PROJECT_ID)
            blob = _gcs.bucket(config.PHOTO_BUCKET).blob(rel)
            data = blob.download_as_bytes(timeout=15)
            if len(_photo_cache) > 200:
                _photo_cache.clear()
            _photo_cache[rel] = (data, blob.content_type or ctype)
            return _photo_cache[rel]
        except Exception as e:  # noqa: BLE001
            log.warning("photo %s unavailable: %s", rel, str(e)[:200])
    return None


# ---------------------------------------------------------------------------------------------- layout drafts & versions (CONTRACT v11)
def load_draft(store_id: str) -> dict | None:
    sid = re.sub(r"[^A-Za-z0-9_\-]", "_", store_id or "S01")
    rel = f"layout_drafts/{sid}.json"
    if use_bq():
        try:
            global _gcs
            if _gcs is None:
                from google.cloud import storage

                _gcs = storage.Client(project=config.PROJECT_ID)
            blob = _gcs.bucket(config.PHOTO_BUCKET).blob(rel)
            if blob.exists():
                return json.loads(blob.download_as_text())
        except Exception as e:  # noqa: BLE001
            log.warning("load draft from GCS failed: %s", e)
    p = config.state_dir() / rel
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def save_draft(store_id: str, draft: dict) -> None:
    sid = re.sub(r"[^A-Za-z0-9_\-]", "_", store_id or "S01")
    rel = f"layout_drafts/{sid}.json"
    data_str = json.dumps(draft, ensure_ascii=False, indent=1)
    if use_bq():
        try:
            global _gcs
            if _gcs is None:
                from google.cloud import storage

                _gcs = storage.Client(project=config.PROJECT_ID)
            blob = _gcs.bucket(config.PHOTO_BUCKET).blob(rel)
            blob.upload_from_string(data_str, content_type="application/json")
        except Exception as e:  # noqa: BLE001
            log.warning("save draft to GCS failed: %s", e)
    p = config.state_dir() / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data_str, encoding="utf-8")


def delete_draft(store_id: str) -> None:
    sid = re.sub(r"[^A-Za-z0-9_\-]", "_", store_id or "S01")
    rel = f"layout_drafts/{sid}.json"
    if use_bq():
        try:
            global _gcs
            if _gcs is None:
                from google.cloud import storage

                _gcs = storage.Client(project=config.PROJECT_ID)
            blob = _gcs.bucket(config.PHOTO_BUCKET).blob(rel)
            if blob.exists():
                blob.delete()
        except Exception as e:  # noqa: BLE001
            log.warning("delete draft from GCS failed: %s", e)
    p = config.state_dir() / rel
    if p.exists():
        p.unlink()


def publish_layout_version(store_id: str, version: int, layout_dict: dict) -> None:
    layout_str = json.dumps(layout_dict, ensure_ascii=False)
    if use_bq():
        from google.cloud import bigquery

        client = bq()
        rows = [{"store_id": store_id, "version": version, "layout": layout_str}]
        job_config = bigquery.LoadJobConfig(
            schema=[
                bigquery.SchemaField("store_id", "STRING"),
                bigquery.SchemaField("version", "INTEGER"),
                bigquery.SchemaField("layout", "STRING"),
            ],
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        job = client.load_table_from_json(rows, f"{config.PROJECT_ID}.{config.BQ_DATASET}.store_layouts", job_config=job_config)
        job.result()
    p_state = config.state_dir() / f"layouts/{store_id}.json"
    p_state.parent.mkdir(parents=True, exist_ok=True)
    p_state.write_text(json.dumps(layout_dict, ensure_ascii=False, indent=1), encoding="utf-8")
    # local version history (used by rollback when BigQuery is not available)
    p_hist = config.state_dir() / f"layout_versions/{store_id}/v{int(version)}.json"
    p_hist.parent.mkdir(parents=True, exist_ok=True)
    p_hist.write_text(json.dumps(layout_dict, ensure_ascii=False), encoding="utf-8")


def regenerate_shelf_slots(store_id: str, layout_dict: dict) -> None:
    from . import layout_admin

    new_slots = list(layout_admin.slot_universe(layout_dict).values())
    if use_bq():
        from google.cloud import bigquery

        client = bq()
        other_slots = bq_rows(f"SELECT store_id, slot_id, fixture_id, tier, `index`, width_cm, fixture_type, x, y "
                              f"FROM {table('shelf_slots')} WHERE store_id != '{store_id}'", timeout=60)
        all_slots = other_slots + new_slots
        job_config = bigquery.LoadJobConfig(
            schema=[
                bigquery.SchemaField("store_id", "STRING"),
                bigquery.SchemaField("slot_id", "STRING"),
                bigquery.SchemaField("fixture_id", "STRING"),
                bigquery.SchemaField("tier", "INTEGER"),
                bigquery.SchemaField("index", "INTEGER"),
                bigquery.SchemaField("width_cm", "INTEGER"),
                bigquery.SchemaField("fixture_type", "STRING"),
                bigquery.SchemaField("x", "FLOAT"),
                bigquery.SchemaField("y", "FLOAT"),
            ],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        job = client.load_table_from_json(all_slots, f"{config.PROJECT_ID}.{config.BQ_DATASET}.shelf_slots", job_config=job_config)
        job.result()
    # local mode: never rewrite the seed data dir — keep the regenerated slots in the state dir
    p_seed = _path("shelf_slots.jsonl")
    p = config.state_dir() / "shelf_slots.jsonl"
    src = p if p.exists() else p_seed
    if src.exists():
        existing = [r for r in read_jsonl(src) if r.get("store_id") != store_id]
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            for r in existing + new_slots:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_layout_change(row: dict) -> dict:
    row = dict(row)
    row.setdefault("published_at", now_iso())
    if use_bq():
        _bq_insert("store_layout_changes", row)
    else:
        _append_jsonl(config.state_dir() / "store_layout_changes.jsonl", row)
    return row


def list_layout_versions(store_id: str) -> list[dict]:
    if use_bq():
        try:
            from google.cloud import bigquery

            changes = bq_rows(
                f"SELECT version, base_version, action, published_by, published_at, summary "
                f"FROM {table('store_layout_changes')} WHERE store_id = @sid ORDER BY version DESC",
                [bigquery.ScalarQueryParameter("sid", "STRING", store_id)]
            )
            all_versions = bq_rows(
                f"SELECT DISTINCT version FROM {table('store_layouts')} WHERE store_id = @sid ORDER BY version DESC",
                [bigquery.ScalarQueryParameter("sid", "STRING", store_id)]
            )
            v_map = {r["version"]: r for r in changes}
            out = []
            for av in all_versions:
                v = int(av["version"])
                if v in v_map:
                    out.append(v_map[v])
                else:
                    out.append({
                        "version": v,
                        "base_version": v - 1 if v > 1 else None,
                        "action": "baseline" if v == 2 else "publish",
                        "published_by": "system",
                        "published_at": None,
                        "summary": "Baseline layout" if v == 2 else f"Layout v{v}"
                    })
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("list_layout_versions failed: %s", e)
    rows = read_jsonl(config.state_dir() / "store_layout_changes.jsonl")
    s_rows = [r for r in rows if r.get("store_id") == store_id]
    s_rows.sort(key=lambda x: int(x.get("version", 0)), reverse=True)
    base = read_json(f"layouts/{store_id}.json", {}) or {}
    bv = int(base.get("version") or 2)
    if not any(int(r.get("version", 0)) == bv for r in s_rows):
        s_rows.append({"version": bv, "base_version": bv - 1, "action": "baseline",
                       "published_by": "system", "published_at": None, "summary": "Baseline layout"})
    return s_rows


def get_layout_version(store_id: str, version: int) -> dict | None:
    if use_bq():
        try:
            from google.cloud import bigquery

            rows = bq_rows(
                f"SELECT layout FROM {table('store_layouts')} WHERE store_id = @sid AND version = @ver LIMIT 1",
                [bigquery.ScalarQueryParameter("sid", "STRING", store_id),
                 bigquery.ScalarQueryParameter("ver", "INT64", version)]
            )
            if rows and rows[0].get("layout"):
                lay = rows[0]["layout"]
                return json.loads(lay) if isinstance(lay, str) else lay
        except Exception as e:  # noqa: BLE001
            log.warning("get_layout_version failed: %s", e)
    p_hist = config.state_dir() / f"layout_versions/{store_id}/v{int(version)}.json"
    if p_hist.exists():
        return json.loads(p_hist.read_text(encoding="utf-8"))
    base = read_json(f"layouts/{store_id}.json", None)
    if base and int(base.get("version") or 2) == int(version):
        return base
    return None


def latest_layout_versions() -> dict[str, int]:
    out: dict[str, int] = {}
    if use_bq():
        try:
            rows = bq_rows(f"SELECT store_id, MAX(version) as max_v FROM {table('store_layouts')} GROUP BY store_id")
            for r in rows:
                out[str(r["store_id"])] = int(r["max_v"])
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("query latest_layout_versions failed: %s", e)
    for sid in ["S01", "S02", "S03"]:
        p_state = config.state_dir() / f"layouts/{sid}.json"
        try:
            lay = json.loads(p_state.read_text(encoding="utf-8")) if p_state.exists() else read_json(f"layouts/{sid}.json", {})
        except Exception:  # noqa: BLE001
            lay = read_json(f"layouts/{sid}.json", {})
        out[sid] = int((lay or {}).get("version", 2))
    return out

