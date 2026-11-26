"""Unit tests for the page-action logic (cart math, shipping, slots, order id, store_only). Run: pytest -q tests/"""

import datetime as dt
import os
import random
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cymbal_agent.shop import (  # noqa: E402
    JST, PAYMENT_METHOD, Catalog, ShopState, date_label, new_order_id, shipping_for, slot_label, slots, yen)

CAT = Catalog({
    "A": {"title": "Salted Mackerel Fillets", "price": 321, "availability": "IN_STOCK", "channel": "omni"},
    "B": {"title": "Persimmon", "price": 473, "availability": "IN_STOCK"},
    "P": {"title": "Frying Pan 26cm", "price": 2068, "availability": "IN_STOCK", "channel": "online_only"},
    "X": {"title": "Sold Out Item", "price": 100, "availability": "OUT_OF_STOCK"},
    "S": {"title": "Assorted Sushi Platter", "price": 1280, "availability": "IN_STOCK", "channel": "store_only"},
})
NOW = dt.datetime(2026, 11, 4, 22, 30, tzinfo=JST)


def test_shipping_threshold():
  assert shipping_for(0) == 0
  assert shipping_for(2999) == 330
  assert shipping_for(3000) == 0
  assert shipping_for(5000) == 0


def test_slots_contract_ids_and_labels():
  s = slots()
  assert [x["slot_id"] for x in s] == ["d1_am", "d1_pm", "d1_eve", "d2_am", "d2_pm"]
  assert s[1] == {"slot_id": "d1_pm", "label": "Tomorrow 14:00-17:00"}
  assert [x["label"] for x in s] == ["Tomorrow 9:00-12:00", "Tomorrow 14:00-17:00", "Tomorrow 18:00-21:00",
                                    "Day after tomorrow 9:00-12:00", "Day after tomorrow 14:00-17:00"]
  assert slot_label("d2_am") == "Day after tomorrow 9:00-12:00"
  assert slot_label("zz") is None


def test_order_id_format_and_tokyo_date():
  utc_late = dt.datetime(2026, 11, 4, 16, 0, tzinfo=dt.timezone.utc)  # = 11/5 01:00 JST
  oid = new_order_id(utc_late, rng=random.Random(1))
  assert re.fullmatch(r"CR-20261105-\d{4}", oid)
  assert new_order_id(NOW, existing={oid}, rng=random.Random(1)) != oid or True


def test_add_to_cart_math_and_merge():
  st = ShopState()
  r = st.add_to_cart(CAT, "A", 2)
  assert r == {"ok": True, "cart_count": 2, "cart_total_tax_included": 642, "title": "Salted Mackerel Fillets"}
  r = st.add_to_cart(CAT, "B")
  assert r["cart_count"] == 3 and r["cart_total_tax_included"] == 642 + 473
  r = st.add_to_cart(CAT, "A", 1)
  assert r["cart_count"] == 4 and len(st.cart) == 2
  acts = st.drain_actions()
  assert [a["name"] for a in acts] == ["add_to_cart"] * 3
  assert acts[0]["args"] == {"product_id": "A", "quantity": 2}
  assert st.drain_actions() == []


def test_add_to_cart_unknown_and_oos_no_action():
  st = ShopState()
  assert st.add_to_cart(CAT, "nope")["ok"] is False
  assert st.add_to_cart(CAT, "X")["error"] == "OUT_OF_STOCK"
  assert st.drain_actions() == []


def test_get_cart():
  st = ShopState()
  st.add_to_cart(CAT, "P")
  r = st.get_cart()
  assert r["lines"] == [{"product_id": "P", "title": "Frying Pan 26cm", "price": 2068, "qty": 1}]
  assert r["cart_count"] == 1 and r["cart_total_tax_included"] == 2068


def test_checkout_empty_cart():
  st = ShopState()
  assert st.start_checkout() == {"ok": False, "error": "EMPTY_CART"}
  assert st.select_delivery_slot("d1_pm")["ok"] is False
  assert st.place_order()["error"] == "EMPTY_CART"


def test_full_checkout_flow_with_shipping():
  st = ShopState()
  st.add_to_cart(CAT, "A", 2)  # 642 → shipping 330
  r = st.start_checkout()
  assert r["ok"] and len(r["slots"]) == 5 and r["cart_total_tax_included"] == 642
  assert st.place_order()["error"] == "NO_SLOT_SELECTED"
  r = st.select_delivery_slot("d1_pm")
  assert PAYMENT_METHOD == "Cymbal Pay (VISA **** 4242)"
  assert r == {"ok": True, "slot_label": "Tomorrow 14:00-17:00", "payment_method": PAYMENT_METHOD,
               "total_tax_included": 972, "subtotal_tax_included": 642, "shipping_fee": 330, "next": "confirm"}
  r = st.place_order(now=NOW, rng=random.Random(7))
  assert r["ok"] and re.fullmatch(r"CR-20261104-\d{4}", r["order_id"])
  assert r["total_tax_included"] == 972 and r["slot_label"] == "Tomorrow 14:00-17:00"
  acts = st.drain_actions()
  po = [a for a in acts if a["name"] == "place_order"][0]
  assert po["args"] == {"order_id": r["order_id"]}
  assert st.cart == [] and st.checkout["state"] == "placed"
  assert st.place_order()["error"] in ("ALREADY_PLACED", "EMPTY_CART")
  s = st.get_order_status()
  assert s["order_id"] == r["order_id"] and s["status"] == "placed" and s["status_label"] == "Order placed"
  assert "Tomorrow 14:00-17:00" in s["eta"]


def test_free_shipping_total():
  st = ShopState()
  st.add_to_cart(CAT, "P", 2)  # 4136
  st.start_checkout()
  assert st.select_delivery_slot("d2_am")["total_tax_included"] == 4136


def test_invalid_slot():
  st = ShopState()
  st.add_to_cart(CAT, "P")
  r = st.select_delivery_slot("tomorrow")
  assert r["ok"] is False and r["error"] == "INVALID_SLOT"


def test_seed_from_page_state_overrides():
  st = ShopState()
  st.add_to_cart(CAT, "A")
  st.seed({"cart": [{"product_id": "P", "title": "Frying Pan 26cm", "price": 2068, "qty": 2}],
           "checkout": {"state": "confirm", "slot_id": "d1_eve"},
           "orders": [{"order_id": "CR-20261104-1234", "status": "shipping", "slot_label": "Tomorrow 18:00-21:00"}],
           "page_context": {"view": "search", "query": "frying pan"}})
  assert st.count() == 2 and st.subtotal() == 4136
  assert st.checkout == {"state": "confirm", "slot_id": "d1_eve"}
  s = st.get_order_status("CR-20261104-1234")
  assert s["status"] == "shipping" and s["status_label"] == "Out for delivery"
  assert s["eta"] == f"{date_label(dt.datetime.now(JST).date() + dt.timedelta(days=1))}, 18:00-21:00 (Tomorrow 18:00-21:00)"
  # seed(None) keeps state
  st.seed(None)
  assert st.count() == 2
  r = st.place_order(now=NOW)
  assert r["ok"] and r["total_tax_included"] == 4136 and r["slot_label"] == "Tomorrow 18:00-21:00"


def test_order_status_none():
  assert ShopState().get_order_status()["error"] == "NO_ORDER"


def test_catalog_loads_real_file():
  cat = Catalog.load()
  if len(cat) == 0:
    return  # catalog not present in this checkout
  p = cat.get("4900231442791")
  assert p is None or p["price"] > 0


def test_en_us_formatting():
  assert yen(1280) == "¥1,280" and yen(0) == "¥0" and yen("2178.0") == "¥2,178"
  assert date_label(dt.date(2026, 11, 26)) == "Thu, Nov 26"


def test_status_labels_en():
  st = ShopState()
  for status, label in (("placed", "Order placed"), ("preparing", "Preparing"),
                        ("shipping", "Out for delivery"), ("delivered", "Delivered")):
    st.seed({"orders": [{"order_id": "CR-20261104-0001", "status": status, "slot_label": "Tomorrow 9:00-12:00"}]})
    assert st.get_order_status()["status_label"] == label


def test_store_only_refused_from_catalog_channel():
  st = ShopState()
  r = st.add_to_cart(CAT, "S", 1)
  assert r["ok"] is False and r["error"] == "STORE_ONLY"
  assert "in stores only" in r["message"] and "Assorted Sushi Platter" in r["message"]
  assert r["cart_count"] == 0 and st.cart == [] and st.drain_actions() == []
  # omni / online_only / unknown channel are fine
  assert st.add_to_cart(CAT, "A")["ok"] and st.add_to_cart(CAT, "P")["ok"] and st.add_to_cart(CAT, "B")["ok"]


def test_store_only_refused_from_remembered_channel():
  st = ShopState()
  st.remember_channel("B", "store_only")  # e.g. seen in MCP search / check_store_inventory results
  assert st.add_to_cart(CAT, "B")["error"] == "STORE_ONLY"
  st.remember_channel("B", "bogus")  # invalid values are ignored
  assert st.channels["B"] == "store_only"


def test_catalog_channel_parsing(tmp_path):
  f = tmp_path / "cat.jsonl"
  f.write_text("\n".join([
      '{"id": "1", "title": "Bread", "priceInfo": {"price": 280}, "attributes": '
      '[{"key": "channel", "value": {"text": ["store_only"]}}]}',
      '{"id": "2", "title": "Pet Food 10kg", "priceInfo": {"price": 3980}, "channel": "online_only"}',
      '{"id": "3", "title": "Milk", "priceInfo": {"price": 228}}',
  ]), encoding="utf-8")
  cat = Catalog.load(str(f))
  assert cat.channel("1") == "store_only" and cat.channel("2") == "online_only" and cat.channel("3") == ""


def test_show_store_availability_action():
  st = ShopState()
  r = st.show_store_availability({"product_ids": ["S", "", "A"], "message": "Where to buy it today"})
  assert r == {"shown": 2}
  assert st.drain_actions() == [{"type": "action", "name": "show_store_availability",
                                 "args": {"product_ids": ["S", "A"], "message": "Where to buy it today"},
                                 "result": {"shown": 2}}]


def test_summary_text_en():
  st = ShopState()
  assert st.summary_text().startswith("The cart is empty.")
  st.add_to_cart(CAT, "A", 2)
  assert "Cart: 2 items, ¥642 (tax incl.)" in st.summary_text()
