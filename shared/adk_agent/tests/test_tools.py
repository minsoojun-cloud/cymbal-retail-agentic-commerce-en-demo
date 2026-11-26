"""Tool-level tests with a mocked MCP: find_store_stock, show_store_availability SSE action, store_only refusal.

Run: uv run --no-project --with pytest --with-requirements requirements.txt python -m pytest -q tests/
"""

import asyncio
import json
import os
import sys
import types as pytypes

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

from cymbal_agent import mcp_client, tools  # noqa: E402
from cymbal_agent.shop import Catalog  # noqa: E402

STOCK = {"items": [{
    "product_id": "SUSHI1", "title": "Assorted Sushi Platter", "channel": "store_only",
    "stores": [{"store_id": "S02", "name": "Cymbal Retail Musashi-Kosugi", "address": "Kawasaki, Kanagawa",
                "open_now": True, "hours": "9:00-22:00", "quantity": 6, "availability": "IN_STOCK",
                "pickup": True, "shelf_label": "Deli counter"}],
    "online_available": False}]}


class Ctx:
  """Minimal stand-in for google.adk ToolContext (session.id + state)."""

  def __init__(self, sid, **state):
    self.session = pytypes.SimpleNamespace(id=sid)
    self.state = dict(state)


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
  monkeypatch.setattr(tools, "CATALOG", Catalog({
      "SUSHI1": {"title": "Assorted Sushi Platter", "price": 1280, "availability": "IN_STOCK", "channel": "store_only"},
      "PAN1": {"title": "Frying Pan 26cm", "price": 2068, "availability": "IN_STOCK", "channel": "omni"},
      "BREAD1": {"title": "Fresh Baked Croissant", "price": 180, "availability": "IN_STOCK", "channel": ""},
  }))
  tools.STATES.clear()
  yield
  tools.STATES.clear()


def _mock_mcp(monkeypatch, responses):
  calls = []

  async def fake(name, arguments):
    calls.append((name, arguments))
    r = responses[name]
    return r(arguments) if callable(r) else json.loads(json.dumps(r))

  monkeypatch.setattr(mcp_client, "call_tool", fake)
  return calls


def run(coro):
  return asyncio.run(coro)


def test_tools_registered():
  names = [f.__name__ for f in tools.ALL_TOOLS]
  assert "find_store_stock" in names and "show_store_availability" in names
  assert tools.STATUS_LABELS["find_store_stock"] == "Checking store stock…"
  assert tools.STATUS_LABELS["search_products"] == "Searching products…"


def test_find_store_stock_calls_mcp(monkeypatch):
  calls = _mock_mcp(monkeypatch, {"check_store_inventory": STOCK})
  ctx = Ctx("s1")
  r = run(tools.find_store_stock(["SUSHI1", ""], ctx, store_id="s02"))
  assert calls == [("check_store_inventory", {"product_ids": ["SUSHI1"], "store_id": "S02"})]
  assert r == STOCK
  assert tools.shop_state("s1").channels == {"SUSHI1": "store_only"}
  # no store filter → store_id omitted
  run(tools.find_store_stock(["PAN1"], ctx))
  assert calls[-1] == ("check_store_inventory", {"product_ids": ["PAN1"]})


def test_find_store_stock_fallback_on_mcp_error(monkeypatch):
  _mock_mcp(monkeypatch, {"check_store_inventory": {"error": "MCP HTTP 500: boom"}})
  r = run(tools.find_store_stock(["SUSHI1", "PAN1"], Ctx("s2")))
  assert r["error"].startswith("MCP HTTP 500")
  assert "temporarily unavailable" in r["message"]
  assert [(i["product_id"], i["channel"], i["online_available"]) for i in r["items"]] == [
      ("SUSHI1", "store_only", False), ("PAN1", "omni", True)]
  assert run(tools.find_store_stock([], Ctx("s2")))["error"] == "NO_PRODUCTS"


def test_show_store_availability_sse_action():
  import server  # noqa: E402  (imports ADK agent; no network)
  ctx = Ctx("s3")
  r = run(tools.show_store_availability(["SUSHI1"], "Where to buy the sushi platter today", ctx))
  assert r == {"shown": 1}
  turn = server._Turn(tools.shop_state("s3"), 0.0)
  ev = pytypes.SimpleNamespace(author="cymbal_concierge", content=None, partial=False)
  payloads = turn.process(ev)
  assert payloads == [{"type": "action", "name": "show_store_availability",
                       "args": {"product_ids": ["SUSHI1"], "message": "Where to buy the sushi platter today"},
                       "result": {"shown": 1}}]
  line = server._sse(payloads[0])
  print(line.strip())
  assert line == ('data: {"type": "action", "name": "show_store_availability", "args": {"product_ids": ["SUSHI1"], '
                  '"message": "Where to buy the sushi platter today"}, "result": {"shown": 1}}\n\n')
  assert server._chips(turn.actions, tools.shop_state("s3"), True)[0] == "Which store is open now?"


def test_add_to_cart_refuses_store_only():
  ctx = Ctx("s4")
  r = run(tools.add_to_cart("SUSHI1", ctx))
  assert r["ok"] is False and r["error"] == "STORE_ONLY" and "in stores only" in r["message"]
  assert tools.shop_state("s4").drain_actions() == []
  assert run(tools.add_to_cart("PAN1", ctx))["ok"] is True


def test_add_to_cart_refuses_channel_learned_from_search(monkeypatch):
  _mock_mcp(monkeypatch, {"search_products": {"productDetails": [
      {"productId": "BREAD1", "title": "Fresh Baked Croissant", "price": 180, "channel": "store_only"},
      {"productId": "SHR1", "title": "Shrimp Tempura", "price": 498, "allergens": ["shrimp"]}], "totalSize": 2}})
  ctx = Ctx("s5", exclude_keywords=["Shrimp"])
  res = run(tools.search_products("croissant", ctx, filter='pickupInStore: ANY("S01")'))
  assert [p["productId"] for p in res["productDetails"]] == ["BREAD1"] and res["excluded_by_allergy"] == 1
  r = run(tools.add_to_cart("BREAD1", ctx))
  assert r["ok"] is False and r["error"] == "STORE_ONLY"


def test_dinner_set_add_skips_store_only(monkeypatch):
  _mock_mcp(monkeypatch, {"recommend_dinner_set": {"theme": "Japanese", "sets": [{
      "name": "Sushi Night", "total_tax_included": 3348, "items": [
          {"role": "Main", "product_id": "SUSHI1", "title": "Assorted Sushi Platter", "price": 1280,
           "quantity": 1, "tags": ["In season"], "reason": "Fresh today"},
          {"role": "Side", "product_id": "PAN1", "title": "Frying Pan 26cm", "price": 2068,
           "quantity": 1, "tags": ["Frequently bought"], "reason": "Your usual"}]}]}})
  ctx = Ctx("s6", customer_id="CR-0001")
  r = run(tools.recommend_dinner_set(ctx, request="Recommend dinner for tonight"))
  st = tools.shop_state("s6")
  sp = st.drain_actions()[0]
  assert sp["name"] == "show_products_on_page"
  assert sp["args"]["message"] == "Sushi Night (¥3,348, tax incl.)"
  assert [s["label"] for s in sp["args"]["sections"]] == ["Main", "Side"]
  assert r["sets"][0]["items"][0]["tags"] == ["In season"]
  r = run(tools.add_dinner_set_to_cart(ctx))
  assert r["ok"] is False and r["items_added"] == 1
  assert r["added"][0]["error"] == "STORE_ONLY" and r["added"][1]["ok"] is True
  assert [a["args"]["product_id"] for a in st.drain_actions()] == ["PAN1"]


def test_mcp_url_from_env(monkeypatch):
  import importlib
  monkeypatch.setenv("MCP_URL", "https://cymbal-commerce-mcp-123.asia-northeast1.run.app/mcp")
  m = importlib.reload(mcp_client)
  assert m.MCP_URL == "https://cymbal-commerce-mcp-123.asia-northeast1.run.app"
  monkeypatch.delenv("MCP_URL")
  monkeypatch.setenv("PROJECT_NUMBER", "999")
  monkeypatch.setenv("REGION", "us-central1")
  m = importlib.reload(mcp_client)
  assert m.MCP_URL == "https://cymbal-commerce-mcp-999.us-central1.run.app"
  monkeypatch.delenv("PROJECT_NUMBER")
  monkeypatch.delenv("REGION")
  importlib.reload(mcp_client)
