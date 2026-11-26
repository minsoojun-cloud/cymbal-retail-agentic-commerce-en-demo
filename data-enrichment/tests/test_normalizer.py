"""Stage 2 unit normalization (Cymbal grocery + GM)."""
import pytest

from app.normalizer import normalize, parse_allergens, parse_dimensions, parse_months


@pytest.mark.parametrize("key,raw,value,unit", [
    ("net_weight_g", "3 fillets (approx. 240 g)", 240, "g"),
    ("net_weight_g", "1kg", 1000, "g"),
    ("net_weight_g", "\uff11\uff0e\uff15\uff4b\uff47", 1500, "g"),   # fullwidth digits/letters are NFKC-normalized
    ("net_weight_g", "200 g x 3 bags", 600, "g"),
    ("net_weight_g", "450 g (150 g x 3)", 450, "g"),
    ("net_weight_g", "1,200 g", 1200, "g"),
    ("net_volume_ml", "1.8 L", 1800, "ml"),
    ("net_volume_ml", "700ml x 3", 700, "ml"),
    ("net_volume_ml", "Refill, large size 1200 ml", 1200, "ml"),
    ("pack_count", "10 pcs", 10, "pcs"),
    ("pack_count", "3 fillets (approx. 240 g)", 3, "pcs"),
    ("pack_count", "40 g x 12 bags", 12, "pcs"),
    ("pack_count", "Fresh Pacific saury from Hokkaido, 2 whole fish", 2, "pcs"),
    ("pack_count", "Pack of 6", 6, "pcs"),
    ("servings", "Serves 4", 4, "servings"),
    ("servings", "10 servings", 10, "servings"),
    ("best_before_days", "12 months from manufacture", 360, "days"),
    ("best_before_days", "1 year", 365, "days"),
    ("best_before_days", "180 days", 180, "days"),
    ("cook_time_min", "Microwave at 600 W for approx. 2 min 30 sec", 2.5, "min"),
    ("cook_time_min", "5 minutes", 5, "min"),
    ("width_cm", "W45 x D45 x H40 cm", 45, "cm"),
    ("depth_cm", "W42 x D54 x H42 cm", 54, "cm"),
    ("height_cm", "Width 45 x Depth 45 x Height 40 cm", 40, "cm"),
    ("width_cm", "37 x 22 cm", 37, "cm"),
    ("depth_cm", "370 x 220 mm", 22, "cm"),
    ("diameter_cm", "Diameter 26 x depth 7.5 cm", 26, "cm"),
    ("diameter_cm", "28 cm", 28, "cm"),
])
def test_numeric(key, raw, value, unit):
    r = normalize(key, raw)
    assert r.ok, r.note
    assert r.value == pytest.approx(value) and r.unit == unit


@pytest.mark.parametrize("raw,value", [
    ("Chilled food", "Chilled"), ("Keep refrigerated (10\u00b0C or below)", "Chilled"),
    ("Store at -18\u00b0C or below", "Frozen"), ("Frozen food", "Frozen"),
    ("Store away from direct sunlight and high humidity", "Ambient"), ("Ambient", "Ambient"),
])
def test_storage(raw, value):
    r = normalize("storage_method", raw)
    assert r.ok and r.value == value


def test_storage_unknown_fails():
    assert not normalize("storage_method", "Best enjoyed as soon as possible").ok


@pytest.mark.parametrize("raw,value", [
    ("Allergens: shrimp, wheat, egg, milk", "shrimp, wheat, egg, milk"),
    ("Allergens: milk ingredients, soybean", "milk, soybean"),
    ("Contains: prawns, crab", "shrimp, crab"),
    ("wheat, soy, chicken", "wheat, soybean, chicken"),
    ("Allergens: none", "none"),
    ("allergens: none", "none"),
    ("ALLERGY INFORMATION - Allergens: wheat, egg and milk", "wheat, egg, milk"),
])
def test_allergens(raw, value):
    r = normalize("allergens", raw)
    assert r.ok, r.note
    assert r.value == value


def test_allergens_unknown_token_fails():
    items, note = parse_allergens("Allergens: wheat, nutmeg")
    assert items is None and "nutmeg" in note
    assert not normalize("allergens", "Allergens: wheat, nutmeg").ok


def test_allergen_order_is_canonical():
    items, _ = parse_allergens("milk, shrimp, wheat")
    assert items == ["shrimp", "wheat", "milk"]


def test_months():
    assert parse_months("Oct-Feb") == [10, 11, 12, 1, 2]
    assert parse_months("October to February") == [10, 11, 12, 1, 2]
    assert parse_months("10 / 11 / 12") == [10, 11, 12]
    assert normalize("season", "In season: Sep-Nov").value == "Sep-Nov"
    assert normalize("season", "autumn").value == "Sep-Nov"
    assert normalize("season", "Mar, Apr").value == "Mar, Apr"


def test_bool_and_pet():
    assert normalize("ih_compatible", "Induction and gas compatible").value is True
    assert normalize("ih_compatible", "Gas only").value is False
    assert normalize("dishwasher_safe", "Not dishwasher safe").value is False
    assert normalize("target_pet", "Dry food for senior dogs").value == "Dog"
    assert normalize("target_pet", "Cat food pouch").value == "Cat"


def test_cooking_and_list():
    assert normalize("cooking_method", "Microwave or heat in a frying pan").value == "Microwave, Frying pan"
    assert normalize("dishes", "teriyaki / meuniere, bento").value == "teriyaki, meuniere, bento"


def test_dimensions_assumed_order():
    d = parse_dimensions("45 x 45 x 40 cm")
    assert d["w"] == 45 and d["d"] == 45 and d["h"] == 40 and d["_assumed"]


def test_unknown_key():
    assert not normalize("tatami", "6 mats").ok
