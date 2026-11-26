#!/usr/bin/env python3
"""Bundle fallback data into shared/mcp_server/data/ (used when BigQuery is unreachable).

Copies (English, CONTRACT_EN.md v14; source files are owned by `data`):
  ../seed/personas/personas.json          → data/personas.json          (CR-0001 Sakura Tanaka, CR-0002, CR-0003)
  ../seed/personas/purchase_history.jsonl → data/purchase_history.jsonl
  first existing of ../data/stores_en.json, ../data/stores.json, ../store-navi/data/stores.json
                                        → data/stores.json            (check_store_inventory fallback)

Usage: python3 prepare_data.py [--force]
"""
import json
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
SRC = REPO / "seed" / "personas"
DST = HERE / "data"
STORE_SOURCES = [REPO / "seed" / "catalog" / "stores_en.json", REPO / "store-navi" / "data" / "stores.json"]
JA_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def copy(src: pathlib.Path, dst: pathlib.Path, force: bool) -> None:
    if not src.exists():
        print(f"skip {src} (missing)")
        return
    text = src.read_text(encoding="utf-8")
    if JA_RE.search(text) and not force:
        print(f"skip {src} (still contains Japanese; use --force to copy anyway)")
        return
    shutil.copyfile(src, dst)
    print(f"wrote {dst}")


def main() -> int:
    force = "--force" in sys.argv
    DST.mkdir(exist_ok=True)
    personas = json.loads((SRC / "personas.json").read_text(encoding="utf-8"))
    personas = personas if isinstance(personas, list) else [personas]
    if not any(p.get("customer_id") == "CR-0001" for p in personas):
        print("seed/personas/personas.json has no CR-0001; aborting")
        return 1
    copy(SRC / "personas.json", DST / "personas.json", force)
    copy(SRC / "purchase_history.jsonl", DST / "purchase_history.jsonl", force)
    for s in STORE_SOURCES:
        if s.exists():
            copy(s, DST / "stores.json", force)
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
