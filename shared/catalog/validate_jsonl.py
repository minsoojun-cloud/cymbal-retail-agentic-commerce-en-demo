"""Validate Retail product JSONL before loading (required fields, prices, images)."""
import json
import sys
from collections import Counter

path = sys.argv[1]
errors, stats = [], Counter()
ids = set()
for i, line in enumerate(open(path, encoding="utf-8"), 1):
    r = json.loads(line)
    stats["total"] += 1
    if not r.get("id") or not r.get("title"):
        errors.append(f"line {i}: missing id/title")
    if r["id"] in ids:
        errors.append(f"line {i}: duplicate id {r['id']}")
    ids.add(r["id"])
    p = r.get("priceInfo") or {}
    if not p.get("price"):
        stats["no_price"] += 1
    if p.get("originalPrice", 0) < p.get("price", 0):
        errors.append(f"line {i}: originalPrice < price")
    n = len(r.get("images") or [])
    stats["multi_image"] += n > 1
    stats["no_image"] += n == 0
    stats["with_description"] += bool(r.get("description"))
    stats["on_sale"] += any(a["key"] == "on_sale" and a["value"].get("text") == ["true"] for a in r.get("attributes", []))
print(dict(stats))
if errors:
    print("\n".join(errors[:50]))
    sys.exit(1)
print("OK")
