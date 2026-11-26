"""Gate 1/2 rules, food-safety allergen evidence rule and the 3-stage gate decision."""
import os

os.environ["OFFLINE"] = "true"

from app.config import settings  # noqa: E402
from app.pipeline import process_stage1  # noqa: E402
from app.rules import (AUTO_APPROVED, NEEDS_REVIEW, REJECTED, allergen_mentions, check_allergen_evidence,  # noqa: E402
                       check_consistency, check_limits, composite_score, decide, evidence_match)

settings.offline = True


def test_evidence_match_levels():
    src = "Net weight: 3 fillets (approx. 240 g) / Origin: Shizuoka, Japan"
    assert evidence_match("Origin: Shizuoka, Japan", src) == 1.0
    assert evidence_match("Origin:  Shizuoka Japan", src) == 0.8          # NFKC / whitespace / punctuation squash
    assert evidence_match("Swordfish caught off Shizuoka", src, "Shizuoka, Japan") == 0.5  # only the raw value is found
    assert evidence_match("Hokkaido", src) == 0.0


def test_composite_score_weights():
    assert composite_score(1.0, 1.0, 1.0) == 1.0
    assert composite_score(1.0, 0.8, None) == round(0.6 + 0.32, 3)
    assert composite_score(0.5, 0.9, 0.9) == round(0.25 + 0.27 + 0.18, 3)


def test_limits_reject():
    assert check_limits("net_weight_g", 50000)       # 50 kg of food in one pack -> physically implausible
    assert check_limits("net_weight_g", 240) is None
    assert check_limits("width_cm", 900)


def test_decide_three_stages():
    th = 0.9
    assert decide(True, "", "Outside physical limits", None, 1.0, 1.0, th)[0] == REJECTED
    assert decide(False, "no unit", None, None, 1.0, 1.0, th)[0] == NEEDS_REVIEW
    assert decide(True, "", None, None, 0.0, 0.95, th)[0] == NEEDS_REVIEW
    assert decide(True, "", None, None, 1.0, 0.95, th)[0] == AUTO_APPROVED
    st, gate, reason = decide(True, "", None, None, 1.0, 0.95, th, "no allergen evidence")
    assert st == NEEDS_REVIEW and gate == "gate1" and "Food safety rule" in reason


def test_consistency_frozen_title():
    issues = check_consistency({"storage_method": "Chilled", "_group": "prepared"}, "Cymbal Frozen Carbonara 260 g")
    assert "storage_method" in issues
    assert not check_consistency({"storage_method": "Frozen", "_group": "prepared"}, "Cymbal Frozen Carbonara 260 g")


# ── allergen evidence rule ───────────────────────────────────────────────────
SRC = ("[Title] Cymbal Shrimp Pilaf 450 g\n[Description] Pilaf packed with savory shrimp.\n"
       "Net weight: 450 g / Storage: Frozen / Allergens: shrimp, wheat, milk\n[Attributes]\nallergens: shrimp / wheat / milk")


def test_allergen_ok_when_statement_quoted():
    assert check_allergen_evidence("shrimp, wheat, milk", "Allergens: shrimp, wheat, milk", 1.0, SRC,
                                   "Cymbal Shrimp Pilaf 450 g") is None


def test_allergen_review_when_not_grounded():
    assert check_allergen_evidence("shrimp, wheat, milk", "Shrimp Pilaf", 1.0, SRC, "Cymbal Shrimp Pilaf 450 g")  # no context
    assert check_allergen_evidence("shrimp, wheat, milk", "Allergens: shrimp, wheat, milk", 0.0, SRC, "")  # not in source
    assert check_allergen_evidence("shrimp, wheat, milk, egg", "Allergens: shrimp, wheat, milk", 1.0, SRC, "")  # egg missing


def test_allergen_title_mentions_mandatory_item():
    r = check_allergen_evidence("wheat, milk", "Allergens: wheat, milk", 1.0, "Allergens: wheat, milk", "Cymbal Shrimp Gratin")
    assert r and "shrimp" in r


def test_allergen_none_only_structured_needs_review():
    src = "[Description] Slowly pickled in sake lees.\n[Attributes]\nallergens: none"
    r = check_allergen_evidence("none", "allergens: none", 1.0, src, "Narazuke Pickles", "attributes")
    assert r and "structured attribute" in r
    assert check_allergen_evidence("none", "Allergens: none", 1.0, "Allergens: none", "Narazuke Pickles", "description") is None


def test_allergen_mentions_false_hits():
    assert "wheat" not in allergen_mentions("Cymbal Buckwheat Soba 3 servings", only=["wheat"])
    assert "buckwheat" in allergen_mentions("New Year tempura buckwheat soba", only=["buckwheat"])
    assert "milk" not in allergen_mentions("Soy milk 1000 ml")
    assert "egg" not in allergen_mentions("Grilled eggplant")


FOOD = {"sku": "F1", "title": "Cymbal Shrimp Pilaf 450 g", "brand": "Cymbal", "category": "Frozen > Frozen Rice",
        "group": "prepared",
        "description": "Pilaf packed with savory shrimp.\nNet weight: 450 g / Storage: Frozen / Allergens: shrimp, wheat, milk",
        "raw_attributes": {}}


def test_pipeline_missing_allergen_goes_to_review():
    p = {**FOOD, "description": "Pilaf packed with savory shrimp. Net weight: 450 g"}
    recs = process_stage1(p, {"attributes": [
        {"key": "net_weight_g", "raw_value": "450 g", "evidence": "Net weight: 450 g", "source_field": "description",
         "confidence": 0.98}], "not_found": ["allergens"]}, None, "j", "m")
    al = next(r for r in recs if r["attr_key"] == "allergens")
    assert al["gate_status"] == NEEDS_REVIEW and al["value"] is None and "guess" in al["reason"]
    assert next(r for r in recs if r["attr_key"] == "net_weight_g")["gate_status"] == AUTO_APPROVED


def test_pipeline_guessed_allergen_is_not_auto_approved():
    p = {**FOOD, "description": "Pilaf packed with savory shrimp. Net weight: 450 g"}
    recs = process_stage1(p, {"attributes": [
        {"key": "allergens", "raw_value": "shrimp", "evidence": "savory shrimp", "source_field": "description",
         "confidence": 0.99}], "not_found": []}, None, "j", "m")
    assert recs[0]["gate_status"] == NEEDS_REVIEW and "Food safety rule" in recs[0]["reason"]


def test_pipeline_grounded_allergen_auto_approved():
    recs = process_stage1(FOOD, {"attributes": [
        {"key": "allergens", "raw_value": "Allergens: shrimp, wheat, milk", "evidence": "Allergens: shrimp, wheat, milk",
         "source_field": "description", "confidence": 0.99}], "not_found": []}, None, "j", "m")
    assert recs[0]["gate_status"] == AUTO_APPROVED and recs[0]["value"] == "shrimp, wheat, milk"


def test_pipeline_conflict_with_catalog_attribute():
    p = {**FOOD, "raw_attributes": {"allergens": "wheat / milk"}}
    recs = process_stage1(p, {"attributes": [
        {"key": "allergens", "raw_value": "Allergens: shrimp, wheat, milk", "evidence": "Allergens: shrimp, wheat, milk",
         "source_field": "description", "confidence": 0.99}], "not_found": []}, None, "j", "m")
    assert recs[0]["gate_status"] == NEEDS_REVIEW and "does not match" in recs[0]["reason"]
