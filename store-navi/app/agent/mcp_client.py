"""Minimal MCP JSON-RPC client for cymbal-commerce-mcp (copied from shared/adk_agent/cymbal_agent/mcp_client.py) (private Cloud Run, stateless JSON responses).

Auth: Google ID token with audience = MCP base URL, fetched from the metadata server on Cloud Run
(google.oauth2.id_token.fetch_id_token) and cached ~45 min; locally falls back to
`gcloud auth print-identity-token`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("store-navi.mcp")

def _default_mcp_url() -> str:
  """MCP_URL env, else the deterministic Cloud Run URL of cymbal-commerce-mcp in PROJECT_NUMBER (no hard-coded ids)."""
  v = (os.environ.get("MCP_URL") or "").strip()
  if v:
    return v.rstrip("/")
  num = (os.environ.get("PROJECT_NUMBER") or "").strip()
  if num:
    return (f"https://{os.environ.get('MCP_SERVICE', 'cymbal-commerce-mcp')}-{num}."
            f"{os.environ.get('REGION', 'asia-northeast1')}.run.app")
  return ""


MCP_URL = _default_mcp_url()
_TOKEN: dict[str, Any] = {"token": None, "exp": 0.0, "force_gcloud": False}
_LOCK = threading.Lock()
_CLIENT: Optional[httpx.AsyncClient] = None


def _fetch_token() -> Optional[str]:
  if not _TOKEN["force_gcloud"]:
    try:
      from google.auth.transport.requests import Request
      from google.oauth2 import id_token
      tok = id_token.fetch_id_token(Request(), MCP_URL)
      if tok:
        return tok
    except Exception as exc:  # local user ADC cannot mint ID tokens
      logger.info("ID token via ADC/metadata unavailable (%s); trying gcloud", exc)
  if shutil.which("gcloud"):
    try:
      p = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True,
                         timeout=20, check=False)
      if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    except Exception as exc:
      logger.warning("gcloud print-identity-token failed: %s", exc)
  return None


def auth_headers(force_refresh: bool = False) -> dict[str, str]:
  if MCP_URL.startswith("http://localhost") or MCP_URL.startswith("http://127."):
    return {}
  with _LOCK:
    if force_refresh or not _TOKEN["token"] or time.time() >= _TOKEN["exp"]:
      tok = _fetch_token()
      if tok:
        _TOKEN.update(token=tok, exp=time.time() + 45 * 60)
    return {"Authorization": f"Bearer {_TOKEN['token']}"} if _TOKEN["token"] else {}


def _client() -> httpx.AsyncClient:
  global _CLIENT
  if _CLIENT is None:
    _CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0),
                                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16))
  return _CLIENT


def _parse(resp: httpx.Response) -> Any:
  raw = resp.text
  if raw.startswith("event:") or raw.startswith("data:"):
    raw = next((l[5:].strip() for l in raw.splitlines() if l.startswith("data:")), "{}")
  payload = json.loads(raw)
  if "error" in payload:
    return {"error": f"MCP error: {payload['error']}"}
  content = (payload.get("result") or {}).get("content") or []
  texts = [c.get("text", "") for c in content if c.get("type", "text") == "text"]
  if not texts:
    return {"error": "empty MCP result"}
  try:
    return json.loads(texts[0])
  except ValueError:
    return {"text": texts[0]}


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
  """tools/call → parsed JSON of the first text content ({error:...} on failure)."""
  body = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000, "method": "tools/call",
          "params": {"name": name, "arguments": {k: v for k, v in arguments.items() if v is not None}}}
  t0 = time.perf_counter()
  try:
    for attempt in range(2):
      headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 **(await asyncio.to_thread(auth_headers, attempt > 0))}
      resp = await _client().post(f"{MCP_URL}/mcp", json=body, headers=headers)
      if resp.status_code in (401, 403) and attempt == 0:
        logger.warning("MCP %s returned %s; refreshing token", name, resp.status_code)
        continue
      if resp.status_code != 200:
        return {"error": f"MCP HTTP {resp.status_code}: {resp.text[:200]}"}
      return _parse(resp)
    return {"error": "MCP auth failed"}
  except Exception as exc:
    logger.exception("MCP %s failed", name)
    return {"error": f"MCP call failed: {exc}"}
  finally:
    logger.info("mcp %s %.0fms", name, (time.perf_counter() - t0) * 1000)


async def list_tools() -> Any:
  headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             **(await asyncio.to_thread(auth_headers))}
  resp = await _client().post(f"{MCP_URL}/mcp", headers=headers,
                              json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
  resp.raise_for_status()
  return [t["name"] for t in resp.json()["result"]["tools"]]
