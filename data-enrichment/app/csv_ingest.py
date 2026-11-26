"""CSV parsing and sample CSV generation for Live Enrichment testing."""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .schema import CATEGORY_GROUPS, category_group

# Flexible header aliases (English; matched case-insensitively after normalizing spaces/dashes to "_")
HEADER_MAP = {
    "sku": {"sku", "id", "p_cd", "product_id", "item_id", "product_code", "item_code", "item_number", "jan", "gtin", "upc"},
    "title": {"title", "name", "product_name", "item_name", "product_title"},
    "brand": {"brand", "brands", "maker", "manufacturer"},
    "category": {"category", "categories", "category_path", "product_category", "department"},
    "description": {"description", "desc", "body", "product_description", "long_description", "details"},
    "price": {"price", "sale_price", "retail_price", "price_jpy", "unit_price", "price_tax_included"},
    "product_url": {"product_url", "uri", "url", "link", "product_link"},
    "image_url": {"image_url", "image", "image_link", "thumbnail", "picture"},
    "group": {"group", "category_group"},
    "raw_attributes_json": {"raw_attributes_json", "raw_attributes", "attributes_json"},
}

SAMPLE_PRODUCTS = [
    {
        "sku": "CSV-FOOD-001",
        "title": "Cymbal Basic Crispy Chicken Tenders (Frozen)",
        "brand": "Cymbal Basic",
        "category": "Frozen > Frozen Sides > Fried Chicken",
        "description": "Tender chicken breast marinated in a soy-based seasoning and coated in a light potato-starch batter "
                       "for a crispy finish. Just microwave from frozen (600 W for approx. 2 min 30 sec). Great for bento "
                       "boxes or as an extra dinner side. Net weight: 300 g (approx. 12 pcs) / Ingredient origin: Japan / "
                       "Storage: Store at -18\u00b0C or below / Allergens: wheat, soybean, chicken / "
                       "Best before: 12 months from manufacture",
        "price": 498,
        "product_url": "https://example.com/cymbal/p/CSV-FOOD-001",
        "image_url": "",
    },
    {
        "sku": "CSV-FOOD-002",
        "title": "Hokkaido Autumn Salmon Fillets, Lightly Salted, 4 Fillets",
        "brand": "",
        "category": "Seafood > Salmon > Salted Salmon",
        "description": "Rich autumn salmon from Hokkaido, lightly salted and cut into fillets. Just grill for breakfast or bento. "
                       "In season: Sep-Nov. 4 fillets (approx. 320 g). Keep refrigerated (10\u00b0C or below).",
        "price": 698,
        "product_url": "https://example.com/cymbal/p/CSV-FOOD-002",
        "image_url": "",
        "origin": "Hokkaido, Japan",
    },
    {
        "sku": "CSV-GM-003",
        "title": "Cymbal Select Lightweight Induction Deep Frying Pan 26 cm",
        "brand": "Cymbal Select",
        "category": "Kitchen > Pots & Pans > Frying Pans",
        "description": "A light, deep frying pan with a cast aluminum body and titanium-reinforced non-stick coating, so food "
                       "does not stick. One pan for stir-fries and stews. Induction and gas compatible (200 V induction OK). "
                       "Dishwasher safe. Size: diameter 26 x depth 7.5 cm, weight approx. 780 g.",
        "price": 2980,
        "product_url": "https://example.com/cymbal/p/CSV-GM-003",
        "image_url": "",
        "material": "Cast aluminum alloy, fluororesin (non-stick) coating",
        "color": "Brown",
    },
]

SAMPLE_CSV_COLUMNS = [
    "sku", "title", "brand", "category", "description", "price", "product_url", "image_url",
    "size", "origin", "storage_type", "allergens", "material", "color",
]


def sample_csv_text() -> str:
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.DictWriter(buf, fieldnames=SAMPLE_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for p in SAMPLE_PRODUCTS:
        writer.writerow({k: p.get(k, "") for k in SAMPLE_CSV_COLUMNS})
    return buf.getvalue()


def decode_csv_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _norm_header(h: str) -> str:
    return re.sub(r"[\s_\-]+", "_", (h or "").strip().lower())


def _parse_price(val: Any) -> float | None:
    if val is None:
        return None
    s = re.sub(r"[^\d.]", "", str(val))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_csv_text(text: str) -> tuple[list[dict], list[str]]:
    """Parse CSV text into normalized product dicts ready for enrichment."""
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("CSV data is empty")

    reader = csv.DictReader(io.StringIO(text))
    headers = [h for h in (reader.fieldnames or []) if h]
    if not headers:
        raise ValueError("CSV header row not found")

    # Map each column to canonical field or raw_attribute
    col_map: dict[str, str] = {}
    for h in headers:
        nh = _norm_header(h)
        matched = None
        for canon, aliases in HEADER_MAP.items():
            if nh in aliases or h.strip() in aliases:
                matched = canon
                break
        if matched and matched not in col_map.values():
            col_map[h] = matched
        else:
            col_map[h] = f"raw:{h.strip()}"

    products: list[dict] = []
    for idx, row in enumerate(reader, start=1):
        if not row or not any((v or "").strip() for v in row.values()):
            continue
        p: dict[str, Any] = {
            "sku": "",
            "title": "",
            "brand": "",
            "category": "",
            "description": "",
            "price": None,
            "product_url": "",
            "image_url": "",
            "group": "",
            "raw_attributes": {},
        }
        for h, val in row.items():
            if not h:
                continue
            v = (val or "").strip()
            if not v:
                continue
            target = col_map.get(h, f"raw:{h}")
            if target == "price":
                p["price"] = _parse_price(v)
            elif target == "raw_attributes_json":
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        for kv in parsed:
                            if isinstance(kv, dict) and kv.get("k"):
                                p["raw_attributes"][str(kv["k"])] = str(kv.get("v", ""))
                    elif isinstance(parsed, dict):
                        for rk, rv in parsed.items():
                            p["raw_attributes"][str(rk)] = str(rv)
                except Exception:
                    p["raw_attributes"]["raw_attributes_json"] = v
            elif target.startswith("raw:"):
                rk = target[4:]
                p["raw_attributes"][rk] = v
            else:
                p[target] = v

        if not p["sku"]:
            p["sku"] = f"CSV-{idx:04d}"
        if not p["title"]:
            p["title"] = p["raw_attributes"].get("title") or f"Product {p['sku']}"
        if p["group"] not in CATEGORY_GROUPS:
            p["group"] = category_group(f"{p['category']} {p['title']}")
        if not p["category"]:
            p["category"] = CATEGORY_GROUPS.get(p["group"], "Other")
        products.append(p)

    if not products:
        raise ValueError("No valid product rows found")
    return products, headers
