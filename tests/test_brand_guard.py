"""Brand / language guard (CONTRACT_EN.md hard rules 1-2, owner: data).

Scans deployable data, static assets and prompt/code files for
  (a) forbidden real retailer / brand names, and
  (b) Japanese characters (only allowed inside the `title_ja` product attribute, legacy docs and source dirs).
Run: python -m pytest -q tests/test_brand_guard.py         (set BRAND_GUARD_VERBOSE=1 to print every hit)
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JP = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
FORBIDDEN = re.compile(
    r"\bKFM\b|Kansai Food Market|Izumiya|Hankyu|\bOasis\b|Kansai Super|\bAEON\b|Topvalu|Top Valu|\bWAON\b|Green Beans|"
    r"Greenbeans|LAZONA|Grand Tree|Hikarie|Kosugi Square|"
    r"関西フードマーケット|イズミヤ|阪急|オアシス|関西スーパー|イオン|トップバリュ|グリーンビーンズ",
    re.I)

SCAN_DIRS = [
    "seed", "shared/mcp_server", "shared/adk_agent", "install", "docs", "online-store/static", "online-store/app", "store-navi/data",
    "store-navi/static", "store-navi/app", "data-enrichment/static", "data-enrichment/app",
]
TEXT_EXT = {".json", ".jsonl", ".js", ".html", ".css", ".py", ".md", ".txt", ".svg", ".csv", ".env"}
SKIP_PARTS = ("/tests/", "/testdata/", "/__pycache__/", "/node_modules/", "/.venv/", "/img/")
ALLOW_PATH = re.compile(r"(_legacy\.md$|/data/kfm/|/shared/catalog/source_ja/|/static/img/)")


def iter_files():
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [x for x in dns if not x.startswith(".") and x not in ("node_modules", "__pycache__", "tests")]
            for fn in fns:
                p = os.path.join(dp, fn)
                rel = "/" + os.path.relpath(p, ROOT)
                if os.path.splitext(fn)[1] not in TEXT_EXT or ALLOW_PATH.search(rel):
                    continue
                if any(s in rel + "/" for s in SKIP_PARTS[:-1]):
                    continue
                if os.path.getsize(p) > 60_000_000:
                    continue
                yield p, rel


def strip_title_ja(line: str) -> str:
    """Remove Retail attribute {"key": "title_ja", ...} values and plain "title_ja": "..." pairs."""
    if "title_ja" not in line:
        return line
    try:
        obj = json.loads(line)
    except Exception:  # noqa: BLE001
        return re.sub(r'"title_ja"\s*:\s*("(?:[^"\\]|\\.)*"|\{[^}]*\}\s*\})', '"title_ja": ""', line)

    def walk(o):
        if isinstance(o, dict):
            if o.get("key") == "title_ja":
                return {}
            return {k: walk(v) for k, v in o.items() if k != "title_ja"}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    return json.dumps(walk(obj), ensure_ascii=False)


def scan():
    jp_hits, brand_hits = [], []
    for p, rel in iter_files():
        try:
            text = open(p, encoding="utf-8").read()
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        if p.endswith(".json") and "title_ja" in text:
            try:
                lines = [strip_title_ja(json.dumps(json.loads(text), ensure_ascii=False))]
            except Exception:  # noqa: BLE001
                pass
        for n, line in enumerate(lines, 1):
            line2 = strip_title_ja(line) if p.endswith(".jsonl") else line
            if JP.search(line2):
                jp_hits.append(f"{rel}:{n}: {JP.search(line2).group(0)} …{line2[max(0, JP.search(line2).start() - 30):][:80]}")
            m = FORBIDDEN.search(line2)
            if m:
                brand_hits.append(f"{rel}:{n}: {m.group(0)}")
    return jp_hits, brand_hits


@pytest.fixture(scope="module")
def hits():
    return scan()


def _report(name, hs):
    if os.environ.get("BRAND_GUARD_VERBOSE"):
        print("\n".join(hs))
    by_file = {}
    for h in hs:
        by_file.setdefault(h.split(":")[0], 0)
        by_file[h.split(":")[0]] += 1
    return f"{len(hs)} {name} hits in {len(by_file)} files: " + ", ".join(
        f"{f}({c})" for f, c in sorted(by_file.items(), key=lambda x: -x[1])[:25]) + "\nfirst: " + "\n".join(hs[:15])


def test_no_forbidden_brand_names(hits):
    assert not hits[1], _report("brand", hits[1])


def test_no_japanese_outside_title_ja(hits):
    assert not hits[0], _report("japanese", hits[0])
