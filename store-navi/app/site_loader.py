"""Import the existing storefront modules (online-store/app/{catalog,search,ai}.py) UNCHANGED as package `site_lib`.

- Container / deploy: deploy.sh copies them into `<srv>/site_lib/` → regular package import.
- Local dev: no copy, no symlink — a synthetic package `site_lib` whose __path__ points at ../online-store/app,
  so `site_lib.search` resolves its relative imports (`from .catalog import …`, `from . import ai`) there.
"""
from __future__ import annotations

import importlib
import sys
import types

from .config import REPO_DIR, SN_DIR


def ensure_site_lib() -> types.ModuleType:
    mod = sys.modules.get("site_lib")
    if mod is not None:
        return mod
    staged = SN_DIR / "site_lib"
    if (staged / "search.py").exists():
        if str(SN_DIR) not in sys.path:
            sys.path.insert(0, str(SN_DIR))
        return importlib.import_module("site_lib")
    src = REPO_DIR / "online-store" / "app"
    if not (src / "search.py").exists():
        raise ImportError(f"site_lib not found (neither {staged} nor {src})")
    mod = types.ModuleType("site_lib")
    mod.__path__ = [str(src)]  # type: ignore[attr-defined]
    mod.__file__ = str(src / "__init__.py")
    mod.__package__ = "site_lib"
    sys.modules["site_lib"] = mod
    return mod


def catalog_module():
    ensure_site_lib()
    return importlib.import_module("site_lib.catalog")


def search_module():
    ensure_site_lib()
    return importlib.import_module("site_lib.search")
