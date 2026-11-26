"""Embedded ADK agent: tool declarations (schema), show_store_items enrichment (numbering = route order)."""
from __future__ import annotations

import pytest

from app import inventory


def test_agent_builds_with_tool_schema():
    from google.adk.tools.function_tool import FunctionTool

    from app.agent import agent, tools

    a = agent.build_agent("gemini-3.5-flash")
    assert a.name == "store_concierge" and a.model == "gemini-3.5-flash"
    names = {t.__name__ for t in tools.ALL_TOOLS}
    assert names == {"search_products", "get_product_details", "check_availability", "show_store_items", "suggest_recipes",
                     "suggest_replies"}
    decl = FunctionTool(tools.show_store_items)._get_declaration()
    schema = decl.parameters_json_schema if decl.parameters is None else decl.parameters.model_dump(exclude_none=True)
    props = schema["properties"]
    assert {"theme", "advice", "items", "red_flag"} <= set(props)
    assert "tool_context" not in props
    assert set(schema.get("required") or []) >= {"theme", "advice", "items"}
    item_schema = props["items"]["items"]
    if "$ref" in item_schema:
        item_schema = schema["$defs"][item_schema["$ref"].split("/")[-1]]
    assert {"product_id", "role", "reason"} <= set(item_schema["properties"])
    for fn in tools.ALL_TOOLS:
        d = FunctionTool(fn)._get_declaration()
        assert d.name == fn.__name__ and d.description


def test_instruction_contains_compliance_and_context():
    from types import SimpleNamespace

    from app.agent import agent, tools

    st = tools.turn_state("pytest-instr")
    st.begin("S01", "CR-0002", "I've had a fever of 39C for 3 days and it won't go down", {})
    ctx = SimpleNamespace(session=SimpleNamespace(id="pytest-instr"), state={})
    text = agent.build_instruction(ctx)
    from app import stores

    for must in ("#7119", "119", "TELL Lifeline", "one per customer", "Pharmacist-only (Class 1)", "the strongest",
                 "Red flags detected", "Hiroshi", stores.get_store("S01")["name"]):
        assert must in text, must
    assert not __import__("re").search(r"[\u3040-\u30ff\u4e00-\u9fff]", agent.BASE_INSTRUCTION)


def test_build_store_items_numbering_and_alternatives(scenarios):
    from app.agent.tools import build_store_items

    fever = scenarios["fever_set_S01"]
    cold = scenarios["cold_tablet_out_at_S01"]["product_id"]
    items = [{"product_id": pid, "role": "r", "reason": "x"} for pid in fever[:3]] + [{"product_id": cold, "role": "Cold medicine", "reason": "y"}]
    args, summary = build_store_items("S01", "CR-0001", "Theme", "Advice", items, [], None)
    in_store = [it for it in args["items"] if it["in_store"]]
    assert [it["no"] for it in in_store] == list(range(1, len(in_store) + 1))
    assert [s["product_id"] for s in args["route"]["stops"]] == [it["product_id"] for it in in_store]
    out = [it for it in args["items"] if not it["in_store"]]
    for it in out:
        assert it["no"] is None and it["availability"]["nearby"] and it["availability"]["online"]["url"]
    assert args["disclaimer"].startswith("This service provides general information") and summary["shown"] == len(args["items"])


def test_build_store_items_red_flag_removes_medicines(scenarios):
    from app.agent import safety
    from app.agent.tools import build_store_items

    cold = scenarios["cold_tablet_out_at_S01"]["product_id"]
    fever = scenarios["fever_set_S01"]
    flags = safety.detect_red_flags("I've had a fever of 39C for 3 days and it won't go down")
    items = [{"product_id": cold}, {"product_id": fever[0]}]
    args, summary = build_store_items("S01", "CR-0001", "t", "", items, flags, None)
    assert args["red_flag"] is True and summary["removed_by_safety"] >= 1
    assert all(it["product"].get("drug_class") not in safety.MEDICINE_CLASSES for it in args["items"])


@pytest.mark.parametrize("q", ["pain reliever", "cooling gel sheet"])
def test_local_search_tool_fallback(q):
    from app.agent.tools import _local_search

    res = _local_search(q, 8, "S01")
    assert res and all("id" in p for p in res)


def test_abuse_control_item_limits():
    from app import deps
    from app.agent import safety

    inventory.ensure_loaded()
    pid = next(p for (s, p) in inventory._inv if s == "S01" and (deps.to_product(p) or {}).get("abuse_control"))
    p = deps.to_product(pid)
    assert safety.item_limits(p)["max_qty"] == 1
    assert any("one per customer" in n for n in safety.compliance_notes(p))
