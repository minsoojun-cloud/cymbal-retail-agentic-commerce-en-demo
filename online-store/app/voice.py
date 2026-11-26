"""Voice for the AI Concierge chat (env VOICE_ENABLED=1, set on cymbal-site).

POST /api/voice/stt  body = recorded audio (audio/webm;codecs=opus from MediaRecorder; ogg/wav also accepted)
                     → {text, engine, ms}
    1. Cloud Speech-to-Text v2 `recognizers/_:recognize`, model chirp_2 @ asia-southeast1, language en-US (env
       VOICE_STT_LANG), auto decoding, automatic punctuation.  env VOICE_STT_MODEL / VOICE_STT_LOCATION;
    2. chirp_3 @ us (second Speech-to-Text attempt);
    3. Gemini transcription (VOICE_GEMINI_MODEL, default gemini-3.5-flash @ global, then GEMINI_MODEL).
POST /api/voice/tts  {text} → audio/mpeg (Cloud TTS en-US-Chirp3-HD-Aoede, speakingRate 1.05); markdown / emoji
                     stripped; in-memory LRU cache (+ browser cache headers).

REST calls with the runtime service account (google-auth ADC); no extra SDK dependencies.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict

import httpx

log = logging.getLogger("cymbal-site.voice")

MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_TTS_CHARS = 400
STT_LANG = os.environ.get("VOICE_STT_LANG", "en-US")
TTS_VOICE = os.environ.get("VOICE_TTS_VOICE", "en-US-Chirp3-HD-Aoede")
TTS_LANG = "-".join(TTS_VOICE.split("-")[:2]) or "en-US"
TTS_RATE = float(os.environ.get("VOICE_TTS_RATE", "1.05") or 1.05)


def enabled() -> bool:
    return (os.environ.get("VOICE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def project() -> str:
    return os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""


# ── auth (ADC access token, cached) ─────────────────────────────────────────────────────────────
_creds = None
_creds_lock = asyncio.Lock()


def _token_sync() -> str:
    global _creds
    import google.auth
    import google.auth.transport.requests

    if _creds is None:
        _creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _creds.valid or not _creds.token:
        _creds.refresh(google.auth.transport.requests.Request())
    return _creds.token


async def access_token() -> str:
    async with _creds_lock:
        return await asyncio.to_thread(_token_sync)


async def _headers() -> dict:
    h = {"Authorization": "Bearer " + await access_token(), "Content-Type": "application/json"}
    if _creds is not None and not getattr(_creds, "service_account_email", None):
        h["x-goog-user-project"] = project()  # local user ADC needs a quota project; the runtime SA does not
    return h


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=30, pool=5),
                                    limits=httpx.Limits(max_keepalive_connections=8))
    return _client


# ── speech to text ──────────────────────────────────────────────────────────────────────────────
def stt_attempts() -> list[tuple[str, str]]:
    first = (os.environ.get("VOICE_STT_MODEL") or "chirp_2",
             os.environ.get("VOICE_STT_LOCATION") or "asia-southeast1")
    out = [first]
    for alt in [("chirp_3", "us"), ("chirp_2", "asia-southeast1")]:
        if alt not in out:
            out.append(alt)
    return out[:2]


async def stt_chirp(audio: bytes, model: str, location: str) -> str:
    host = "speech.googleapis.com" if location == "global" else f"{location}-speech.googleapis.com"
    url = f"https://{host}/v2/projects/{project()}/locations/{location}/recognizers/_:recognize"
    body = {"config": {"autoDecodingConfig": {}, "languageCodes": [STT_LANG], "model": model,
                       "features": {"enableAutomaticPunctuation": True}},
            "content": base64.b64encode(audio).decode()}
    r = await client().post(url, headers=await _headers(), json=body)
    if r.status_code != 200:
        raise RuntimeError(f"speech {model}@{location} HTTP {r.status_code}: {r.text[:200]}")
    parts = []
    for res in r.json().get("results", []) or []:
        alts = res.get("alternatives") or []
        if alts and alts[0].get("transcript"):
            parts.append(alts[0]["transcript"])
    return " ".join(p.strip() for p in parts).strip()


async def stt_gemini(audio: bytes, mime: str) -> str:
    from google import genai
    from google.genai import types

    models = [os.environ.get("VOICE_GEMINI_MODEL") or "gemini-3.5-flash",
              os.environ.get("GEMINI_MODEL") or "gemini-3-flash-preview"]
    gc = genai.Client(vertexai=True, project=project(), location=os.environ.get("GEMINI_LOCATION", "global"))
    last: Exception | None = None
    for m in dict.fromkeys(models):
        try:
            resp = await gc.aio.models.generate_content(
                model=m,
                contents=[types.Part.from_bytes(data=audio, mime_type=(mime or "audio/webm").split(";")[0]),
                          "Transcribe this English speech verbatim. Output only the transcript; output an empty string if there is no speech."],
                config=types.GenerateContentConfig(temperature=0))
            return (resp.text or "").strip().strip("\"")
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("gemini stt %s failed: %s", m, e)
    raise RuntimeError(f"gemini stt failed: {last}")


async def transcribe(audio: bytes, mime: str = "audio/webm") -> dict:
    t0 = time.time()
    errors = []
    for model, loc in stt_attempts():
        try:
            text = await stt_chirp(audio, model, loc)
            return {"text": text, "engine": f"speech-v2:{model}@{loc}", "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:160])
            log.warning("stt attempt failed: %s", e)
    text = await stt_gemini(audio, mime)
    return {"text": text, "engine": "gemini", "ms": int((time.time() - t0) * 1000), "fallback_errors": errors}


# ── text to speech ──────────────────────────────────────────────────────────────────────────────
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200d\u2300-\u23FF]")


def clean_for_speech(text: str) -> str:
    """Strip markdown / emoji / URLs so the reply reads naturally."""
    s = str(text or "")
    s = re.sub(r"```.*?```", " ", s, flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"(\*\*|__|`|~~)", "", s)
    s = re.sub(r"(?m)^\s*#{1,6}\s*", "", s)
    s = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", s)
    s = re.sub(r"(?m)^\s*\|.*\|\s*$", " ", s)  # tables
    s = s.replace("*", "")
    s = _EMOJI.sub("", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s*\n+\s*", ". ", s.strip())
    s = re.sub(r"([.!?:;,])\s*\.(\s|$)", r"\1\2", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = s.strip(" .")[:MAX_TTS_CHARS].strip()
    if s and s[-1] not in ".!?":
        s += "."
    return s


_tts_cache: "OrderedDict[str, bytes]" = OrderedDict()
TTS_CACHE_MAX = 256


async def synthesize(text: str, fmt: str = "mp3") -> tuple[bytes, str, bool]:
    """→ (audio bytes, mime, cache_hit)"""
    enc, mime = ("OGG_OPUS", "audio/ogg") if fmt == "ogg" else ("MP3", "audio/mpeg")
    key = hashlib.sha256(f"{TTS_VOICE}|{TTS_RATE}|{enc}|{text}".encode()).hexdigest()
    hit = _tts_cache.get(key)
    if hit is not None:
        _tts_cache.move_to_end(key)
        return hit, mime, True
    body = {"input": {"text": text},
            "voice": {"languageCode": TTS_LANG, "name": TTS_VOICE},
            "audioConfig": {"audioEncoding": enc, "speakingRate": TTS_RATE}}
    r = await client().post("https://texttospeech.googleapis.com/v1/text:synthesize", headers=await _headers(), json=body)
    if r.status_code != 200:
        raise RuntimeError(f"tts HTTP {r.status_code}: {r.text[:200]}")
    audio = base64.b64decode(r.json()["audioContent"])
    _tts_cache[key] = audio
    while len(_tts_cache) > TTS_CACHE_MAX:
        _tts_cache.popitem(last=False)
    return audio, mime, False
