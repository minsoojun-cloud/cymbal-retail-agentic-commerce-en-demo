# AI Commerce Search features: what works on `<PROJECT_ID>` (probed 2026-11-04)

Base: `https://retail.googleapis.com/v2alpha/projects/<PROJECT_NUMBER>/locations/global/catalogs/default_catalog`
Headers: `Authorization: Bearer $(gcloud auth print-access-token)`, `X-Goog-User-Project: <PROJECT_ID>`.
The runtime SA `cymbal-runtime@…` has `roles/retail.editor`.
Re-run probes: `source config.env && python3 shared/catalog/setup_search_features.py --steps probe`. Raw responses go to the
catalog scratch dir (`probe_*.json`).

| Feature | Status | Notes |
|---|---|---|
| Search `servingConfigs/default_search:search` | ✅ works | 21,130 products (2026-11-11 supermarket expansion; was 8,807), live right after import (e.g. フライパン 15, お弁当 71, ドッグフード 20, 保湿 クリーム 16, プレゼント 38, 新米 13) |
| Static facets (`facetSpecs`) | ✅ works | `categories`, `brands`, `price` intervals, and `attributes.top_category` / `on_sale` / `storage_type` / `labels` / `brand_line` / `material` / `color` / `origin` all return values |
| Dynamic facets | ⚠️ accepted, nothing returned yet | The serving config has `dynamicFacetSpec.mode=ENABLED`, and the request field is accepted. No `dynamicFacet:true` facets come back yet, because the model needs search traffic and indexing. Use static `facetSpecs` in the demo |
| Filter | ✅ works | `attributes.top_category: ANY("キッチン・調理器具") AND price: IN(1000.0, 5000.0)` |
| `conversationalSearchSpec` (inside `:search`) | ⚠️ accepted, no result | No error, but the response has no `conversationalSearchResult`. Generative questions can't be enabled yet: `PATCH generativeQuestionFeature {featureEnabled:true}` → 400 "data requirements aren't met" (0 generated questions) |
| **`:conversationalSearch` RPC (separate endpoint)** | ✅ **works** | Multi-turn query refinement in Japanese (`refinedSearch[].query`). `conversationalTextResponse` / `followupQuestion` come back **in English** (there is no languageCode field; it is rejected) |
| `tileNavigationSpec` | ⚠️ accepted, empty | The response always has `"tileNavigationResult": {}`. Build tiles client-side from facet values (for example, the top `categories` level-3 values) |
| Autocomplete `:completeQuery` | ❌ blocked (custom data) | The `completionData:import` from BQ needs `cloud-retail-customer-data-access@system.gserviceaccount.com` to have BigQuery Data Viewer. The grant fails with "One or more users named in the policy do not belong to a permitted customer" (org policy `iam.allowedPolicyMemberDomains`). `cloud-retail` dataset: empty (no user events). **Fallback: `data/autocomplete_suggestions.jsonl`** |
| Spell / kana | ⚠️ | `ふらいぱん` (hiragana) → 0 results, no correction. Katakana and kanji queries work |

`completionConfig` is set to `{matchingOrder: "out-of-order", maxSuggestions: 10, minPrefixLength: 1}`.
`conversationalSearchCustomizationConfig.retailerDisplayName` is set to "Cymbal Retail".

## 1. Search with facets: working request

`POST {base}/servingConfigs/default_search:search`
```json
{
  "query": "お弁当",
  "visitorId": "v-7f3a9c21",
  "userInfo": {"userId": "CR-0001"},
  "pageSize": 24,
  "offset": 0,
  "languageCode": "ja",
  "filter": "attributes.on_sale: ANY(\"true\")",
  "facetSpecs": [
    {"facetKey": {"key": "categories"}, "limit": 10},
    {"facetKey": {"key": "brands"}, "limit": 10},
    {"facetKey": {"key": "attributes.top_category"}},
    {"facetKey": {"key": "attributes.on_sale"}},
    {"facetKey": {"key": "attributes.storage_type"}},
    {"facetKey": {"key": "price", "intervals": [{"maximum": 300}, {"minimum": 300, "maximum": 1000},
                                                 {"minimum": 1000, "maximum": 3000}, {"minimum": 3000}]}}
  ],
  "dynamicFacetSpec": {"mode": "ENABLED"},
  "spellCorrectionSpec": {"mode": "AUTO"},
  "queryExpansionSpec": {"condition": "AUTO", "pinUnexpandedResults": true}
}
```
Response (real excerpt):
```json
{"results": [{"id": "4981294385812", "product": {"name": "projects/<PROJECT_NUMBER>/locations/global/catalogs/default_catalog/branches/0/products/4981294385812"}}],
 "facets": [
   {"key": "attributes.top_category", "values": [{"value": "お惣菜・ミールキット", "count": "27"}, {"value": "キッチン・調理器具", "count": "13"}]},
   {"key": "price", "values": [{"interval": {"maximum": 300}, "count": "18"}, {"interval": {"minimum": 300, "maximum": 1000}, "count": "34"}]}],
 "totalSize": 71, "attributionToken": "…", "nextPageToken": "…", "tileNavigationResult": {}}
```
> [!IMPORTANT]
> - `results[]` contain **only `id`**; no product fields come back. Hydrate them from `data/products_retail.jsonl` or BQ `cymbal.products` (keyed by `id`).
> - Facet `count` is a **string**.
> - `totalSize` is **absent** when there are 0 results, and `correctedQuery` is absent unless a correction was made.
> - `queryExpansionInfo` can appear (seen for プレゼント).

Filter syntax used by facets:
- Text attribute: `attributes.top_category: ANY("ペット用品")`
- Categories: `categories: ANY("キッチン・調理器具 > フライパン・鍋 > フライパン")`
- Brands: `brands: ANY("Cymbal Home")`
- Price: `price: IN(1000.0, 3000.0)` (also `IN(*, 300.0)` or `IN(3000.0, *)`)
- Combine conditions with `AND` / `OR`.

Exclude shrimp items for the persona: `NOT attributes.allergens: ANY("えび")`. Note that `allergens` is only set on food items.

## 2. Conversational search: working (separate RPC)

`POST {base}/servingConfigs/default_search:conversationalSearch`. The response body is a **JSON array** (stream chunks). Use the **last** element (`state: "SUCCEEDED"`).

Turn 1:
```json
{"query": "焦げ付きにくいフライパンが欲しい", "visitorId": "v-7f3a9c21",
 "conversationalFilteringSpec": {"enableConversationalFiltering": true}}
```
→ `{"conversationId": "e4d85e7f-…", "refinedSearch": [{"query": "焦げ付きにくいフライパン"}], "userQueryTypes": ["SIMPLE_PRODUCT_SEARCH"]}`

Turn 2 (reuse `conversationId`):
```json
{"query": "IH対応で26cmくらい", "visitorId": "v-7f3a9c21", "conversationId": "e4d85e7f-…",
 "conversationalFilteringSpec": {"enableConversationalFiltering": true}}
```
→ `{"refinedSearch": [{"query": "IH対応の26cm焦げ付きにくいフライパン"}], "userQueryTypes": ["SIMPLE_PRODUCT_SEARCH"]}`

Intent-type example (`userQueryTypes: ["INTENT_REFINEMENT"]`) for 「今夜の夕飯に鍋をしたいので材料を教えて」:
```json
{"conversationId": "8f31…", "refinedSearch": [{"query": "鍋つゆ"}, {"query": "長ねぎ"}, {"query": "きのこ類"}, {"query": "豚肉薄切り"}, {"query": "鶏団子"}, {"query": "うどん"}],
 "conversationalTextResponse": "For a delicious hot pot dinner, … (English)",
 "followupQuestion": {"followupQuestion": "Would you prefer a soy sauce-based broth or a miso-based broth …? (English)"},
 "state": "SUCCEEDED"}
```
Other request fields:
- `searchParams {filter, canonicalFilter, sortBy, boostSpec}`
- `userInfo`, `pageCategories`, `safetySettings`
- `conversationalFilteringSpec.userAnswer {textAnswer | selectedAnswer{productAttributeValue{name,value}}}`, and `conversationalFilteringMode`

Response fields:
- `conversationId`, `refinedSearch[].query`, `conversationalTextResponse`
- `followupQuestion {followupQuestion, suggestedAnswers[].productAttributeValue}`
- `conversationalFilteringResult {followupQuestion, additionalFilter.productAttributeValue}`
- `state`, `userQueryTypes`

**Suggested site mapping** to the CONTRACT `conversation` object:
- `id` = `conversationId`
- `refined_query` = `refinedSearch[0].query`. Run a normal `:search` with this query to get the results.
- `followup_question` = `followupQuestion.followupQuestion`. It is English, so translate it with Gemini or show a Japanese template.
- `suggested_answers` = the `suggestedAnswers` values, or the other `refinedSearch[].query` values as chips (all Japanese).

In-search variant (accepted, no result until generative questions can be enabled):
```json
"conversationalSearchSpec": {"followupConversationRequested": true, "conversationId": "", "userAnswer": {"textAnswer": "…"}}
```
Response field when it works: `conversationalSearchResult {conversationId, refinedQuery, followupQuestion, suggestedAnswers[].productAttributeValue, additionalFilter}`.

## 3. Tile navigation

Request: `"tileNavigationSpec": {"tileNavigationRequested": true, "appliedTiles": [<Tile>]}`
Response: `tileNavigationResult.tiles[] {productAttributeValue{name,value} | productAttributeInterval{name,interval}, representativeProductId}`

The response is currently always `{}`. Recommended approach: build tiles from the `categories` facet, using the last path segment as the label and `categories: ANY("<full path>")` as the filter.

## 4. Autocomplete

- Suggestions table: BQ `cymbal.autocomplete_suggestions`, 5,312 rows. Schema: `suggestion STRING, globalScore FLOAT, frequency INTEGER, languageCode STRING`.
- Local copy: `data/autocomplete_suggestions.jsonl` (`{"suggestion", "score"}`, sorted by score). It includes the demo queries お弁当 冷凍, フライパン, プレゼント, 新米, ドッグフード, 保湿 クリーム, sub-category names, brands, and short product titles.
- Site fallback: substring/prefix match on this file, top 10.

If the org policy is lifted, run `python3 shared/catalog/setup_search_features.py --steps complete`, then:
`GET {base}:completeQuery?query=フラ&visitorId=v1&languageCodes=ja&dataset=user-data&maxSuggestions=8`
→ `{"completionResults": [{"suggestion": "フライパン"}], "attributionToken": "…"}`
(today it returns `{}` for both `user-data` and `cloud-retail`).
