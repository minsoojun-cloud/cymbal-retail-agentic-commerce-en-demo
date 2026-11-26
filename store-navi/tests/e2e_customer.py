"""Playwright e2e for the Store Navi customer app (mobile emulation 390×844 + geolocation).

    python tests/e2e_customer.py --base-url http://127.0.0.1:8090 [--scenarios data/scenarios.json] [--headed]

Flow: GPS → home (nearest store) → location simulation (inside Kawasaki Station) → demo login switch → enter store →
autocomplete → results (current store fixed first row incl. Out of stock, nearby, online) → "In-stock at this store only" →
Find on map (floor map + tiers) → AI chat (fever) → recommendation card (numbered) → Find all on the map (Next) → Buy all
(online order id) → red-flag consultation (see a doctor first, no medicine). Screenshots → --out (default: coordinator scratch/screens).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

HERE = Path(__file__).resolve().parent
SN = HERE.parent
DEFAULT_OUT = Path(__import__("tempfile").gettempdir()) / "store_app_e2e"
KAWASAKI = {"latitude": 35.5316, "longitude": 139.6981}


def scenarios(path: str | None) -> dict:
    for cand in ([Path(path)] if path else []) + [SN / "data" / "scenarios.json", HERE / "fixtures" / "data" / "scenarios.json"]:
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
    return {}


class Run:
    def __init__(self, page: Page, out: Path, prefix: str):
        self.page, self.out, self.prefix = page, out, prefix
        self.n = 0
        self.checks: list[tuple[str, bool, str]] = []

    def shot(self, name: str) -> None:
        self.n += 1
        self.page.wait_for_timeout(400)
        p = self.out / f"{self.prefix}{self.n:02d}_{name}.png"
        self.page.screenshot(path=str(p))
        print("  screenshot", p.name)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, bool(ok), detail))
        print(("  PASS " if ok else "  FAIL ") + name + (f" — {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8090")
    ap.add_argument("--scenarios", default=None)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--prefix", default="customer_")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--chat-timeout", type=float, default=90)
    ap.add_argument("--id-token", action="store_true", help="send `gcloud auth print-identity-token` (private Cloud Run)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sc = scenarios(a.scenarios)
    cold = (sc.get("cold_tablet_out_at_S01") or {}).get("product_id")
    base = a.base_url.rstrip("/")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        dev = pw.devices["iPhone 13"]
        ctx = browser.new_context(**{**dev, "viewport": {"width": 390, "height": 844}}, locale="ja-JP",
                                  timezone_id="Asia/Tokyo", geolocation=KAWASAKI, permissions=["geolocation"])
        if a.id_token:
            import subprocess

            tok = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, check=True).stdout.strip()
            ctx.route(base + "/**", lambda route: route.continue_(headers={**route.request.headers, "authorization": "Bearer " + tok}))
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        r = Run(page, out, a.prefix)

        # 1. home with GPS
        page.goto(base + "/", wait_until="networkidle")
        page.wait_for_selector("#store-hero")
        page.wait_for_function("document.querySelector('.store-hero__name') && document.querySelector('.geo-bar__state b').textContent.includes('GPS')", timeout=15000)
        page.wait_for_selector("#home-coupons .coupon:not(.sn-skel)", timeout=15000)
        r.check("home: nearest store = Kawasaki Station", "Kawasaki Station" in page.inner_text(".store-hero__name"))
        r.check("home: open badge + tel link", page.locator("#store-tel").count() == 1 and page.locator(".store-hero .sn-pill").count() >= 1)
        r.check("home: staff cross link icon", page.locator("#sn-staff-link").count() == 1,
                page.get_attribute("#sn-staff-link", "data-tooltip") or "(STAFF_URL unset)")
        r.shot("home_gps")

        # 2. location simulation
        page.click("#geo-open")
        page.wait_for_selector("#geo-list .geo-opt")
        r.shot("geo_simulation")
        page.click("#geo-list [data-key=in_S01]")
        page.wait_for_selector(".instore-banner", timeout=10000)
        r.check("simulation: inside Kawasaki Station → in-store banner", "You are inside" in page.inner_text(".instore-banner"))
        page.click("#geo-open")
        page.click("#geo-list [data-key=far_tokyo]")
        page.wait_for_function("!document.querySelector('.instore-banner') && document.querySelector('.geo-bar__state b').textContent.includes('Tokyo Station')", timeout=10000)
        r.check("simulation: near Tokyo Station → no in-store", page.locator(".instore-banner").count() == 0)
        r.shot("geo_far")
        page.click("#geo-open")
        page.click("#geo-list [data-key=in_S01]")
        page.wait_for_selector(".instore-banner")

        # 3. demo login switch
        page.click("#hdr-login")
        page.wait_for_selector("#login-list .login-opt")
        r.shot("login_switcher")
        page.click("#login-list [data-cid=CR-0002]")
        page.wait_for_function("document.querySelector('#hdr-avatar').textContent.trim().length > 0")
        page.click("#enter-store")
        page.wait_for_selector("#st-coupons .coupon:not(.sn-skel)", timeout=15000)
        r.check("login CR-0002: personal coupon title", "Hiroshi" in page.inner_text("#personal-title"))
        r.shot("store_hiroshi")
        page.click("#hdr-login")
        page.click("#login-list [data-cid=CR-0001]")
        page.wait_for_selector("#st-coupons .coupon:not(.sn-skel)", timeout=15000)
        page.wait_for_function("document.querySelector('#personal-title').textContent.includes('Sakura')", timeout=10000)
        r.check("login CR-0001: Just for you, Sakura", "Sakura" in page.inner_text("#personal-title"))
        r.shot("store_sakura")

        # 4. autocomplete
        page.click("#sb-input")
        page.keyboard.type("cold", delay=60)
        page.wait_for_selector("#sb-ac li", timeout=10000)
        r.check("autocomplete: suggestions for 'cold'", page.locator("#sb-ac li").count() >= 3)
        r.shot("autocomplete")
        page.fill("#sb-input", "cold medicine tablets")
        page.keyboard.press("Enter")
        page.wait_for_selector("#res-list .pcard:not(.sn-skel)", timeout=30000)
        first_card = page.locator("#res-list .pcard").first
        first_row = first_card.locator(".avail__row").first
        r.check("results: current store fixed first row", "This store" in first_row.inner_text())
        if "Out of stock" not in first_row.inner_text() and "Not carried" not in first_row.inner_text():
            r.check("results: in-stock item hides other stores by default",
                    not first_card.locator(".avail__other").is_visible() and first_card.locator("[data-more-avail]").is_visible())
            first_card.locator("[data-more-avail]").click()
            r.check("results: 'See stock at other stores & online' expands other stores",
                    first_card.locator(".avail__other").is_visible() and "Online" in first_card.locator(".avail__other").inner_text())
        cb = page.locator("#res-list .pcard").first.locator("[data-cart]")
        r.check("cart icon button with aria-label Add to cart", cb.count() == 1 and cb.get_attribute("aria-label") == "Add to cart"
                and cb.locator("svg").count() == 1 and "+" not in cb.inner_text())
        cb.click()
        r.check("cart icon toggles to In cart", page.locator("#res-list .pcard").first.locator("[data-cart]").get_attribute("aria-pressed") == "true")
        if cold and page.locator(f'.pcard[data-pid="{cold}"]').count():
            card = page.locator(f'.pcard[data-pid="{cold}"]')
            card.scroll_into_view_if_needed()
            txt = card.inner_text()
            r.check("scenario: cold medicine tablets Out of stock at S01 + nearby + online",
                    "Out of stock" in card.locator(".avail__row--current").inner_text() and "Musashi-Kosugi" in txt and "Online" in txt)
            r.check("compliance: drug_class badge + notice", card.locator(".sn-drug").count() >= 1 and card.locator(".sn-notice").count() >= 1)
            r.shot("results_cold_out_of_stock")
        else:
            r.check("scenario product present in results", False, f"{cold} not in first page")
            r.shot("results")
        # 5. in-store only toggle
        page.click("#only-store")
        page.wait_for_function("location.hash.includes('only=1') && document.querySelector('#res-count') && "
                               "/in stock at/i.test(document.querySelector('#res-count').textContent)", timeout=30000)
        page.wait_for_selector("#res-list .pcard:not(.sn-skel), #res-list .res-empty", timeout=30000)
        rows = page.locator("#res-list .avail__row--current")
        ok = all("Out of stock" not in rows.nth(i).inner_text() for i in range(rows.count()))
        r.check("toggle: only in-stock items at this store", ok and rows.count() > 0, f"{rows.count()} items")
        r.shot("results_in_store_only")

        # 6. floor map
        page.locator("[data-find]").first.click()
        page.wait_for_selector("#mapv:not([hidden]) svg.floor .fx.is-target, #mapv:not([hidden]) svg.floor .fixed.is-target", timeout=15000)
        page.wait_for_selector("#map-elev .elev__cell.is-on, #map-elev .elev__rx", timeout=15000)
        page.wait_for_timeout(900)
        r.check("map: target highlighted + route + front elevation slot", page.locator("svg.floor .route").count() >= 1 and
                (page.locator("#map-elev .elev__cell.is-on").count() >= 1 or page.locator("#map-elev .elev__rx").count() == 1))
        r.check("map: product photo + name pinned at top", page.locator("#map-product .mpb__title").count() == 1 and
                page.locator("#map-product .sn-img").count() == 1)
        r.check("map: sticky bottom back button", page.locator("#map-back").is_visible())
        r.check("map: slot label shown", "shelf" in page.inner_text("#map-product") or "counter" in page.inner_text("#map-product").lower())
        r.shot("floormap_single")
        page.click("#map-zoom-in")
        page.wait_for_timeout(400)
        r.shot("floormap_zoom")
        page.click("#map-back")

        # 7. AI chat — fever
        page.goto(base + "/#/store/S01", wait_until="networkidle")
        page.click("#tabbar [data-tab=ai]")
        page.wait_for_selector("#chat:not([hidden])")
        page.fill("#chat-input", "I've had a fever since yesterday and feel like I'm getting a cold")
        page.click("#chat-send")
        page.wait_for_selector("#chat-log .rec", timeout=a.chat_timeout * 1000)
        page.wait_for_function("!document.querySelector('#chat-send').disabled && !document.querySelector('#chat-log .is-typing')",
                               timeout=a.chat_timeout * 1000)
        nums = page.locator("#chat-log .rec .rec-item__no:not(.is-off)")
        r.check("chat: recommendation card with numbered items", nums.count() >= 2, f"{nums.count()} numbered")
        r.check("chat: disclaimer shown", page.locator("#chat-log .disclaimer").count() >= 1)
        meds = page.locator("#chat-log .rec").last.locator(".sn-drug:not(.sn-drug--q):not(.sn-drug--abuse)")
        r.check("chat: at most one medicine per symptom group", meds.count() <= 2, f"{meds.count()} medicine badges")
        r.shot("chat_fever_recommendation")
        page.locator("#chat-log .rec").last.scroll_into_view_if_needed()
        r.shot("chat_fever_card_bottom")

        # 8. Find all on the map
        page.locator("#chat-log .rec [data-act=find]").last.click()
        page.wait_for_selector("#mapv:not([hidden]) .mpb__no", timeout=15000)
        page.wait_for_timeout(1200)
        r.check("multi-route: step 1 shown with pins", page.locator("svg.floor .pin").count() >= 2)
        r.shot("floormap_multi_step1")
        page.click("#mp-next")
        page.wait_for_timeout(600)
        r.check("multi-route: Next → step 2", "2 /" in page.inner_text("#map-product .mpb__meta"))
        r.shot("floormap_multi_step2")
        page.click("#map-close")

        # 9. Buy all
        page.click("#tabbar [data-tab=ai]")
        page.locator("#chat-log .rec [data-act=buy]").last.click()
        page.wait_for_selector("#sheet-cart:not([hidden]) .cart-line")
        r.shot("cart_store_list")
        page.click("#cart [data-mode=pickup]")
        r.shot("cart_pickup")
        page.click("#cart [data-mode=online]")
        page.click("#cart-online")
        page.wait_for_selector("#order-id")
        oid = page.inner_text("#order-id")
        r.check("online order: mock order id", oid.startswith("CR-"), oid)
        r.shot("cart_online_order")
        page.click("#cart-finish")

        # 10. red flag
        page.click("#tabbar [data-tab=ai]")
        recs_before = page.locator("#chat-log .rec").count()
        page.fill("#chat-input", "I've had a fever of 39C for 3 days and it won't go down")
        page.click("#chat-send")
        page.wait_for_timeout(500)
        page.wait_for_function("!document.querySelector('#chat-send').disabled && !document.querySelector('#chat-log .is-typing')",
                               timeout=a.chat_timeout * 1000)
        time.sleep(0.5)
        bots = page.locator("#chat-log .msg--bot")
        first_text = bots.nth(bots.count() - 1).inner_text() if bots.count() else ""
        all_bot = page.inner_text("#chat-log")
        r.check("red flag: see-a-doctor at the beginning of the reply",
                "doctor" in all_bot.split("it won't go down")[-1][:160], first_text[:60])
        recs = page.locator("#chat-log .rec")
        new_meds = sum(recs.nth(i).locator(".sn-drug--1, .sn-drug--s2, .sn-drug--2, .sn-drug--3").count()
                       for i in range(recs_before, recs.count()))
        r.check("red flag: no medicines in card(s) of this turn", new_meds == 0, f"{recs.count() - recs_before} new card(s)")
        r.check("red flag: disclaimer shown", page.locator("#chat-log .disclaimer").count() >= 2)
        page.locator("#chat-log .msg--user").last.scroll_into_view_if_needed()
        page.evaluate("document.querySelector('#chat-log').scrollTop = document.querySelector('#chat-log .msg--user:last-of-type') ? "
                      "[...document.querySelectorAll('#chat-log .msg--user')].pop().offsetTop - 12 : 0")
        r.shot("chat_red_flag")
        r.check("no JS errors", not errors, "; ".join(errors[:3]))
        browser.close()
    failed = [c for c in r.checks if not c[1]]
    print(f"\n{len(r.checks) - len(failed)}/{len(r.checks)} checks passed; screenshots in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
