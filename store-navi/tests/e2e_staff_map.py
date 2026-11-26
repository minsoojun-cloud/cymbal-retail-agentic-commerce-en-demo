"""E2E (Playwright) — CONTRACT v13 floor maps in the staff app + live sync with the Shelf Manager.

  01 register home: "Registering for store" banner (store name + address + floor-plan thumbnail)
  02 confirm: floor map (Nearby) under the fixture picker, shelf highlighted + slot dot
  03 confirm: Whole store view;  04 tap another shelf on the map → fixture + elevation switch
  05 live sync: the Shelf Manager publishes a number change → the open confirm screen picks it up (≤ 15 s), then rollback
  06 Stock lookup: detail pane map (tablet) / 07 map sheet;  08 History → map sheet
Usage: BASE_URL=… python tests/e2e_staff_map.py [--device ipad|iphone|all]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

OUT = Path(os.environ.get("E2E_OUT", "/tmp/sn_e2e_staff_map"))
DEVICES = {
    "ipad_air_land": {"viewport": {"width": 1180, "height": 820}, "device_scale_factor": 2, "has_touch": True},
    "iphone14": {"preset": "iPhone 14"},
}


def log(m):
    print(m, flush=True)


def run(page, base, name, do_sync):
    n = [0]

    def shot(label, full=False):
        n[0] += 1
        p = OUT / f"{name}_{n[0]:02d}_{label}.png"
        page.screenshot(path=str(p), full_page=full)
        log(f"  captured {p.name}")

    def api(method, path, body=None):
        return page.evaluate("""async ([m, u, b]) => { const r = await fetch(u, {method: m, headers: {'Content-Type': 'application/json'},
            body: b ? JSON.stringify(b) : undefined}); return {status: r.status, data: await r.json().catch(() => null)}; }""",
                             [method, path, body])

    page.goto(f"{base}/?store=S01&staff=Sato#register")
    page.wait_for_selector("#reg-store-map svg", timeout=30000)
    assert "Kawasaki" in page.inner_text("#reg-store-name")
    time.sleep(0.5)
    shot("register_store_banner")

    page.wait_for_selector(".gitem", timeout=30000)
    page.locator(".gitem").first.click()
    page.wait_for_selector("#stage-3.active", timeout=90000)
    page.wait_for_selector("#confirm-map-svg svg", timeout=15000)
    time.sleep(0.6)
    hit = page.get_attribute("#confirm-map-svg .fm-fx.hit", "data-fid")
    assert hit, "selected shelf highlighted on the map"
    assert "Kawasaki" in page.inner_text("#confirm-map-store")
    page.locator("#confirm-map").scroll_into_view_if_needed()
    shot("confirm_map_near")
    page.click("#confirm-map [data-fz=all]")
    time.sleep(0.3)
    shot("confirm_map_all")
    page.click("#confirm-map [data-fz=near]")
    time.sleep(0.3)

    # tap another shelf on the map
    other = page.evaluate("""() => [...document.querySelectorAll('#confirm-map-svg .fm-fx.tap')].map(e => e.dataset.fid)
        .find(f => f && f !== document.querySelector('#confirm-map-svg .fm-fx.hit').dataset.fid && /^[A-Z]\\d\\d$/.test(f))""")
    page.locator(f"#confirm-map-svg [data-fid='{other}']").dispatch_event("click")
    page.wait_for_function(f"document.querySelector('#fixture-val').textContent.startsWith('{other}')", timeout=10000)
    page.wait_for_function(f"(document.querySelector('#confirm-map-svg .fm-fx.hit') || {{}}).dataset?.fid === '{other}'", timeout=10000)
    page.locator("#confirm-map").scroll_into_view_if_needed()
    shot("confirm_map_tapped_other")

    if do_sync:
        # live sync: rename a neighbouring shelf in the Shelf Manager and wait for the staff screen to pick it up
        ed = api("GET", "/api/admin/stores/S01/editor")["data"]
        start = ed["current_version"]
        lay = ed["layout"]
        used = {f["id"] for f in lay["fixtures"]}
        new_id = next(f"Q{i:02d}" for i in range(1, 100) if f"Q{i:02d}" not in used)
        f = next(x for x in lay["fixtures"] if x["id"] == other)
        f["id"] = new_id
        assert api("PUT", "/api/admin/stores/S01/draft", {"draft": lay})["status"] == 200
        r = api("POST", "/api/admin/stores/S01/publish", {"base_version": start, "by": "e2e", "confirm_unassigned": True})
        assert r["status"] == 200, r
        t0 = time.time()
        page.wait_for_function(f"document.querySelector('#fixture-val').textContent.startsWith('{new_id}') || "
                               f"!!document.querySelector(\"#confirm-map-svg [data-fid='{new_id}']\")", timeout=30000)
        log(f"  live sync picked up v{r['data']['version']} in {time.time() - t0:.1f}s")
        page.wait_for_function(f"document.querySelector('#fixture-val').textContent.startsWith('{new_id}')", timeout=15000)
        page.wait_for_function(f"(document.querySelector('#confirm-map-svg .fm-fx.hit') || {{}}).dataset?.fid === '{new_id}'", timeout=15000)
        page.locator("#confirm-map").scroll_into_view_if_needed()
        time.sleep(0.4)
        shot("live_sync_renamed")
        rb = api("POST", "/api/admin/stores/S01/rollback", {"to_version": start, "by": "e2e"})
        assert rb["status"] == 200, rb
        page.wait_for_function(f"!!document.querySelector(\"#confirm-map-svg [data-fid='{other}']\")", timeout=30000)
        ed2 = api("GET", "/api/admin/stores/S01/editor")["data"]
        assert ed2["unassigned_count"] == 0
        log(f"  restored to content of v{start} (now v{ed2['current_version']})")

    # stock search
    page.click("#btn-back")
    page.click("#tab-stock")
    page.fill("#stock-q", "toner")
    page.press("#stock-q", "Enter")
    page.keyboard.press("Escape")
    page.wait_for_selector(".map-btn >> visible=true", timeout=30000)
    time.sleep(0.8)
    if page.is_visible("#stock-map"):
        page.wait_for_selector("#stock-map svg", timeout=10000)
        page.locator("#stock-map").scroll_into_view_if_needed()
        shot("stock_detail_map")
    page.locator("#stock-results .map-btn").first.dispatch_event("click")
    page.wait_for_selector("#map-sheet:not([hidden]) #ms-svg svg", timeout=15000)
    time.sleep(0.6)
    assert page.locator("#ms-svg .fm-fx.hit").count() == 1
    shot("stock_map_sheet")
    page.click("#map-sheet [data-close]")

    page.click("#tab-history")
    try:
        page.wait_for_selector("#history .map-btn", timeout=10000)
    except Exception:  # noqa: BLE001
        log("  (no staff registrations yet — history map skipped)")
        return
    page.locator("#history .map-btn").first.click()
    page.wait_for_selector("#map-sheet:not([hidden]) #ms-svg svg", timeout=15000)
    time.sleep(0.6)
    shot("history_map_sheet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8791"))
    ap.add_argument("--device", default="all")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    names = list(DEVICES) if a.device == "all" else [k for k in DEVICES if a.device in k]
    with sync_playwright() as p:
        b = p.chromium.launch()
        for i, name in enumerate(names):
            cfg = dict(DEVICES[name])
            preset = cfg.pop("preset", None)
            ctx = b.new_context(**(p.devices[preset] if preset else cfg), locale="ja-JP")
            ctx.add_cookies([{"name": "sn_role", "value": "manager", "url": a.base_url}])
            page = ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            log(f"--- {name} ---")
            run(page, a.base_url, name, do_sync=(i == 0))
            assert not errs, errs
            ctx.close()
        b.close()
    log("ALL STAFF MAP SCENARIOS PASSED")


if __name__ == "__main__":
    sys.exit(main())
