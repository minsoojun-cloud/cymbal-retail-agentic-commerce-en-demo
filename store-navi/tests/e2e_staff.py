"""Playwright E2E for the Cymbal Staff app (touch-first UI v10; CONTRACT v9 slot ids).

Flow per device: gallery → analyzing → confirm (elevation slot selection) → done → stock search → stock detail/adjust
(stepper + "Set to 0" confirm sheet) → history. Each step: screenshot <SCREEN_DIR>/<device>_<step>.png, assert no horizontal
overflow, and assert visible buttons are ≥ 44 px touch targets.

Local (starts uvicorn app.main:app with APP_MODE=staff DATA_MODE=local, Gemini mocked unless --live-vision):
  cd store-navi && python tests/e2e_staff.py [--device iphone14 pixel7 ipad_mini ipad_air_land ipad_pro_land | all]
Against a deployed staff service (add --id-token when public access is off):
  BASE_URL=https://cymbal-store-staff-….run.app python tests/e2e_staff.py --device all [--id-token]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SN = Path(__file__).resolve().parents[1]
SCREEN_DIR = Path(os.environ.get("SCREEN_DIR", str(Path(tempfile.gettempdir()) / "screens" / "staff_en")))
HEADERS: dict[str, str] = {}
DEVICES = {
    "iphone14": {"preset": "iPhone 14"},
    "pixel7": {"preset": "Pixel 7"},
    "ipad_mini": {"preset": "iPad Mini"},
    "ipad_air_land": {"viewport": {"width": 1180, "height": 820}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True,
                      "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
    "ipad_pro_land": {"viewport": {"width": 1366, "height": 1024}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True,
                      "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"},
}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(live_vision: bool) -> tuple[subprocess.Popen, str]:
    port = free_port()
    env = {**os.environ, "APP_MODE": "staff", "DATA_MODE": "local", "SEARCH_MODE": "local",
           "SN_STATE_DIR": tempfile.mkdtemp(prefix="sn_e2e_staff_"), "RETAIL_LOCAL_INVENTORY": "0", "STAFF_PREWARM": "0",
           "CUSTOMER_URL": os.environ.get("CUSTOMER_URL", "https://example.invalid/customer")}
    if not live_vision:
        env["STAFF_VISION_MOCK"] = "1"
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            cwd=SN, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/api/stores", timeout=1)
            return proc, base
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("server did not start")


def get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
        return json.loads(r.read())


def id_token() -> str:
    tok = os.environ.get("ID_TOKEN") or subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True,
                                                       text=True, check=True).stdout.strip()
    if not tok:
        raise SystemExit("could not get an identity token (gcloud auth login?)")
    return tok


OVERFLOW_JS = """() => {
  const iw = window.innerWidth;
  const wide = [...document.querySelectorAll('.card, .detect-card, .actionbar, .sr, .hrow, .hero, .gitem, .searchbar, .sel-sum, .elev-wrap, .sheet')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && !e.closest('[hidden]') && (r.right > iw + 1 || r.left < -1); })
    .map(e => (e.id || e.className) + ':' + Math.round(e.getBoundingClientRect().right));
  return {sw: Math.max(document.scrollingElement.scrollWidth, document.body.scrollWidth), iw, wide};
}"""
TARGETS_JS = """() => [...document.querySelectorAll('button, a.btn, label.btn, .fab, [role=tab]')]
  .filter(e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
                 return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && !e.closest('[hidden]') && !e.closest('.elev--mini')
                 && !e.matches('.map-btn, .fmap__seg button'); })  // v13 inline map controls: compact secondary targets
  .map(e => { const r = e.getBoundingClientRect(); return {t: (e.id || e.className || e.tagName) + ' ' + (e.textContent || '').trim().slice(0, 14),
                                                            w: Math.round(r.width), h: Math.round(r.height)}; })
  .filter(x => Math.min(x.w, x.h) < 44)"""


def check(page, dev: str, step: str, report: list, full: bool = False):
    page.wait_for_timeout(250)
    page.screenshot(path=str(SCREEN_DIR / f"{dev}_{step}.png"), full_page=full)
    o = page.evaluate(OVERFLOW_JS)
    small = page.evaluate(TARGETS_JS)
    assert o["sw"] <= o["iw"] and not o["wide"], f"{dev}/{step}: horizontal overflow {o}"
    assert not small, f"{dev}/{step}: touch targets < 44px: {small[:6]}"
    report.append(f"{dev}_{step}.png (no overflow, targets ≥44px)")


def run_device(pw, browser, base: str, dev: str, ph: dict, report: list):
    from playwright.sync_api import expect

    spec = DEVICES[dev]
    opts = dict(pw.devices[spec["preset"]]) if "preset" in spec else {k: v for k, v in spec.items()}
    ctx = browser.new_context(**opts, locale="en-US", extra_http_headers=HEADERS or None, service_workers="block")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sid, pid, slots = ph["store_id"], ph["product_id"], list(ph["slot_ids"])
    vw = page.viewport_size["width"]
    phone = vw < 600
    page.goto(f"{base}/?store={sid}&staff=Sato#register")
    expect(page.locator(".gitem").first).to_be_visible(timeout=20000)
    expect(page.locator("#who-store")).not_to_contain_text("Select")
    if phone:
        expect(page.locator(".fab")).to_be_visible()
        expect(page.locator(".nav")).to_have_css("bottom", "0px")
    else:
        assert page.locator(".nav").bounding_box()["width"] < 140, "tablet must use a navigation rail"
    check(page, dev, "1_gallery", report)

    page.locator(f".gitem[data-id='{ph['id']}']").click()
    expect(page.locator("#stage-2")).to_have_class("stage active", timeout=5000)
    check(page, dev, "2_analyzing", report)
    expect(page.locator("#stage-3")).to_have_class("stage active", timeout=90000)
    expect(page.locator("#detect")).to_contain_text(slots[0])
    expect(page.locator("#f-elev .slot").first).to_be_visible(timeout=10000)
    if phone:
        expect(page.locator(".nav")).to_be_hidden()  # full-screen flow
    expect(page.locator(".actionbar").first).to_be_visible()
    bar = page.locator("#stage-3 .actionbar").bounding_box()
    vh = page.viewport_size["height"]
    assert bar and 0 <= bar["y"] and bar["y"] + bar["height"] <= vh + 1, f"{dev}: action bar not visible without scrolling: {bar}"
    check(page, dev, "3_confirm", report)
    sel = page.eval_on_selector_all("#f-elev .slot.sel", "els => els.map(e => e.dataset.slot)")
    if sel != slots:
        page.locator(f"#f-elev .slot[data-slot='{slots[0]}']").click()
        for s_ in slots[1:]:
            page.locator(f"#f-elev .slot[data-slot='{s_}']").click()
    assert page.eval_on_selector_all("#f-elev .slot.sel", "els => els.map(e => e.dataset.slot)") == slots
    page.locator("#elev-wrap").scroll_into_view_if_needed()
    expect(page.locator("#sel-sum")).to_contain_text(slots[0])
    check(page, dev, "3b_elevation_selection", report)
    # fixture picker bottom sheet opens & closes
    page.click("#fixture-btn")
    expect(page.locator("#fixture-sheet")).to_be_visible()
    check(page, dev, "3c_fixture_sheet", report)
    page.locator("#fixture-sheet [data-close]").click()
    page.click("#btn-register")
    expect(page.locator("#stage-4")).to_have_class("stage active", timeout=30000)
    expect(page.locator("#done-loc")).to_contain_text(slots[0])
    expect(page.locator("#done-elev .slot.sel")).to_have_count(len(slots))
    expect(page.locator("#done-sync")).to_contain_text("AI Commerce Search Local Inventory", timeout=10000)
    check(page, dev, "4_done", report)
    av = get_json(f"{base}/api/products/{pid}/availability?store_id={sid}")["current"]["shelf"]
    assert av["slot_ids"] == slots, av
    report.append(f"{dev}: customer availability → {av.get('slot_label')}")
    page.click("#btn-again")
    expect(page.locator("#stage-1")).to_have_class("stage active")

    # stock
    page.click("#tab-stock")
    page.fill("#stock-q", pid)
    page.press("#stock-q", "Enter")
    expect(page.locator("#stock-results .sr").first).to_be_visible(timeout=20000)
    page.wait_for_timeout(300)
    check(page, dev, "5_stock_search", report)
    detail = page.locator("#stock-detail")
    scope = "#stock-detail" if detail.is_visible() else "#stock-results .sr >> nth=0"
    card = page.locator(scope)
    expect(card.locator(".srow")).to_have_count(3)
    expect(card.locator(".srow.here")).to_contain_text(slots[0])
    card.locator(".srow.here [data-adj='1']").click()
    expect(page.locator("#toast")).to_contain_text("units", timeout=10000)
    card = page.locator(scope)
    card.locator(".srow.here [data-zero]").click()
    expect(page.locator("#confirm-sheet")).to_be_visible()
    check(page, dev, "6a_stock_zero_confirm", report)
    page.click("#cf-ok")
    expect(page.locator(scope).locator(".srow.here .st")).to_contain_text("Out of stock", timeout=10000)
    page.wait_for_timeout(300)
    check(page, dev, "6_stock_detail_adjust", report)
    assert get_json(f"{base}/api/products/{pid}/availability?store_id={sid}")["current"]["status"] == "out_of_stock"
    page.request.patch(f"{base}/api/staff/inventory", data={"store_id": sid, "product_id": pid, "quantity": 12})

    # history
    page.click("#tab-history")
    expect(page.locator(".hrow").first).to_contain_text(slots[0], timeout=45000)
    check(page, dev, "7_history", report)
    assert not errors, errors
    ctx.close()


def manifest_check(base: str, report: list):
    m = get_json(f"{base}/manifest.webmanifest")
    assert m["name"] == "Cymbal Staff" and m["display"] == "standalone" and m["start_url"].startswith("/")
    assert m["theme_color"].lower() == "#1a73e8" and m.get("orientation") == "any"
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
    for i in m["icons"]:
        with urllib.request.urlopen(urllib.request.Request(base + i["src"], headers=HEADERS), timeout=30) as r:
            assert r.status == 200 and r.headers.get_content_type() == "image/png"
    with urllib.request.urlopen(urllib.request.Request(base + "/sw.js", headers=HEADERS), timeout=30) as r:
        assert r.status == 200 and "javascript" in r.headers.get_content_type()
    report.append("manifest.webmanifest valid (name/standalone/theme/192+512+maskable icons) + /sw.js served")


def sw_check(browser, base: str, report: list):
    ctx = browser.new_context(extra_http_headers=HEADERS or None)
    page = ctx.new_page()
    page.goto(f"{base}/?store=S01&staff=Sato#register")
    ok = page.evaluate("""async () => { const r = await navigator.serviceWorker.ready; return !!r.active; }""")
    assert ok, "service worker not active"
    report.append("service worker registered & active (scope /)")
    ctx.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", default=os.environ.get("E2E_PHOTO"))
    ap.add_argument("--device", nargs="*", default=["iphone14", "ipad_air_land"], help="device keys or 'all'")
    ap.add_argument("--live-vision", action="store_true", help="use real Gemini in the local server")
    ap.add_argument("--id-token", action="store_true", help="send Authorization: Bearer <gcloud identity token> (Cloud Run IAM)")
    a = ap.parse_args()
    devs = list(DEVICES) if a.device in (["all"], "all") else a.device
    base = os.environ.get("BASE_URL", "").rstrip("/")
    if a.id_token:
        HEADERS["Authorization"] = f"Bearer {id_token()}"
    proc = None
    if not base:
        proc, base = start_server(a.live_vision)
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    try:
        demo_dir = Path(os.environ.get("STAFF_DEMO_DIR") or (SN / "static" / "staff" / "demo_photos"))
        photos = json.loads((demo_dir / "demo_photos.json").read_text(encoding="utf-8"))["photos"]
        ph = next((p for p in photos if p["id"] == a.photo), None) if a.photo else None
        ph = ph or next(p for p in photos if p.get("slot_ids"))
        before = get_json(f"{base}/api/products/{ph['product_id']}/availability?store_id={ph['store_id']}")["current"].get("shelf") or {}
        print(f"photo {ph['id']} {ph['product_id']} → {ph['slot_ids']} (before {before.get('slot_ids')})", flush=True)
        manifest_check(base, report)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            if base.startswith("https://") or "127.0.0.1" in base or "localhost" in base:
                sw_check(browser, base, report)
            for d in devs:
                print(f"--- {d}", flush=True)
                run_device(pw, browser, base, d, ph, report)
            browser.close()
        print("\nE2E STAFF PASSED")
        for c in report:
            print(" ✓", c)
        print("screenshots:", SCREEN_DIR)
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
