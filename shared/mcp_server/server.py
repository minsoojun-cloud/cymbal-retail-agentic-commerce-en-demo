"""Cymbal Retail Commerce MCP Server (AI Commerce Search (Retail API) + BigQuery).

Ported from greenbeens/shared/mcp_server/server.py — all GECX (CX Agent Studio) compatibility
patches are kept:
  1. loose Accept header check
  2. minimal list_tools with clean_schema (no title/default)
  3. FastMCP(stateless_http=True, json_response=True, DNS rebinding protection off), endpoint /mcp

Tools (see ../CONTRACT_EN.md):
  - search_products        : Retail search via servingConfigs/default_search (max 100 items, IN_STOCK only,
                             "under ¥N" price helper; grocery + general merchandise attributes)
  - get_product_details    : full product (description / attributes / images / rating)
  - compare_products       : comparison rows (Price/Size/Labels/Rating/Origin or Material...) ready for the
                             client-side show_comparison function
  - get_customer_profile   : BigQuery cymbal.customers (fallback: bundled personas.json)
  - get_purchase_history   : BigQuery cymbal.purchase_history (fallback: bundled JSONL)
  - recommend_dinner_set   : dinner product sets (Main/Side/Soup/Staple/Dessert & fruit) from persona + history
                             frequency + sale + seasonal items (dinner.py; catalog from BigQuery cymbal.products, cached)
  - check_store_inventory  : per-store stock for products (Retail Product.local_inventories, fallback BigQuery
                             cymbal.store_inventory) + store info (cymbal.stores, fallback bundled stores JSON)
"""
import asyncio
import datetime as dt
import inspect
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator

from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool as MCPTool

import dinner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cymbal-commerce-mcp")

# ── GECX compatibility patches ────────────────────────────────────────────────


# 1. Bypass strict Accept header validation
def loose_check_accept_headers(self, request):
    return True, True


StreamableHTTPServerTransport._check_accept_headers = loose_check_accept_headers


# 2. Return minimal tool definitions (GECX prefers standard name/description/inputSchema)
def clean_schema(schema):
    """Recursively remove fields that often cause issues in strict parsers."""
    if not isinstance(schema, dict):
        return schema
    schema.pop("title", None)
    schema.pop("default", None)
    if "properties" in schema:
        for prop in schema["properties"].values():
            clean_schema(prop)
    for _, v in list(schema.items()):
        if isinstance(v, dict):
            clean_schema(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    clean_schema(item)
    return schema


async def list_tools_minimal(self) -> list[MCPTool]:
    result = []
    for info in self._tool_manager.list_tools():
        cleaned_desc = inspect.cleandoc(info.description) if info.description else ""
        cleaned_schema = clean_schema(json.loads(json.dumps(info.parameters)))  # deep copy
        result.append(MCPTool(name=info.name, description=cleaned_desc, inputSchema=cleaned_schema))
    return result


FastMCP.list_tools = list_tools_minimal

# 3. Stateless JSON transport
mcp = FastMCP(
    "CymbalCommerceMCP",
    stateless_http=True,  # Required for GECX (stateless requests)
    json_response=True,  # JSON responses instead of SSE
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# ── Configuration ────────────────────────────────────────────────────────────
PROJECT_ID = os.getenv("PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""  # set by deploy.sh (config.env)
LOCATION = os.getenv("LOCATION", "global")
CATALOG = os.getenv("CATALOG", "default_catalog")
SERVING_CONFIG = os.getenv("SERVING_CONFIG", "default_search")
COMPACT_RESULTS = os.getenv("COMPACT_RESULTS", "false").lower() == "true"
BRANCH = os.getenv("BRANCH", "0")
BQ_DATASET = os.getenv("BQ_DATASET", "cymbal")
BQ_LOCATION = os.getenv("BQ_LOCATION", "asia-northeast1")
DEFAULT_VISITOR_ID = "mcp-agent-session"

MAX_PAGE_SIZE = 100  # requirement: max 100 products per search
SEARCH_TIMEOUT_S = 4.5  # Retail search RPC timeout
HYDRATE_BUDGET_S = 4.5  # overall budget for parallel get_product hydration
BQ_TIMEOUT_S = 8.0
IN_STOCK_FILTER = 'availability: ANY("IN_STOCK")'

_HERE = Path(__file__).resolve().parent
DATA_DIRS = [Path(os.getenv("DATA_DIR", _HERE / "data")), _HERE.parent.parent / "seed" / "personas"]

# "under ¥3,000" / "below 500 yen" / "less than JPY 1000" / "¥500 or less" / "up to 2,000 yen" / "max ¥800"
PRICE_CAP_RE = re.compile(
    r"\b(?:under|below|less than|up to|within|max(?:imum)?|no more than|cheaper than|at most)\s*"
    r"(?:¥|jpy\s*)?\s*([0-9][0-9,]*)(?:\s*(?:yen|jpy)\b)?"
    r"|(?:¥|jpy\s*)\s*([0-9][0-9,]*)\s*(?:or (?:less|under|cheaper|below)|max\b)"
    r"|\b([0-9][0-9,]*)\s*(?:yen|jpy)\s*(?:or (?:less|under|cheaper|below)|max\b)",
    re.I,
)

# ── Lazy clients (overridable in tests) ──────────────────────────────────────
_clients: dict = {}


def _search_client():
    if "search" not in _clients:
        from google.cloud import retail_v2

        _clients["search"] = retail_v2.SearchServiceClient()
    return _clients["search"]


def _product_client():
    if "product" not in _clients:
        from google.cloud import retail_v2

        _clients["product"] = retail_v2.ProductServiceClient()
    return _clients["product"]


def _bq_client():
    if "bq" not in _clients:
        from google.cloud import bigquery

        _clients["bq"] = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
    return _clients["bq"]


def get_catalog_name():
    return f"projects/{PROJECT_ID}/locations/{LOCATION}/catalogs/{CATALOG}"


def get_serving_config():
    return f"{get_catalog_name()}/servingConfigs/{SERVING_CONFIG}"


def product_name(product_id: str) -> str:
    return f"{get_catalog_name()}/branches/{BRANCH}/products/{product_id}"


# ── Helpers ──────────────────────────────────────────────────────────────────


def clamp_page_size(page_size) -> int:
    """Clamp page_size to 1..100 (requirement: max 100 products)."""
    try:
        n = int(page_size) if page_size is not None else 20
    except (TypeError, ValueError):
        n = 20
    return max(1, min(n, MAX_PAGE_SIZE))


def extract_price_cap(query: str):
    """"under ¥3,000" / "500 yen or less" → 3000 / 500 (None if absent)."""
    if not query:
        return None
    m = PRICE_CAP_RE.search(query)
    if not m:
        return None
    digits = next(g for g in m.groups() if g).replace(",", "")
    try:
        return int(digits)
    except ValueError:
        return None


def build_filter(user_filter, query: str, include_out_of_stock: bool = False):
    """Compose the Retail filter: (user filter | price-cap helper) AND IN_STOCK.

    Returns (filter_string, price_cap).
    """
    parts = []
    price_cap = None
    uf = (user_filter or "").strip()
    if uf:
        parts.append(uf)
    else:
        price_cap = extract_price_cap(query)
        if price_cap:
            parts.append(f"price: IN(*, {float(price_cap)}i)")
    if not include_out_of_stock:
        parts.append(IN_STOCK_FILTER)
    if len(parts) > 1:
        return " AND ".join(f"({p})" for p in parts), price_cap
    return (parts[0] if parts else ""), price_cap


def attrs_to_dict(attributes) -> dict:
    """Retail CustomAttribute map → plain dict (single values unwrapped)."""
    out = {}
    if not attributes:
        return out
    for k, v in attributes.items():
        text = list(getattr(v, "text", []) or [])
        nums = list(getattr(v, "numbers", []) or [])
        if text:
            out[k] = text[0] if len(text) == 1 else text
        elif nums:
            out[k] = nums[0] if len(nums) == 1 else nums
    return out


def _to_int(x):
    try:
        return int(round(float(x))) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _join(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " / ".join(str(x) for x in v if x not in (None, ""))
    return str(v)


def _rating(p):
    """Retail Product.rating → {average, count} or None."""
    r = getattr(p, "rating", None)
    try:
        if r and (r.average_rating or r.rating_count):
            return {"average": round(float(r.average_rating), 2), "count": int(r.rating_count)}
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def summarize_product(product_id: str, p) -> dict:
    """Retail Product → widget-compatible productDetails item."""
    attrs = attrs_to_dict(getattr(p, "attributes", None))
    cats = list(getattr(p, "categories", []) or [])
    subtitle = cats[0].split(">")[-1].strip() if cats else (attrs.get("top_category") or "")
    pi = getattr(p, "price_info", None)
    price = _to_int(getattr(pi, "price", None)) if pi else None
    original = _to_int(getattr(pi, "original_price", None)) if pi else None
    if not original or (price and original < price):
        original = price
    on_sale_raw = attrs.get("on_sale")
    on_sale = str(on_sale_raw).lower() == "true" if on_sale_raw is not None else bool(price and original and price < original)
    promo = attrs.get("promo_text")
    if isinstance(promo, list):
        promo = " / ".join(promo)
    labels = attrs.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]
    brands = list(getattr(p, "brands", []) or [])
    item = {
        "productId": product_id,
        "title": getattr(p, "title", "") or "",
        "subtitle": subtitle,
        "price": price or 0,
        "priceTaxExcluded": _to_int(attrs.get("price_tax_excluded")),
        "originalPrice": original or 0,
        "onSale": on_sale,
        "discountRate": float(attrs.get("discount_rate") or 0.0),
        "promoText": promo or "",
        "size": attrs.get("size") or "",
        "allergens": (" / ".join(a) if isinstance(a := attrs.get("allergens"), list) else (a or "")),
        "brand": brands[0] if brands else (attrs.get("brand_line") or ""),
        "topCategory": attrs.get("top_category") or (cats[0].split(">")[0].strip() if cats else ""),
        "channel": str(attrs.get("channel") or "omni"),  # omni | store_only (not deliverable) | online_only
        "labels": labels,
        "origin": _join(attrs.get("origin")),
        "material": _join(attrs.get("material")),
        "color": _join(attrs.get("color")),
        "capacity": _join(attrs.get("capacity")),
        "rating": _rating(p),
        "uri": getattr(p, "uri", "") or "",
        "imageUris": [img.uri for img in (getattr(p, "images", []) or []) if getattr(img, "uri", None)],
    }
    if COMPACT_RESULTS:
        # The agent never needs URLs/images (the page fetches them by ID); drop them and empty defaults to save tokens.
        for k in ("uri", "imageUris", "priceTaxExcluded", "subtitle"):
            item.pop(k, None)
        if not item["onSale"]:
            for k in ("onSale", "discountRate", "originalPrice"):
                item.pop(k, None)
        for k in ("promoText", "size", "brand", "labels", "origin", "material", "color", "capacity", "rating"):
            if not item.get(k):
                item.pop(k, None)
        if str(item.get("allergens") or "").lower() in ("", "none"):
            item.pop("allergens", None)
    return item


def _needs_hydration(p) -> bool:
    return not (
        getattr(p, "title", None)
        and getattr(p, "images", None)
        and getattr(p, "uri", None)
        and getattr(p, "attributes", None)
        and "on_sale" in getattr(p, "attributes", {})
    )


def _hydrate(product_client, product_id, p, timeout):
    try:
        name = getattr(p, "name", None) or product_name(product_id)
        return product_client.get_product(name=name, timeout=max(0.5, timeout))
    except Exception as e:  # keep partial search data
        logger.warning("get_product failed for %s: %s", product_id, e)
        return p


def _is_invalid_argument(e: Exception) -> bool:
    try:
        from google.api_core import exceptions as gexc

        if isinstance(e, gexc.InvalidArgument):
            return True
    except Exception:
        pass
    return "INVALID_ARGUMENT" in str(e) or str(e).startswith("400")


def _do_search(query, page_size, filter_str, visitor_id):
    from google.cloud import retail_v2

    req = retail_v2.SearchRequest(
        placement=get_serving_config(),
        branch=f"{get_catalog_name()}/branches/{BRANCH}",
        query=query or "",
        page_size=page_size,
        filter=filter_str or "",
        visitor_id=visitor_id or DEFAULT_VISITOR_ID,
    )
    resp = _search_client().search(request=req, timeout=SEARCH_TIMEOUT_S)
    results = []
    total = 0
    for r in resp:  # pager — first page only (page_size <= 100)
        results.append(r)
        if len(results) >= page_size:
            break
    try:
        total = int(resp.total_size)
    except Exception:
        total = len(results)
    return results, total


def strip_price_phrase(query: str) -> str:
    """"carrots under ¥300" → "carrots" (the phrase hurts text matching once it is a filter)."""
    q = PRICE_CAP_RE.sub(" ", query or "")
    q = re.sub(r"\s+", " ", q).strip()
    filler = r"(?:for|and|that|are|is|the|a|an|of|priced|costing|with|items?|products?)"
    q = re.sub(rf"^(?:[\s,;:.-]|{filler}\b)+|(?:[\s,;:.-]|\b{filler})+$", "", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


SEARCH_BQ_FALLBACK = os.getenv("SEARCH_BQ_FALLBACK", "true").lower() == "true"
_ON_SALE_RE = re.compile(r'attributes\.on_sale:\s*ANY\("true"\)')
_CAT_RE = re.compile(r'categories:\s*ANY\("([^"]+)"\)')
_STOPWORDS = {"a", "an", "the", "for", "of", "and", "with", "some", "any", "me", "please", "i", "want", "need"}
_TOKEN_SPLIT = re.compile(r"[\s,;/]+")


def _bq_row_to_product(row: dict):
    """cymbal.products row (Retail schema) → object compatible with summarize_product."""
    from types import SimpleNamespace as NS

    attrs = {}
    for a in row.get("attributes") or []:
        v = a.get("value") or {}
        attrs[a.get("key")] = NS(text=list(v.get("text") or []), numbers=list(v.get("numbers") or []))
    pi = row.get("priceInfo") or {}
    rt = row.get("rating") or {}
    return NS(
        name=product_name(row.get("id")), title=row.get("title") or "", categories=list(row.get("categories") or []),
        price_info=NS(price=pi.get("price"), original_price=pi.get("originalPrice")), attributes=attrs,
        images=[NS(uri=i.get("uri")) for i in row.get("images") or []], uri=row.get("uri") or "",
        description=row.get("description") or "", brands=list(row.get("brands") or []),
        availability=NS(name=row.get("availability") or "IN_STOCK"),
        rating=NS(average_rating=rt.get("averageRating") or 0.0, rating_count=rt.get("ratingCount") or 0),
    )


def bq_search_fallback(query: str, page_size: int, user_filter, price_cap) -> list:
    """Keyword search on BigQuery cymbal.products (used while the Retail index is still empty/lagging)."""
    from google.cloud import bigquery

    tokens = [t for t in _TOKEN_SPLIT.split(query or "") if t and t.lower() not in _STOPWORDS][:4]
    where, params = ["availability = 'IN_STOCK'"], []
    for i, t in enumerate(tokens):
        where.append(f"(STRPOS(LOWER(title), @t{i}) > 0 OR STRPOS(LOWER(ARRAY_TO_STRING(categories, ' ')), @t{i}) > 0 "
                     f"OR STRPOS(LOWER(ARRAY_TO_STRING(tags, ' ')), @t{i}) > 0)")
        params.append(bigquery.ScalarQueryParameter(f"t{i}", "STRING", t.lower()))
    if price_cap:
        where.append("priceInfo.price <= @cap")
        params.append(bigquery.ScalarQueryParameter("cap", "FLOAT64", float(price_cap)))
    uf = user_filter or ""
    if _ON_SALE_RE.search(uf):
        where.append("priceInfo.price < priceInfo.originalPrice")
    m = _CAT_RE.search(uf)
    if m:
        where.append("STRPOS(ARRAY_TO_STRING(categories, ' '), @cat) > 0")
        params.append(bigquery.ScalarQueryParameter("cat", "STRING", m.group(1)))
    sql = (f"SELECT id, title, categories, brands, description, attributes, priceInfo, rating, availability, uri, images "
           f"FROM `{PROJECT_ID}.{BQ_DATASET}.products` WHERE {' AND '.join(where)} "
           f"ORDER BY rating.ratingCount DESC LIMIT {int(page_size)}")
    return [(r["id"], _bq_row_to_product(r)) for r in _bq_query(sql, params)]


def search_products_sync(query, page_size=20, filter=None, visitor_id=None) -> dict:
    t0 = time.monotonic()
    page_size = clamp_page_size(page_size)
    filter_str, price_cap = build_filter(filter, query)
    query_cap = extract_price_cap(query)
    if query_cap:
        query = strip_price_phrase(query)
    warning = None
    try:
        results, total = _do_search(query, page_size, filter_str, visitor_id)
    except Exception as e:
        if (filter or price_cap) and _is_invalid_argument(e):
            # retry once without the user / price filter (keep IN_STOCK)
            warning = f"filter '{filter_str}' was rejected by the catalog ({str(e)[:160]}); retried without it."
            logger.warning(warning)
            filter_str, price_cap = IN_STOCK_FILTER, query_cap  # cap still post-filtered
            results, total = _do_search(query, page_size, filter_str, visitor_id)
        else:
            raise

    details = []
    if results:
        remaining = max(0.5, HYDRATE_BUDGET_S - (time.monotonic() - t0))
        pc = _product_client()
        ex = ThreadPoolExecutor(max_workers=min(len(results), 20))
        futs = {}
        for r in results:
            if _needs_hydration(r.product):
                futs[r.id] = ex.submit(_hydrate, pc, r.id, r.product, remaining)
        if futs:
            wait(list(futs.values()), timeout=remaining)
        ex.shutdown(wait=False, cancel_futures=True)
        for r in results:
            f = futs.get(r.id)
            p = f.result() if (f is not None and f.done() and not f.cancelled()) else r.product
            item = summarize_product(r.id, p)
            if price_cap and item["price"] and item["price"] > price_cap:
                continue
            details.append(item)

    source = "retail"
    if not details and SEARCH_BQ_FALLBACK and (query or "").strip():
        try:
            for pid, p in bq_search_fallback(query, page_size, filter, price_cap or query_cap):
                details.append(summarize_product(pid, p))
            total = len(details)
            source = "bigquery_fallback"
        except Exception as e:  # never fail the tool because of the fallback
            logger.warning("BQ search fallback failed: %s", e)

    out = {"productDetails": details, "totalSize": total}
    if source != "retail":
        out["_source"] = source
    if price_cap:
        out["appliedPriceCap"] = price_cap
    if warning:
        out["warning"] = warning
    return out


# ── BigQuery / bundled-data helpers ──────────────────────────────────────────


def _find_data_file(name: str):
    for d in DATA_DIRS:
        p = Path(d) / name
        if p.exists():
            return p
    return None


def _load_personas() -> list:
    p = _find_data_file("personas.json")
    if not p:
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def _load_history_rows() -> list:
    p = _find_data_file("purchase_history.jsonl")
    if not p:
        return []
    with p.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _bq_query(sql: str, params: list) -> list:
    from google.cloud import bigquery

    job = _bq_client().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [dict(row.items()) for row in job.result(timeout=BQ_TIMEOUT_S)]


def _maybe_json(v):
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def get_customer_profile_sync(customer_id: str) -> dict:
    from google.cloud import bigquery

    try:
        rows = _bq_query(
            f"SELECT * FROM `{PROJECT_ID}.{BQ_DATASET}.customers` WHERE customer_id = @cid LIMIT 1",
            [bigquery.ScalarQueryParameter("cid", "STRING", customer_id)],
        )
        if rows:
            prof = {k: _maybe_json(v) for k, v in rows[0].items()}
            # profile_json (full persona JSON written by seed/personas/load_to_bq.sh) wins over autodetected columns
            pj = prof.pop("profile_json", None)
            if isinstance(pj, dict):
                prof.update(pj)
            # fill fields missing in an older BQ table from the bundled persona (e.g. family_preferences)
            for bundled in _load_personas():
                if bundled.get("customer_id") == customer_id:
                    for k, v in bundled.items():
                        if prof.get(k) in (None, "", []):
                            prof[k] = v
            prof["_source"] = "bigquery"
            return prof
        logger.info("customer %s not found in BQ; trying bundled personas", customer_id)
    except Exception as e:
        logger.warning("BQ customers query failed (%s); using bundled personas.json", e)
    for prof in _load_personas():
        if prof.get("customer_id") == customer_id:
            return {**prof, "_source": "bundled"}
    return {"error": f"customer {customer_id} not found"}


def _summarize_history(rows: list, customer_id, month, year, limit) -> dict:
    rows = sorted(rows, key=lambda r: (str(r.get("order_date")), str(r.get("order_id"))), reverse=True)
    agg = {}
    for r in rows:
        a = agg.setdefault(
            r.get("product_id"),
            {"product_id": r.get("product_id"), "product_title": r.get("product_title"), "category": r.get("category"), "quantity": 0, "times": 0},
        )
        a["quantity"] += int(r.get("quantity") or 0)
        a["times"] += 1
    top = sorted(agg.values(), key=lambda a: (-a["times"], -a["quantity"]))[:15]
    orders = {r.get("order_id") for r in rows}
    return {
        "customerId": customer_id,
        "month": month,
        "year": year,
        "orderCount": len(orders),
        "totalRows": len(rows),
        "totalAmount": sum(int(r.get("amount") or 0) for r in rows),
        "topProducts": top,
        "purchases": [{k: (str(v) if k == "order_date" else v) for k, v in r.items()} for r in rows[:limit]],
    }


def parse_month(month):
    """10 / "10" / "Oct" / "October" → (10, None); "2025-10" / "2025/10" / "2025-10-01" → (10, 2025)."""
    if month is None or month == "":
        return None, None
    if isinstance(month, (int, float)):
        m = int(month)
        return (m if 1 <= m <= 12 else None), None
    s = str(month).strip()
    mm = re.match(r"^(\d{4})\s*[-/.]\s*(\d{1,2})", s)
    if mm:
        y, m = int(mm.group(1)), int(mm.group(2))
        return (m if 1 <= m <= 12 else None), y
    mm = re.match(r"^(\d{1,2})$", s)
    if mm and 1 <= int(mm.group(1)) <= 12:
        return int(mm.group(1)), None
    names = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    mm = re.match(r"^([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?$", s.lower())
    if mm and mm.group(1) in names:
        return names.index(mm.group(1)) + 1, (int(mm.group(2)) if mm.group(2) else None)
    raise ValueError(f"invalid month '{month}' (use 1-12 or 'YYYY-MM')")


def get_purchase_history_sync(customer_id: str, month=None, limit=30, year=None) -> dict:
    from google.cloud import bigquery

    limit = max(1, min(int(limit or 30), 200))
    month, parsed_year = parse_month(month)
    year = int(year) if year else parsed_year
    where = ["customer_id = @cid"]
    params = [bigquery.ScalarQueryParameter("cid", "STRING", customer_id)]
    if month:
        where.append("EXTRACT(MONTH FROM order_date) = @m")
        params.append(bigquery.ScalarQueryParameter("m", "INT64", month))
    if year:
        where.append("EXTRACT(YEAR FROM order_date) = @y")
        params.append(bigquery.ScalarQueryParameter("y", "INT64", year))
    sql = (
        "SELECT order_id, customer_id, order_date, product_id, product_title, category, quantity, "
        f"unit_price_tax_included, amount FROM `{PROJECT_ID}.{BQ_DATASET}.purchase_history` "
        f"WHERE {' AND '.join(where)} ORDER BY order_date DESC, order_id DESC LIMIT 1000"
    )
    source = "bigquery"
    try:
        rows = _bq_query(sql, params)
    except Exception as e:
        logger.warning("BQ purchase_history query failed (%s); using bundled JSONL", e)
        source = "bundled"
        rows = []
        for r in _load_history_rows():
            d = str(r.get("order_date", ""))
            if r.get("customer_id") != customer_id:
                continue
            if month and (len(d) < 7 or int(d[5:7]) != month):
                continue
            if year and (len(d) < 4 or int(d[:4]) != year):
                continue
            rows.append(r)
    out = _summarize_history(rows, customer_id, month, year, limit)
    out["_source"] = source
    return out


# ── comparison ──────────────────────────────────────────────────────────────

MAX_COMPARE = 4


def parse_product_ids(product_ids) -> list:
    """["a","b"] / "a,b" / "a b" / '["a","b"]' → unique ids (order kept, max MAX_COMPARE)."""
    if product_ids is None:
        return []
    if isinstance(product_ids, str):
        txt = product_ids.strip()
        if txt.startswith("["):
            try:
                product_ids = json.loads(txt)
            except ValueError:
                product_ids = re.split(r"[\s,;]+", txt.strip("[]"))
        else:
            product_ids = re.split(r"[\s,;]+", txt)
    out = []
    for x in product_ids:
        x = str(x).strip().strip('"\'')
        if x and x not in out:
            out.append(x)
    return out[:MAX_COMPARE]


def _yen(v) -> str:
    return f"¥{int(v):,}" if v else "-"


def _is_food(p, attrs: dict) -> bool:
    v = attrs.get("is_food")
    if v is not None:
        return str(v).lower() == "true"
    return bool(attrs.get("storage_type") or attrs.get("allergens"))


def build_comparison(items: list) -> dict:
    """items: [(product_id, retail Product)] → {title, product_ids, products, rows:[{label, values}]}.

    Rows (always): Price (incl. tax), Size, Labels, Rating. Then Origin for food, Material for general
    merchandise (both when mixed), plus optional Brand / Capacity / Color / Allergens / Sale when present.
    """
    summaries, attrs_list, foods = [], [], []
    for pid, p in items:
        summaries.append(summarize_product(pid, p))
        a = attrs_to_dict(getattr(p, "attributes", None))
        attrs_list.append(a)
        foods.append(_is_food(p, a))

    def row(label, values, always=False):
        values = [v if v not in (None, "") else "-" for v in values]
        if always or any(v != "-" for v in values):
            rows.append({"label": label, "values": values})

    rows: list = []
    row("Price (incl. tax)", [_yen(s.get("price")) + (f" (reg. {_yen(s.get('originalPrice'))})" if s.get("onSale") and s.get("originalPrice") and s.get("originalPrice") != s.get("price") else "") for s in summaries], True)
    row("Size", [s.get("size") for s in summaries], True)
    row("Labels", [" / ".join(s.get("labels") or []) for s in summaries], True)
    row("Rating", [f"★{s['rating']['average']:.1f} ({s['rating']['count']} reviews)" if s.get("rating") else "-" for s in summaries], True)
    if any(foods):
        row("Origin", [s.get("origin") for s in summaries], True)
    if not all(foods):
        row("Material", [s.get("material") for s in summaries], True)
    row("Brand", [s.get("brand") for s in summaries])
    row("Capacity", [s.get("capacity") for s in summaries])
    row("Color", [s.get("color") for s in summaries])
    if any(foods):
        row("Allergens", [s.get("allergens") if f else "" for s, f in zip(summaries, foods)])
    row("Sale", ["{}% off".format(int(round(s.get("discountRate") or 0))) if s.get("onSale") and s.get("discountRate") else "" for s in summaries])
    cats = {s.get("subtitle") or "" for s in summaries}
    title = f"{cats.pop()} comparison" if len(cats) == 1 and "" not in cats else "Product comparison"
    return {
        "title": title,
        "product_ids": [s["productId"] for s in summaries],
        "products": [{"productId": s["productId"], "title": s["title"], "price": s.get("price")} for s in summaries],
        "rows": rows,
    }


def compare_products_sync(product_ids) -> dict:
    ids = parse_product_ids(product_ids)
    if len(ids) < 2:
        return {"error": "compare_products needs 2-4 product_ids", "product_ids": ids, "rows": []}
    pc = _product_client()
    with ThreadPoolExecutor(max_workers=len(ids)) as ex:
        futs = {pid: ex.submit(pc.get_product, name=product_name(pid), timeout=4.0) for pid in ids}
    items, missing = [], []
    for pid in ids:
        try:
            items.append((pid, futs[pid].result()))
        except Exception as e:  # keep going with the others
            logger.warning("compare: get_product failed for %s: %s", pid, e)
            missing.append(pid)
    if len(items) < 2:
        return {"error": f"products not found: {missing}", "product_ids": [i for i, _ in items], "rows": []}
    out = build_comparison(items)
    if missing:
        out["missing"] = missing
    return out


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


# ── dinner set (CONTRACT v2: recommend_dinner_set) ──────────────────────────

DINNER_TOP_REGEX = dinner.DINNER_TOP_REGEX
DINNER_EXTRA_REGEX = dinner.DINNER_EXTRA_REGEX
DINNER_CATALOG_TTL_S = int(os.getenv("DINNER_CATALOG_TTL_S", "900"))
DINNER_BQ_TIMEOUT_S = float(os.getenv("DINNER_BQ_TIMEOUT_S", "25"))
DINNER_CATALOG_FILE = os.getenv("DINNER_CATALOG_FILE", str(_HERE.parent.parent / "seed" / "catalog" / "products_retail.jsonl"))
# Retail-search fallback queries (used only when BigQuery and the local catalog file are both unavailable).
DINNER_SEARCH_QUERIES = ["autumn salmon fillets", "salted mackerel", "saury", "cod fillets", "karaage", "hamburg steak",
                         "sliced pork", "chicken thigh", "pork belly shabu-shabu", "chicken meatballs", "meal kit",
                         "sweet omelet", "sweet corn", "salad", "spinach", "shimeji", "napa cabbage", "cabbage",
                         "miso soup", "tofu", "hot pot soup base", "medium hot curry roux", "pork for curry",
                         "packaged rice", "udon", "apples", "persimmons", "mandarins", "pears", "bananas"]
_dinner_cache = {"at": 0.0, "items": None, "source": ""}
_dinner_lock = threading.Lock()


def _dinner_catalog_bq() -> list:
    from google.cloud import bigquery

    sql = (
        "SELECT id, title, categories, attributes, priceInfo, images, rating, availability, description "
        f"FROM `{PROJECT_ID}.{BQ_DATASET}.products` WHERE availability = 'IN_STOCK' AND ("
        "REGEXP_CONTAINS(TRIM(SPLIT(categories[SAFE_OFFSET(0)], '>')[SAFE_OFFSET(0)]), @tops) "
        "OR REGEXP_CONTAINS(ARRAY_TO_STRING(categories, ' '), @extra))"
    )
    params = [bigquery.ScalarQueryParameter("tops", "STRING", DINNER_TOP_REGEX),
              bigquery.ScalarQueryParameter("extra", "STRING", DINNER_EXTRA_REGEX)]
    job = _bq_client().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
    return [dinner.normalize_product(dict(r.items())) for r in job.result(timeout=DINNER_BQ_TIMEOUT_S)]


def _dinner_catalog_file() -> list:
    p = Path(DINNER_CATALOG_FILE)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(dinner.normalize_product(json.loads(line)))
    return out


def _dinner_catalog_search() -> list:
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(search_products_sync, q, 30, None, None) for q in DINNER_SEARCH_QUERIES]
        for f in futs:
            try:
                out += [dinner.normalize_summary(s) for s in f.result().get("productDetails", [])]
            except Exception as e:  # keep the others
                logger.warning("dinner search fallback failed: %s", e)
    return out


def _load_dinner_catalog() -> tuple:
    for source, fn in (("bigquery", _dinner_catalog_bq), ("file", _dinner_catalog_file), ("retail_search", _dinner_catalog_search)):
        try:
            items = [i for i in fn() if i]
        except Exception as e:
            logger.warning("dinner catalog via %s failed: %s", source, e)
            continue
        if items:
            logger.info("dinner catalog: %d items from %s", len(items), source)
            return items, source
    return [], "none"


def _refresh_dinner_catalog():
    if not _dinner_lock.acquire(blocking=False):
        return  # a refresh is already running
    try:
        items, source = _load_dinner_catalog()
        if items:
            _dinner_cache.update(at=time.monotonic(), items=items, source=source)
    finally:
        _dinner_lock.release()


def dinner_catalog() -> tuple:
    """(items, source) — BigQuery cymbal.products (cached, stale-while-revalidate), else local catalog file, else
    Retail search."""
    c = _dinner_cache
    if c["items"]:
        if time.monotonic() - c["at"] >= DINNER_CATALOG_TTL_S:
            threading.Thread(target=_refresh_dinner_catalog, daemon=True).start()
        return c["items"], c["source"]
    with _dinner_lock:  # cold: load synchronously (once)
        if not c["items"]:
            items, source = _load_dinner_catalog()
            if items:
                c.update(at=time.monotonic(), items=items, source=source)
    return (c["items"] or []), (c["source"] or "none")


def dinner_history_rows(customer_id: str) -> tuple:
    from google.cloud import bigquery

    try:
        rows = _bq_query(
            "SELECT product_id, ANY_VALUE(product_title) AS product_title, ANY_VALUE(category) AS category, "
            f"COUNT(*) AS times FROM `{PROJECT_ID}.{BQ_DATASET}.purchase_history` WHERE customer_id = @cid GROUP BY product_id",
            [bigquery.ScalarQueryParameter("cid", "STRING", customer_id)],
        )
        return rows, "bigquery"
    except Exception as e:
        logger.warning("BQ history aggregation failed (%s); using bundled JSONL", e)
        return [r for r in _load_history_rows() if r.get("customer_id") == customer_id], "bundled"


def recommend_dinner_set_sync(customer_id: str, request: str = "", budget_jpy=0, servings=0, today=None) -> dict:
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_prof = ex.submit(get_customer_profile_sync, customer_id)
        f_hist = ex.submit(dinner_history_rows, customer_id)
        f_cat = ex.submit(dinner_catalog)
        profile, (rows, hsrc), (catalog, csrc) = f_prof.result(), f_hist.result(), f_cat.result()
    if profile.get("error"):
        profile = {"customer_id": customer_id}
    if not catalog:
        return {"error": "catalog unavailable", "sets": []}
    res = dinner.recommend(profile, rows, catalog, request=request or "", budget_jpy=_int0(budget_jpy),
                           servings=_int0(servings), customer_id=customer_id, today=today)
    res["_source"] = {"profile": profile.get("_source", "none"), "history": hsrc, "catalog": csrc}
    if not res["sets"]:
        res["error"] = "no set could be composed (catalog too small or every candidate excluded)"
    return res


def _int0(v) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


if os.getenv("DINNER_WARM", "false").lower() == "true":  # warm the catalog cache on Cloud Run start-up
    threading.Thread(target=dinner_catalog, daemon=True).start()


# ── store inventory (CONTRACT v14: check_store_inventory) ───────────────────

STORE_TZ = dt.timezone(dt.timedelta(hours=9))  # Asia/Tokyo (no DST)
LOW_STOCK_QTY = 3
MAX_STORE_IDS = 20
STORES_TTL_S = int(os.getenv("STORES_TTL_S", "600"))
_REPO = _HERE.parent.parent
STORE_FILES = [os.getenv("STORES_FILE", ""), "stores.json", "stores_en.json",
               str(_REPO / "seed" / "catalog" / "stores_en.json"),
               str(_REPO / "store-navi" / "data" / "stores.json")]
INVENTORY_FILES = [os.getenv("STORE_INVENTORY_FILE", ""), "store_inventory.jsonl",
                   str(_REPO / "seed" / "catalog" / "store_inventory_en.jsonl"), str(_REPO / "seed" / "catalog" / "store_inventory.jsonl"),
                   str(_REPO / "store-navi" / "data" / "store_inventory.jsonl")]
_stores_cache = {"at": 0.0, "stores": None, "source": ""}
_catalog_index = {"items": None}


def parse_ids(product_ids, limit: int = MAX_STORE_IDS) -> list:
    """Like parse_product_ids but without the 4-item compare cap."""
    if product_ids is None:
        return []
    if isinstance(product_ids, str):
        txt = product_ids.strip()
        if txt.startswith("["):
            try:
                product_ids = json.loads(txt)
            except ValueError:
                product_ids = re.split(r"[\s,;]+", txt.strip("[]"))
        else:
            product_ids = re.split(r"[\s,;]+", txt)
    out = []
    for x in product_ids:
        x = str(x).strip().strip('"\'')
        if x and x not in out:
            out.append(x)
    return out[:limit]


def _resolve_file(name: str):
    if not name:
        return None
    p = Path(name)
    if p.is_absolute():
        return p if p.exists() else None
    return _find_data_file(name)


def _norm_store(s: dict) -> dict:
    s = {k: _maybe_json(v) for k, v in dict(s).items()}
    return s


def _load_stores_file() -> list:
    for name in STORE_FILES:
        p = _resolve_file(name)
        if p:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("stores") or list(data.values())
            return [_norm_store(s) for s in data if isinstance(s, dict) and s.get("store_id")]
    return []


def load_stores() -> tuple:
    """(stores, source) — BigQuery cymbal.stores (cached), else bundled/local stores JSON."""
    c = _stores_cache
    if c["stores"] and time.monotonic() - c["at"] < STORES_TTL_S:
        return c["stores"], c["source"]
    stores, source = [], "none"
    try:
        stores = [_norm_store(r) for r in _bq_query(
            f"SELECT * FROM `{PROJECT_ID}.{BQ_DATASET}.stores` ORDER BY store_id", [])]
        source = "bigquery"
    except Exception as e:
        logger.warning("BQ stores query failed (%s); using local stores JSON", e)
    if not stores:
        stores, source = _load_stores_file(), "file"
    if stores:
        c.update(at=time.monotonic(), stores=stores, source=source)
    return stores, source


def _hhmm(v) -> int | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(v or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def store_hours(store: dict) -> tuple:
    """→ (open_min, close_min, label "9:00 AM – 10:00 PM") from hours {open, close} | "09:00-22:00"."""
    h = store.get("hours") or {}
    if isinstance(h, str):
        parts = re.split(r"\s*[-–~]\s*", h, maxsplit=1)
        h = {"open": parts[0], "close": parts[1] if len(parts) > 1 else ""}
    o, cl = _hhmm(h.get("open")), _hhmm(h.get("close"))
    label = f"{_ampm(o)} \u2013 {_ampm(cl)}" if o is not None and cl is not None else ""  # same as site stores.hours_label
    return o, cl, label


def _ampm(m: int) -> str:
    """600 → "10:00 AM" (en-US, same format as online-store /api/products/{id}/stores)."""
    hh, mm = divmod(m, 60)
    return f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh % 24 < 12 else 'PM'}"


def is_open_now(store: dict, now: dt.datetime | None = None) -> bool:
    o, cl, _ = store_hours(store)
    if o is None or cl is None:
        return False
    now = (now or dt.datetime.now(STORE_TZ)).astimezone(STORE_TZ)
    m = now.hour * 60 + now.minute
    if cl <= o:  # past midnight (e.g. 09:00-01:00) or 24h (00:00-00:00)
        return m >= o or m < cl or cl == o
    return o <= m < cl


def _li_attrs(li) -> dict:
    out = {}
    for k, v in (getattr(li, "attributes", None) or {}).items():
        nums, text = list(getattr(v, "numbers", []) or []), list(getattr(v, "text", []) or [])
        if nums:
            out[k] = nums[0] if len(nums) == 1 else nums
        elif text:
            out[k] = text[0] if len(text) == 1 else text
    return out


def _inv_record(quantity=None, availability=None, shelf_id=None, slot_ids=None, tier=None, fulfillment=None) -> dict:
    q = _to_int(quantity)
    if q is None:
        q = 1 if fulfillment and "pickup-in-store" in fulfillment else 0
    av = str(availability or ("IN_STOCK" if q > 0 else "OUT_OF_STOCK")).upper()
    if isinstance(slot_ids, str):
        slot_ids = [x for x in re.split(r"[\s,]+", slot_ids) if x]
    return {"quantity": max(q, 0), "availability": av, "shelf_id": shelf_id or "", "slot_ids": list(slot_ids or []),
            "tier": _to_int(tier)}


def _retail_inventory(ids: list) -> dict:
    """{pid: {"title", "channel", "found", "inv": {store_id: rec} | None}} via Retail GetProduct (local_inventories)."""
    out = {}
    pc = _product_client()

    def one(pid):
        return pc.get_product(name=product_name(pid), timeout=4.0)

    with ThreadPoolExecutor(max_workers=max(1, min(len(ids), 10))) as ex:
        futs = {pid: ex.submit(one, pid) for pid in ids}
    for pid in ids:
        try:
            p = futs[pid].result()
        except Exception as e:
            logger.info("check_store_inventory: Retail get_product failed for %s (%s)", pid, str(e)[:120])
            continue
        attrs = attrs_to_dict(getattr(p, "attributes", None))
        inv = {}
        for li in getattr(p, "local_inventories", None) or []:
            a = _li_attrs(li)
            inv[li.place_id] = _inv_record(a.get("quantity"), a.get("availability"), a.get("shelf_id"),
                                           a.get("slot_ids"), a.get("tier"), list(getattr(li, "fulfillment_types", []) or []))
        out[pid] = {"title": getattr(p, "title", "") or "", "channel": str(attrs.get("channel") or "omni"),
                    "found": True, "inv": inv or None}
    return out


def _bq_inventory(ids: list) -> dict:
    """{pid: {store_id: rec}} from BigQuery cymbal.store_inventory (+ latest shelf_locations when available)."""
    from google.cloud import bigquery

    params = [bigquery.ArrayQueryParameter("ids", "STRING", ids)]
    base = f"`{PROJECT_ID}.{BQ_DATASET}`"
    sql_join = (
        "SELECT i.store_id, i.product_id, i.quantity, i.availability, s.fixture_id, s.slot_ids, s.tier "
        f"FROM {base}.store_inventory i LEFT JOIN (SELECT store_id, product_id, fixture_id, slot_ids, tier "
        f"FROM {base}.shelf_locations WHERE product_id IN UNNEST(@ids) "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY store_id, product_id ORDER BY registered_at DESC) = 1) s "
        "USING (store_id, product_id) WHERE i.product_id IN UNNEST(@ids)"
    )
    try:
        rows = _bq_query(sql_join, params)
    except Exception as e:
        logger.info("store_inventory+shelf_locations join failed (%s); plain inventory", str(e)[:120])
        rows = _bq_query(f"SELECT store_id, product_id, quantity, availability FROM {base}.store_inventory "
                         "WHERE product_id IN UNNEST(@ids)", params)
    out = {}
    for r in rows:
        out.setdefault(str(r.get("product_id")), {})[str(r.get("store_id"))] = _inv_record(
            r.get("quantity"), r.get("availability"), r.get("fixture_id"), r.get("slot_ids"), r.get("tier"))
    return out


def _file_inventory(ids: list) -> dict:
    want, out = set(ids), {}
    for name in INVENTORY_FILES:
        p = _resolve_file(name)
        if not p:
            continue
        with p.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                pid = str(r.get("product_id"))
                if pid in want:
                    out.setdefault(pid, {})[str(r.get("store_id"))] = _inv_record(
                        r.get("quantity"), r.get("availability"), r.get("fixture_id") or r.get("shelf_id"),
                        r.get("slot_ids"), r.get("tier"))
        return out
    return out


def _catalog_lookup(ids: list) -> dict:
    """{pid: (title, channel)} from BigQuery cymbal.products, else the local catalog file (cached index)."""
    from google.cloud import bigquery

    out = {}
    try:
        rows = _bq_query(f"SELECT id, title, attributes FROM `{PROJECT_ID}.{BQ_DATASET}.products` WHERE id IN UNNEST(@ids)",
                         [bigquery.ArrayQueryParameter("ids", "STRING", ids)])
        for r in rows:
            a = dinner._attr_map(r.get("attributes"))
            out[str(r["id"])] = (r.get("title") or "", str(dinner._first(a.get("channel"), "omni") or "omni"))
        if out:
            return out
    except Exception as e:
        logger.info("BQ products lookup failed (%s); local catalog file", str(e)[:120])
    if _catalog_index["items"] is None:
        idx = {}
        p = Path(DINNER_CATALOG_FILE)
        if p.exists():
            with p.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        r = json.loads(line)
                        a = dinner._attr_map(r.get("attributes"))
                        idx[str(r.get("id"))] = (r.get("title") or "", str(dinner._first(a.get("channel"), "omni") or "omni"))
        _catalog_index["items"] = idx
    return {pid: _catalog_index["items"][pid] for pid in ids if pid in _catalog_index["items"]}


def _shelf_label(rec: dict) -> str:
    slots = rec.get("slot_ids") or []
    if slots:
        tier = f", shelf {rec['tier']} from the bottom" if rec.get("tier") else ""
        return f"Aisle {rec.get('shelf_id') or slots[0].split('-')[0]}{tier} (slot {slots[0]})"
    return f"Aisle {rec['shelf_id']}" if rec.get("shelf_id") else ""


def check_store_inventory_sync(product_ids, store_id: str = "", now: dt.datetime | None = None) -> dict:
    ids = parse_ids(product_ids)
    if not ids:
        return {"items": [], "error": "check_store_inventory needs at least one product_id"}
    store_id = (store_id or "").strip().upper()
    stores, store_src = load_stores()
    if store_id:
        stores = [s for s in stores if str(s.get("store_id")).upper() == store_id]
        if not stores:
            return {"items": [], "error": f"unknown store_id {store_id}"}
    try:
        retail = _retail_inventory(ids)
    except Exception as e:
        logger.warning("Retail local inventory lookup failed (%s)", e)
        retail = {}
    need_inv = [pid for pid in ids if not (retail.get(pid) or {}).get("inv")]
    fallback, inv_src = {}, "retail_local_inventory"
    if need_inv:
        try:
            fallback = _bq_inventory(need_inv)
            inv_src = "bigquery" if len(need_inv) == len(ids) else "mixed"
        except Exception as e:
            logger.warning("BQ store_inventory query failed (%s); local inventory file", e)
            fallback = _file_inventory(need_inv)
            inv_src = "file" if len(need_inv) == len(ids) else "mixed"
    need_meta = [pid for pid in ids if not (retail.get(pid) or {}).get("found")]
    meta = _catalog_lookup(need_meta) if need_meta else {}

    items = []
    for pid in ids:
        r = retail.get(pid) or {}
        title, channel = (r.get("title"), r.get("channel")) if r.get("found") else meta.get(pid, ("", "omni"))
        inv = r.get("inv") or fallback.get(pid) or {}
        rows = []
        for s in stores:
            sid = str(s.get("store_id"))
            rec = inv.get(sid)
            _, _, hours = store_hours(s)
            carried = rec is not None and channel != "online_only"
            qty = rec["quantity"] if carried else 0
            avail = "IN_STOCK" if qty > 0 else "OUT_OF_STOCK"  # contract enum; `carried` tells "not sold here"
            row = {"store_id": sid, "name": s.get("name") or sid, "address": s.get("address") or "",
                   "open_now": is_open_now(s, now), "hours": hours, "quantity": qty, "availability": avail,
                   "carried": carried, "low_stock": 0 < qty <= LOW_STOCK_QTY, "pickup": qty > 0}
            if rec and qty > 0 and _shelf_label(rec):
                row["shelf_label"] = _shelf_label(rec)
            rows.append(row)
        rows.sort(key=lambda x: (x["quantity"] <= 0, x["store_id"]))
        item = {"product_id": pid, "title": title or "", "channel": channel or "omni", "stores": rows,
                "online_available": (channel or "omni") != "store_only",
                "in_stock_store_count": sum(1 for x in rows if x["quantity"] > 0)}
        if not r.get("found") and pid not in meta:
            item["error"] = "product not found"
        items.append(item)
    return {"items": items, "_source": {"inventory": inv_src, "stores": store_src}}


# month accepts int (10) or "YYYY-MM"; schema stays a plain string for GECX.
MonthArg = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, (int, float)) else v)]
# product_ids accepts a JSON array or a comma separated string; schema stays array<string>.
IdsArg = Annotated[list[str], BeforeValidator(lambda v: parse_product_ids(v) if isinstance(v, str) else v)]
StoreIdsArg = Annotated[list[str], BeforeValidator(lambda v: parse_ids(v) if isinstance(v, str) else v)]

# ── MCP tools ────────────────────────────────────────────────────────────────


@mcp.tool()
async def search_products(query: str, page_size: int = 10, filter: str = None, visitor_id: str = None) -> str:
    """
    Search the Cymbal Retail catalog (groceries, household, kitchen, beauty, pet, home & living, health & medicine)
    in natural language via AI Commerce Search (servingConfigs/default_search). Max 100 items, in stock only.

    Args:
      query: search words, e.g. "frying pan", "medium hot curry roux", "shampoo", "cat food".
             A phrase like "under ¥3,000" automatically adds a price filter.
      page_size: number of results 1-100 (default 10; values above 100 are capped at 100).
      filter: optional Retail filter expression. Deals: 'attributes.on_sale: ANY("true")'; price cap:
              'price: IN(*, 3000.0i)'; category: 'categories: ANY("Kitchen")'; in-store pickup at a store:
              'pickupInStore: ANY("S01")' (S01 Kawasaki Station, S02 Musashi-Kosugi, S03 Shibuya).
      visitor_id: visitor id for personalization (optional).
    Returns JSON: {"productDetails":[{productId,title,price(JPY, tax incl.),originalPrice,onSale,discountRate,
      promoText,size,allergens,brand,topCategory,channel,labels,origin,material,color,capacity,rating}], "totalSize"}
      channel: omni | store_only (in-store only, cannot be added to the delivery cart) | online_only.
    """
    try:
        res = await asyncio.to_thread(search_products_sync, query, page_size, filter, visitor_id)
        return _dumps(res)
    except Exception as e:
        logger.exception("search_products failed")
        return _dumps({"productDetails": [], "totalSize": 0, "error": f"search failed: {e}"})


@mcp.tool()
async def get_product_details(product_id: str) -> str:
    """
    Get full details for one product ID: description, all attributes (origin/material/allergens/storage/size/
    channel), price (JPY, tax incl.), images and rating.
    """

    def _run():
        p = _product_client().get_product(name=product_name(product_id), timeout=4.0)
        out = summarize_product(product_id, p)
        rating = _rating(p)
        out.update(
            {
                "description": p.description or "",
                "brands": list(p.brands or []),
                "categories": list(p.categories or []),
                "availability": getattr(p.availability, "name", str(p.availability)),
                "attributes": attrs_to_dict(p.attributes),
                "rating": rating,
            }
        )
        return out

    try:
        return _dumps(await asyncio.to_thread(_run))
    except Exception as e:
        return _dumps({"error": f"Error finding product {product_id}: {e}"})


@mcp.tool()
async def compare_products(product_ids: IdsArg) -> str:
    """
    Compare 2-4 products. The result's title / product_ids / rows can be passed as-is to the client function
    show_comparison.
    Returns {"title","product_ids","products":[{productId,title,price}],"rows":[{"label","values":[...]}]} —
    rows: Price (incl. tax) / Size / Labels / Rating / Origin (food) / Material (general merchandise)
    (+ Brand / Capacity / Color / Allergens / Sale when available). values are in product_ids order.
    Args:
      product_ids: array of product IDs to compare (productId from search_products, 2-4 items).
    """
    try:
        return _dumps(await asyncio.to_thread(compare_products_sync, product_ids))
    except Exception as e:
        logger.exception("compare_products failed")
        return _dumps({"error": f"compare failed: {e}", "rows": []})


@mcp.tool()
async def get_customer_profile(customer_id: str) -> str:
    """
    Get the customer profile (household, taste, allergies, budget, delivery slot, loyalty), e.g. customer_id="CR-0001".
    Never suggest products containing the customer's allergens (e.g. shrimp).
    """
    try:
        return _dumps(await asyncio.to_thread(get_customer_profile_sync, customer_id))
    except Exception as e:
        return _dumps({"error": f"profile lookup failed: {e}"})


@mcp.tool()
async def get_purchase_history(customer_id: str, month: MonthArg = None, limit: int = 50) -> str:
    """
    Get the customer's purchase history (newest first).
    Args:
      customer_id: e.g. "CR-0001"
      month: 1-12 (that month in every year) or "YYYY-MM" (a specific month). "This time last year" = this month of
             last year, e.g. "2025-11".
      limit: max number of purchase lines to return (default 50, max 200).
    Returns JSON: {orderCount, totalAmount, topProducts[frequently bought], purchases[order_id,order_date,product_id,
      product_title,category,quantity,unit_price_tax_included,amount]}
    """
    try:
        return _dumps(await asyncio.to_thread(get_purchase_history_sync, customer_id, month, limit))
    except Exception as e:
        return _dumps({"error": f"history lookup failed: {e}"})


IntArg = Annotated[int, BeforeValidator(_int0)]


@mcp.tool()
async def recommend_dinner_set(customer_id: str, request: str = "", budget_jpy: IntArg = 0, servings: IntArg = 0) -> str:
    """
    For "what's for dinner tonight" / meal ideas, compose dinner product sets (Main, Side, Soup, Staple when needed,
    Dessert & fruit): 1 primary set + up to 2 alternatives, 4-5 items each. Combines the customer's purchase history
    (frequently bought), family preferences (family_preferences), allergy exclusion (e.g. shrimp), this week's deals
    (on_sale) and in-season items (is_seasonal); in stock only, no bulk sizes, no in-store-only items.
    Args:
      customer_id: e.g. "CR-0001"
      request: the customer's request verbatim (e.g. "something with meat", "fish", "hot pot", "quick",
               "budget ¥2,000", "for 2 people"). Empty if none.
      budget_jpy: budget in JPY, tax incl. (optional, 0 = none).
      servings: number of people (optional, 0 = household size).
    Returns JSON: {theme, servings, signals:{frequent, family, sale_count, seasonal, excluded},
      sets:[{name, concept, items:[{role, product_id, title, price, quantity, subtotal, image, tags, reason}],
      total_tax_included}], show_products_on_page_args:{message, theme, product_ids, reasons, sections}}
      show_products_on_page_args can be passed as-is to show_products_on_page for the primary set.
      roles: Main / Side / Soup / Staple / Dessert & fruit; tags: Frequently bought / Family favorite / On sale /
      In season / Quick.
    """
    try:
        return _dumps(await asyncio.to_thread(recommend_dinner_set_sync, customer_id, request, budget_jpy, servings))
    except Exception as e:
        logger.exception("recommend_dinner_set failed")
        return _dumps({"error": f"dinner set failed: {e}", "sets": []})


@mcp.tool()
async def check_store_inventory(product_ids: StoreIdsArg, store_id: str = "") -> str:
    """
    Check in-store stock for products at Cymbal Retail stores (S01 Cymbal Retail Kawasaki Station, S02 Cymbal Retail
    Musashi-Kosugi, S03 Cymbal Retail Shibuya). Use for "can I buy it in store?", "can I pick it up?", "which store
    has it?" and for store_only products (in-store only, cannot be delivered).
    Reads Retail Product.local_inventories (fallback BigQuery cymbal.store_inventory) + cymbal.stores.
    Args:
      product_ids: array of product IDs (1-20).
      store_id: optional store id ("S01"/"S02"/"S03") to check only that store; empty = all stores.
    Returns JSON: {items:[{product_id, title, channel ("omni"|"store_only"|"online_only"), online_available,
      in_stock_store_count, stores:[{store_id, name, address, open_now, hours ("9:00 AM – 10:00 PM", Asia/Tokyo),
      quantity, availability ("IN_STOCK"|"OUT_OF_STOCK"), carried (false = not sold at that store), low_stock, pickup,
      shelf_label?}]}]}
      stores are sorted in-stock first. online_available=false means the product cannot be added to the delivery cart.
    """
    try:
        return _dumps(await asyncio.to_thread(check_store_inventory_sync, product_ids, store_id))
    except Exception as e:
        logger.exception("check_store_inventory failed")
        return _dumps({"items": [], "error": f"store inventory lookup failed: {e}"})


app = mcp.streamable_http_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), proxy_headers=True, forwarded_allow_ips="*")
