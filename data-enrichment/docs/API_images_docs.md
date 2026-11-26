# API contract: (1) Seasonal Image Studio (2) Extract from Documents

All JSON (UTF-8). SSE endpoints are `POST` + `text/event-stream`; each line is `data: {json}\n\n` and the `event` field carries the event type (same format as the existing `/api/products/{sku}/enrich`; the frontend uses the existing `postStream()`).
Errors are `{"detail": "..."}` (HTTP 4xx/5xx). All user-visible strings (labels, reasons, messages) are English.

Implementation: [routes_images.py](../app/routes_images.py), [image_presets.py](../app/image_presets.py), [image_gen.py](../app/image_gen.py), [image_search.py](../app/image_search.py), [routes_docs.py](../app/routes_docs.py), [doc_extract.py](../app/doc_extract.py).

---

## (1) Seasonal Image Studio

### `GET /api/images/presets?sku={sku}`
```json
{
  "sku": "4913667472959",
  "product": {"sku": "...", "title": "Ibaraki Napa Cabbage, 1/2 cut", "brand": "", "group": "fresh", "group_label": "Fresh (produce, meat, seafood)",
              "image_url": "https://<SITE_BASE_URL>/img/products/....jpg", "has_image": true, "category": "..."},
  "presets": [
    {"id": "winter-nabe", "label": "Winter Hot Pot", "season": "winter", "season_label": "Winter", "icon": "🍲",
     "available": true, "reason": "", "evidence": "Approved tag \"Great for hot pot\"",
     "facts": [{"label": "Net weight", "value": "500 g"}],
     "prompt": "A cozy winter evening: a family gathered around a steaming clay hot pot..."},
    {"id": "summer-bbq", "label": "Summer: BBQ", "season": "summer", "season_label": "Summer", "icon": "🍖",
     "available": false, "reason": "No evidence of barbecue / grilling use for this product", "evidence": "", "facts": [], "prompt": "..."}
  ],
  "options": {
    "models": [{"id": "gemini-3.1-flash-image", "label": "Flash (fast, standard)"}, {"id": "gemini-3-pro-image", "label": "Pro (high quality, best for text)"}],
    "default_model": "gemini-3.1-flash-image",
    "sizes": [{"value": 300, "label": "300×300"}, {"value": 600, "label": "600×600"}, {"value": 1024, "label": "1024×1024"}, {"value": 0, "label": "Original size"}],
    "default_size": 600,
    "aspects": ["1:1", "4:3", "3:4", "16:9"], "default_aspect": "1:1",
    "text_modes": [{"id": "none", "label": "No text"}, {"id": "copy", "label": "Tagline only"}, {"id": "spec", "label": "With specs"}],
    "default_text_mode": "none",
    "counts": [2, 3, 4], "default_count": 3,
    "bucket": "products_images_<PROJECT_ID>"
  }
}
```
- `presets` contains only the presets relevant to the product's category group (plus the all-category ones). `available=false` is shown greyed out in the UI with `reason` as tooltip.
- Availability is rule-based (no LLM): approved feature tag (case-insensitive) → `on_sale` / `promo_text` → season months → approved numeric attribute → keyword evidence in the source text (category path excluded, word-start anchored).
- `size`: square output is `size×size`; other aspect ratios use long edge = `size`. `0` keeps the model output size.

Preset ids (stable; stored in image rows and GCS object names):

| id | label | season | evidence (any of) |
|---|---|---|---|
| `spring-sakura` | Spring: Cherry Blossom | spring | months Mar-Apr, tags Good for bento / Entertaining, keywords (cherry blossom, picnic, bento...) |
| `newlife` | Spring: New Life | newlife | tags Space-saving / Stackable / Time-saver / Single-use size / Good for bento, keywords |
| `summer-cool` | Summer: Cool Treats | summer | months Jun-Aug, tag Cooling, keywords (serve chilled, iced, somen...) |
| `summer-bbq` | Summer: BBQ | summer | tag Great for BBQ, keywords (BBQ, grill, yakiniku...) |
| `autumn-taste` | Autumn Harvest | autumn | months Sep-Nov, tag In season, keywords |
| `halloween` | Halloween | autumn | tags Party / Kid favorite, keywords (Halloween, pumpkin, candy...) |
| `winter-nabe` | Winter Hot Pot | winter | tag Great for hot pot, keywords (hot pot, nabe, shabu-shabu, sukiyaki, oden...) |
| `christmas` | Christmas | winter | tags Party / Entertaining / Gift, keywords |
| `yearend` | New Year | yearend | months Dec-Jan, tags Entertaining / Gift / Party, keywords |
| `quick-dinner` | Weeknight: Quick Dinner | all | tags Time-saver / Microwavable / Just heat, `cook_time_min` ≤ 15, keywords |
| `ec-white` | E-commerce: White Background | all | always |
| `lifestyle` | Lifestyle | all | always |
| `sale` | Sale | all | `on_sale=true`, `promo_text`, keywords (sale, special offer...) |

### `GET /api/products/{sku}/images`
```json
{
  "sku": "...",
  "registered": [{"url": "https://<SITE_BASE_URL>/img/products/....jpg", "kind": "original", "label": "Registered image (catalog)"}],
  "images": [ImageRow, ...]   // status != deleted, newest first
}
```
`ImageRow`:
```json
{"image_id": "4913667472959_winter-nabe_20261202-143012_1", "sku": "4913667472959",
 "gcs_uri": "gs://products_images_<PROJECT_ID>/4913667472959/4913667472959_winter-nabe_20261202-143012_1.jpg",
 "url": "/api/img/4913667472959/4913667472959_winter-nabe_20261202-143012_1.jpg",
 "kind": "generated", "status": "candidate|saved|deleted", "is_primary": false,
 "preset": "winter-nabe", "preset_label": "Winter Hot Pot", "season": "winter",
 "prompt": "...", "extra_prompt": "...", "base_image_uri": "...", "base_kind": "registered|gcs|upload|web|none", "source_url": "",
 "model": "gemini-3.1-flash-image", "width": 300, "height": 300, "aspect": "1:1",
 "fidelity_score": 0.92, "fidelity_note": "Same product, no text.",
 "created_at": "2026-12-02T05:30:12+00:00", "updated_at": "..."}
```
- `preset_label` for multiple presets is joined with `" + "`; with no preset it is `"Custom"`.
- `fidelity_note` is English; `"No base image (illustrative image)"` when generated without a base image, `"Offline (check skipped)"` in offline mode.

### `POST /api/products/{sku}/images/upload` (multipart, field `file`)
Upload a base image (jpg/png/webp, ≤ 10 MB). → `{"base": {"type": "upload", "uri": "gs://.../{sku}/base/{sku}_base_{ts}.jpg", "url": "/api/img/{sku}/base/...", "width": 800, "height": 800}}`

### `POST /api/products/{sku}/images/search`
body `{}` → finds product image candidates with Gemini + Google Search grounding (a few seconds to ~20 s).
```json
{"query": "Cymbal Basic Frozen Carbonara 260g", "candidates": [{"url": "https://maker.example/img.jpg", "page_url": "https://maker.example/product", "title": "Page title", "width": 800, "height": 800, "via": "grounding"}], "message": ""}
```
`message` explains an empty result (e.g. `"No usable product image was found on the web. ..."`).

### `POST /api/products/{sku}/images/generate` (SSE)
body:
```json
{"base": {"type": "registered|gcs|upload|web|none", "uri": "https://... | gs://..."},
 "presets": ["winter-nabe"], "extra_prompt": "Use a Japanese-style tatami room",
 "n": 3, "aspect": "1:1", "text_mode": "none", "model": "gemini-3.1-flash-image", "size": 300}
```
- `registered`: the product's registered image when `uri` is omitted. `gcs`: a `gs://` path. `upload`: the `uri` from the upload API. `web`: a search candidate `url`. `none`: no base image (text-only generation; marked as illustrative).
- Presets that are not available for the product → HTTP 400 `"Preset(s) not available for this product: ..."`.
- events:
  - `{"event":"start","n":3,"model":"...","base_url":"...","prompt":"full final prompt","facts":[...],"size":300,"aspect":"1:1"}`
  - `{"event":"progress","index":1,"status":"generating"}`
  - `{"event":"image","index":1,"image": ImageRow}` (`status=candidate`, already saved to GCS)
  - `{"event":"image_error","index":2,"message":"..."}`
  - `{"event":"complete","count":3,"elapsed_ms":18000}`
  - `{"event":"error","message":"..."}`

### `POST /api/products/{sku}/images/save`
body `{"image_ids": ["..."], "primary_id": "...|null", "update_aics": true}`
```json
{"saved": [ImageRow...],
 "bq": {"table": "<PROJECT_ID>.cymbal_enrichment.product_images", "rows": 2},
 "aics": {"table": "<PROJECT_ID>.cymbal.products", "updated": true, "images_total": 2, "backup": "products_backup_20261111", "message": ""}}
```

### `DELETE /api/products/{sku}/images/{image_id}`
→ `{"deleted": true, "image_id": "...", "gcs_deleted": true, "aics": {...}|null}` (deleting a saved image also removes it from cymbal.products)

### `GET /api/img/{path}`
Proxy for GCS objects (in the `products_images_…` bucket). Usable directly as `<img src>`.

---

## (2) Extract from Documents (#/docs)

Sample documents (fictional, regenerate with `python scripts/make_samples.py`): [samples/docs](../samples/docs)
- `spec_cymbal_frypan_20cm.pdf` - supplier spec sheet, Cymbal Basic Non-Stick Frying Pan 20 cm (JAN 4954950777214)
- `label_cymbal_carbonara.png` - food label photo, Cymbal Basic Frozen Carbonara 260 g, nutrition facts + allergen box "Allergens: wheat, egg, milk, pork"
- `price_list_2026winter_nabe.xlsx` / `.csv` - 2026 Winter Hot Pot Fair price list (4 existing JANs + 1 new JAN 4990000000017)

### `POST /api/docs/upload` (multipart, field `file`, one file per request, ≤ 30 MB)
→ `{"file": {"uri": "gs://products_images_…/_docs_uploads/20261202/{uuid}_{name}", "name": "spec.pdf", "mime": "application/pdf", "size": 123456, "url": "/api/img/_docs_uploads/..."}}`

### `POST /api/docs/list`
body `{"gcs_uri": "gs://bucket/prefix/" | "gs://bucket/file.pdf"}` → `{"files": [{"uri","name","mime","size"}], "skipped": 0}` (supported: jpg/jpeg/png/webp/pdf/xlsx/csv, up to 20 files)

### `POST /api/docs/extract` (SSE)
body `{"files": [{"uri","name","mime"}]}`
- `{"event":"start","count":2,"model":"gemini-3.8-flash"}`
- `{"event":"file_start","index":0,"name":"spec.pdf"}`
- `{"event":"file_done","index":0,"name":"spec.pdf","uri":"gs://...","mime":"...","document_type":"spec_sheet","latency_ms":8000,"input_tokens":3000,"output_tokens":800,"model":"...","products":[ExtractedProduct...]}`
- `{"event":"file_error","index":1,"name":"...","message":"..."}`
- `{"event":"complete","files":2,"products":3}`

`document_type`: `spec_sheet | catalog | price_list | label_photo | manual | other`.

`ExtractedProduct`:
```json
{"idx": 0, "model_number": "CB-FP20", "jan": "4954950777214", "sku_hint": "", "title": "Cymbal Basic Non-Stick Frying Pan 20 cm", "brand": "Cymbal Basic", "category": "Kitchen",
 "price": 1280, "description": "Original description text",
 "attributes": [{"key": "ih_compatible", "label": "Induction compatible", "value": "Induction compatible (100 V / 200 V), gas compatible", "evidence": "Induction compatible (100 V / 200 V)", "page": 1}],
 "confidence": 0.93,
 "matches": [{"sku": "4954950777214", "title": "...", "image_url": "...", "score": 0.99, "why": "JAN match"}],
 "suggested_sku": "4954950777214"}
```
- `matches[].why`: `SKU match` (1.0) | `JAN match` (0.99) | `Model number match` (0.98) | `Model number in title` (0.9) | `Model number prefix match` (0.8) | `Title similarity NN%` (0.4-0.8). `suggested_sku` is set when the top score ≥ 0.8.
- `price` is JPY tax-included (display as `¥1,280`).

### `POST /api/docs/preview`
body `{"sku": "4954950777214" | null, "new_sku": "4990000000017" | null, "product": ExtractedProduct, "mode": "fill|overwrite"}`
```json
{"sku": "...", "is_new": false,
 "diff": [{"field": "description", "label": "Description", "before": "...", "after": "...", "action": "add|overwrite|keep|new"},
          {"field": "attr:ih_compatible", "label": "Induction compatible", "before": "", "after": "Induction compatible (100 V / 200 V)", "action": "add"}],
 "changes": 3, "group": "kitchen"}
```
- `fill` (default) = only fill empty fields; `overwrite` = replace with the document value. Identity fields (title / brand / category) are never overwritten on existing products.
- Fixed diff labels: `Product name`, `Brand`, `Category`, `Price`, `Description`, `Model number`; attribute labels come from the extraction.

### `POST /api/docs/apply` (SSE)
body:
```json
{"items": [{"sku": "4954950777214", "new_sku": null, "product": ExtractedProduct, "mode": "fill",
            "file_uri": "gs://...", "file_name": "spec.pdf", "document_type": "spec_sheet"}],
 "enrich": true, "update_aics": true}
```
- `{"event":"start","count":1,"job_id":"doc-20261202143012","model":"..."}`
- `{"event":"item_start","index":0,"sku":"..."}`
- `{"event":"merged","index":0,"sku":"...","changes":3,"diff":[...]}`
- `{"event":"enriched","index":0,"sku":"...","attr_count":12,"tag_count":4,"status":"completed|needs_review","fill_before":0.4,"fill_after":0.9,"latency_ms":9000,"model":"..."}`
- `{"event":"item_error","index":0,"sku":"...","message":"..."}`
- `{"event":"saved","bq":{"dataset":"...","tables":{"…source_products":1,...,"…doc_extractions":1}}}`
- `{"event":"aics","result":{"table":"…cymbal.products","updated":1,"inserted":0,"backup":"products_backup_20261111","message":""}}`
- `{"event":"complete","results":[{"sku":"...","status":"ok|error","changes":3}],"elapsed_ms":15000}`

Document statements are kept in the product's `doc_sources` and appear in the source text under the `[Documents]` section, so they can be used as evidence by the enrichment pipeline.
