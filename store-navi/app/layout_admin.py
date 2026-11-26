"""Cymbal Store Navi — Layout Administration & Shelf Slot Engine (CONTRACT v11/v12).

Provides:
- slot_universe(layout): generate all valid slot dicts respecting tier_slots & disabled_slots
- auto_compute_zones(layout): calculate bounding rects for zones marked auto_rect
- diff(old_layout, new_layout): compute added/removed/renamed/modified fixtures and slot mapping
- impact(store_id, diff_result, shelf_locations): compute unassigned vs reassigned products
- validate(layout): floor plan validation (bounds, overlaps, naming)
- publish(store_id, draft, base_version, by, confirm_unassigned): publish new layout version
- rollback(store_id, to_version, by, restore_products): revert to historical layout version
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import deps, layout, repository

log = logging.getLogger("store-navi.layout_admin")

SLOT_RE = re.compile(r"^([A-Z]{1,2}\d{2}|RX)-(\d)(\d{2})$")
FIXTURE_ID_RE = re.compile(r"^([A-Z]{1,2}\d{2}|RX)$")


def parse_slot(sid: str) -> tuple[str, int, int] | None:
    m = SLOT_RE.match(str(sid or ""))
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3))


def slot_id(fixture_id: str, tier: int, index: int) -> str:
    return f"{fixture_id}-{int(tier)}{int(index):02d}"


def slot_points_for_tier(f: dict, n: int) -> list[tuple[float, float]]:
    """Slot centres (metres) left->right as seen by a customer facing the fixture."""
    x, y, w, h, face = float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"]), f.get("face", "S")
    pts = []
    n = max(1, n)
    for k in range(n):
        t = (k + 0.5) / n
        if face == "S":      # customer south, looking north: left = west
            pts.append((x + w * t, y + h / 2))
        elif face == "N":    # customer north, looking south: left = east
            pts.append((x + w * (1 - t), y + h / 2))
        elif face == "W":    # customer west, looking east: left = north
            pts.append((x + w / 2, y + h * t))
        else:                # E: customer east, looking west: left = south
            pts.append((x + w / 2, y + h * (1 - t)))
    return [(round(a, 2), round(b, 2)) for a, b in pts]


def slot_universe(lay: dict) -> dict[str, dict]:
    """Generate all valid slot records for layout: slot_id -> slot_dict."""
    store_id = lay.get("store_id", "S01")
    universe: dict[str, dict] = {}
    fixtures = lay.get("fixtures", [])

    for f in fixtures:
        fid = f.get("id", "")
        ftype = f.get("type", "gondola")
        tiers = int(f.get("tiers") or (1 if ftype in layout.FLAT_TYPES else 5))
        default_spt = int(f.get("slots_per_tier") or 1)
        tier_slots = f.get("tier_slots") or {}
        disabled_slots = set(f.get("disabled_slots") or [])
        width_cm = int(f.get("slot_width_cm") or (60 if ftype in layout.FLAT_TYPES else 30))

        for t in range(1, tiers + 1):
            spt = int(tier_slots.get(str(t)) or tier_slots.get(t) or default_spt)
            pts = slot_points_for_tier(f, spt)
            for idx in range(1, spt + 1):
                sid = slot_id(fid, t, idx)
                if sid in disabled_slots:
                    continue
                px, py = pts[idx - 1] if idx - 1 < len(pts) else (float(f["x"]), float(f["y"]))
                universe[sid] = {
                    "store_id": store_id,
                    "slot_id": sid,
                    "fixture_id": fid,
                    "tier": t,
                    "index": idx,
                    "width_cm": width_cm,
                    "fixture_type": ftype,
                    "x": px,
                    "y": py,
                }

    # Add counter RX slots if present
    c = lay.get("counter")
    if c:
        cid = c.get("id", "RX")
        rx_count = int(c.get("slots_per_tier") or 18)
        for n in range(1, rx_count + 1):
            sid = f"{cid}-1{n:02d}"
            px = round(float(c.get("x", 0)) + float(c.get("w", 1.2)) / 2, 2)
            py = round(float(c.get("y", 0)) + min(float(c.get("h", 3.6)) - 0.1, 0.15 + 0.3 * (n - 1)), 2)
            universe[sid] = {
                "store_id": store_id,
                "slot_id": sid,
                "fixture_id": cid,
                "tier": 1,
                "index": n,
                "width_cm": 30,
                "fixture_type": "counter",
                "x": px,
                "y": py,
            }

    return universe


def auto_compute_zones(lay: dict) -> list[dict]:
    """Compute and update bounding rects for zones marked auto_rect: true."""
    fixtures = lay.get("fixtures", [])
    zones = lay.get("zones", [])
    updated_zones = []

    for z in zones:
        z_copy = dict(z)
        if z_copy.get("auto_rect", True):
            zid = z_copy.get("id")
            matching = [f for f in fixtures if f.get("zone") == zid]
            if matching:
                min_x = min(float(f["x"]) for f in matching)
                min_y = min(float(f["y"]) for f in matching)
                max_x = max(float(f["x"]) + float(f["w"]) for f in matching)
                max_y = max(float(f["y"]) + float(f["h"]) for f in matching)
                # Apply 0.2m padding, clamped to store boundaries
                w_store = float(lay.get("width", 70.0))
                h_store = float(lay.get("height", 42.0))
                pad = 0.2
                x1 = max(0.0, round(min_x - pad, 2))
                y1 = max(0.0, round(min_y - pad, 2))
                x2 = min(w_store, round(max_x + pad, 2))
                y2 = min(h_store, round(max_y + pad, 2))
                z_copy["rect"] = [x1, y1, round(x2 - x1, 2), round(y2 - y1, 2)]
        updated_zones.append(z_copy)
    return updated_zones


def diff(old_lay: dict, new_lay: dict) -> dict[str, Any]:
    """Compute structural diff and slot mapping between two layout versions."""
    old_fx = old_lay.get("fixtures", [])
    new_fx = new_lay.get("fixtures", [])

    old_by_uid = {f.get("uid") or f["id"]: f for f in old_fx}
    new_by_uid = {f.get("uid") or f["id"]: f for f in new_fx}

    added_uids = set(new_by_uid) - set(old_by_uid)
    removed_uids = set(old_by_uid) - set(new_by_uid)
    common_uids = set(old_by_uid) & set(new_by_uid)

    added_fixtures = [new_by_uid[u] for u in added_uids]
    removed_fixtures = [old_by_uid[u] for u in removed_uids]
    renamed_fixtures = []
    modified_fixtures = []

    for u in common_uids:
        of = old_by_uid[u]
        nf = new_by_uid[u]
        if of.get("id") != nf.get("id"):
            renamed_fixtures.append({"old": of, "new": nf})
        elif of != nf:
            modified_fixtures.append({"old": of, "new": nf})

    old_univ = slot_universe(old_lay)
    new_univ = slot_universe(new_lay)

    old_by_id = {f.get("id"): f for f in old_fx}
    slot_map: dict[str, str | None] = {}
    for sid in old_univ:
        parsed = parse_slot(sid)
        if not parsed:
            slot_map[sid] = sid if sid in new_univ else None
            continue
        fid, tier, idx = parsed
        # Find matching fixture in old_fx
        fx_match = old_by_id.get(fid)
        if not fx_match:
            slot_map[sid] = sid if sid in new_univ else None
            continue
        uid = fx_match.get("uid") or fx_match["id"]
        if uid in removed_uids:
            slot_map[sid] = None
        elif uid in new_by_uid:
            new_f = new_by_uid[uid]
            cand = slot_id(new_f["id"], tier, idx)
            slot_map[sid] = cand if cand in new_univ else None
        else:
            slot_map[sid] = None

    return {
        "added_fixtures": added_fixtures,
        "removed_fixtures": removed_fixtures,
        "renamed_fixtures": renamed_fixtures,
        "modified_fixtures": modified_fixtures,
        "old_slot_count": len(old_univ),
        "new_slot_count": len(new_univ),
        "slot_map": slot_map,
    }


def _shelf_rows(store_id: str) -> list[dict]:
    """Latest shelf row per product from the in-memory inventory cache (falls back to repository)."""
    try:
        from . import inventory
        return inventory.latest_shelf_rows(store_id)
    except Exception:  # noqa: BLE001
        log.exception("inventory cache unavailable, loading shelf rows from repository")
        return [r for r in repository.load_shelf_rows() if r.get("store_id") == store_id]


def _note_prev(r: dict) -> tuple[list[str], str | None]:
    note = r.get("note")
    if isinstance(note, str) and note.startswith("{"):
        try:
            note = json.loads(note)
        except Exception:  # noqa: BLE001
            note = None
    if isinstance(note, dict):
        return list(note.get("previous_slot_ids") or []), note.get("previous_fixture_id")
    return [], None


def _is_unassigned(r: dict) -> bool:
    from . import inventory
    return inventory.is_unassigned(r)


def _raw_slots(r: dict) -> list[str]:
    raw = r.get("slot_ids") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.strip("[]").replace('"', "").split(",") if x.strip()]
    raw = [str(x) for x in raw]
    if not raw:
        fid = r.get("fixture_id") or r.get("gondola_id")
        if fid and r.get("tier") not in (None, "") and r.get("position") not in (None, ""):
            raw = [slot_id(fid, int(r["tier"]), int(r["position"]))]
    return raw


def _product_brief(catalog, pid: str) -> tuple[str, str | None]:
    prod = deps.to_product(catalog.get(pid)) if catalog and catalog.get(pid) else None
    imgs = (prod or {}).get("images") or []
    return (prod or {}).get("title") or pid, (imgs[0] if imgs else (prod or {}).get("image"))


def impact(store_id: str, diff_res: dict[str, Any], shelf_rows: list[dict] | None = None) -> dict[str, Any]:
    """Product impact of a layout change: which products move to new slot ids (rename) and which become
    unassigned (their slots no longer exist)."""
    if shelf_rows is None:
        shelf_rows = _shelf_rows(store_id)
    try:
        catalog = deps.get_catalog()
    except Exception:  # noqa: BLE001
        catalog = None

    latest: dict[str, dict] = {}
    for r in shelf_rows:
        if r.get("store_id") != store_id:
            continue
        pid = str(r.get("product_id"))
        old = latest.get(pid)
        if old is None or str(r.get("registered_at", "")) >= str(old.get("registered_at", "")):
            latest[pid] = r

    slot_map = diff_res["slot_map"]
    unassigned_products: list[dict] = []
    reassigned_products: list[dict] = []
    unaffected_count = 0

    for pid, r in latest.items():
        if _is_unassigned(r):
            prev, prev_fid = _note_prev(r)
            title, thumb = _product_brief(catalog, pid)
            unassigned_products.append({"product_id": pid, "title": title, "thumbnail": thumb,
                                        "previous_slot_ids": prev, "previous_fixture_id": prev_fid,
                                        "reason": "already_unassigned"})
            continue
        raw_slots = _raw_slots(r)
        if not raw_slots:
            continue
        surviving = [slot_map[s] for s in raw_slots if s in slot_map and slot_map[s] is not None]
        lost = [s for s in raw_slots if s in slot_map and slot_map[s] is None]
        untouched = [s for s in raw_slots if s not in slot_map]   # slots unknown to the layout (e.g. RX) stay
        new_slots = surviving + untouched
        if not new_slots and lost:
            title, thumb = _product_brief(catalog, pid)
            unassigned_products.append({"product_id": pid, "title": title, "thumbnail": thumb,
                                        "previous_slot_ids": raw_slots,
                                        "previous_fixture_id": (parse_slot(raw_slots[0]) or [None])[0]
                                        or r.get("fixture_id") or r.get("gondola_id"),
                                        "reason": "slots_deleted_or_fixture_removed"})
        elif new_slots != raw_slots:
            title, _thumb = _product_brief(catalog, pid)
            reassigned_products.append({"product_id": pid, "title": title, "old_slots": raw_slots,
                                        "new_slots": new_slots, "reason": "fixture_renamed_or_slots_reduced"})
        else:
            unaffected_count += 1

    new_unassigned = [u for u in unassigned_products if u["reason"] != "already_unassigned"]
    return {
        "unassigned_products": unassigned_products,
        "new_unassigned_products": new_unassigned,
        "reassigned_products": reassigned_products,
        "unassigned_count": len(unassigned_products),
        "new_unassigned_count": len(new_unassigned),
        "reassigned_count": len(reassigned_products),
        "unaffected_count": unaffected_count,
    }


def assign_uids(lay: dict) -> dict:
    """Give fixtures drawn in the editor (no uid / temporary `NEW-…` uid) a permanent uid `<store>-F####`."""
    store_id = lay.get("store_id") or "S01"
    fixtures = lay.get("fixtures") or []
    nums = []
    for f in fixtures:
        m = re.match(rf"^{re.escape(store_id)}-F(\d+)$", str(f.get("uid") or ""))
        if m:
            nums.append(int(m.group(1)))
    nxt = max(nums or [0]) + 1
    for f in fixtures:
        u = str(f.get("uid") or "")
        if not u or u.startswith("NEW-"):
            f["uid"] = f"{store_id}-F{nxt:04d}"
            nxt += 1
    return lay


def validate(lay: dict) -> list[str]:
    """Validate layout floor plan structure, shelf numbers and geometry."""
    errs: list[str] = []
    w_store = float(lay.get("width", 70.0))
    h_store = float(lay.get("height", 42.0))
    fixtures = lay.get("fixtures", [])

    unnumbered = [f for f in fixtures if not str(f.get("id") or "").strip()]
    if unnumbered:
        errs.append(f"{len(unnumbered)} shelf/shelves have no shelf number (gray dashed shelves on the floor plan)")

    seen: dict[str, int] = {}
    for f in fixtures:
        fid = str(f.get("id") or "").strip()
        if fid:
            seen[fid] = seen.get(fid, 0) + 1
    dups = sorted(k for k, v in seen.items() if v > 1)
    if dups:
        errs.append(f"Duplicate shelf numbers: {', '.join(dups)}")

    for f in fixtures:
        fid = str(f.get("id") or "").strip()
        label = fid or "(not set)"
        if fid and not FIXTURE_ID_RE.match(fid) or (fid == "RX" and f.get("type") != "counter"):
            errs.append(f"Shelf number '{fid}' must be 1-2 uppercase letters + 2 digits (e.g. A01, BA12)")
        fx, fy = float(f.get("x", 0)), float(f.get("y", 0))
        fw, fh = float(f.get("w", 0)), float(f.get("h", 0))
        if fw <= 0 or fh <= 0:
            errs.append(f"Shelf {label} has an invalid size")
        if fx < -0.01 or fy < -0.01 or fx + fw > w_store + 0.01 or fy + fh > h_store + 0.01:
            errs.append(f"Shelf {label} is outside the store bounds ({w_store:g} m × {h_store:g} m)")
        tiers = int(f.get("tiers") or 0)
        if tiers < 1 or tiers > 6:
            errs.append(f"Shelf {label}: tiers ({tiers}) must be between 1 and 6")
        if int(f.get("slots_per_tier") or 0) < 1:
            errs.append(f"Shelf {label} has an invalid number of slots")
    return errs


def _unassigned_row(store_id: str, pid: str, prev_slots: list[str], prev_fid: str | None, by: str, now: str) -> dict:
    return {
        "store_id": store_id, "product_id": pid, "fixture_id": None, "gondola_id": None, "slot_ids": [],
        "tier": None, "facings": 0, "side": None, "position": None, "photo_uri": None,
        "source": "layout_change", "registered_by": by, "registered_at": now,
        "note": json.dumps({"unassigned": True, "previous_slot_ids": prev_slots,
                            "previous_fixture_id": prev_fid}, ensure_ascii=False),
    }


def _slot_row(store_id: str, pid: str, slots: list[str], by: str, now: str, source: str, note: str) -> dict:
    p = parse_slot(slots[0])
    fid = p[0] if p else slots[0].rsplit("-", 1)[0]
    return {
        "store_id": store_id, "product_id": pid, "fixture_id": fid, "gondola_id": fid, "slot_ids": slots,
        "tier": p[1] if p else 1, "facings": len(slots), "side": None, "position": p[2] if p else 1,
        "photo_uri": None, "source": source, "registered_by": by, "registered_at": now, "note": note,
    }


def _apply_impact(store_id: str, impact_res: dict, by: str, now: str) -> list[dict]:
    rows = []
    for u in impact_res["new_unassigned_products"]:
        rows.append(_unassigned_row(store_id, u["product_id"], u["previous_slot_ids"], u.get("previous_fixture_id"),
                                    by, now))
    for r in impact_res["reassigned_products"]:
        if r["new_slots"]:
            rows.append(_slot_row(store_id, r["product_id"], r["new_slots"], by, now, "layout_change",
                                  f"Auto-updated after shelf number change ({','.join(r['old_slots'])} → {','.join(r['new_slots'])})"))
    return rows


def _commit_rows(rows: list[dict]) -> None:
    if not rows:
        return
    repository.append_shelf_locations(rows)
    from . import inventory
    for r in rows:
        try:
            inventory.record_shelf(r)
        except Exception:  # noqa: BLE001
            log.exception("record_shelf failed")


def _next_version(store_id: str, base_version: int = 0) -> int:
    current_versions = repository.latest_layout_versions()
    return max(current_versions.get(store_id, 2), int(base_version or 0)) + 1


def publish(store_id: str, draft: dict, base_version: int, by: str, confirm_unassigned: bool = False) -> dict[str, Any]:
    """Publish a draft as the next layout version; renamed shelves carry their products, removed slots make
    their products unassigned."""
    draft["store_id"] = store_id
    assign_uids(draft)
    errs = validate(draft)
    if errs:
        raise ValueError(f"Layout validation error: {'; '.join(errs)}")

    current_lay = layout.get_layout(store_id) or {}
    diff_res = diff(current_lay, draft)
    impact_res = impact(store_id, diff_res)
    if impact_res["new_unassigned_count"] > 0 and not confirm_unassigned:
        raise ValueError(f"{impact_res['new_unassigned_count']} product(s) will become unassigned (no shelf number). Confirmation is required to publish.")

    new_version = _next_version(store_id, base_version)
    draft["zones"] = auto_compute_zones(draft)
    draft["version"] = new_version
    now = repository.now_iso()

    repository.publish_layout_version(store_id, new_version, draft)
    repository.regenerate_shelf_slots(store_id, draft)
    layout.reload()
    _commit_rows(_apply_impact(store_id, impact_res, by, now))

    summary = (f"Published v{new_version}: added {len(diff_res['added_fixtures'])} / removed {len(diff_res['removed_fixtures'])} / "
               f"renumbered {len(diff_res['renamed_fixtures'])} / modified {len(diff_res['modified_fixtures'])} / "
               f"unassigned products {impact_res['new_unassigned_count']}")
    repository.append_layout_change({"store_id": store_id, "version": new_version, "base_version": base_version,
                                     "action": "publish", "published_by": by, "published_at": now,
                                     "summary": summary})
    repository.delete_draft(store_id)
    return {"store_id": store_id, "version": new_version, "base_version": base_version,
            "unassigned_count": impact_res["new_unassigned_count"],
            "reassigned_count": impact_res["reassigned_count"], "summary": summary}


def rollback(store_id: str, to_version: int, by: str, restore_products: bool = True) -> dict[str, Any]:
    """Re-publish a previous layout as a new version. Products follow their shelf back (rename reverted) and
    products made unassigned whose previous slots exist again are restored."""
    target_lay = repository.get_layout_version(store_id, to_version)
    if not target_lay:
        raise ValueError(f"Layout version {to_version} not found")
    target_lay = json.loads(json.dumps(target_lay))
    target_lay["store_id"] = store_id

    current_lay = layout.get_layout(store_id) or {}
    rows = _shelf_rows(store_id)
    diff_res = diff(current_lay, target_lay)
    impact_res = impact(store_id, diff_res, rows)
    now = repository.now_iso()
    new_rows = _apply_impact(store_id, impact_res, by, now)

    restored = 0
    if restore_products:
        univ = slot_universe(target_lay)
        for r in rows:
            if not _is_unassigned(r):
                continue
            prev, _fid = _note_prev(r)
            keep = [s for s in prev if s in univ]
            if keep:
                new_rows.append(_slot_row(store_id, str(r.get("product_id")), keep, by, now, "rollback",
                                          f"Shelf number restored by rollback (v{to_version})"))
                restored += 1

    new_version = _next_version(store_id)
    target_lay["version"] = new_version
    repository.publish_layout_version(store_id, new_version, target_lay)
    repository.regenerate_shelf_slots(store_id, target_lay)
    layout.reload()
    _commit_rows(new_rows)

    summary = (f"Rollback: restored v{to_version} (shelf numbers restored {restored} / renumbered {impact_res['reassigned_count']} / "
               f"unassigned products {impact_res['new_unassigned_count']})")
    repository.append_layout_change({"store_id": store_id, "version": new_version, "base_version": to_version,
                                     "action": "rollback", "published_by": by, "published_at": now,
                                     "summary": summary})
    repository.delete_draft(store_id)
    return {"store_id": store_id, "version": new_version, "restored_to_version": to_version,
            "restored_products_count": restored, "summary": summary}
