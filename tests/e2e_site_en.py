#!/usr/bin/env python3
"""Playwright E2E + screenshots for the English Cymbal Retail PC site (CONTRACT_EN v14).

Checks: English page (<html lang="en">, no Japanese characters in visible text), "Cymbal Retail" logo, header Store App
link (when STORE_APP_URL is set), search + "Pick up in store" facet, product detail -> "Available in stores" modal,
store-only product cannot be added to the delivery cart, chat dinner set (mock or live agent), chat store availability
card (show_store_availability). Screenshots at 1440x900.

usage:
  python tests/e2e_site_en.py --base-url http://127.0.0.1:8090 --out /tmp/shots [--id-token] [--live-wait 150]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")


def parse_cfg(js: str) -> dict:
    assert js.startswith("window.CR_CONFIG = "), js[:80]
    return json.loads(js[len("window.CR_CONFIG = "):].strip().rstrip(";"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--out", default="/tmp/cr_e2e_en")
    ap.add_argument("--query", default="curry")
    ap.add_argument("--id-token", action="store_true", help="send `gcloud auth print-identity-token` to --base-url only")
    ap.add_argument("--live-wait", type=float, default=120)
    a = ap.parse_args()
    base = a.base_url.rstrip("/")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    token = subprocess.check_output(["gcloud", "auth", "print-identity-token"], text=True).strip() if a.id_token else ""
    hdr = {"Authorization": "Bearer " + token} if token else {}
    results: list[dict] = []
    shots: list[str] = []

    with httpx.Client(timeout=30, headers=hdr) as c:
        cfg = parse_cfg(c.get(base + "/config.js").text)
        pool = c.get(base + "/api/search", params={"q": "", "page_size": 100}).json()["results"]
        omni = next((p for p in pool if p.get("channel") == "omni"
                     and any(s["quantity"] > 0 for s in c.get(f"{base}/api/products/{p['id']}/stores").json()["stores"])), None)
        store_only = next((p for p in pool if p.get("channel") == "store_only"), None)
    print("config:", {k: cfg.get(k) for k in ("agentMode", "agentBackend", "storeAppUrl", "enrichUrl", "searchEngine")})
    print("omni:", omni and omni["id"], "store_only:", store_only and store_only["id"])

    def step(name, fn):
        t0 = time.time()
        try:
            info = fn()
            results.append({"step": name, "ok": True, "info": info})
            print(f"  ok   {name} ({time.time() - t0:.1f}s) {json.dumps(info, ensure_ascii=False)[:400] if info else ''}")
        except Exception as e:  # noqa: BLE001
            results.append({"step": name, "ok": False, "error": str(e)[:600]})
            print(f"  FAIL {name}: {e}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="Asia/Tokyo")
        if token:
            ctx.route(base + "/**", lambda r: r.continue_(headers={**r.request.headers, "authorization": "Bearer " + token}))
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def shot(name: str) -> str:
            p = str(out / f"{name}.png")
            page.screenshot(path=p)
            shots.append(p)
            return p

        def no_japanese(where: str):
            txt = page.evaluate("document.body.innerText")
            m = JP.findall(txt)
            assert not m, f"{where}: Japanese text {''.join(m[:20])!r}"

        def home():
            page.goto(base + "/", wait_until="networkidle")
            assert page.evaluate("document.documentElement.lang") == "en"
            assert "Cymbal" in page.inner_text("#cr-logo")
            page.wait_for_timeout(800)
            no_japanese("home")
            info = {"title": page.title()}
            if cfg.get("storeAppUrl"):
                link = page.locator("#cr-storeapp-link")
                assert link.count() == 1 and link.get_attribute("href") == cfg["storeAppUrl"]
                info["storeapp_tooltip"] = link.get_attribute("data-tooltip") or link.get_attribute("title")
            shot("01_home")
            return info

        def search():
            page.fill("#cr-search-input", a.query)
            page.press("#cr-search-input", "Enter")
            page.wait_for_selector("[data-stores], .cr-card, .cr-product", timeout=20000)
            page.wait_for_timeout(1500)
            no_japanese("search")
            body = page.inner_text("#cr-view")
            assert "Pick up in store" in body, "pickupInStore facet not rendered"
            shot("02_search")
            return {"pickup_facet": True}

        def product_store_modal():
            assert omni, "no omni product with store stock"
            page.goto(base + f"/?product={omni['id']}", wait_until="networkidle")
            page.wait_for_selector("#cr-pm-stores", timeout=15000)
            page.click("#cr-pm-stores")
            page.wait_for_selector("#cr-store-modal .cr-store-modal__body", timeout=15000)
            page.wait_for_timeout(1200)
            txt = page.inner_text("#cr-store-modal")
            assert "Cymbal Retail" in txt and ("in stock" in txt.lower() or "out of stock" in txt.lower()), txt[:300]
            no_japanese("store modal")
            shot("03_product_store_modal")
            return {"product": omni["id"], "stores_text": txt[:160]}

        def store_only_guard():
            assert store_only, "no store_only product"
            page.goto(base + f"/?product={store_only['id']}", wait_until="networkidle")
            page.wait_for_selector("#cr-pm-add", timeout=15000)
            btn = page.locator("#cr-pm-add")
            assert btn.is_disabled() and "In-store only" in btn.inner_text()
            r = page.evaluate("p => Promise.resolve(window.Cart.add(p, 1))", store_only)
            assert r and r.get("ok") is False and r.get("error") == "STORE_ONLY", r
            shot("04_store_only_product")
            return {"product": store_only["id"], "cart_add": r}

        def chat_dinner():
            page.goto(base + "/", wait_until="networkidle")
            page.click("#cr-fab")
            page.fill("#cr-chat-input", "Recommend something for dinner tonight")
            page.press("#cr-chat-input", "Enter")
            page.wait_for_selector(".cr-set-sec, .crc-set", timeout=a.live_wait * 1000)
            page.wait_for_timeout(2500)
            no_japanese("chat dinner")
            shot("05_chat_dinner_set")
            return {"sections": page.locator(".cr-set-sec").count()}

        def chat_store():
            page.fill("#cr-chat-input", "Which store has the potato salad? I want to pick it up today.")
            page.press("#cr-chat-input", "Enter")
            page.wait_for_selector(".crc-stcard", timeout=a.live_wait * 1000)
            page.wait_for_timeout(1500)
            shot("06_chat_store_availability")
            return {"modal_open": page.locator("#cr-store-modal").is_visible()}

        for name, fn in [("home", home), ("search", search), ("product store modal", product_store_modal),
                         ("store-only guard", store_only_guard), ("chat dinner set", chat_dinner),
                         ("chat store availability", chat_store)]:
            step(name, fn)
        browser.close()

    ok = all(r["ok"] for r in results) and not errors
    print("page errors:", errors[:5])
    print("screenshots:", *shots, sep="\n  ")
    (out / "e2e_result.json").write_text(json.dumps({"ok": ok, "results": results, "errors": errors, "shots": shots},
                                                    ensure_ascii=False, indent=1))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
