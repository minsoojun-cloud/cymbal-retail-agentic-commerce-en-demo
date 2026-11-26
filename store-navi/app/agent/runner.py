"""Runs one concierge turn and yields CONTRACT v3 SSE payloads (status / text / action / chips / done / error).

live: embedded ADK Runner (InMemorySessionService) with the store_concierge agent (model env AGENT_MODEL @ Vertex global)
mock: scripted responder (mock.py); also used as a fallback when the live model fails before producing output.
Guardrails: the red-flag preface is emitted by the server BEFORE any model text, model text is post-filtered sentence by
sentence (safety.TextFilter), crisis turns never reach the model.
Agent trace: `done` carries `trace` = [{tool, label, detail, source, source_label}] ("How I answered" in the UI).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Optional

from .. import config, offers
from . import mock, safety
from .tools import SOURCE_LABELS, STATUS_LABELS, TRACE_LABELS, turn_state

log = logging.getLogger("store-navi.agent")

APP_NAME = "store_concierge"
AUTHOR = "store_concierge"
_runner = None
_session_service = None


def agent_mode() -> str:
    m = (os.environ.get("AGENT_MODE") or "").strip().lower()
    if m in ("live", "mock"):
        return m
    return "live" if config.data_mode() == "bq" else "mock"


def _ensure_runner():
    global _runner, _session_service
    if _runner is None:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
        if config.PROJECT_ID:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", config.PROJECT_ID)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("AGENT_LOCATION", "global"))
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from .agent import build_agent

        _session_service = InMemorySessionService()
        _runner = Runner(app_name=APP_NAME, agent=build_agent(), session_service=_session_service)
    return _runner, _session_service


def sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class _Collector:
    def __init__(self, st, t0: float):
        self.st = st
        self.t0 = t0
        self.seg = ""
        self.held = ""
        self.sep = ""
        self.segments: list[str] = []
        self.filter = safety.TextFilter()
        self.emitted_text = False
        self.actions: list[dict] = []
        self.seen_fc: set[str] = set()
        self.tool_calls: list[str] = []
        self.timings: dict[str, Optional[int]] = {"first_text_ms": None, "first_action_ms": None, "total_ms": None}

    def _ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)

    def _emit_text(self, t: str) -> list[dict]:
        out = []
        clean = self.filter.feed(t)
        if clean:
            out.append({"type": "text", "text": clean, "author": AUTHOR})
            self.emitted_text = True
            if self.timings["first_text_ms"] is None:
                self.timings["first_text_ms"] = self._ms()
        return out

    def _text(self, t: str) -> list[dict]:
        """Append a model text delta. A new segment that merely repeats an earlier one (the model re-generates its
        answer after a trailing tool call) is held back while it is a prefix of a previous segment and dropped if it
        turns out to be a duplicate."""
        if not t:
            return []
        if not self.seg:
            t = t.lstrip()
            if not t:
                return []
            self.held = ""
            self.sep = "\n\n" if any(s.strip() for s in self.segments) else ""
        self.seg += t
        prev = [s.strip() for s in self.segments if s.strip()]
        cur = self.seg.strip()
        if prev and any(p.startswith(cur) for p in prev):
            self.held += t           # still identical to an earlier segment → hold
            return []
        out_t = (self.sep + self.held + t) if (self.held or self.sep) else t
        self.held, self.sep = "", ""
        return self._emit_text(out_t)

    def drain(self) -> list[dict]:
        acts = self.st.drain()
        if acts and self.timings["first_action_ms"] is None:
            self.timings["first_action_ms"] = self._ms()
        self.actions.extend(acts)
        return acts

    def process(self, ev: Any) -> list[dict]:
        out: list[dict] = []
        if getattr(ev, "author", "") == "user":
            return out
        content = getattr(ev, "content", None)
        parts = list(getattr(content, "parts", None) or []) if content else []
        for p in parts:
            fc = getattr(p, "function_call", None)
            if fc:
                fid = getattr(fc, "id", None) or f"{fc.name}-{len(self.seen_fc)}"
                if fid not in self.seen_fc:
                    self.seen_fc.add(fid)
                    self.tool_calls.append(fc.name)
                    if STATUS_LABELS.get(fc.name):
                        out.append({"type": "status", "text": STATUS_LABELS[fc.name]})
        texts = [p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)]
        if getattr(ev, "partial", False):
            for t in texts:
                out += self._text(t)
        else:
            full = "".join(texts)
            if full:
                streamed = self.seg.lstrip("\n")
                if not streamed:
                    out += self._text(full)
                elif full.startswith(streamed) and len(full) > len(streamed):
                    out += self._text(full[len(streamed):])
                self.segments.append(full)
                self.seg = ""
            elif self.seg:
                self.segments.append(self.seg.lstrip("\n"))
                self.seg = ""
        out += self.drain()
        return out

    def finish(self) -> list[dict]:
        out = self.drain()
        rest = self.filter.flush()
        if rest.strip():
            out.append({"type": "text", "text": rest, "author": AUTHOR})
            self.emitted_text = True
        self.timings["total_ms"] = self._ms()
        return out


def default_chips(actions: list[dict], flags: list[dict]) -> list[str]:
    if any(f["level"] == "crisis" for f in flags):
        return ["Talk to a pharmacist"]
    if any(a.get("name") == "show_store_items" for a in actions):
        return ["Find all on the map", "Stock at nearby stores?", "Anything for kids?"]
    return ["I feel feverish", "Dinner ideas", "Where is the oral rehydration solution?"]


def _trace_row(tool: str, detail: str, source: str) -> dict:
    return {"tool": tool, "label": TRACE_LABELS.get(tool, tool), "detail": detail[:140], "source": source,
            "source_label": SOURCE_LABELS.get(source, source)}


def build_trace(st, actions: list[dict], mode: str, customer_id: str) -> list[dict]:
    """Prefix (guardrail + profile rows) + the per-tool rows recorded by the tools (live) or derived from the
    scripted actions (mock)."""
    rows = [_trace_row("red_flag_check", (", ".join(f["label"] for f in st.flags) or "no red flags")
                       + (" · quantity-limit signal" if st.abuse else ""), "guardrails"),
            _trace_row("persona", f"{customer_id} preferences, allergies & purchase history", "profile")]
    tool_rows = list(st.trace)
    if not tool_rows:
        for a in actions:
            if a.get("name") == "show_recipes":
                q = (a.get("args") or {}).get("query", "")
                tool_rows.append(_trace_row("suggest_recipes", f'"{q[:60]}" → dish cards (allergies respected)', "recipes"))
                tool_rows.append(_trace_row("check_availability", "ingredient stock at this store", "bigquery" if
                                            config.data_mode() == "bq" else "local"))
            if a.get("name") == "show_store_items":
                args = a.get("args") or {}
                n = len(args.get("items") or [])
                src = "aics" if (args.get("engine") == "retail") else ("mcp" if mode == "live" else "local")
                tool_rows.append(_trace_row("search_products", f'"{args.get("theme", "")}" candidates', src))
                tool_rows.append(_trace_row("check_availability", f"{n} products · this store → nearby → online",
                                            "bigquery" if config.data_mode() == "bq" else "local"))
                tool_rows.append(_trace_row("show_store_items",
                                            f'{n} items · route {int((args.get("route") or {}).get("length_m") or 0)} m',
                                            "store_layout"))
                if args.get("removed_by_safety"):
                    tool_rows.append(_trace_row("safety_filter",
                                                f'{args["removed_by_safety"]} item(s) removed by OTC rules', "guardrails"))
    return rows + tool_rows


async def run_turn(body: dict) -> AsyncIterator[dict]:
    t0 = time.perf_counter()
    session_id = str(body.get("session_id") or "") or f"s-{uuid.uuid4().hex}"
    store_id = str(body.get("store_id") or config.DEFAULT_STORE)
    customer_id = str(body.get("customer_id") or config.DEFAULT_CUSTOMER)
    message = str(body.get("message") or "")
    st = turn_state(session_id)
    st.begin(store_id, customer_id, message, body.get("page_state") or {})
    yield {"type": "status", "text": "Thinking…"}
    pre = safety.red_flag_preface(st.flags)
    if not pre and st.abuse:
        pre = safety.ABUSE_GUIDANCE  # COMPLIANCE §1: quantity limit / pharmacist first (server-side, before model text)
    mode = agent_mode()
    crisis = any(f["level"] == "crisis" for f in st.flags)
    used = mode
    actions: list[dict] = []
    chips: list[str] = []
    timings: dict[str, Any] = {}
    if pre:
        yield {"type": "text", "text": pre + "\n\n", "author": AUTHOR}
    if mode == "live" and not crisis:
        col = _Collector(st, t0)
        try:
            runner, sessions = _ensure_runner()
            from google.adk.agents.run_config import RunConfig, StreamingMode
            from google.genai import types

            persona = offers.get_persona(customer_id)
            user_id = customer_id or "anonymous"
            state = {"customer_id": customer_id, "customer_name": persona.get("name", ""), "store_id": store_id}
            if await sessions.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id) is None:
                await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id, state=state)
            content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
            async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=content,
                                             state_delta=state,
                                             run_config=RunConfig(streaming_mode=StreamingMode.SSE)):
                for payload in col.process(ev):
                    yield payload
            for payload in col.finish():
                yield payload
            actions, chips, timings = col.actions, st.chips, col.timings
            if not col.emitted_text:
                yield {"type": "text", "text": "I've put the options on screen." if actions else
                       "Sorry, I couldn't answer that. Please try again.", "author": AUTHOR}
        except Exception as e:  # noqa: BLE001
            log.exception("live agent failed")
            if col.emitted_text or col.actions:
                yield {"type": "error", "message": "Sorry, something went wrong. Please try again.",
                       "detail": str(e)[:200]}
                actions = col.actions
            else:
                used = "mock-fallback"
                mode = "mock"
    if mode == "mock" or crisis:
        if crisis:
            used = "server"
        for payload in mock.respond(message, store_id, customer_id, st.flags, st.abuse, st.age, emit_preface=False):
            if payload["type"] == "status" and payload["text"] == "Thinking…":
                continue
            if payload["type"] == "chips":
                chips = payload["items"]
                continue
            if payload["type"] == "action":
                actions.append(payload)
            yield payload
    yield {"type": "chips", "items": (chips or default_chips(actions, st.flags))[:4]}
    yield {"type": "done", "session_id": session_id, "agent": used, "red_flags": [f["code"] for f in st.flags],
           "trace": build_trace(st, actions, used, customer_id),
           "timings": {**timings, "total_ms": int((time.perf_counter() - t0) * 1000)}}


async def stream(body: dict) -> AsyncIterator[str]:
    try:
        async for payload in run_turn(body):
            yield sse(payload)
    except Exception as e:  # noqa: BLE001
        log.exception("agent stream failed")
        yield sse({"type": "error", "message": "Sorry, an error occurred. Please try again.",
                   "detail": str(e)[:200]})
        yield sse({"type": "done", "session_id": body.get("session_id")})
