"""Online Gemini calls (live enrichment) via google-genai on Vertex AI."""
from __future__ import annotations

import asyncio
import os
import time

from google import genai
from google.genai import types

from .config import settings
from .pipeline import parse_json_text
from .prompts import gmc_prompt, stage1_prompt, stage3_prompt
from .schema import gmc_schema, stage1_schema, stage3_schema

# Cloudtop's enterprise-cert proxy breaks mTLS for some endpoints; plain TLS is fine.
os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=settings.project_id, location=settings.vertex_location)
    return _client


def _retryable(e: Exception) -> bool:
    s = repr(e)
    return any(x in s for x in ("429", "503", "500", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE", "overloaded"))


async def _call(prompt: str, schema: dict, model: str | None = None, allow_fallback: bool = True) -> dict:
    """generate_content with JSON schema; retries transient errors and (optionally) falls back to a second model.

    `model` defaults to the model selected for this request (header X-Enrich-Model → models.current()).
    The returned dict always carries the model that actually produced the answer (`model`)."""
    from . import models as model_registry
    primary = model or model_registry.current()
    fb = settings.gemini_fallback_model
    models = [primary] + ([fb] if allow_fallback and fb and fb != primary else [])
    last: Exception | None = None
    for mi, m in enumerate(models):
        for attempt in range(2):
            try:
                r = await _call_once(m, prompt, schema)
                r["requested_model"] = primary
                r["fallback"] = m != primary
                return r
            except Exception as e:  # noqa: BLE001
                last = e
                if not _retryable(e):
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


async def _call_once(model: str, prompt: str, schema: dict) -> dict:
    t0 = time.time()
    resp = await client().aio.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=(types.ThinkingConfig(thinking_level=settings.thinking_level)
                             if settings.thinking_level else None),
        ),
    )
    cand = resp.candidates[0] if resp.candidates else None
    usage = resp.usage_metadata
    resp_tok = getattr(usage, "candidates_token_count", 0) or 0
    think_tok = getattr(usage, "thoughts_token_count", 0) or 0
    return {
        "data": parse_json_text(resp.text),
        "raw_text": resp.text,
        "model": model,
        "avg_logprobs": getattr(cand, "avg_logprobs", None) if cand else None,
        "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
        # billing view: output = response + reasoning (both billed at the output rate)
        "output_tokens": resp_tok + think_tok,
        "response_tokens": resp_tok,
        "thinking_tokens": think_tok,
        "latency_ms": int((time.time() - t0) * 1000),
    }


async def run_stage1(p: dict, model: str | None = None, allow_fallback: bool = True) -> dict:
    return await _call(stage1_prompt(p, p["group"]), stage1_schema(p["group"]), model, allow_fallback)


async def run_stage3(p: dict, model: str | None = None, allow_fallback: bool = True) -> dict:
    return await _call(stage3_prompt(p, p["group"]), stage3_schema(p["group"]), model, allow_fallback)


async def run_gmc(p: dict, model: str | None = None, allow_fallback: bool = True) -> dict:
    return await _call(gmc_prompt(p, p["group"]), gmc_schema(), model, allow_fallback)


def run_sync(p: dict) -> tuple[dict, dict]:
    async def both():
        return await asyncio.gather(run_stage1(p), run_stage3(p))
    return tuple(asyncio.run(both()))  # type: ignore[return-value]

