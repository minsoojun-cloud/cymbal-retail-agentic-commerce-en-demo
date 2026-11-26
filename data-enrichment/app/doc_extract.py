"""Extract product information from unstructured files (spec sheet PDF / food label photo / price list Excel / CSV)
with Gemini, match to SKUs (JAN = Cymbal product id, model number, title similarity), and merge into the product master."""
from __future__ import annotations

import io
import json
import re
import time
import unicodedata

from .config import settings
from . import storage

SUPPORTED = {"jpg", "jpeg", "png", "webp", "pdf", "xlsx", "csv"}
MAX_FILES = 20
MAX_BYTES = 30 * 1024 * 1024
MAX_ROWS = 2000

EXTRACT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "document_type": {"type": "STRING", "enum": ["spec_sheet", "catalog", "price_list", "label_photo", "manual", "other"]},
        "products": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "model_number": {"type": "STRING"}, "jan": {"type": "STRING"}, "sku_hint": {"type": "STRING"},
            "title": {"type": "STRING"}, "brand": {"type": "STRING"}, "category": {"type": "STRING"},
            "price": {"type": "NUMBER", "nullable": True}, "description": {"type": "STRING"},
            "attributes": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
                "key": {"type": "STRING"}, "label": {"type": "STRING"}, "value": {"type": "STRING"},
                "evidence": {"type": "STRING"}, "page": {"type": "INTEGER", "nullable": True}},
                "required": ["key", "label", "value", "evidence"]}},
            "confidence": {"type": "NUMBER"}},
            "required": ["model_number", "title", "attributes", "confidence"]}},
    },
    "required": ["document_type", "products"],
}


def ext_of(name: str) -> str:
    return name.lower().rsplit(".", 1)[-1] if "." in name else ""


def is_supported(name: str) -> bool:
    return ext_of(name) in SUPPORTED


def xlsx_to_text(data: bytes, max_rows: int = MAX_ROWS) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"### Sheet: {ws.title}")
        n = 0
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c).replace("\t", " ").replace("\n", " ") for c in row]
            if not any(cells):
                continue
            out.append("\t".join(cells).rstrip("\t"))
            n += 1
            if n >= max_rows:
                out.append("... (truncated)")
                break
    return "\n".join(out)


def known_keys_text(products: list[dict]) -> str:
    from .schema import ATTRS
    keys: dict[str, str] = {}
    for a in ATTRS:
        for rk in a.raw_keys:
            keys.setdefault(rk, a.label)
    for p in products:
        for k in (p.get("raw_attributes") or {}):
            keys.setdefault(k, "")
    return ", ".join(f"{k}({v})" if v else k for k, v in sorted(keys.items()))


def extract_prompt(file_name: str, keys_text: str) -> str:
    return f"""You maintain the product master for "Cymbal Retail", a grocery supermarket and general online store. From the attached document "{file_name}" (spec sheet, catalog, price list, photo of a food label, etc.), extract the product information for each product it describes.

## Strict rules
1. Never invent values that are not written in the document (no guessing or filling in from general knowledge).
2. Copy each value exactly as written in the document, including units (no conversion or rounding).
3. evidence must be a verbatim copy of the supporting text in the document (max 80 characters).
4. key is lowercase English snake_case. If it corresponds to an existing catalog key, always use that key; otherwise create a new descriptive English snake_case key. label is a short English field name.
   Existing keys: {keys_text}
   Common keys: size (net contents / size), origin (origin / country of origin), storage_type (storage method), allergens (allergens), ingredients (ingredients),
   best_before (best-before date), cooking_time (cooking time), material (material), color (color), dimensions (dimensions), ih_compatible (induction compatible),
   dishwasher_safe (dishwasher safe), manufacturer (manufacturer / distributor)
5. The "Allergens" / "Contains" box on a food label is the most important item. Use key=allergens and copy the box text verbatim, e.g. "Allergens: wheat, egg, milk".
   If it cannot be read or there is no such box, do not output allergens (no guessing).
6. model_number is the model / item number (empty string if none). jan is the JAN/EAN code (13 digits) if present. price is a number only if a tax-included price is stated, otherwise null.
7. description is the product description text in the document (do not summarize; empty string if none).
8. If one document contains several products, put each in products (one per row if each table row is a product). If there is no product information, products is an empty array.
9. page is the PDF page number (1-based), otherwise null. confidence is the overall confidence of the extraction, 0.0-1.0.
10. Write label and description in English. If the document is in another language, keep value/evidence verbatim but write label in English.
"""


async def extract_file(f: dict, products: list[dict]) -> dict:
    """f = {uri, name, mime}. Returns {document_type, products, input_tokens, output_tokens, latency_ms}."""
    from google.genai import types
    t0 = time.time()
    name = f.get("name") or f["uri"].rsplit("/", 1)[-1]
    ext = ext_of(name)
    mime = f.get("mime") or storage.guess_mime(name)
    prompt = extract_prompt(name, known_keys_text(products))
    if ext in ("xlsx", "csv"):
        data, _ = storage.download(f["uri"])
        if len(data) > MAX_BYTES:
            raise ValueError("File exceeds 30 MB")
        if ext == "xlsx":
            text = xlsx_to_text(data)
        else:
            from .csv_ingest import decode_csv_bytes
            text = "\n".join(decode_csv_bytes(data).splitlines()[:MAX_ROWS])
        contents = [prompt + "\n## Document content (table, tab-separated)\n" + text[:400_000]]
    else:
        contents = [types.Part.from_uri(file_uri=f["uri"], mime_type=mime), prompt]
    if settings.offline:
        return {"document_type": "other", "products": [], "input_tokens": 0, "output_tokens": 0, "latency_ms": 1}
    from . import models as model_registry
    from .gemini_client import client
    model = model_registry.current()
    resp = await client().aio.models.generate_content(
        model=model, contents=contents,
        config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json",
                                           response_schema=EXTRACT_SCHEMA,
                                           thinking_config=types.ThinkingConfig(thinking_level=settings.thinking_level or "LOW")))
    from .pipeline import parse_json_text
    data = parse_json_text(resp.text)
    usage = resp.usage_metadata
    return {
        "document_type": data.get("document_type") or "other",
        "products": data.get("products") or [],
        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
        "output_tokens": (getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0),
        "latency_ms": int((time.time() - t0) * 1000),
        "model": model,
    }


# ── SKU matching ─────────────────────────────────────────────────────────────

def norm_model(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def title_sim(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / max(1, len(A | B))


def match_sku(ex: dict, products: list[dict], limit: int = 5) -> list[dict]:
    mn = norm_model(ex.get("model_number") or "")
    jan = re.sub(r"\D", "", ex.get("jan") or "")
    hint = (ex.get("sku_hint") or "").strip()
    out = []
    for p in products:
        raw = p.get("raw_attributes") or {}
        score, why = 0.0, ""
        if hint and hint == p["sku"]:
            score, why = 1.0, "SKU match"
        elif jan and jan in (re.sub(r"\D", "", str(raw.get("jan_code") or "")), p["sku"]):
            score, why = 0.99, "JAN match"
        else:
            pm = norm_model(str(raw.get("model_number") or ""))
            if mn and pm and mn == pm:
                score, why = 0.98, "Model number match"
            elif mn and len(mn) >= 5 and mn in norm_model(p.get("title") or ""):
                score, why = 0.9, "Model number in title"
            elif mn and pm and len(mn) >= 5 and (mn.startswith(pm) or pm.startswith(mn)):
                score, why = 0.8, "Model number prefix match"
            else:
                s = title_sim(ex.get("title") or "", p.get("title") or "")
                if s >= 0.25:
                    score, why = round(0.4 + s * 0.4, 3), f"Title similarity {s:.0%}"
        if score:
            out.append({"sku": p["sku"], "title": p.get("title") or "", "image_url": p.get("image_url") or "",
                        "score": score, "why": why})
    out.sort(key=lambda m: -m["score"])
    return out[:limit]


# ── Merge ────────────────────────────────────────────────────────────────────

IDENTITY_FIELDS = {"title", "brand", "category"}


def _same(a, b) -> bool:
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _blank(v) -> bool:
    return v is None or str(v).strip() == ""


def merge(product: dict | None, ex: dict, mode: str, sku: str, file_name: str, file_uri: str,
          group_fn=None) -> tuple[dict, list[dict]]:
    """Return (merged_product, diff). mode: fill (only empty fields) | overwrite."""
    is_new = product is None
    base = json.loads(json.dumps(product)) if product else {
        "sku": sku, "title": "", "brand": "", "category": "", "description": "", "product_url": "", "image_url": "",
        "price": None, "group": None, "raw_attributes": {}, "doc_sources": [],
    }
    diff: list[dict] = []

    def put(field: str, label: str, before, after, setter):
        if _blank(after):
            return
        if is_new:
            action = "new"
        elif _blank(before):
            action = "add"
        elif _same(before, after):
            action = "keep"
        elif field in IDENTITY_FIELDS:
            action = "keep"   # never replace an existing product name/brand/category with document wording
        else:
            action = "overwrite" if mode == "overwrite" else "keep"
        if action in ("new", "add", "overwrite"):
            setter(after)
        diff.append({"field": field, "label": label, "before": "" if before is None else str(before),
                     "after": str(after), "action": action})

    put("title", "Product name", base.get("title"), ex.get("title"), lambda v: base.__setitem__("title", v))
    put("brand", "Brand", base.get("brand"), ex.get("brand"), lambda v: base.__setitem__("brand", v))
    put("category", "Category", base.get("category"), ex.get("category"), lambda v: base.__setitem__("category", v))
    put("price", "Price", base.get("price"), ex.get("price"), lambda v: base.__setitem__("price", float(v)))
    put("description", "Description", base.get("description"), ex.get("description"),
        lambda v: base.__setitem__("description", v))
    raw = base.setdefault("raw_attributes", {})
    if ex.get("model_number"):
        put("attr:model_number", "Model number", raw.get("model_number"), ex["model_number"],
            lambda v: raw.__setitem__("model_number", v))
    docs = [d for d in base.get("doc_sources") or [] if d.get("file_uri") != file_uri]
    for a in ex.get("attributes") or []:
        k = re.sub(r"[^a-z0-9_]", "_", str(a.get("key") or "").lower()).strip("_")
        if not k:
            continue
        before = raw.get(k)
        n_before = len(diff)
        put(f"attr:{k}", a.get("label") or k, before, a.get("value"), lambda v, k=k: raw.__setitem__(k, v))
        if len(diff) > n_before:
            # keep the document statement as evidence even when the catalog value is kept (corroboration / allergen box)
            docs.append({"file_name": file_name, "file_uri": file_uri, "page": a.get("page"), "key": k,
                         "label": a.get("label") or k, "value": a.get("value"), "evidence": a.get("evidence") or ""})
    base["doc_sources"] = docs
    if not base.get("title"):
        base["title"] = ex.get("model_number") or sku
    if not base.get("group"):
        from .schema import category_group
        base["group"] = category_group((base.get("category") or "") + " " + (base.get("title") or ""))
    base["source"] = "doc"
    return base, diff
