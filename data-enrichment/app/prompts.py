"""Prompts for multi-stage extraction (Stage 1), classification (Stage 3) and GMC conversational attributes."""
from __future__ import annotations

from .schema import ALLERGENS_28, CATEGORY_GROUPS, LIST_SEP, NO_ALLERGENS, attrs_for_group

RETAILER = 'the grocery supermarket and omnichannel e-commerce retailer "Cymbal Retail"'


def source_text(p: dict) -> str:
    """Canonical source text used both for prompting and for evidence verification."""
    lines = [f"[Title] {p.get('title', '')}", f"[Brand] {p.get('brand', '')}",
             f"[Category] {p.get('category', '')}", f"[Description] {p.get('description', '')}", "[Attributes]"]
    for k, v in (p.get("raw_attributes") or {}).items():
        lines.append(f"{k}: {v}")
    docs = p.get("doc_sources") or []
    if docs:
        lines.append("[Documents]")
        for d in docs:
            where = f"{d.get('file_name', '')}" + (f" p.{d['page']}" if d.get("page") else "")
            lines.append(f"[{where}] {d.get('label') or d.get('key', '')}: {d.get('evidence') or d.get('value', '')}")
    return "\n".join(lines)


def stage1_prompt(p: dict, group: str) -> str:
    defs = "\n".join(f"- {a.key}: {a.label}" + (f" (final unit {a.unit})" if a.unit else "") + (f" ... {a.hint}" if a.hint else "")
                     for a in attrs_for_group(group))
    return f"""You maintain the product master data for {RETAILER}.
Extract the specified attributes from the source data below ({CATEGORY_GROUPS[group]}). Write everything in English.

## Strict rules
1. raw_value must be the value exactly as written in the source, INCLUDING its unit. Never convert units, calculate or round
   (conversion is done later by the system).
   Example: source "Net weight: 3 fillets (approx. 240 g)" -> raw_value for net_weight_g is "3 fillets (approx. 240 g)",
   and for pack_count also "3 fillets (approx. 240 g)".
   Example: source "W45 x D45 x H40 cm" -> raw_value for width_cm / depth_cm / height_cm is "W45 x D45 x H40 cm" in all three.
2. evidence must be a verbatim, contiguous substring of the source that supports the value (no summarizing or paraphrasing,
   max 80 characters).
3. If an attribute is not stated in the source, do NOT guess - put its key in not_found (hallucination prevention).
4. Do not infer from general knowledge (e.g. "it is shrimp tempura so the allergen is shrimp", "it is a pot so it is
   induction compatible") unless the source states it explicitly.
5. **allergens are the most important attribute for food safety.** Only when there is an explicit statement such as
   "Allergens:", "Contains:" or "Allergy information", copy that statement verbatim into raw_value
   (e.g. "Allergens: shrimp, wheat, egg, milk"). If there is no such statement, the key MUST go to not_found.
   The labelled allergens are these 28 items: {LIST_SEP.join(ALLERGENS_28)}. "{NO_ALLERGENS}" means none are labelled.
6. If a structured attribute ("key: value" under [Attributes]) has the value, source_field is "attributes" and evidence
   is copied in the form "key: value".
7. Self-assess confidence from 0.0 to 1.0 according to how clearly the source states the value.

## Attributes to extract
{defs}

## Source data
{source_text(p)}
"""


def stage3_prompt(p: dict, group: str) -> str:
    return f"""You are an e-commerce merchandising editor at {RETAILER}.
Read the source data below ({CATEGORY_GROUPS[group]}) and create standard feature tags, search keywords and a summary used
for search and filtering. Write everything in natural US English.

## Strict rules
1. Choose feature_tags only from the given standard tag dictionary (enum). Do not add tags without evidence in the source.
2. The evidence for each tag must be a verbatim substring of the source (max 80 characters).
3. Map differently worded expressions to the closest standard tag (e.g. "no bones, easy for kids to eat" ->
   Boneless / easy to eat, "just pan-fry" -> Time-saver).
4. Use the "In season" tag only when the source mentions a season or period (labels such as in season, season_months, etc.).
5. ai_summary must be 20-35 words using only facts in the source. Do not state allergens, health benefits or anything else
   that is not in the source.

## Source data
{source_text(p)}
"""


def gmc_prompt(p: dict, group: str) -> str:
    return f"""You are a specialist in Google Merchant Center (GMC) Conversational Attributes
(https://support.google.com/merchants/answer/17085370).
From the source data below for a product sold by {RETAILER} ({CATEGORY_GROUPS.get(group, group)}), create conversational
attribute data in natural US English so that AI search (e.g. AI Mode in Google Search) and conversational shopping agents
can answer shopper questions accurately.

## Strict rules
1. **item_group_id / item_group_title**:
   - If the source attribute base_id exists, use it as item_group_id as-is. Otherwise create a short ID from the common part
     of the product name without size, pack count or color.
   - item_group_title is the easy-to-understand series name without size, pack count, color, etc.
     (e.g. "Cymbal White Eggs").
2. **variant_options [variant_option]**:
   - Extract 2-4 properties that identify this product within its group (size, pack count, flavor / spiciness, color,
     origin, etc.), each with evidence.
3. **questions_and_answers [question_and_answer]**:
   - Write **3-5** concrete questions shoppers are likely to ask, with answers
     (food examples: amount / how many servings, storage, cooking method and time, origin, allergens, serving suggestions.
      household / home goods examples: size, material, induction / dishwasher / microwave compatibility, target pet or age,
      scent, care).
   - **Answers must be based ONLY on facts in the source data (no guessing or hallucination).**
   - Only write an allergen question when the source has an explicit allergen statement, and answer exactly as labelled.
     If there is no statement, do not write any allergen question.
   - Copy a verbatim contiguous substring of the source that supports each answer into evidence (max 80 characters).
4. **product_highlights [product_highlight]**:
   - Summarize the key selling points as short bullet points (3-5 items, max 80 characters each).
5. **related_mentions [related_product]**:
   - Extract related or separately sold products explicitly mentioned in the description (e.g. "goes well with ...",
     "pair it with ..."). If none, return an empty array `[]`.
6. **document_link**: the URL if the source contains one, otherwise an empty string (never invent one).

## Source data
{source_text(p)}
"""
