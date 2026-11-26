"""runner._Collector: streamed text deltas, final aggregate events, duplicate re-generated segments, post-filter."""
from __future__ import annotations

from types import SimpleNamespace as NS

from app.agent import runner


class _St:
    def drain(self):
        return []


def _ev(text, partial):
    return NS(author="store_concierge", partial=partial, content=NS(role="model", parts=[NS(text=text, function_call=None, thought=False)]))


def _texts(col, events):
    out = []
    for e in events:
        out += [p["text"] for p in col.process(e) if p["type"] == "text"]
    out += [p["text"] for p in col.finish() if p["type"] == "text"]
    return "".join(out)


def test_stream_then_final_not_duplicated():
    col = runner._Collector(_St(), 0)
    txt = _texts(col, [_ev("Hello. ", True), _ev("Feel better soon.", True), _ev("Hello. Feel better soon.", False)])
    assert txt == "Hello. Feel better soon."


def test_regenerated_segment_dropped():
    col = runner._Collector(_St(), 0)
    a = "Here are support items until you see a doctor. "
    b = "Tap Find all on the map to walk the shelves."
    txt = _texts(col, [_ev(a, True), _ev(b, True), _ev(a + b, False),
                       _ev(a, True), _ev(b, True), _ev(a + b, False)])
    assert txt.count(a) == 1 and txt.count(b) == 1


def test_new_segment_after_tool_kept():
    col = runner._Collector(_St(), 0)
    txt = _texts(col, [_ev("This is the first answer.", False), _ev("One more tip.", True), _ev("One more tip.", False)])
    assert "This is the first answer." in txt and "One more tip." in txt and "\n\n" in txt


def test_banned_words_filtered_in_stream():
    col = runner._Collector(_St(), 0)
    txt = _texts(col, [_ev("This medicine will ", True), _ev("cure your cold. Rest well.", True), _ev("This medicine will cure your cold. Rest well.", False)])
    assert "will cure" not in txt and "Rest well." in txt


def test_trace_built_for_mock_turn():
    from app.agent import tools

    st = tools.turn_state("pytest-trace")
    st.begin("S01", "CR-0001", "hello", {})
    rows = runner.build_trace(st, [{"name": "show_store_items", "args": {"theme": "t", "items": [{}, {}], "route": {"length_m": 42}}}],
                              "mock", "CR-0001")
    tools_used = [r["tool"] for r in rows]
    assert tools_used[:2] == ["red_flag_check", "persona"]
    assert "show_store_items" in tools_used and all(r["source_label"] for r in rows)
