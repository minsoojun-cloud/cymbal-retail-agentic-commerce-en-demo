"""pytest setup: offline DATA_MODE=local against the real store data files (store-navi/data) when present, else the
dev fixtures (tests/fixtures/data). Local appends go to a temp SN_STATE_DIR."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SN = HERE.parent
REAL = SN / "data"
FIX = HERE / "fixtures" / "data"

os.environ["DATA_MODE"] = "local"
os.environ.setdefault("APP_MODE", "customer")
os.environ.setdefault("AGENT_MODE", "mock")
os.environ.setdefault("DATA_DIR", str(REAL if (REAL / "stores.json").exists() and (REAL / "store_inventory.jsonl").exists() else FIX))
os.environ.setdefault("SN_STATE_DIR", tempfile.mkdtemp(prefix="sn_state_test_"))
os.environ.pop("STAFF_URL", None)
os.environ.setdefault("ONLINE_STORE_URL", "https://cymbal-site.example.test")
if str(SN) not in sys.path:
    sys.path.insert(0, str(SN))


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(os.environ["DATA_DIR"])


@pytest.fixture(scope="session")
def scenarios(data_dir) -> dict:
    p = data_dir / "scenarios.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@pytest.fixture(scope="session")
def client():
    """Customer-mode app. Staff/admin test modules switch APP_MODE=staff and purge `app.*`, so re-import cleanly."""
    from fastapi.testclient import TestClient

    if os.environ.get("APP_MODE") != "customer" or getattr(sys.modules.get("app.config"), "APP_MODE", "customer") != "customer":
        os.environ["APP_MODE"] = "customer"
        os.environ["DATA_MODE"] = "local"
        os.environ["AGENT_MODE"] = "mock"
        for m in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            del sys.modules[m]
    from app import main

    with TestClient(main.app) as c:
        yield c


def sse_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out
