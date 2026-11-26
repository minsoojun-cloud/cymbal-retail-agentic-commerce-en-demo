"""BFF proxy to the ADK agent service (CONTRACT v3).

Browser → POST /api/agent/stream (this site) → POST ${ADK_AGENT_URL}/api/chat/stream (Cloud Run, private)
with `Authorization: Bearer <ID token for ADK_AGENT_URL>`. The SSE body is streamed through unchanged.

ID token:
  1. google.oauth2.id_token.fetch_id_token (metadata server on Cloud Run / service-account ADC), audience =
     ADK_AGENT_AUDIENCE or ADK_AGENT_URL;
  2. local fallback: `gcloud auth print-identity-token` (user credentials; accepted by Cloud Run).
Tokens are cached until ~5 minutes before expiry.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import time
from typing import AsyncIterator

import httpx

log = logging.getLogger("cymbal-site.agent")

_token: dict[str, tuple[str, float]] = {}
_lock = asyncio.Lock()


def agent_url() -> str:
    return (os.environ.get("ADK_AGENT_URL") or "").strip().rstrip("/")


def audience() -> str:
    return (os.environ.get("ADK_AGENT_AUDIENCE") or agent_url()).strip()


def _exp_of(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:  # noqa: BLE001
        return time.time() + 600


def _fetch_token_sync(aud: str) -> str:
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), aud)
    except Exception as e:  # noqa: BLE001 (user ADC cannot mint ID tokens for an audience)
        log.info("fetch_id_token unavailable (%s) → gcloud fallback", type(e).__name__)
    gcloud = shutil.which("gcloud")
    if not gcloud:
        raise RuntimeError("no ID token source (metadata / service account / gcloud)")
    out = subprocess.run([gcloud, "auth", "print-identity-token"], capture_output=True, text=True, timeout=30)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError("gcloud auth print-identity-token failed: " + out.stderr.strip()[:200])
    return out.stdout.strip()


async def id_token() -> str:
    aud = audience()
    hit = _token.get(aud)
    if hit and hit[1] - 300 > time.time():
        return hit[0]
    async with _lock:
        hit = _token.get(aud)
        if hit and hit[1] - 300 > time.time():
            return hit[0]
        tok = await asyncio.to_thread(_fetch_token_sync, aud)
        _token[aud] = (tok, _exp_of(tok))
        return tok


def sse(obj: dict) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


async def stream(body: dict, client: httpx.AsyncClient | None = None) -> AsyncIterator[bytes]:
    """Yield the upstream SSE bytes unchanged; upstream / auth failures become an `error` + `done` event."""
    url = agent_url() + "/api/chat/stream"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10))
    t0 = time.time()
    try:
        try:
            headers["Authorization"] = "Bearer " + await id_token()
        except Exception as e:  # noqa: BLE001
            log.warning("ID token unavailable: %s (calling without auth)", e)
        async with client.stream("POST", url, json=body, headers=headers) as r:
            if r.status_code != 200:
                detail = (await r.aread())[:300].decode("utf-8", "replace")
                log.error("agent upstream %s: %s", r.status_code, detail)
                if r.status_code in (401, 403):
                    _token.clear()
                yield sse({"type": "error", "message": f"Could not connect to the AI Concierge ({r.status_code}).",
                           "status": r.status_code})
                yield sse({"type": "done", "session_id": body.get("session_id", "")})
                return
            async for chunk in r.aiter_raw():
                if chunk:
                    yield chunk
        log.info("agent stream done in %.1fs (session %s)", time.time() - t0, body.get("session_id", "")[:12])
    except (httpx.HTTPError, OSError) as e:
        log.error("agent stream failed: %r", e)
        yield sse({"type": "error", "message": "The connection to the AI Concierge was interrupted. Please try again."})
        yield sse({"type": "done", "session_id": body.get("session_id", "")})
    finally:
        if own:
            await client.aclose()
