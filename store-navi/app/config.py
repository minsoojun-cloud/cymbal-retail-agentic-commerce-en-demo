"""Runtime configuration (env) shared by every Store Navi module.

APP_MODE      customer | staff            (which SPA + which API routes)
DATA_MODE     local | bq                  local = JSON files in DATA_DIR + LocalSearch; bq (default on Cloud Run) =
                                          BigQuery store tables + Retail (AICS) search
DATA_DIR      store data files (stores.json, layouts/, store_inventory.jsonl, …). Default: store-navi/data when
              stores.json exists there, else tests/fixtures/data (dev fixtures).
SN_STATE_DIR  local-mode append-only state (staff registrations / inventory events / uploaded photos)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SN_DIR = APP_DIR.parent                       # store-navi (or /srv in the container)
REPO_DIR = SN_DIR.parent

APP_MODE = (os.environ.get("APP_MODE") or "customer").strip().lower()
if APP_MODE not in ("customer", "staff"):
    APP_MODE = "customer"


def data_mode() -> str:
    m = (os.environ.get("DATA_MODE") or "").strip().lower()
    if m in ("local", "bq"):
        return m
    # Cloud Run sets K_SERVICE → BigQuery; local dev → files
    return "bq" if os.environ.get("K_SERVICE") else "local"


def data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    real = SN_DIR / "data"
    if (real / "stores.json").exists():
        return real
    return SN_DIR / "tests" / "fixtures" / "data"


def state_dir() -> Path:
    p = Path(os.environ.get("SN_STATE_DIR") or (Path(tempfile.gettempdir()) / "store_navi_state"))
    p.mkdir(parents=True, exist_ok=True)
    return p


STATIC_DIR = Path(os.environ.get("STATIC_DIR") or (SN_DIR / "static"))

# No hard-coded project ids (CONTRACT_EN hard rule 4): everything comes from env (deploy.sh sources ../config.env).
PROJECT_ID = (os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
PROJECT_NUMBER = (os.environ.get("PROJECT_NUMBER") or "").strip()
REGION = os.environ.get("REGION", "asia-northeast1")
BQ_DATASET = os.environ.get("BQ_DATASET", "cymbal")
PHOTO_BUCKET = os.environ.get("PHOTO_BUCKET") or (f"{PROJECT_ID}-store-navi" if PROJECT_ID else "")
REFRESH_SEC = float(os.environ.get("STORE_DATA_REFRESH_SEC", "20"))


def _default_site_url() -> str:
    """ONLINE_STORE_URL → SITE_BASE_URL → deterministic Cloud Run URL of cymbal-site (when PROJECT_NUMBER is set)."""
    for k in ("ONLINE_STORE_URL", "SITE_BASE_URL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v.rstrip("/")
    if PROJECT_NUMBER:
        return f"https://{os.environ.get('SITE_SERVICE', 'cymbal-site')}-{PROJECT_NUMBER}.{REGION}.run.app"
    return ""


ONLINE_STORE_URL = _default_site_url()
# CONTRACT v14: availability from Retail LocalInventory (Product.local_inventories) first, BigQuery cache fallback.
RETAIL_LOCAL_INVENTORY = (os.environ.get("RETAIL_LOCAL_INVENTORY") or "1").strip().lower() not in ("0", "false", "off", "no")
DEFAULT_CUSTOMER = "CR-0001"
DEFAULT_STORE = "S01"


def http_url(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    return v if v.startswith(("https://", "http://")) else ""


def product_images_dir() -> Path:
    env = os.environ.get("PRODUCT_IMAGES_DIR")
    if env:
        return Path(env)
    staged = SN_DIR / "product_images"
    if staged.exists():
        return staged
    return REPO_DIR / "online-store" / "static" / "img" / "products"
