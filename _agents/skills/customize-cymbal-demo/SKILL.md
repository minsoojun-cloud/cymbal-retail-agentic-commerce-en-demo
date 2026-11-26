---
name: customize-cymbal-demo
description: Customize an installed Cymbal Retail Agentic Commerce demo (English edition) for a customer, then apply each change with the smallest re-run. Covers the brand name, logo and colors; replacing or adding products and product images; the shopper persona, allergies and purchase history; store layouts, shelves, inventory and coupons; and the AI agents' instructions and Gemini models. Use when the user asks to rebrand the demo, load their own products or stores, change personas, or change agent prompts or models.
---

# Customize skill for the Cymbal Retail demo (`customize-cymbal-demo`)

When you adapt the demo for a customer (a supermarket, drugstore, general retailer or e-commerce business), run the
recipe that matches the change. Then apply it with a partial re-run (`./install/install.sh --steps …` /
`--from …`, or a single `deploy.sh`). Do not re-run the whole installer. Full details are in
[docs/CUSTOMIZE.md](../../../docs/CUSTOMIZE.md).

Before you start, make sure the demo is installed (`install/config.env` exists and `./install/install.sh --steps
90_verify` passes). Confirm the target `PROJECT_ID` with the user.

**Rules**

- Settings go in `install/config.env`. Never edit the root `config.env`.
- Keep `seed/` project-independent. URLs stay `__SITE_BASE_URL__/…`. Never write real project ids, project numbers or
  `run.app` URLs into tracked files.
- Keep the UI and data in one language (English by default). Use only brand names and images the customer owns or has
  approved. Never use real third-party retailer or manufacturer names.
- Finish every recipe with verification (Recipe 6).

---

## Quick reference: what you change → what you re-run

| Change | Files | Re-run |
|---|---|---|
| Brand name, logo, colors | `online-store/static/{index.html,img/logo.svg,css/theme.css}`, `store-navi/static/shared/{img,css}`, `store-navi/static/{customer,staff,admin}`, `data-enrichment/static` | `./install/install.sh --steps 70_apps -y` (or one `deploy.sh`) |
| Products, images | `seed/catalog/products_retail.jsonl`, `online-store/static/img/products/`, `seed/catalog/{autocomplete_suggestions.jsonl,recipes_en.json}` | `./install/install.sh --from 50_catalog_import -y` |
| Persona, allergies, history | `seed/personas/*`, `seed/bigquery/cymbal/{customers,purchase_history}.jsonl.gz`, `shared/mcp_server/data/*`, `store-navi/data/*`, `online-store/app/main.py` | `./install/install.sh --steps 40_data,60_backend,70_apps -y` |
| Stores, layouts, shelves, coupons | `/admin` in the browser, or `store-navi/scripts/store_layout_defs.py` + `store-navi/data/*` (+ `seed/bigquery/cymbal/*`) | Option A: none. Option B: `build_store_data.py` → `setup_bq.py` → `push_local_inventory.py --force` → `--steps 70_apps` |
| Online-store concierge prompt or tools | `shared/adk_agent/cymbal_agent/{agent.py,tools.py}` | `source config.env && bash shared/adk_agent/deploy.sh` |
| MCP tools | `shared/mcp_server/server.py` | `./install/install.sh --steps 60_backend -y` |
| Store App "Ask AI" prompt or safety | `store-navi/app/agent/{agent.py,safety.py,tools.py}` | `source config.env && SERVICE_NAME=$STORE_APP_SERVICE bash store-navi/deploy.sh` |
| Models, voice | `install/config.env` | `./install/install.sh --steps 15_models,60_backend,70_apps -y` |

---

## Recipe 1: Customer brand (name, logo, colors)

1. Find every occurrence of the wordmark:
   ```bash
   grep -rn "Cymbal Retail" online-store/static store-navi/static data-enrichment/static \
     shared/adk_agent/cymbal_agent store-navi/app online-store/app data-enrichment/app
   ```
   Replace the display name in the HTML, in the agent greetings (`BASE_INSTRUCTION`) and in server strings. Product
   brand lines (`Cymbal Basic`, `Cymbal Select`, `Cymbal Organic`) are catalog data. Change them in Recipe 2 only if
   the customer asks.
2. Replace the logos: `online-store/static/img/logo.svg` and `favicon.svg`, the inline SVG `.cr-logo` in
   `online-store/static/index.html`, and `store-navi/static/shared/img/logo.svg` and `favicon.svg`.
3. Change the colors by editing only the CSS variable *values*. The online store uses `--cr-blue`, `--cr-text`,
   `--cr-green`, … in `online-store/static/css/theme.css`. The store apps use the tokens in
   `store-navi/static/shared/css/`. Check contrast (text on primary at least 4.5:1).
4. Apply with `./install/install.sh --steps 70_apps -y`, then reload each app and take screenshots for the user.

---

## Recipe 2: Replace or add products

1. Edit `seed/catalog/products_retail.jsonl`. It has one Retail `Product` per line: `type: PRIMARY`,
   `languageCode: "en"`, `currencyCode: "JPY"` and tax-included prices. Keep these rules:
   - The `channel` attribute is `omni`, `store_only` or `online_only`. It drives the store badges, the "Available in
     stores" modal and Local Inventory.
   - `allergens` use English names (`shrimp`, `crab`, `wheat`, `egg`, `milk`, `peanut`, `buckwheat`, `walnut`…). The
     concierge's allergy guard depends on them.
   - Categories use the top-level names in `shared/catalog/category_map_en.json`.
   - `uri` is `__SITE_BASE_URL__/?product=<id>`, and images are `__SITE_BASE_URL__/img/products/<file>`.
2. Put the images (600×600 JPEG, white background) in `online-store/static/img/products/`. Use only the customer's own
   images or images you generated. The store app copies the same folder at deploy time.
3. Keep the related files consistent: autocomplete (`seed/catalog/autocomplete_suggestions.jsonl`) and recipes
   (`seed/catalog/recipes_en.json`). Every ingredient `product_id` must exist.
4. Validate, then apply:
   ```bash
   python3 shared/catalog/validate_jsonl.py seed/catalog/products_retail.jsonl
   ./install/install.sh --from 50_catalog_import -y
   ```
   The FULL import removes products that are no longer in the file. Local Inventory is re-pushed with `--force`.
5. New `store_only` or `omni` products need shelf slots and stock (Recipe 4). Otherwise they show as unavailable in
   stores.

---

## Recipe 3: Persona (family, allergies, preferences) and purchase history

The demo shopper is **CR-0001 Sakura Tanaka**: a family of 4, with a shrimp allergy (the kids) and a liking for
medium-hot curry. CR-0002 and CR-0003 are store-app personas.

1. Edit `seed/personas/personas.json`: `household`, `allergies`, `dislikes`, `family_preferences`,
   `favorite_products_keywords`, …. Keep `customer_id`. If needed, edit `seed/personas/purchase_history.jsonl`, using
   product ids that exist in the catalog.
2. Write the same data into the BigQuery seed: `seed/bigquery/cymbal/customers.jsonl.gz` (update `profile_json` too)
   and `purchase_history.jsonl.gz`. Decompress, edit and recompress.
3. Sync the copies in `shared/mcp_server/data/` (MCP fallback) and `store-navi/data/` (store app), and the display name
   in `online-store/app/main.py` (`CUSTOMERS`).
4. If the allergen changes, update the example sentence in `<allergy>` in `shared/adk_agent/cymbal_agent/agent.py`.
5. Apply with `./install/install.sh --steps 40_data,60_backend,70_apps -y`. Then ask the concierge for a dinner plan
   and confirm that the new allergen is excluded.

---

## Recipe 4: Stores, layouts, shelves, in-store stock

**Option A (no code)**: use the Shelf Manager at `<staff URL>/admin` to edit shelves, numbers, zones and tiers, then
**Review & publish**. Use the Staff App to register products on shelves from photos. Each registration goes to
BigQuery and is pushed to Local Inventory right away. Re-running the installer keeps staff registrations, but
`40_data` resets published layouts to the seed.

**Option B (rebuild from definitions)**:

```bash
vi store-navi/scripts/store_layout_defs.py      # fixtures, aisles, planogram groups
vi store-navi/data/stores.json                  # names, addresses, hours, lat/lng
python3 store-navi/scripts/build_store_data.py all
python3 store-navi/scripts/gen_personas.py
source config.env
python3 store-navi/scripts/setup_bq.py --skip-iam
python3 store-navi/scripts/push_local_inventory.py --force
./install/install.sh --steps 70_apps -y
```

To make Option B permanent for future installs, also update the matching `seed/bigquery/cymbal/<table>.jsonl.gz` files
(for example with `bq extract --destination_format NEWLINE_DELIMITED_JSON --compression GZIP`). Store ids `S01` to
`S03` are referenced by the demo flow. If you rename stores, keep the ids or update the defaults (`--store S01` in
the smoke test, the store app's default store).

---

## Recipe 5: Agent instructions and models

- **Online-store AI Concierge**: `BASE_INSTRUCTION` in `shared/adk_agent/cymbal_agent/agent.py` (sections such as
  `<allergy>` and `<greeting>`, dinner-set and reply-style rules). The tools are in `cymbal_agent/tools.py`, and they
  call the MCP server `shared/mcp_server/server.py`. Apply with `bash shared/adk_agent/deploy.sh` (after
  `source config.env`). If you changed MCP tools, use `--steps 60_backend`.
- **Store App "Ask AI"**: `BASE_INSTRUCTION` in `store-navi/app/agent/agent.py`. The health-safety policy is in
  `safety.py`: no diagnosis, red-flag check, pharmacist hand-off for Class 1. **Keep the safety rules.** Apply by
  redeploying the store app.
- **Models and voice**: set `GEMINI_MODEL`, `AGENT_MODEL`, `ENRICH_GEMINI_MODEL`, `IMAGE_MODEL`, `GEMINI_LOCATION` and
  `VOICE_ENABLED` in `install/config.env`. Run `./install/install.sh --steps 15_models,60_backend,70_apps -y`. A pinned
  model is used as is. An empty field means the default, with a probed fallback. Never hard-code model names in code.

---

## Recipe 6: Verify and report (always)

1. Run `./install/install.sh --steps 90_verify` (or `source config.env && python3 tests/smoke_install.py`). All checks
   must pass.
2. Walk through the demo: concierge dinner set with the allergy guard, a store-only item with **Available in stores**,
   checkout; Store App on a phone and a tablet (search with live stock, Find on map, "Did you forget?", Ask AI with
   "How I answered"); one Staff App shelf registration that appears in the Store App; and the Enrichment Studio.
3. Check that no project-specific values leaked into tracked files:
   `git status` and `git diff --stat`. Then grep for `run.app` and the project id in `seed/`, `shared/`,
   `online-store/`, `store-navi/` and `data-enrichment/`.
4. Report to the user what changed (files), what was re-run, the verification results, and screenshots of the changed
   screens.
