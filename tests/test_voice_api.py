"""CONTRACT v6 storefront tests: voice endpoints (mocked Google clients), VOICE_ENABLED config flag, image rewrite.

run: pytest tests/test_voice_api.py   (from repo root)
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "online-store"
sys.path.insert(0, str(SITE))

DATA = SITE / "testdata" / "products_en.jsonl"     # small English fixture
FULL_DATA = ROOT / "seed" / "catalog" / "products_retail.jsonl"


def cfg_of(text: str) -> dict:
    return json.loads(text[len("window.CR_CONFIG = "):].rstrip(";\n"))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("DATA_FILE", str(DATA))
    monkeypatch.setenv("AI_MOCK", "1")
    monkeypatch.setenv("PROJECT_ID", "test-project")
    monkeypatch.setenv("PERSONA_DIR", str(SITE / "testdata" / "personas"))
    for k in ("ADK_AGENT_URL", "AGENT_BACKEND", "VOICE_ENABLED", "ENRICH_URL", "STORE_APP_URL"):
        monkeypatch.delenv(k, raising=False)
    from fastapi.testclient import TestClient
    from app import main, voice

    voice._tts_cache.clear()
    main.reset_engine()
    with TestClient(main.app) as c:
        yield c


class FakeResp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._p = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._p


class FakeClient:
    """records POSTs; answers Speech-to-Text v2 / TTS like the real REST APIs"""

    def __init__(self, stt_status=200):
        self.calls: list[tuple[str, dict]] = []
        self.stt_status = stt_status

    async def post(self, url, headers=None, json=None):  # noqa: A002
        self.calls.append((url, json))
        if "texttospeech.googleapis.com" in url:
            return FakeResp(200, {"audioContent": base64.b64encode(b"ID3fake-mp3").decode()})
        if "-speech.googleapis.com" in url:
            if self.stt_status != 200:
                return FakeResp(self.stt_status, {"error": {"message": "denied"}})
            return FakeResp(200, {"results": [{"alternatives": [{"transcript": "Recommend dinner for tonight."}]}]})
        return FakeResp(404, {})


@pytest.fixture()
def fake(monkeypatch):
    from app import voice

    fc = FakeClient()

    async def tok():
        return "test-token"

    monkeypatch.setattr(voice, "client", lambda: fc)
    monkeypatch.setattr(voice, "access_token", tok)
    return fc


# ── config flag ─────────────────────────────────────────────────────────────────────────────────
def test_config_voice_off_by_default(client):
    cfg = cfg_of(client.get("/config.js").text)
    assert "voiceEnabled" not in cfg and "voice" not in cfg


def test_config_voice_on(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "1")
    cfg = cfg_of(client.get("/config.js").text)
    assert cfg["voiceEnabled"] is True
    assert cfg["voice"]["sttEndpoint"] == "/api/voice/stt" and cfg["voice"]["ttsEndpoint"] == "/api/voice/tts"
    assert cfg["voice"]["ttsVoice"] == "en-US-Chirp3-HD-Aoede" and cfg["voice"]["lang"] == "en-US"


@pytest.mark.parametrize("val", ["0", "", "false"])
def test_config_voice_falsy(client, monkeypatch, val):
    monkeypatch.setenv("VOICE_ENABLED", val)
    assert "voiceEnabled" not in cfg_of(client.get("/config.js").text)


def test_voice_endpoints_404_when_disabled(client, fake):
    assert client.post("/api/voice/stt", content=b"abc", headers={"Content-Type": "audio/webm"}).status_code == 404
    assert client.post("/api/voice/tts", json={"text": "Hello"}).status_code == 404
    assert fake.calls == []


def test_index_has_voice_script_but_no_voice_dom(client):
    html = client.get("/").text
    assert "/js/voice.js" in html            # inert unless CR_CONFIG.voiceEnabled
    assert "cr-voice-mic" not in html       # the mic is injected by voice.js only


# ── STT ─────────────────────────────────────────────────────────────────────────────────────────
def test_stt_chirp(client, fake, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "1")
    r = client.post("/api/voice/stt", content=b"\x1aE\xdf\xa3webm", headers={"Content-Type": "audio/webm;codecs=opus"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["text"] == "Recommend dinner for tonight."
    assert j["engine"] == "speech-v2:chirp_2@asia-southeast1"
    url, body = fake.calls[0]
    assert url == ("https://asia-southeast1-speech.googleapis.com/v2/projects/test-project/"
                   "locations/asia-southeast1/recognizers/_:recognize")
    assert body["config"]["model"] == "chirp_2" and body["config"]["languageCodes"] == ["en-US"]
    assert "autoDecodingConfig" in body["config"]
    assert base64.b64decode(body["content"]) == b"\x1aE\xdf\xa3webm"


def test_stt_falls_back_to_gemini(client, fake, monkeypatch):
    from app import voice

    monkeypatch.setenv("VOICE_ENABLED", "1")
    fake.stt_status = 403

    async def gem(audio, mime):
        assert mime.startswith("audio/webm")
        return "Add the set to my cart"

    monkeypatch.setattr(voice, "stt_gemini", gem)
    j = client.post("/api/voice/stt", content=b"abc", headers={"Content-Type": "audio/webm"}).json()
    assert j["text"] == "Add the set to my cart" and j["engine"] == "gemini"
    assert len([c for c in fake.calls if "speech" in c[0]]) == 2  # chirp_2@asia-southeast1, chirp_3@us
    assert "us-speech.googleapis.com" in fake.calls[1][0] and fake.calls[1][1]["config"]["model"] == "chirp_3"


def test_stt_errors(client, fake, monkeypatch):
    from app import voice

    monkeypatch.setenv("VOICE_ENABLED", "1")
    assert client.post("/api/voice/stt", content=b"", headers={"Content-Type": "audio/webm"}).status_code == 400
    monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 10)
    assert client.post("/api/voice/stt", content=b"x" * 11, headers={"Content-Type": "audio/webm"}).status_code == 413
    monkeypatch.setattr(voice, "MAX_AUDIO_BYTES", 8 * 1024 * 1024)
    fake.stt_status = 500

    async def boom(audio, mime):
        raise RuntimeError("no gemini")

    monkeypatch.setattr(voice, "stt_gemini", boom)
    r = client.post("/api/voice/stt", content=b"abc", headers={"Content-Type": "audio/webm"})
    assert r.status_code == 502 and r.json()["error"] == "STT_FAILED"


# ── TTS ─────────────────────────────────────────────────────────────────────────────────────────
def test_tts_voice_and_cache(client, fake, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "1")
    r = client.post("/api/voice/tts", json={"text": "Here is your **salmon dinner set** 🍁"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3fake-mp3" and r.headers["x-tts-cache"] == "miss"
    url, body = fake.calls[0]
    assert url == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert body["voice"] == {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Aoede"}
    assert body["audioConfig"] == {"audioEncoding": "MP3", "speakingRate": 1.05}
    assert body["input"]["text"] == "Here is your salmon dinner set."
    r2 = client.post("/api/voice/tts", json={"text": "Here is your **salmon dinner set** 🍁"})
    assert r2.headers["x-tts-cache"] == "hit" and len(fake.calls) == 1
    r3 = client.post("/api/voice/tts", json={"text": "Test", "format": "ogg"})
    assert r3.headers["content-type"] == "audio/ogg" and fake.calls[-1][1]["audioConfig"]["audioEncoding"] == "OGG_OPUS"


def test_tts_empty(client, fake, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "1")
    assert client.post("/api/voice/tts", json={"text": "🍁 **  **"}).status_code == 400
    assert fake.calls == []


@pytest.mark.parametrize("src,want", [
    ("The **total** is ¥2,417", "The total is ¥2,417."),
    ("- Main: salmon\n- Side: hijiki", "Main: salmon. Side: hijiki."),
    ("See [details](https://example.com) here 😊", "See details here."),
    ("### Tonight's set\nAll ready!", "Tonight's set. All ready!"),
])
def test_clean_for_speech(src, want):
    from app.voice import clean_for_speech

    assert clean_for_speech(src) == want


# ── images (CONTRACT v6: relative /img/products/<file>) ─────────────────────────────────────────
@pytest.mark.parametrize("uri,want", [
    ("https://cymbal-site-123.asia-northeast1.run.app/img/products/4901_p.jpg", "/img/products/4901_p.jpg"),
    ("https://cymbal-site-123.asia-northeast1.run.app/img/products/a_b.jpg?v=2", "/img/products/a_b.jpg"),
    ("gs://example-catalog/img/products/x_p.jpg", "/img/products/x_p.jpg"),
    ("/img/products/x_p.jpg", "/img/products/x_p.jpg"),
    ("", ""),
])
def test_local_image(uri, want):
    from app.catalog import local_image

    assert local_image(uri) == want


def test_api_images_are_relative(client):
    r = client.get("/api/search", params={"q": "curry", "page_size": 12}).json()
    imgs = [p["image"] for p in r["results"] if p.get("image")]
    assert imgs and all(i.startswith("/img/products/") for i in imgs), imgs[:3]
    ids = ",".join(p["id"] for p in r["results"][:5])
    prods = client.get("/api/products", params={"ids": ids}).json()["products"]
    assert all(p["image"].startswith("/img/products/") for p in prods if p.get("image"))
    home = client.get("/api/home").json()
    assert all(c["image"].startswith("/img/products/") for c in home["categories"] if c.get("image"))
    rec = client.get("/api/recommendations", params={"type": "sale", "limit": 8}).json()
    assert all(p["image"].startswith("/img/products/") for p in rec["items"] if p.get("image"))


def test_catalog_uris_stay_absolute():
    """Retail needs absolute URIs: the catalog file keeps them; only the site rewrites."""
    if not FULL_DATA.exists():
        pytest.skip("full catalog not present")
    line = next(ln for ln in FULL_DATA.open(encoding="utf-8") if '"images"' in ln)
    uri = json.loads(line)["images"][0]["uri"]
    assert uri.startswith(("https://", "__SITE_BASE_URL__/")), uri  # seed placeholder, rendered at install time
