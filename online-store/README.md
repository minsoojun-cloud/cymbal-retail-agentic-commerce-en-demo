# online-store — Cymbal Retail online store (PC)

FastAPI BFF + vanilla JS (no build step), Google Shopping visual tone. Everything user-visible is English
(`<html lang="en">`, JPY formatted `en-US`, Asia/Tokyo). Contract: [`../CONTRACT_EN.md`](../CONTRACT_EN.md) (v14);
legacy behaviour/APIs: [`../CONTRACT_JA_legacy.md`](../CONTRACT_JA_legacy.md).

| Path | Role |
|---|---|
| `app/main.py` | Routes: `/`, `/config.js`, `/api/search`, `/api/autocomplete`, `/api/ai-summary`, `/api/products`, `/api/products/{id}/stores`, `/api/stores`, `/api/home`, `/api/recommendations`, `/api/agent/stream`, `/api/voice/{stt,tts}`, `/api/features`, `/api/health` |
| `app/agent_proxy.py` | `POST /api/agent/stream` → `${ADK_AGENT_URL}/api/chat/stream` with an ID token for that audience (metadata server on Cloud Run; local fallback `gcloud auth print-identity-token`; cached). Adds `customer_id`/`customer_name`/`visitor_id` from cookies, streams SSE through unchanged; upstream failures become `error` + `done`. 503 `AGENT_NOT_CONFIGURED` without `ADK_AGENT_URL` (page then uses its scripted mock concierge). |
| `app/stores.py` | **Omnichannel availability**: `GET /api/products/{id}/stores?lat=&lng=` → `{product_id, channel, online_available, stores:[{store_id,name,short_name,address,phone,lat,lng,open_now,hours,quantity,availability,pickup,shelf_label?,distance_m?}], source}`. Sources: AI Commerce Search Local Inventory (Retail API `Product.local_inventories`) → BigQuery `cymbal.store_inventory` (+`shelf_locations`) → local files. Store master: BigQuery `cymbal.stores` → `STORES_FILE` / `data/stores_en.json` / `store-navi/data/stores.json`. Also feeds the local engine's `pickupInStore` facet. |
| `app/recommend.py` | `GET /api/recommendations?type=for_you\|buy_again\|sale\|seasonal\|new\|popular` from the local catalog + persona files (`PERSONA_DIR`, `data/personas/`, `seed/personas/`): purchase-history frequency, family `like_keywords`, allergen / bulk-size exclusion, one product per image. Tags: Frequently bought / Family favorite / On sale / In season. |
| `app/search.py` | AI Commerce Search (Retail API v2alpha `SearchService` (`servingConfigs/default_search`)): query expansion, spell correction, dynamic facets, explicit facet specs (incl. `pickupInStore`), tile navigation, conversational search. A rejected feature is disabled for 10 min and its UI data synthesized. Retail unavailable → local engine (`engine: local-fallback`). `LocalSearch` in `DATA_FILE` mode (supports `pickupInStore` facet / `pickupInStore: ANY("S01")` filter). |
| `app/ai.py` | "AI Overview" with Gemini (`GEMINI_MODEL`, default `gemini-3-flash-preview`, location `global`): summary, highlight reasons, follow-up question. Template fallback. |
| `app/catalog.py` | JSONL catalog cache (`DATA_FILE` or `data/products_retail.jsonl`) → front-end Product shape (+ `channel`: `omni` \| `store_only` \| `online_only`) |
| `app/voice.py` | `VOICE_ENABLED=1`: STT Speech-to-Text v2 `chirp_2` en-US (`VOICE_STT_LANG`), fallback chirp_3@us, then Gemini; TTS `en-US-Chirp3-HD-Aoede` (`VOICE_TTS_VOICE`). |
| `static/js/stores.js` | "Available in stores" modal, store badges, `CRClientTools.show_store_availability`, store-only cart guard |
| `static/js/*.js` | app (helpers, card, detail modal, header cross links), search, home, cart, checkout, orders, chat (SSE client + mock agent), chat_rich / chat_cards (cards in chat), voice, autocomplete, router |

## Env
`PROJECT_ID`, `PROJECT_NUMBER` (Retail resource names), `BQ_DATASET` (default `cymbal`), `ADK_AGENT_URL`,
`AGENT_BACKEND` (`adk` default / `mock`), `ENRICH_URL`, `STORE_APP_URL`, `FLYER_URL` (header 36px icon links, http(s) only),
`VOICE_ENABLED`, `DELIVERY_SPEED_SEC`, `RETAIL_LOCAL_INVENTORY` (default on), `STORE_SOURCE=local` (force local store files),
`STORES_FILE`, `STORE_INVENTORY_FILE`, `SHELF_LOCATIONS_FILE`, `PERSONA_DIR`. No project ids/URLs are hard-coded.

## Local
```bash
source ../config.env
python3 -m venv /tmp/cr_en && /tmp/cr_en/bin/pip install -r requirements-dev.txt -r requirements.txt && /tmp/cr_en/bin/playwright install chromium
# offline engine with the small English fixture (catalog, stores, inventory, persona)
DATA_FILE=$PWD/testdata/products_en.jsonl STORES_FILE=$PWD/testdata/stores.json \
  STORE_INVENTORY_FILE=$PWD/testdata/store_inventory.jsonl PERSONA_DIR=$PWD/testdata/personas AI_MOCK=1 \
  STORE_APP_URL=https://example.com/store /tmp/cr_en/bin/uvicorn app.main:app --port 8090
# live chat path without Gemini (repo root): fake agent, then ADK_AGENT_URL=http://127.0.0.1:8091 for the site
/tmp/cr_en/bin/uvicorn tests.fake_adk_agent:app --port 8091
# live Retail / BigQuery
GOOGLE_CLOUD_QUOTA_PROJECT=$PROJECT_ID /tmp/cr_en/bin/uvicorn app.main:app --port 8081
# tests
/tmp/cr_en/bin/python -m pytest ../tests/test_site_api.py ../tests/test_voice_api.py
/tmp/cr_en/bin/python ../tests/e2e_site_en.py --base-url http://127.0.0.1:8090 --out /tmp/shots
```

## Deploy
`./deploy.sh` (owner: infra) → Cloud Run `cymbal-site` (asia-northeast1, public) with `AGENT_BACKEND=adk`, `VOICE_ENABLED=1`,
`ADK_AGENT_URL`, `ENRICH_URL`, `STORE_APP_URL` from `../config.env`. Runtime SA needs `run.invoker` on `cymbal-adk-agent`,
Retail viewer, BigQuery dataViewer/jobUser (stores / store_inventory), Speech/TTS.

- Product images are always served by the site: `/img/products/<file>` (absolute catalog URIs rewritten in
  `catalog.local_image` and, for cached carts / agent payloads, `CR.localImg`).
- The GECX / CES bridge path of the Japanese demo has been removed; the ADK agent is the only live backend.
