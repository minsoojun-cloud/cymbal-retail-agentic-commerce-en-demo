"""Cymbal Store Navi — Shelf Number Administration API (CONTRACT v11).

Endpoints (mounted under /api/admin):
  GET    /api/admin/stores/{sid}/editor        Layout, draft, version, zones, unassigned stats
  PUT    /api/admin/stores/{sid}/draft         Save current draft
  DELETE /api/admin/stores/{sid}/draft         Discard draft
  POST   /api/admin/stores/{sid}/draft/preview Diff & impact analysis
  POST   /api/admin/stores/{sid}/publish       Publish draft as new version
  GET    /api/admin/stores/{sid}/versions      Version history
  POST   /api/admin/stores/{sid}/rollback      Roll back to historical version

Role verification:
  Requires manager role (cookie `sn_role=manager`, query `role=manager`, or header `X-Staff-Role: manager`).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import inventory, layout, layout_admin, repository, stores

log = logging.getLogger("store-navi.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_manager(request: Request) -> str:
    """Enforce manager role check."""
    r = (
        request.cookies.get("sn_role")
        or request.query_params.get("role")
        or request.headers.get("x-staff-role")
        or ""
    ).strip().lower()
    if r != "manager":
        raise HTTPException(status_code=403, detail="Manager role required. Please switch to Manager mode.")
    return r


class DraftSaveReq(BaseModel):
    draft: dict[str, Any]
    editor_name: str | None = "Manager"


class PublishReq(BaseModel):
    base_version: int = Field(default=2, ge=1)
    by: str = Field(default="Manager")
    confirm_unassigned: bool = Field(default=False)


class RollbackReq(BaseModel):
    to_version: int = Field(ge=1)
    by: str = Field(default="Manager")
    restore_products: bool = Field(default=True)


@router.get("/stores/{sid}/editor")
def get_editor_state(sid: str, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    active_layout = layout.get_layout(sid)
    draft = repository.load_draft(sid)
    versions = repository.latest_layout_versions()
    current_version = versions.get(sid, 2)
    unassigned_items = inventory.unassigned_products(sid)
    version_history = repository.list_layout_versions(sid)

    return {
        "store": st,
        "current_version": current_version,
        "layout": active_layout,
        "draft": draft,
        "has_draft": draft is not None,
        "unassigned_count": len(unassigned_items),
        "unassigned_sample": unassigned_items[:10],
        "unassigned_items": unassigned_items[:200],
        "fixture_product_counts": inventory.fixture_product_counts(sid),
        "versions": version_history,
    }


@router.put("/stores/{sid}/draft")
def save_editor_draft(sid: str, req: DraftSaveReq, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    draft = req.draft
    draft["store_id"] = sid
    # Auto compute zones if marked auto_rect
    draft["zones"] = layout_admin.auto_compute_zones(draft)
    repository.save_draft(sid, draft)
    return {"ok": True, "store_id": sid, "message": "Draft saved"}


@router.delete("/stores/{sid}/draft")
def delete_editor_draft(sid: str, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    repository.delete_draft(sid)
    return {"ok": True, "store_id": sid, "message": "Draft discarded"}


@router.post("/stores/{sid}/draft/preview")
def preview_editor_draft(sid: str, req: DraftSaveReq, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    draft = req.draft
    draft["store_id"] = sid
    draft["zones"] = layout_admin.auto_compute_zones(draft)

    errors = layout_admin.validate(draft)
    current_layout = layout.get_layout(sid) or {}
    diff_res = layout_admin.diff(current_layout, draft)
    impact_res = layout_admin.impact(sid, diff_res)

    def brief(f: dict) -> dict:
        return {k: f.get(k) for k in ("uid", "id", "label", "zone", "type", "tiers", "slots_per_tier",
                                      "slot_width_cm", "length_cm")}

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "diff": {
            "added_fixtures": [brief(f) for f in diff_res["added_fixtures"]],
            "removed_fixtures": [brief(f) for f in diff_res["removed_fixtures"]],
            "renamed_fixtures": [{"old": brief(x["old"]), "new": brief(x["new"])} for x in diff_res["renamed_fixtures"]],
            "modified_fixtures": [{"old": brief(x["old"]), "new": brief(x["new"])} for x in diff_res["modified_fixtures"]],
            "old_slot_count": diff_res["old_slot_count"],
            "new_slot_count": diff_res["new_slot_count"],
        },
        "impact": impact_res,
    }


@router.post("/stores/{sid}/publish")
def publish_layout(sid: str, req: PublishReq, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    draft = repository.load_draft(sid)
    if not draft:
        raise HTTPException(status_code=400, detail="There is no draft to publish")

    try:
        res = layout_admin.publish(
            store_id=sid,
            draft=draft,
            base_version=req.base_version,
            by=req.by,
            confirm_unassigned=req.confirm_unassigned,
        )
        return {"ok": True, **res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("publish failed")
        raise HTTPException(status_code=500, detail=f"An error occurred while publishing the layout: {e}")


@router.get("/stores/{sid}/versions")
def get_version_history(sid: str, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    history = repository.list_layout_versions(sid)
    return {"store_id": sid, "versions": history}


@router.post("/stores/{sid}/rollback")
def rollback_layout(sid: str, req: RollbackReq, request: Request):
    _check_manager(request)
    st = stores.get_store(sid)
    if not st:
        raise HTTPException(status_code=404, detail=f"Store not found: {sid}")

    try:
        res = layout_admin.rollback(
            store_id=sid,
            to_version=req.to_version,
            by=req.by,
            restore_products=req.restore_products,
        )
        return {"ok": True, **res}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("rollback failed")
        raise HTTPException(status_code=500, detail=f"An error occurred during rollback: {e}")
