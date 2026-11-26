# API Contract (frontend ⇔ backend)

All responses are JSON (UTF-8). All labels, reasons and messages are English.
Model selection: send `X-Enrich-Model: <model id>` (or `?model=`) with any request; unknown / unavailable ids fall
back to the default model.

## Cost fields (USD)

Unit prices are USD per 1M tokens (official Vertex AI list price, Standard / Global / ≤200K tier). **Every cost the API
returns has a `*_usd` field** (primary, show this) and a `*_jpy` sibling (= USD × `usd_jpy`, env `USD_JPY`, default 150).
Thinking tokens are billed at the output rate. A model whose price is not on the official page returns `null` costs.

| Where | USD fields | JPY reference fields |
|---|---|---|
| `GET /api/models` → `models[]`, `GET /api/config` → `models[]` | `price_in_usd_per_mtok`, `price_out_usd_per_mtok` (also legacy `price_in`, `price_out`, same USD values) | — |
| `GET /api/cost/models` → `models[].price` | `input_usd_per_mtok`, `output_usd_per_mtok`, `periods[].input_usd_per_mtok`, `periods[].output_usd_per_mtok` | — |
| `GET /api/cost/models` → `models[].estimate` | `per_sku_usd`, `total_usd`, `breakdown.{input,output,thinking}.usd` | `per_sku_jpy`, `total_jpy`, `breakdown.*.jpy`, `usd_jpy` |
| `GET /api/cost/estimate` | `per_sku_usd`, `total_usd` | `per_sku_jpy`, `total_jpy` |
| `GET /api/dashboard` → `cost` | `per_sku_usd`, `total_usd` | `per_sku_jpy`, `total_jpy`, `usd_jpy` |
| Live runs / CSV enrich results / docs re-enrich / batch jobs | `cost_usd` | `cost_jpy` |

Backend helper: `app.pricing.cost_fields(input_tokens, output_tokens_incl_thinking, model, usd_jpy)` →
`{"cost_usd": float|null, "cost_jpy": float|null}`.

## Common: Record object (`rec`)
One extracted attribute or feature tag of a SKU.
```json
{
  "sku": "4909270706758",
  "kind": "attribute",              // "attribute" | "tag"
  "attr_key": "net_weight_g",       // tags: "tag:Boneless, easy to eat"
  "label": "Net weight",            // English label (tags: tag name)
  "raw_value": "3 slices (approx. 240 g)", // Gemini transcription (as written in source)
  "value": 240.0,                   // normalized value (number | string | bool | null)
  "unit": "g",
  "norm_note": "Took the approximate weight in grams",
  "evidence": "3 slices (approx. 240 g)", // snippet copied from source text (highlight this in source_text)
  "source_field": "description",    // title | description | attributes | ""
  "self_conf": 0.95,                // model self-reported
  "evidence_match": 1.0,            // 1.0 exact / 0.8 fuzzy / 0.5 raw value only / 0 not found
  "token_conf": 0.97,               // exp(avgLogprobs) or null
  "score": 0.97,                    // composite
  "gate_status": "auto_approved",   // auto_approved | needs_review | rejected | approved | edited | human_rejected
  "gate": "gate2",                  // gate1 (rules) | gate2 (score)
  "reason": "Composite score 0.97 >= 0.90",
  "job_id": "live-20260930-...",
  "model": "gemini-3.8-flash",
  "created_at": "2026-09-30T02:40:00+00:00",
  "reviewed": {"action": "approve", "comment": "", "at": "..."}   // optional, only after HITL
}
```
Status display: auto_approved=🟢 Auto-approved, approved=🟢 Approved (human), edited=🔵 Edited, needs_review=🟡 Needs review,
rejected=🔴 Rejected (rule), human_rejected=⚫ Rejected (human).

## GET /api/config
```json
{"threshold":0.9,"model":"gemini-3.8-flash","default_model":"gemini-3.8-flash","batch_model":"gemini-3.8-flash",
 "models":[{"id":"gemini-3.8-flash","label":"Gemini 3.8 Flash","status":"available","price_in_usd_per_mtok":1.5,"price_out_usd_per_mtok":7.5,...}],
 "pricing_url":"https://cloud.google.com/vertex-ai/generative-ai/pricing","price_retrieved_at":"2026-11-11","usd_jpy":150,
 "groups":{"fresh":"Fresh (produce, meat, fish)","prepared":"Deli, frozen & staples","grocery":"Packaged food & drinks","kitchen":"Kitchen & cookware","living":"Household goods","beauty":"Beauty","pet":"Pet supplies"},
 "feature_tags":{"fresh":["In season","Domestic",...],...}, "allergens_28":["shrimp","crab",...], "store_url":"<env STORE_URL>",
 "weights":{"evidence":0.5,"self":0.3,"token":0.2}}
```

## GET /api/dashboard
```json
{
  "totals":{"skus":100,"processed":100,"attributes":1234,"tags":456,"auto_approved":1000,"needs_review":150,"rejected":5,"human_approved":20,"human_rejected":3},
  "fill_rate":{"before":0.42,"after":0.88},
  "auto_approve_rate":0.83,
  "by_group":[{"group":"fresh","label":"Fresh (produce, meat, fish)","skus":9,"processed":9,"fill_before":0.47,"fill_after":0.69,"auto_rate":0.86,"needs_review":8,"avg_tags_after":3.2}],
  "funnel":{"source_skus":100,"extracted":1234,"normalized":1200,"gate1_passed":1190,"auto_approved":1000,"needs_review":150,"rejected":5,"golden_skus":80},
  "cost":{"basis":"List price (no promotion applied)","model":"gemini-3.8-flash","price_available":true,
          "per_sku_usd":0.0021,"per_sku_jpy":0.315,"usd_jpy":150,"skus_measured":42,"runs":42,
          "avg_input_tokens":4000,"avg_output_tokens":600,"avg_thinking_tokens":200},
  "recent_reviews":[{"sku":"...","attr_key":"...","action":"approve","reviewed_at":"..."}]
}
```

## GET /api/products?group=&status=&q=
`status` ∈ `completed` | `needs_review` | `not_processed`.
```json
{"items":[{"sku":"...","title":"...","brand":"...","group":"prepared","group_label":"Deli, frozen & staples","image_url":"https://...","price":354,
           "status":"needs_review","fill_before":0.38,"fill_after":0.92,"attr_count":14,"auto_approved":12,"needs_review":2,
           "rejected":0,"tags":["Microwavable","Good for lunch boxes"]}],
 "total":100}
```
`price` is the catalog price in JPY (tax included), shown as `¥354`.

## GET /api/products/{sku}
```json
{
  "product":{"sku":"...","title":"...","brand":"...","category":"Frozen food > Frozen rice > ...","description":"...Net content: 450 g / Origin: Made in Japan / Storage: Frozen / Allergens: shrimp, wheat, milk","product_url":"<storefront product URL>",
             "image_url":"https://...","price":354,"group":"prepared","group_label":"Deli, frozen & staples","raw_attributes":{"size":"450g","storage_type":"Frozen","allergens":"shrimp / wheat / milk"}},
  "source_text":"[Title] ...\n[Description] ...\n[Attributes]\nsize: 450g ...",
  "targets":[{"key":"net_weight_g","label":"Net weight","unit":"g","type":"number","raw_keys":["size"]}],
  "records":[rec, ...],
  "extras":{"search_keywords":["shrimp pilaf","frozen pilaf",...],"ai_summary":"..."},
  "gmc":{...},
  "status":"needs_review","fill_before":0.38,"fill_after":0.92
}
```
`targets` = all golden attributes for the category (show missing ones as grey "Not extracted" cards).

## POST /api/products/{sku}/enrich  (live run, Server-Sent Events)
Response `Content-Type: text/event-stream`; each message `data: {json}\n\n`. Use `fetch()` + ReadableStream reader (POST).
```
{"event":"start","sku":"...","model":"gemini-3.8-flash"}
{"event":"stage","stage":"stage1","status":"running"}          // Gemini extraction
{"event":"stage","stage":"stage3","status":"running"}          // tags, in parallel
{"event":"stage","stage":"stage1","status":"done","latency_ms":4200,"input_tokens":1500,"output_tokens":600,"count":14}
{"event":"stage","stage":"stage3","status":"done","latency_ms":3900,"count":5}
{"event":"stage","stage":"normalize","status":"done","count":14,"failed":1}   // unit normalization
{"event":"stage","stage":"gate","status":"done","summary":{"auto_approved":11,"needs_review":3,"rejected":0}}
{"event":"result","detail":{...same as GET /api/products/{sku}...}}
{"event":"error","message":"..."}
```

## CSV Live Test Endpoints
- `GET /api/csv/sample.csv` — Download sample 3-product CSV file.
- `GET /api/csv/sample` — Returns `{"items": [...], "count": 3, "columns": [...], "csv_text": "..."}`.
- `POST /api/csv/parse` — Body: `{"csv_text": "..."}` → returns parsed products with auto-detected category `group` and `fill_before`.
- `POST /api/csv/enrich` — SSE stream (`text/event-stream`) running the selected model (Stage 1 + Stage 3 + GMC Conversational Attributes) per CSV row and saving to BigQuery (`source_products`, `enriched_attributes`, `sku_extras`, `golden_records`, `gmc_conversational`). Per-row and final results carry `input_tokens`, `output_tokens`, `cost_usd`, `cost_jpy`.
- `POST /api/csv/save-bq` — Body: `{"skus": ["..."]}` → explicitly persists SKUs to all 5 BigQuery tables.

## Google Merchant Center Conversational Attributes Endpoints
- `GET /api/gmc` — Lists per-SKU GMC conversational attribute summaries (`items`).
- `GET /api/gmc/{sku}` — Returns `{"product": {...}, "source_text": "...", "gmc": {...}, "feed_row": {...}}`.
- `POST /api/gmc/{sku}/generate` — Runs the selected model to regenerate GMC conversational attributes for `{sku}` and saves to BigQuery `cymbal_enrichment.gmc_conversational`.
- `PUT /api/gmc/{sku}` — Saves human edits to `item_group_id`, `item_group_title`, `variant_options`, `questions_and_answers`, `product_highlights`, `related_products`, `popularity_rank`, and `document_link`, recomputing GMC feed strings and persisting to BigQuery.
- `GET /api/gmc/export.tsv` / `GET /api/gmc/export.csv` — Exports GMC Supplemental Feed in TSV / CSV format.

## GET /api/reviews?group=
HITL queue (records with gate_status == needs_review), most uncertain first.
```json
{"items":[{"rec":rec,"product":{"sku":"...","title":"...","image_url":"...","group_label":"Fresh (produce, meat, fish)"},"source_text":"..."}],"count":42}
```

## POST /api/reviews/{sku}/{attr_key}
Body: `{"action":"approve"|"edit"|"reject","value":<new value for edit>,"comment":"..."}` (attr_key must be URL-encoded,
e.g. `tag%3ABoneless%2C%20easy%20to%20eat`). For `allergens`, edit values are validated against the 28 allergens
(comma-separated lowercase, e.g. `shrimp, wheat`, or `none`); approving an empty placeholder returns 400.
```json
{"record":rec,"remaining":41}
```

## GET /api/batch/jobs
```json
{"jobs":[{"job_id":"projects/.../batchPredictionJobs/123","display_name":"enrich-20260930-0240","model":"gemini-3.8-flash",
          "state":"JOB_STATE_SUCCEEDED","sku_count":100,"request_count":300,"input_tokens":150000,"output_tokens":60000,
          "cost_usd":0.675,"cost_jpy":101.25,"created_at":"...","updated_at":"...","ingested":true}]}
```
`cost_usd` is the list (standard online) price for the job's tokens; no batch discount is applied.
## POST /api/batch/jobs
Body: `{"group":null|"fresh","limit":10}` → `{"job":{...}}` (submits a real Vertex AI Batch Prediction job; takes minutes).
## POST /api/batch/jobs/refresh
Polls running jobs, ingests finished ones → `{"jobs":[...]}`

## GET /api/models?refresh=
```json
{"models":[{"id":"gemini-3.8-flash","label":"Gemini 3.8 Flash","tier":"flash","status":"available","reason":"","latency_ms":820,
            "checked_at":1790000000.0,"default":true,"batch":true,"batch_note":"","priced":true,
            "price_in":1.5,"price_out":7.5,"price_in_usd_per_mtok":1.5,"price_out_usd_per_mtok":7.5}],
 "default":"gemini-3.8-flash","selected":"gemini-3.8-flash"}
```
`status` ∈ `available` | `unavailable` | `probing`; `reason` e.g. "Busy (429)", "Not available in this project".

## GET /api/cost/models?skus=8807&usd_jpy=150&basis=list
`basis` = `list` (default, regular list price, no promotion) | `promo` (reference: time-limited promotional price for today).
```json
{"skus":8807,"usd_jpy":150,"basis":"list","as_of":"2026-11-25",
 "pricing_url":"https://cloud.google.com/vertex-ai/generative-ai/pricing","price_retrieved_at":"2026-11-11",
 "catalog_count":8807,"demo_skus":42,"sample_sku":{"sku":"...","title":"...","chars":420},
 "unavailable":[...],
 "models":[{"id":"gemini-3.8-flash", "...":"same fields as /api/models",
   "price":{"available":true,"input_usd_per_mtok":1.5,"output_usd_per_mtok":7.5,"note":"List price (no promotion applied)",
            "promo":false,"has_promo":true,"periods":[{"input_usd_per_mtok":0.75,"output_usd_per_mtok":3.75,"start":null,"end":"2026-12-31","note":"Until Dec 31, 2026: ...","promo":true}],
            "url":"https://cloud.google.com/vertex-ai/generative-ai/pricing","region":"Global"},
   "measurement":{"runs":3,"skus":3,"input_tokens":4000,"output_tokens":600,"thinking_tokens":200,"only_sample":false,...},
   "estimate":{"skus":8807,"tokens":{"input":35228000,"output":5284200,"thinking":1761400},"price_available":true,
               "per_sku_usd":0.012,"per_sku_jpy":1.8,"total_usd":105.68,"total_jpy":15852.6,"usd_jpy":150,
               "breakdown":{"input":{"usd":52.84,"jpy":7926.3},"output":{"usd":39.63,"jpy":5944.8},"thinking":{"usd":13.21,"jpy":1981.6}}}}],
 "cheapest":"gemini-3.5-flash-lite"}
```
Models without an official price: `price.available=false`, `price.note="See the official pricing page"`, estimate costs `null`.

## GET /api/cost/estimate?skus=200000
List-price estimate for the selected model.
```json
{"skus":200000,"model":"gemini-3.8-flash","total_usd":2400.0,"per_sku_usd":0.012,"total_jpy":360000,"per_sku_jpy":1.8,
 "price_available":true,"measurement":{...},"basis":"List price (no promotion applied)","pricing_url":"https://cloud.google.com/vertex-ai/generative-ai/pricing"}
```

## POST /api/cost/measure  (SSE)
Body: `{"model":"gemini-3.5-flash-lite"}` → runs the 3 calls once on the representative sample SKU and stores the tokens
(enrichment results are not overwritten). Events: `start` (`sku`, `model`), `stage`, `result`, `error`. 400 for ineligible models.

## GET /api/golden?group= / GET /api/golden/export.csv
```json
{"items":[{"sku":"...","title":"...","group":"fresh","attributes":{"net_weight_g":{"value":240,"unit":"g","score":1.0,"status":"auto_approved"}},
           "feature_tags":["Boneless, easy to eat"],"search_keywords":[...],"ai_summary":"...","fill_rate_before":0.38,"fill_rate_after":0.92,"updated_at":"..."}]}
```
