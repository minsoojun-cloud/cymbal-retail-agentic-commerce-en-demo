"""Playwright smoke test against the deployed Cloud Run service.

    source ../config.env   # PROJECT_NUMBER, REGION, ENRICH_URL, ONLINE_STORE_URL / STORE_URL
    python tests/e2e/smoke_deployed.py "$ENRICH_URL" OUT_DIR [--token "$(gcloud auth print-identity-token)"]

The optional ID token is attached ONLY to requests whose URL starts with the service origin (never to fonts / images /
the storefront). Steps (each saves OUT_DIR/NN_name.png and records pass/fail):
  dashboard, header store link href, studio live run (food + GM), HITL review action, GMC generate,
  image studio (2 images), docs extraction (sample label PNG), batch & cost
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
# Expected header store link: env STORE_URL (what the app reads), else the shared config.env storefront URL.
STORE_URL = os.environ.get("STORE_URL") or os.environ.get("ONLINE_STORE_URL") or os.environ.get("SITE_BASE_URL", "")
FOOD_SKU, GM_SKU, IMG_SKU = "4913067859176", "4946311911337", "4925319234365"


async def main():
    args = [a for a in sys.argv[1:]]
    token = None
    if "--token" in args:
        i = args.index("--token")
        token = args[i + 1]
        del args[i:i + 2]
    base, out = args[0].rstrip("/"), Path(args[1])
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    errors: list[str] = []

    only = set(filter(None, os.environ.get("SMOKE_ONLY", "").split(",")))

    async def step(name, fn):
        if only and name not in only:
            return
        t0 = time.time()
        try:
            note = await fn()
            results.append({"step": name, "ok": True, "sec": round(time.time() - t0, 1), "note": note or ""})
            print(f"✅ {name} ({time.time() - t0:.1f}s) {note or ''}")
        except Exception as e:  # noqa: BLE001
            results.append({"step": name, "ok": False, "sec": round(time.time() - t0, 1), "note": repr(e)[:300]})
            print(f"❌ {name}: {e!r}"[:400])
        finally:
            try:
                await page.screenshot(path=str(out / f"{len(results):02d}_{name}.png"), full_page=False)
            except Exception:
                pass

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
        if token:
            async def auth(route):
                if route.request.url.startswith(base + "/") or route.request.url == base:
                    await route.continue_(headers={**route.request.headers, "authorization": f"Bearer {token}"})
                else:
                    await route.continue_()
            await ctx.route("**/*", auth)
        page = await ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        async def dashboard():
            await page.goto(base + "/#/dashboard")
            await page.wait_for_selector(".kpi-hero", timeout=30000)
            await page.wait_for_timeout(1500)
            txt = await page.inner_text(".kpi-hero")
            return txt.replace("\n", " ")[:80]
        await step("dashboard", dashboard)

        async def header_link():
            a = page.locator("#store-link")
            href = await a.get_attribute("href")
            title = await a.get_attribute("title")
            label = await a.inner_text()
            assert not STORE_URL or href.rstrip("/") == STORE_URL.rstrip("/"), href
            assert title and "Cymbal Retail" in title, title
            assert "store" in (label or title).lower(), label
            box = await page.locator("#store-link .xlink__icon").bounding_box()
            assert box and round(box["width"]) == 36 and round(box["height"]) == 36, box
            await a.hover()
            await page.wait_for_timeout(400)
            return f"href={href} icon={box['width']:.0f}px"
        await step("header_store_link", header_link)

        async def studio(sku):
            await page.goto(f"{base}/#/studio/{sku}")
            await page.wait_for_selector("#btn-live", timeout=30000)
            await page.click("#btn-live")
            # stepper goes LIVE then the result replaces the After panel
            await page.wait_for_function("() => /run live again/i.test(document.querySelector('#btn-live')?.innerText || '')",
                                         timeout=120000)
            await page.wait_for_timeout(1500)
            txt = await page.inner_text("#aft")
            assert "auto-approved" in txt.lower() or "needs review" in txt.lower(), txt[:120]
            return txt.split("\n")[0][:60]
        await step("studio_food_live", lambda: studio(FOOD_SKU))
        await step("studio_gm_live", lambda: studio(GM_SKU))

        async def reviews():
            await page.goto(base + "/#/reviews")
            await page.wait_for_selector("[data-act='approve']", timeout=30000)
            await page.wait_for_timeout(800)
            before = await page.inner_text(".page-head")
            await page.screenshot(path=str(out / "review_before.png"))
            # reject the current (most uncertain) item via keyboard shortcut R, then confirm the queue shrank
            await page.keyboard.press("r")
            await page.wait_for_timeout(2500)
            after = await page.inner_text(".page-head")
            return f"{before.split()[-2:]} → {after.split()[-2:]}"
        await step("hitl_review", reviews)

        async def gmc():
            await page.goto(f"{base}/#/gmc/{FOOD_SKU}")
            await page.wait_for_selector("#btn-gmc-regen", timeout=30000)
            await page.click("#btn-gmc-regen")
            await page.wait_for_selector(".toast-ok", timeout=90000)
            await page.wait_for_timeout(1200)
            n = await page.locator(".qa-item").count()
            assert n >= 1
            return f"Q&A={n} " + (await page.inner_text(".toast-ok"))[:60]
        await step("gmc_generate", gmc)

        async def images():
            await page.goto(f"{base}/#/images/{IMG_SKU}")
            await page.wait_for_selector("#im-gen-btn", timeout=30000)
            await page.wait_for_timeout(1500)
            await page.click("[data-act='preset'][data-id='autumn-taste']")
            await page.click("[data-act='count'][data-v='2']")
            await page.click("#im-gen-btn")
            await page.wait_for_function("() => document.querySelectorAll('#im-res-grid .ic-card img').length >= 2", timeout=240000)
            await page.wait_for_timeout(2500)
            na = await page.locator(".pz.na").count()
            return f"2 images generated, unavailable presets shown={na}"
        await step("image_studio_2_images", images)

        async def docs():
            await page.goto(base + "/#/docs")
            await page.wait_for_selector("#dc-file", state="attached", timeout=30000)
            await page.set_input_files("#dc-file", str(ROOT / "samples/docs/label_cymbal_carbonara.png"))
            await page.wait_for_selector("[data-act='extract']:not([disabled])", timeout=60000)
            await page.click("[data-act='extract']")
            await page.wait_for_function("() => /4911478160614/.test(document.querySelector('#dc-s3')?.innerText || '')", timeout=180000)
            await page.wait_for_timeout(1500)
            await page.locator("#dc-s3").scroll_into_view_if_needed()
            txt = await page.inner_text("#dc-s3")
            assert "jan match" in txt.lower(), "SKU not auto-matched by JAN"
            await page.screenshot(path=str(out / "docs_extracted.png"))
            # apply to BigQuery with re-enrichment (the cymbal.products checkbox stays OFF)
            assert not await page.locator("#dc-aics").is_checked()
            await page.click("[data-act='apply']")
            await page.wait_for_function("() => /applied to bigquery/i.test(document.querySelector('#dc-s4')?.innerText || '')", timeout=180000)
            await page.wait_for_timeout(2000)
            await page.locator("#dc-s4").scroll_into_view_if_needed()
            t4 = await page.inner_text("#dc-s4")
            return ("allergen box extracted; " if "soybean" in txt.lower() else "") + t4.replace("\n", " ")[:120]
        await step("docs_extraction_label", docs)

        async def batch():
            await page.goto(base + "/#/batch")
            await page.wait_for_selector("#b-jobs", timeout=30000)
            await page.wait_for_timeout(3000)
            txt = await page.inner_text("#b-jobs")
            sim = await page.inner_text("#sim-out")
            return f"jobs: {txt.count('JOB_STATE') or txt.lower().count('succeeded')} / sim: {sim.splitlines()[0][:40] if sim else ''}"
        await step("batch_and_cost", batch)

        await browser.close()
    (out / "results.json").write_text(json.dumps({"base": base, "results": results, "console_errors": errors[:50]},
                                                 ensure_ascii=False, indent=1))
    ok = sum(r["ok"] for r in results)
    print(f"\n{ok}/{len(results)} steps passed; console errors: {len(errors)}")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
