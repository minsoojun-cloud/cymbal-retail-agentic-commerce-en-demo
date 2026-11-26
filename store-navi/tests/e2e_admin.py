"""E2E (Playwright) for the v12 Shelf Manager screen (/admin/): floor plan → shelves → shelf numbers.

Tablet/desktop scenario (full cycle, demo state restored at the end):
  01 main floor plan   02 shelf selected → Shelf number panel  03 rename (number change note)
  04 draw a new shelf  05 number the new shelf            06 zone created & assigned
  07 delete a shelf with products                         08 publish confirmation (rename / add / delete / unassigned)
  09 published → error panel lists unassigned products   10 "Assign shelf" → Shelf Registration app reassign screen
  11 reassigned (done, Back to Shelf Manager)             12 back in admin: count decreased
  13 history  14 rollback to the starting version        (+ API: reassigned product put back on its old slot)
Phone scenario: main / selected shelf (panel below the map) / errors.

Usage: BASE_URL=https://cymbal-store-staff-…run.app python tests/e2e_admin.py [--device all|ipad|desktop|iphone]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

OUT = Path(os.environ.get("E2E_OUT", "/tmp/sn_e2e_admin_v12"))
DEVICES = {
    "ipad_air_land": {"viewport": {"width": 1180, "height": 820}, "device_scale_factor": 2, "has_touch": True, "full": True},
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1, "full": True},
    "iphone14": {"preset": "iPhone 14", "full": False},
}


def log(msg):
    print(msg, flush=True)


class Run:
    def __init__(self, page, base, name):
        self.p, self.base, self.name = page, base, name
        self.n = 0

    def shot(self, label):
        self.n += 1
        path = OUT / f"{self.name}_{self.n:02d}_{label}.png"
        self.p.screenshot(path=str(path))
        log(f"  captured {path.name}")

    def ed(self, expr):
        return self.p.evaluate(f"() => {{ const A = window.ShelfNumberAdmin; return {expr}; }}")

    def api(self, method, path, body=None):
        return self.p.evaluate(
            """async ([m, u, b]) => { const r = await fetch(u, {method: m, headers: {'Content-Type': 'application/json'},
                 body: b ? JSON.stringify(b) : undefined}); return {status: r.status, data: await r.json().catch(() => null)}; }""",
            [method, path, body])

    def screen_xy(self, x, y):
        return self.ed(f"""(() => {{ const r = document.querySelector('#plan-svg').getBoundingClientRect(); const s = A.plan.scale;
            return {{x: r.left + ({x} - A.plan.vb.x) * s, y: r.top + ({y} - A.plan.vb.y) * s}}; }})()""")

    def click_fixture(self, fid, center=True):
        if center:
            self.p.evaluate(f"() => {{ const A = window.ShelfNumberAdmin; A.plan.centerOn(A.ED.layout.fixtures.find(f => f.id === {json.dumps(fid)})); }}")
            time.sleep(0.2)
        f = self.ed(f"A.ED.layout.fixtures.find(f => f.id === {json.dumps(fid)})")
        pt = self.screen_xy(f["x"] + f["w"] / 2, f["y"] + f["h"] / 2)
        self.p.mouse.click(pt["x"], pt["y"])
        time.sleep(0.35)

    def wait_loaded(self):
        self.p.wait_for_function("window.ShelfNumberAdmin && window.ShelfNumberAdmin.ED.layout", timeout=30000)
        time.sleep(0.6)


def open_admin(page, base, store="S01"):
    page.goto(f"{base}/admin/?store={store}&role=manager")
    page.wait_for_function("window.ShelfNumberAdmin && window.ShelfNumberAdmin.ED.layout", timeout=30000)
    time.sleep(0.8)


def full_scenario(page, base, name):
    R = Run(page, base, name)
    open_admin(page, base)
    start_version = R.ed("A.ED.version")
    counts = R.ed("A.ED.counts")
    assert R.ed("A.ED.unassigned.length") == 0, "demo must start without unassigned products"
    fx = R.ed("A.ED.layout.fixtures.map(f => ({id: f.id, uid: f.uid, type: f.type}))")
    used = {f["id"] for f in fx}
    cand = sorted([f for f in fx if f["type"] in ("gondola", "wall") and counts.get(f["id"], 0) >= 3],
                  key=lambda f: counts.get(f["id"], 0))
    ren, dele = cand[0], cand[1]
    new_num = next(f"Q{i:02d}" for i in range(1, 100) if f"Q{i:02d}" not in used)
    log(f"  start v{start_version}; rename {ren['id']}→{new_num} ({counts[ren['id']]} products); "
        f"delete {dele['id']} ({counts[dele['id']]} products)")
    R.shot("main_floor_plan")
    assert page.locator("#plan-svg rect.fp-fx").count() > 300, "floor plan should render all shelves"

    # 02 select A01
    R.click_fixture("A01")
    page.wait_for_selector("#f-num", timeout=5000)
    assert page.input_value("#f-num") == "A01"
    assert page.locator(".elev__cell").first.inner_text().startswith("A01-")
    R.shot("shelf_selected_number_panel")

    # 03 rename
    R.click_fixture(ren["id"])
    page.fill("#f-num", new_num)
    page.wait_for_selector("#f-notes .note--info", timeout=3000)
    assert new_num in page.inner_text("#f-notes")
    assert page.locator(".elev__cell").first.inner_text().startswith(f"{new_num}-")
    R.shot("rename_number_change")

    # 04 draw a new shelf in the free floor area above the register
    page.click("[data-tool=draw]")
    page.click("#zoom-fit")
    time.sleep(0.2)
    a, b = R.screen_xy(30.0, 35.4), R.screen_xy(32.7, 36.0)
    page.mouse.move(a["x"], a["y"])
    page.mouse.down()
    page.mouse.move((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, steps=4)
    page.mouse.move(b["x"], b["y"], steps=6)
    page.mouse.up()
    page.wait_for_selector("#f-num", timeout=5000)
    time.sleep(0.3)
    assert page.input_value("#f-num") == ""
    assert R.ed("A.ED.layout.fixtures.filter(f => !f.id).length") == 1
    R.shot("new_shelf_unnumbered")

    # 05 number it with the "Free numbers" helper (prefix W)
    page.fill("#f-num", "W")
    page.click("#f-suggest")
    time.sleep(0.3)
    new_shelf = page.input_value("#f-num")
    assert new_shelf.startswith("W") and len(new_shelf) == 3, new_shelf
    page.click("#t-dec")  # 4 tiers
    time.sleep(0.2)
    R.shot("new_shelf_numbered")

    # 06 zone: create "Seasonal Feature" with the new shelf selected
    page.click("#ptab-zone")
    page.fill("#z-new", "Seasonal Feature")
    page.click("#z-add")
    time.sleep(0.3)
    assert R.ed(f"A.ED.layout.fixtures.find(f => f.id === '{new_shelf}').zone") != ""
    R.shot("zone_created_assigned")
    page.click("#ptab-shelf")

    # 07 delete a shelf with products
    R.click_fixture(dele["id"])
    page.click("#f-del")
    time.sleep(0.4)
    assert R.ed(f"A.ED.layout.fixtures.some(f => f.id === '{dele['id']}')") is False
    R.shot("shelf_deleted")

    # 08 publish confirmation
    page.click("#btn-publish")
    page.wait_for_selector("#publish-modal:not([hidden])", timeout=60000)
    time.sleep(0.5)
    body = page.inner_text("#pm-body")
    assert new_num in body and new_shelf in body and dele["id"] in body, body[:400]
    assert page.is_disabled("#pm-submit")
    R.shot("publish_confirmation")
    page.check("#pm-confirm")

    # 09 publish
    page.click("#pm-submit")
    page.wait_for_selector("#publish-modal", state="hidden", timeout=120000)
    page.wait_for_function(f"window.ShelfNumberAdmin.ED.version > {start_version}", timeout=60000)
    time.sleep(0.8)
    un_count = int(page.inner_text("#un-count"))
    assert un_count == counts[dele["id"]], (un_count, counts[dele["id"]])
    page.locator("#errs").scroll_into_view_if_needed()
    R.shot("published_unassigned_listed")

    # 10 Assign shelf → staff app reassign
    first = page.locator("#un-list li a.btn").first
    href = first.get_attribute("href")
    pid = href.split("reassign=")[1].split("&")[0]
    prev = next(u for u in R.ed("A.ED.unassigned") if u["product_id"] == pid)["previous_slot_ids"]
    assert prev, "previous slot ids must be shown"
    first.click()
    page.wait_for_selector("#stage-3.active", timeout=30000)
    time.sleep(0.6)
    R.shot("staff_reassign_screen")

    # 11 pick the new shelf + a slot and register
    page.click("#fixture-btn")
    page.wait_for_selector(".fx-item", timeout=10000)
    page.fill("#fx-q", new_shelf)
    time.sleep(0.4)
    page.click(f".fx-item[data-id='{new_shelf}']")
    page.wait_for_selector("#f-elev .slot", timeout=15000)
    page.locator("#f-elev .slot").first.click()
    time.sleep(0.3)
    page.click("#btn-register")
    page.wait_for_selector("#stage-4.active", timeout=30000)
    time.sleep(0.6)
    assert page.is_visible("#btn-back-admin")
    R.shot("staff_reassigned_done")

    # 12 back to admin
    page.click("#btn-back-admin")
    R.wait_loaded()
    assert int(page.inner_text("#un-count")) == un_count - 1
    page.locator("#errs").scroll_into_view_if_needed()
    R.shot("admin_count_decreased")

    # 13 history → 14 rollback to the starting version
    page.click("#btn-history")
    page.wait_for_selector("#history-modal:not([hidden])", timeout=60000)
    time.sleep(0.4)
    R.shot("history")
    page.click(f"[data-rb='{start_version}']")
    page.wait_for_selector("#history-modal", state="hidden", timeout=120000)
    page.wait_for_function(f"window.ShelfNumberAdmin.ED.layout.fixtures.some(f => f.id === '{dele['id']}')", timeout=60000)
    time.sleep(0.8)
    R.shot("rolled_back")
    assert R.ed(f"A.ED.layout.fixtures.some(f => f.id === '{ren['id']}')") is True
    assert R.ed(f"A.ED.layout.fixtures.some(f => f.id === '{new_shelf}')") is False
    # put the reassigned product back where it was (pristine demo data)
    r = R.api("POST", "/api/staff/shelf-locations", {"store_id": "S01", "product_id": pid, "slot_ids": prev,
                                                     "staff_name": "e2e", "note": "e2e restore"})
    assert r["status"] == 200, r
    ed = R.api("GET", "/api/admin/stores/S01/editor")["data"]
    assert ed["unassigned_count"] == 0, ed["unassigned_count"]
    assert ed["fixture_product_counts"].get(ren["id"]) == counts[ren["id"]]
    assert ed["fixture_product_counts"].get(dele["id"]) == counts[dele["id"]]
    log(f"  restored: v{ed['current_version']} (content of v{start_version}), unassigned 0")


def phone_scenario(page, base, name):
    R = Run(page, base, name)
    open_admin(page, base)
    R.shot("main_floor_plan")
    R.click_fixture("A01")
    page.wait_for_selector("#f-num", timeout=5000)
    page.locator("#side-body").scroll_into_view_if_needed()
    R.shot("shelf_number_panel")
    assert not page.is_visible("[data-tool=draw]"), "drawing is tablet/desktop only"
    page.locator("#errs").scroll_into_view_if_needed()
    R.shot("errors_panel")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8791"))
    ap.add_argument("--device", default="all")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    names = list(DEVICES) if a.device == "all" else [n for n in DEVICES if a.device in n]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name in names:
            cfg = dict(DEVICES[name])
            full = cfg.pop("full")
            preset = cfg.pop("preset", None)
            ctx = browser.new_context(**(p.devices[preset] if preset else cfg), locale="ja-JP")
            ctx.add_cookies([{"name": "sn_role", "value": "manager", "url": a.base_url}])
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            log(f"--- {name} ---")
            (full_scenario if full else phone_scenario)(page, a.base_url, name)
            assert not errors, errors
            ctx.close()
        browser.close()
    log("ALL E2E SCENARIOS PASSED")


if __name__ == "__main__":
    sys.exit(main())
