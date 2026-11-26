#!/usr/bin/env python3
"""Check that the configured Gemini models answer on Vertex AI in the target project (standard library only).

For each role the configured model (env, or the demo default) is tried first, then newer/older fallbacks.
If the configured model works nothing is written for that role; otherwise the first working fallback is written to
install/.state/models.env as PROBED_<ROLE>=..., which config.env picks up when the user did not pin a model.

  python3 install/lib/probe_models.py --project <id> [--location global] [--out install/.state/models.env] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

ROLES = {
    # env var            default                    fallbacks (tried in order)                       kind
    "GEMINI_MODEL":        ("gemini-3-flash-preview", ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-2.5-flash"], "text"),
    "AGENT_MODEL":         ("gemini-3.5-flash", ["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-2.5-flash"], "text"),
    "ENRICH_GEMINI_MODEL": ("gemini-3.8-flash", ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"], "text"),
    "GEMINI_FALLBACK_MODEL": ("gemini-3.5-flash", ["gemini-3-flash-preview", "gemini-2.5-flash"], "text"),
    "IMAGE_MODEL":         ("gemini-3.1-flash-image", ["gemini-3-pro-image", "gemini-2.5-flash-image"], "image"),
}


def token() -> str:
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()


def probe(project: str, location: str, model: str, kind: str, tok: str) -> tuple[bool, str]:
    host = "https://aiplatform.googleapis.com" if location == "global" else f"https://{location}-aiplatform.googleapis.com"
    url = f"{host}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK."}]}],
            "generationConfig": {"maxOutputTokens": 32}}
    if kind == "image":
        body["generationConfig"] = {"responseModalities": ["TEXT", "IMAGE"]}
        body["contents"][0]["parts"][0]["text"] = "A small red apple on a white background, product photo."
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                                          "X-Goog-User-Project": project})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        if e.code == 429:  # quota exhausted right now, but the model exists and is enabled
            return True, "HTTP 429 (exists, rate limited)"
        return False, f"HTTP {e.code} {msg[:120]!r}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID", ""))
    ap.add_argument("--location", default=os.environ.get("GEMINI_LOCATION", "global"))
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lines, failed = [], []
    tok = "" if a.dry_run else token()
    for role, (default, fallbacks, kind) in ROLES.items():
        configured = os.environ.get(role) or default
        if a.dry_run:
            print(f"  [dry-run] {role}: would probe {configured} then {', '.join(fallbacks)}")
            continue
        chosen = None
        for m in [configured] + [f for f in fallbacks if f != configured]:
            ok, why = probe(a.project, a.location, m, kind, tok)
            print(f"  {role:22s} {m:26s} {'OK ' if ok else 'NG '} {why}", flush=True)
            if ok:
                chosen = m
                break
        if chosen is None:
            failed.append(role)
        elif chosen != configured:
            lines.append(f'PROBED_{role}="{chosen}"')
            print(f"  -> {role} falls back to {chosen}")
    if a.out and not a.dry_run:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write("# Written by install/lib/probe_models.py — fallbacks for models that did not answer\n")
            f.write("\n".join(lines) + ("\n" if lines else ""))
    if failed:
        print(f"WARNING: no working model for {', '.join(failed)} — check Vertex AI access / org policy", file=sys.stderr)
        return 0 if "AGENT_MODEL" not in failed else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
