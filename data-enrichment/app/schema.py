"""Target (golden-record) attribute schema and Gemini responseSchema definitions — Cymbal Retail (grocery + GM)."""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Category groups ──────────────────────────────────────────────────────────
CATEGORY_GROUPS = {
    "fresh": "Fresh (produce, meat, seafood)",
    "prepared": "Deli, frozen & staples",
    "grocery": "Grocery & beverages",
    "kitchen": "Kitchen & cookware",
    "living": "Household & home goods",
    "beauty": "Beauty & personal care",
    "pet": "Pet supplies",
}
GROUP_SHORT = {"fresh": "Fresh", "prepared": "Deli & frozen", "grocery": "Grocery", "kitchen": "Kitchen",
               "living": "Household", "beauty": "Beauty", "pet": "Pet"}
FOOD_GROUPS = ("fresh", "prepared", "grocery")
GM_GROUPS = ("kitchen", "living", "beauty", "pet")

# Cymbal top categories (attributes.top_category / first segment of categories[0], see shared/catalog/category_map_en.json)
TOP_CATEGORY_GROUP = {
    "Produce": "fresh", "Meat": "fresh", "Seafood": "fresh", "Flowers": "fresh",
    "Frozen": "prepared", "Deli & Meal Kits": "prepared", "Rice & Noodles": "prepared",
    "Seasonings & Sauces": "grocery", "Pantry": "grocery", "Snacks & Sweets": "grocery", "Eggs & Dairy": "grocery",
    "Tofu & Natto": "grocery", "Beverages": "grocery", "Bakery": "grocery", "Alcohol": "grocery", "Baby": "grocery",
    "Health & Medicine": "beauty",
    "Kitchen": "kitchen", "Household": "living", "Home & Living": "living",
    "Beauty & Personal Care": "beauty", "Pet": "pet",
}


def category_group(category_path: str) -> str:
    """Map a product category path ('A > B > C') or free text to a category group key."""
    p = category_path or ""
    top = p.split(">")[0].strip()
    if top in TOP_CATEGORY_GROUP:
        return TOP_CATEGORY_GROUP[top]
    pl = p.lower()
    for k, g in TOP_CATEGORY_GROUP.items():
        if k.lower() in pl:
            return g
    # free-text fallbacks (CSV / document ingest)
    rules = (("pet", ("pet", "dog", "cat food", "cat ", "puppy", "kitten")),
             ("beauty", ("cosmetic", "skin care", "skincare", "shampoo", "bath salt", "hand cream", "makeup", "body wash",
                         "lotion")),
             ("kitchen", ("frying pan", "fry pan", "skillet", "pot", "knife", "cutting board", "food container",
                          "cookware", "kitchen tool", "tableware", "kettle")),
             ("living", ("detergent", "tissue", "sponge", "umbrella", "towel", "storage box", "home goods", "household")),
             ("prepared", ("frozen", "deli", "bento", "meal kit", "retort", "cup noodle", "instant", "rice")),
             ("fresh", ("fresh fish", "fillet", "vegetable", "fruit", "beef", "pork", "chicken", "sashimi")))
    for g, words in rules:
        if any(w in pl for w in words):
            return g
    return "grocery"


@dataclass(frozen=True)
class AttrDef:
    key: str
    label: str
    type: str  # number | text | bool
    unit: str = ""
    kind: str = ""  # normalizer kind
    groups: tuple[str, ...] = FOOD_GROUPS
    limits: tuple[float, float] | None = None
    raw_keys: tuple[str, ...] = field(default_factory=tuple)  # source attribute keys that carry the same info
    hint: str = ""   # extraction hint shown to Gemini


ALL = FOOD_GROUPS + GM_GROUPS
ATTRS: list[AttrDef] = [
    # ── amount / size (normalised by Python) ───────────────────────────────
    AttrDef("net_weight_g", "Net weight", "number", "g", "weight_g", FOOD_GROUPS + ("pet",), (1, 30000), ("size",),
            "Net content in g/kg (e.g. approx. 240 g, 1 kg, 200 g x 3 bags)"),
    AttrDef("net_volume_ml", "Volume / capacity", "number", "ml", "volume_ml", ("grocery", "prepared", "kitchen", "living", "beauty"),
            (1, 30000), ("size", "capacity"), "Content or container capacity in ml/L (e.g. 500 ml, 1.8 L, 700 ml x 3)"),
    AttrDef("pack_count", "Pack count", "number", "pcs", "count", ALL, (1, 500), ("size",),
            "Number of pieces / fillets / bags / servings / packs in the package (e.g. 10 pcs, 3 fillets, 5 servings)"),
    AttrDef("servings", "Servings", "number", "servings", "servings", ("prepared", "grocery"), (1, 50), (),
            "e.g. \"serves 4\", \"10 servings\""),
    # ── food facts ─────────────────────────────────────────────────────────
    AttrDef("origin", "Origin / country of origin", "text", "", "origin", ALL, None, ("origin",),
            "e.g. \"Shizuoka, Japan\", \"Japan\", \"USA\", \"Made in Japan\""),
    AttrDef("storage_method", "Storage", "text", "", "storage", FOOD_GROUPS, None, ("storage_type",),
            "Statement that maps to Chilled / Frozen / Ambient (keep refrigerated, frozen food, store at room temperature away from direct sunlight, etc.)"),
    AttrDef("allergens", "Allergens", "text", "", "allergens", FOOD_GROUPS + ("pet",), None, ("allergens",),
            "Copy the \"Allergens:\" / \"Contains:\" statement verbatim. If there is none, it MUST go to not_found (never guess)"),
    AttrDef("best_before_days", "Best before (shelf life)", "number", "days", "days", ("prepared", "grocery"), (1, 3650), (),
            "e.g. \"Best before: 180 days from manufacture\", \"1 year\". If not stated, not_found"),
    AttrDef("cook_time_min", "Cooking time", "number", "min", "minutes", FOOD_GROUPS, (0.2, 600), (),
            "Time to cook / heat (e.g. \"microwave approx. 3 min\", \"ready in 5 minutes\")"),
    AttrDef("cooking_method", "Cooking method", "text", "", "cooking", ("prepared", "grocery", "fresh"), None, (),
            "Microwave / Frying pan / Boil-in-bag / Pot / Toaster / Oven / Ready to eat, etc."),
    AttrDef("dishes", "Suggested dishes / uses", "text", "", "list", FOOD_GROUPS, None, ("dishes",),
            "Dish names or uses written in the source (e.g. teriyaki, meuniere, bento)"),
    AttrDef("season", "Peak season", "text", "", "months", ("fresh",), None, ("season_months",),
            "Season statements such as \"in season\", \"autumn favorite\", \"Oct-Feb\""),
    AttrDef("flavor", "Flavor / spiciness", "text", "", "text", ("prepared", "grocery"), None, (),
            "e.g. \"medium hot\", \"salted\", \"soy sauce\""),
    # ── general merchandise ────────────────────────────────────────────────
    AttrDef("material", "Material", "text", "", "text", GM_GROUPS, None, ("material",),
            "e.g. \"aluminum alloy, fluororesin (non-stick) coating\""),
    AttrDef("width_cm", "Width", "number", "cm", "dim_w", ("kitchen", "living", "pet"), (0.5, 400), ("size",),
            "Copy W x D x H statements verbatim as one value"),
    AttrDef("depth_cm", "Depth", "number", "cm", "dim_d", ("kitchen", "living", "pet"), (0.5, 400), ("size",), ""),
    AttrDef("height_cm", "Height", "number", "cm", "dim_h", ("kitchen", "living", "pet"), (0.5, 400), ("size",), ""),
    AttrDef("diameter_cm", "Diameter", "number", "cm", "diameter", ("kitchen",), (1, 100), ("size",),
            "e.g. \"diameter 26 cm\", \"26 cm\""),
    AttrDef("color", "Color", "text", "", "text", GM_GROUPS, None, ("color",), ""),
    AttrDef("ih_compatible", "Induction (IH) compatible", "bool", "", "bool", ("kitchen",), None, (),
            "e.g. \"induction compatible\", \"gas only\""),
    AttrDef("dishwasher_safe", "Dishwasher safe", "bool", "", "bool", ("kitchen",), None, (),
            "e.g. \"dishwasher safe\", \"not dishwasher safe\""),
    AttrDef("microwave_safe", "Microwave safe", "bool", "", "bool", ("kitchen",), None, (), ""),
    AttrDef("target_pet", "Pet type", "text", "", "pet", ("pet",), None, (), "Dog / Cat / Dog & Cat / Small animal"),
    AttrDef("target_age", "Life stage / age", "text", "", "text", ("pet",), None, (), "e.g. \"adult dog\", \"7+ years\", \"kitten\""),
    AttrDef("scent", "Scent", "text", "", "text", ("beauty", "living"), None, (), "e.g. \"unscented\", \"soap scent\""),
    AttrDef("skin_type", "Skin / hair type", "text", "", "text", ("beauty",), None, (),
            "e.g. \"sensitive skin\", \"dry skin\", \"all skin types\""),
]
ATTR_BY_KEY = {a.key: a for a in ATTRS}
ALL_ATTRIBUTE_KEYS = [a.key for a in ATTRS]


def attrs_for_group(group: str) -> list[AttrDef]:
    return [a for a in ATTRS if group in a.groups]


# ── Food allergens (Japan labelling standard: 8 mandatory + 20 recommended = 28 items), English canonical names ──
ALLERGENS_MANDATORY = ["shrimp", "crab", "walnut", "wheat", "buckwheat", "egg", "milk", "peanut"]
ALLERGENS_RECOMMENDED = ["almond", "abalone", "squid", "salmon roe", "orange", "cashew nut", "kiwifruit", "beef",
                         "sesame", "salmon", "mackerel", "soybean", "chicken", "banana", "pork", "matsutake", "peach",
                         "yam", "apple", "gelatin"]
ALLERGENS_28 = ALLERGENS_MANDATORY + ALLERGENS_RECOMMENDED
NO_ALLERGENS = "none"
LIST_SEP = ", "
# spelling variants (lowercase, matched on word boundaries) -> canonical
ALLERGEN_VARIANTS: dict[str, list[str]] = {
    "shrimp": ["shrimp", "shrimps", "prawn", "prawns"], "crab": ["crab", "crabs"], "walnut": ["walnut", "walnuts"],
    "wheat": ["wheat", "wheat flour"], "buckwheat": ["buckwheat", "soba flour"], "egg": ["egg", "eggs"],
    "milk": ["milk", "dairy", "milk ingredients", "milk products"], "peanut": ["peanut", "peanuts", "groundnut"],
    "almond": ["almond", "almonds"], "abalone": ["abalone"], "squid": ["squid"], "salmon roe": ["salmon roe", "ikura"],
    "orange": ["orange", "oranges"], "cashew nut": ["cashew nut", "cashew nuts", "cashew", "cashews"],
    "kiwifruit": ["kiwifruit", "kiwi fruit", "kiwi"], "beef": ["beef"], "sesame": ["sesame", "sesame seeds"],
    "salmon": ["salmon"], "mackerel": ["mackerel"], "soybean": ["soybean", "soybeans", "soy", "soya"],
    "chicken": ["chicken"], "banana": ["banana", "bananas"], "pork": ["pork"],
    "matsutake": ["matsutake", "matsutake mushroom"], "peach": ["peach", "peaches"],
    "yam": ["yam", "yams", "nagaimo"], "apple": ["apple", "apples"], "gelatin": ["gelatin", "gelatine"],
}

# ── Standard feature tag dictionary (Stage 3) ─────────────────────────────────
FEATURE_TAGS: dict[str, list[str]] = {
    "fresh": ["In season", "Domestic (Japan)", "Freshness guaranteed", "Pre-cut / prepped", "Boneless / easy to eat",
              "Single-use size", "Value pack / bulk", "Organic", "Good for bento", "Kid favorite", "Time-saver",
              "Great for hot pot", "Great for BBQ", "Entertaining"],
    "prepared": ["Time-saver", "Microwavable", "Frying pan", "Boil-in-bag", "Just heat", "Freezer storage",
                 "Individually wrapped", "Good for bento", "Snack / appetizer", "Kid favorite",
                 "Domestic ingredients", "Reduced salt", "Great for hot pot", "Entertaining", "Party"],
    "grocery": ["Domestic ingredients", "Reduced salt", "Low carb", "No additives", "Organic", "Individually wrapped",
                "Value pack / bulk", "Shelf-stable", "Gift", "Kid favorite", "Snack / appetizer", "Breakfast",
                "Time-saver", "Regional specialty", "Great for hot pot"],
    "kitchen": ["Induction compatible", "Gas compatible", "Dishwasher safe", "Microwave safe", "Freezer safe",
                "Oven safe", "Non-stick", "Lightweight", "Stackable", "Made in Japan", "Gift", "Space-saving"],
    "living": ["Made in Japan", "Refill", "Value pack / bulk", "Antibacterial / deodorizing", "UV protection",
               "Rain or shine", "Washable", "Space-saving", "Gift", "Unscented", "Lightweight"],
    "beauty": ["Unscented", "For sensitive skin", "Moisturizing", "Refill", "Gift", "Made in Japan", "Organic",
               "UV protection", "Travel / mini size"],
    "pet": ["For dogs", "For cats", "Domestic (Japan)", "No additives", "For seniors", "For puppies / kittens",
            "Washable", "Warming", "Cooling", "Grain-free", "Treats", "Gift"],
}

# ── Gemini responseSchema (OpenAPI subset used by Vertex AI) ─────────────────


def stage1_schema(group: str) -> dict:
    keys = [a.key for a in attrs_for_group(group)]
    return {
        "type": "OBJECT",
        "properties": {
            "attributes": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "key": {"type": "STRING", "enum": keys},
                        "raw_value": {"type": "STRING", "description": "Value exactly as written in the source, including units (do not convert)"},
                        "evidence": {"type": "STRING", "description": "Verbatim contiguous substring of the source that supports the value (max 80 characters)"},
                        "source_field": {"type": "STRING", "enum": ["title", "description", "attributes", "documents"]},
                        "confidence": {"type": "NUMBER", "description": "Self-assessed confidence 0.0-1.0"},
                    },
                    "required": ["key", "raw_value", "evidence", "source_field", "confidence"],
                },
            },
            "not_found": {"type": "ARRAY", "items": {"type": "STRING", "enum": keys},
                          "description": "Attribute keys not stated in the source (never fill by guessing)"},
        },
        "required": ["attributes", "not_found"],
    }


def stage3_schema(group: str) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "feature_tags": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "tag": {"type": "STRING", "enum": FEATURE_TAGS[group]},
                        "evidence": {"type": "STRING", "description": "Verbatim source substring supporting the tag (max 80 characters)"},
                        "confidence": {"type": "NUMBER"},
                    },
                    "required": ["tag", "evidence", "confidence"],
                },
            },
            "search_keywords": {"type": "ARRAY", "items": {"type": "STRING"},
                                "description": "English keywords shoppers are likely to search for (5-8, grounded in source facts)"},
            "ai_summary": {"type": "STRING", "description": "Summary of the product's key features in 20-35 words (no facts that are not in the source)"},
        },
        "required": ["feature_tags", "search_keywords", "ai_summary"],
    }


def gmc_schema() -> dict:
    """Google Merchant Center Conversational Attributes schema (https://support.google.com/merchants/answer/17085370)."""
    return {
        "type": "OBJECT",
        "properties": {
            "item_group_id": {
                "type": "STRING",
                "description": "Group ID shared by size / pack count / flavor variants (use the source attribute base_id as-is if present)",
            },
            "item_group_title": {
                "type": "STRING",
                "description": "Product group title [item_group_title] (common name without size, pack count, color; e.g. Cymbal White Eggs)",
            },
            "variant_options": {
                "type": "ARRAY",
                "description": "Variant options [variant_option] (name:value pairs such as size, pack count, flavor, color, origin)",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING", "description": "Attribute name (e.g. Size, Pack count, Flavor, Color)"},
                        "value": {"type": "STRING", "description": "Attribute value (e.g. 400 g, 10 pcs, Medium, Medium hot, Navy)"},
                        "evidence": {"type": "STRING", "description": "Verbatim source substring (max 80 characters)"},
                    },
                    "required": ["name", "value", "evidence"],
                },
            },
            "questions_and_answers": {
                "type": "ARRAY",
                "description": "Questions and answers [question_and_answer] (3-5 concrete questions shoppers ask AI search, with accurate answers based only on source facts)",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "question": {"type": "STRING", "description": "Natural shopper question (ends with ?)"},
                        "answer": {"type": "STRING", "description": "Concise, accurate answer based on source facts"},
                        "evidence": {"type": "STRING", "description": "Verbatim contiguous source substring supporting the answer (max 80 characters)"},
                        "confidence": {"type": "NUMBER", "description": "Self-assessed confidence 0.0-1.0"},
                    },
                    "required": ["question", "answer", "evidence", "confidence"],
                },
            },
            "product_highlights": {
                "type": "ARRAY",
                "description": "Product highlights [product_highlight] (3-5 key selling points, max 80 characters each)",
                "items": {"type": "STRING"},
            },
            "related_mentions": {
                "type": "ARRAY",
                "description": "Related products [related_product] (items explicitly mentioned in the description as used together / sold separately)",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "relationship_type": {
                            "type": "STRING",
                            "enum": ["often_bought_with", "accessory", "required_part", "similar_to", "substitute"],
                        },
                        "identifier_type": {"type": "STRING", "enum": ["id", "mpn", "gtin"]},
                        "identifier": {"type": "STRING", "description": "Product ID / GTIN (name if unknown)"},
                        "title": {"type": "STRING", "description": "Related product name"},
                    },
                    "required": ["relationship_type", "identifier_type", "identifier", "title"],
                },
            },
            "document_link": {
                "type": "STRING",
                "description": "Document link [document_link] (URL of a PDF / manual if present in the source, else empty string)",
            },
        },
        "required": [
            "item_group_id",
            "item_group_title",
            "variant_options",
            "questions_and_answers",
            "product_highlights",
            "related_mentions",
            "document_link",
        ],
    }
