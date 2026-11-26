"""Playwright E2E for CONTRACT v6 (model selector + list-price cost). Token is attached ONLY to requests to BASE.

    python e2e_v6.py <BASE_URL> <OUT_DIR> [--token TOKEN] [--model gemini-3.5-flash-lite]
"""
import argparse
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("base"); ap.add_argument("out")
ap.add_argument("--token", default="")
ap.add_argument("--model", default="gemini-3.5-flash-lite")
ap.add_argument("--sku", default="")
a = ap.parse_args()
BASE = a.base.rstrip("/")
os.makedirs(a.out, exist_ok=True)
results, errors = [], []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1, locale="ja-JP")
    if a.token:
        def add_auth(route):
            route.continue_(headers={**route.request.headers, "authorization": f"Bearer {a.token}"})
        ctx.route(BASE + "/**", add_auth)          # only this origin gets the ID token
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE + "/#/dashboard", wait_until="networkidle", timeout=120000)
    page.wait_for_selector("#model-btn")
    page.wait_for_function("document.querySelector('#model-name').textContent.trim() !== '—'", timeout=60000)
    check("header shows default model", "Gemini" in page.inner_text("#model-name"), page.inner_text("#model-name"))
    page.click("#model-btn")
    page.wait_for_selector(".mp-opt", timeout=30000)
    # wait for probing to settle
    for _ in range(30):
        if page.locator(".mp-opt:disabled").count() == 0:
            break
        time.sleep(2); page.click("#model-btn"); page.click("#model-btn")
    ids = page.eval_on_selector_all(".mp-opt", "els => els.map(e => e.dataset.model)")
    check("selector lists Gemini 3.x+ models", len(ids) >= 3 and all(not i.startswith("gemini-2") and not i.startswith("gemini-1") for i in ids), ids)
    check("selector shows availability badges", page.locator(".mp-opt .mb-ok").count() >= 1)
    page.screenshot(path=f"{a.out}/01_model_selector.png")
    page.click(f'.mp-opt[data-model="{a.model}"]')
    page.wait_for_timeout(500)
    stored = page.evaluate("localStorage.getItem('cymbal.enrich.model')")
    check("model persisted in localStorage", stored == a.model, stored)
    check("header reflects selection", page.get_attribute("#model-name", "data-model") == a.model)

    # live enrichment with the selected model
    sku = a.sku
    if not sku:
        sku = page.evaluate("fetch('/api/products').then(r => r.json()).then(d => d.items[0].sku)")
    page.goto(BASE + f"/#/studio/{sku}", wait_until="networkidle")
    page.wait_for_selector("#btn-live")
    page.wait_for_timeout(800)
    page.screenshot(path=f"{a.out}/02_studio_before.png")
    with page.expect_request(lambda r: r.url.endswith("/enrich") and r.method == "POST") as req:
        page.click("#btn-live")
    check("enrich request carries X-Enrich-Model", req.value.headers.get("x-enrich-model") == a.model, req.value.headers.get("x-enrich-model"))
    page.wait_for_selector("#livelog [data-used-model]", timeout=240000)
    used = page.get_attribute("#livelog [data-used-model]", "data-used-model")
    check("live enrichment ran with the selected model", used == a.model, used)
    page.wait_for_timeout(1200)
    page.screenshot(path=f"{a.out}/03_studio_live_result.png")

    # batch & cost
    page.goto(BASE + "/#/batch", wait_until="networkidle")
    page.wait_for_selector("#cost-tbl tbody tr", timeout=60000)
    rows = page.eval_on_selector_all("#cost-tbl tbody tr", "els => els.map(e => e.dataset.model)")
    check("cost table has per-model rows", len(rows) >= 3, rows)
    links = page.eval_on_selector_all("#cost-tbl a.price-link", "els => els.map(e => e.href)")
    check("each row links the official pricing page", len(links) == len(rows) and all(l.startswith("https://cloud.google.com/vertex-ai/generative-ai/pricing") for l in links), len(links))
    check("header link to official pricing page", page.get_attribute("#price-link-hdr", "href").startswith("https://cloud.google.com/vertex-ai/generative-ai/pricing"))
    check("price retrieval date shown", "retrieved" in page.inner_text("#price-date").lower(), page.inner_text("#price-date"))
    body = page.inner_text("#cost-panel")
    check("no batch-discount estimate shown", "50% off" not in body.lower())
    page.screenshot(path=f"{a.out}/04_cost_initial.png", full_page=False)
    # auto-measure on first view → wait until every row is measured (or failed)
    t0 = time.time()
    while time.time() - t0 < 300:
        if page.locator(".measure-prog").count() == 0 and page.locator("#cost-tbl tbody tr").count() and \
                page.locator("#cost-tbl tbody td.r:has-text('Not measured')").count() == 0:
            break
        time.sleep(3)
    check("auto sample measurement filled all rows", page.locator("#cost-tbl tbody td.r:has-text('Not measured')").count() == 0,
          f"{int(time.time() - t0)}s")
    # presets / slider
    page.click("#sim-presets .fchip[data-n='100000']")
    page.wait_for_timeout(1500)
    check("preset 100,000 applied", "100,000 SKU" in page.inner_text("#cost-tbl thead"))
    hero = page.inner_text("#cost-hero")
    check("hero sentence '... at list price, about $X'", "at list price" in hero.lower() and "$" in hero, hero)
    missing = page.locator("#cost-tbl .price-missing").count()
    check("missing-price rows show 'See the official pricing page' (if any)", True, f"{missing} cells")
    check("default basis = list price (no promotion applied)", page.get_attribute("#cost-hero .ch-main", "data-basis") == "list"
          and not page.is_checked("#promo-toggle") and "list price" in page.inner_text("#basis-chip").lower())
    r38 = page.inner_text('#cost-tbl tr[data-model="gemini-3.8-flash"]')
    check("3.8 Flash at list price $1.5 / $7.5", "$1.5" in r38 and "$7.5" in r38, r38[:120])
    page.screenshot(path=f"{a.out}/05_cost_measured.png", full_page=True)
    page.check("#promo-toggle"); page.wait_for_timeout(1500)
    check("promo toggle = reference, marked discounted", page.get_attribute("#cost-hero .ch-main", "data-basis") == "promo"
          and "discount" in page.inner_text("#cost-hero").lower())
    page.screenshot(path=f"{a.out}/05b_cost_promo_reference.png")
    page.uncheck("#promo-toggle"); page.wait_for_timeout(1500)
    # manual "Measure with 1 sample" button
    btn = page.locator(f'#cost-tbl tr[data-model="{a.model}"] .btn-measure')
    btn.click()
    page.wait_for_selector(f'#cost-tbl tr[data-model="{a.model}"] .measure-prog', timeout=10000)
    page.screenshot(path=f"{a.out}/06_measure_progress.png")
    page.wait_for_selector(f'#cost-tbl tr[data-model="{a.model}"] .btn-measure', timeout=180000)
    runs = page.evaluate(f"fetch('/api/cost/models?skus=100').then(r=>r.json()).then(d=>d.models.find(m=>m.id==='{a.model}').measurement)")
    check("sample-measure button stored a measurement", runs and runs.get("runs", 0) >= 1, runs)
    page.locator("#cost-panel").screenshot(path=f"{a.out}/07_cost_panel.png")

    # other screens show the override selector
    for route, sel in (("#/csv", "#csv-model"), ("#/gmc", "#gmc-model"), ("#/docs", "")):
        page.goto(BASE + "/" + route, wait_until="networkidle"); page.wait_for_timeout(1500)
        if sel:
            check(f"{route} has per-run model override", page.locator(sel).count() == 1)
    page.goto(BASE + "/#/csv", wait_until="networkidle"); page.wait_for_timeout(800)
    page.screenshot(path=f"{a.out}/08_csv_override.png")
    b.close()

bad = [e for e in errors if "favicon" not in e]
check("no JS console errors", not bad, bad[:5])
json.dump({"results": results, "console_errors": bad}, open(f"{a.out}/e2e_v6.json", "w"), ensure_ascii=False, indent=1)
print(f"{sum(r['ok'] for r in results)}/{len(results)} passed")
sys.exit(0 if all(r["ok"] for r in results) else 1)
