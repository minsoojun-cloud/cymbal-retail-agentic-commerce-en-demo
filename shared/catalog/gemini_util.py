"""Small Gemini-on-Vertex helper for batch JSON translation (English demo data build).

Config from env: PROJECT_ID (or GEMINI_PROJECT), GEMINI_MODEL, GEMINI_LOCATION (see config.env).
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

PROJECT = os.environ.get("GEMINI_PROJECT") or os.environ.get("PROJECT_ID", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
LOCATION = os.environ.get("GEMINI_LOCATION", "global")

GLOSSARY = """Glossary / rules (MUST follow):
- Brand lines: "Cymbal ベーシック" -> "Cymbal Basic", "Cymbal セレクト" -> "Cymbal Select",
  "Cymbal オーガニック" -> "Cymbal Organic", "Cymbal" stays "Cymbal". "Cymbalポイント"/"Cymbal ポイント" -> "Cymbal Points".
- Store brand is "Cymbal Retail". Never output real retailer names (KFM, Kansai Food Market, Izumiya, Hankyu, Oasis,
  Kansai Super, AEON, Topvalu, WAON, Green Beans); replace them with "Cymbal Retail" / "Cymbal".
  The vegetable いんげん / green beans MUST be written "string beans" (the words "green beans" are banned).
- Fictional maker names written in Japanese: romanize them naturally (e.g. "ハルカ電機" -> "Haruka Denki",
  "白峰とうふ工房" -> "Shiramine Tofu Kobo"). Keep names already in Latin script unchanged.
- Japanese food names with no common English equivalent: use the romanized name + short English gloss where helpful
  (e.g. "natto", "tonkatsu", "karaage (Japanese fried chicken)", "mentaiko", "dashi"). Keep it concise, natural US English.
- Units metric (g, kg, ml, L, cm). "個" -> "pcs" or "count", "枚" -> "sheets/slices", "本" -> "pcs/bottles". "入" -> "pack" phrasing.
- Prices/currency are not translated. Output must contain NO Japanese characters.
"""

_client = None


def client():
    global _client
    if _client is None:
        # explicit timeout: without it a stuck request can hang a worker forever
        _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION,
                               http_options=types.HttpOptions(timeout=int(os.environ.get("GEMINI_TIMEOUT_MS", "90000"))))
    return _client


def gen_json(prompt: str, schema: dict | None = None, retries: int = 5, temperature: float = 0.2):
    cfg = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(thinking_level="low") if "gemini-3" in MODEL else None,
    )
    last = None
    for i in range(retries):
        try:
            r = client().models.generate_content(model=MODEL, contents=prompt, config=cfg)
            if not r.text:
                raise RuntimeError(f"empty response (finish={r.candidates[0].finish_reason if r.candidates else None})")
            return json.loads(r.text)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  gemini retry {i + 1}/{retries}: {str(e)[:160]}", flush=True)
            time.sleep(min(30, 2 ** i + 1))
    raise RuntimeError(f"gemini failed: {last}")


def translate_strings(strings: list[str], context: str, batch: int = 80, workers: int = 16) -> dict[str, str]:
    """Translate a list of short JP strings -> {jp: en}. Missing items retried once."""
    uniq = sorted(set(s for s in strings if s))
    schema = {
        "type": "ARRAY",
        "items": {"type": "OBJECT", "properties": {"i": {"type": "INTEGER"}, "en": {"type": "STRING"}},
                  "required": ["i", "en"]},
    }

    def run(chunk):
        listing = "\n".join(f"{i}\t{s}" for i, s in enumerate(chunk))
        prompt = (f"{GLOSSARY}\nContext: {context}\nTranslate each Japanese string to concise natural US English. "
                  f"Return JSON array of {{i, en}} for every line (i = index).\n\n{listing}")
        out = gen_json(prompt, schema)
        return {chunk[o["i"]]: o["en"].strip() for o in out if 0 <= o.get("i", -1) < len(chunk)}

    res: dict[str, str] = {}
    chunks = [uniq[i:i + batch] for i in range(0, len(uniq), batch)]
    with ThreadPoolExecutor(workers) as ex:
        for d in ex.map(run, chunks):
            res.update(d)
    missing = [s for s in uniq if s not in res]
    if missing:
        chunks = [missing[i:i + 20] for i in range(0, len(missing), 20)]
        with ThreadPoolExecutor(workers) as ex:
            for d in ex.map(run, chunks):
                res.update(d)
    return res
