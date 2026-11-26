"""Configure AI Commerce Search (Retail API v2alpha) serving features for the Cymbal Retail demo.

Steps (all idempotent; run after import_pipeline.sh):
  facets   : serving config `default_search` exists, dynamicFacetSpec.mode=ENABLED
  complete : build autocomplete suggestions from data/products_retail.jsonl (short titles, sub-categories, brands,
             demo queries) -> BigQuery cymbal.autocomplete_suggestions ('suggestions' schema) ->
             CompletionService.importCompletionData; completionConfig matchingOrder=out-of-order
  conv     : conversational search customization (retailerDisplayName) + try enabling generative questions
  probe    : live probes of search (facets / conversationalSearchSpec / tileNavigationSpec), :conversationalSearch and
             :completeQuery; raw responses are written to $CATALOG_SCRATCH/probe_*.json and a summary is printed

Usage:
  source ../config.env && python3 setup_search_features.py [--steps facets,complete,conv,probe]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJECT = os.environ.get("PROJECT_ID", "")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER", "")
DATASET = os.environ.get("BQ_DATASET", "cymbal")
REGION = os.environ.get("REGION", "asia-northeast1")
SCRATCH = os.environ.get("CATALOG_SCRATCH", os.path.join(tempfile.gettempdir(), "cymbal_catalog_probe"))
CATALOG = f"projects/{PROJECT_NUMBER}/locations/global/catalogs/default_catalog"
API = "https://retail.googleapis.com/v2alpha"
SERVING = f"{CATALOG}/servingConfigs/default_search"
SUGGEST_TABLE = "autocomplete_suggestions"

DEMO_QUERIES = [  # (suggestion, score) — demo scenario queries rank first
    ("frozen bento", 1.0), ("bento", 0.98), ("bento box", 0.95), ("frying pan", 0.97), ("frying pan 26 cm", 0.9),
    ("gift", 0.96), ("gift set", 0.9), ("new crop rice", 0.97), ("dog food", 0.96), ("dog food small breed", 0.9),
    ("cat food", 0.9), ("moisturizing cream", 0.96), ("moisturizer sensitive skin", 0.88), ("hand cream", 0.9),
    ("medium hot curry", 0.95), ("curry roux medium hot", 0.9), ("salmon", 0.9), ("hot pot soup base", 0.93),
    ("hot pot set", 0.85), ("shrimp free", 0.85), ("towel gift", 0.88), ("humidifier", 0.86), ("water bottle", 0.86),
    ("tamagoyaki pan", 0.85), ("on sale", 0.9), ("deals", 0.88), ("organic", 0.84), ("frozen food", 0.9),
    ("dinner tonight", 0.9), ("pick up in store", 0.86),
    # OTC medicines
    ("cold medicine", 0.95), ("cold medicine for kids", 0.88), ("cold tablets", 0.86), ("pain relief", 0.92),
    ("headache", 0.88), ("eye drops", 0.92), ("eye drops tired eyes", 0.86), ("pain relief patch", 0.92),
    ("cooling patch", 0.86), ("throat spray", 0.9), ("lozenges", 0.86), ("cough medicine", 0.86),
    ("allergy medicine", 0.88), ("stomach medicine", 0.9), ("energy drink", 0.9), ("vitamin c", 0.86),
    ("anti itch", 0.85), ("cooling gel sheet", 0.88), ("thermometer", 0.86),
    # fresh / in-store
    ("fresh baked bread", 0.9), ("sushi", 0.9), ("sashimi", 0.88), ("pork buns", 0.88), ("kushikatsu", 0.86),
    ("takoyaki", 0.88), ("okonomiyaki", 0.86), ("local vegetables", 0.86), ("flowers", 0.88), ("bouquet", 0.86),
    ("wagyu sukiyaki", 0.86), ("warabi mochi", 0.84), ("ponzu", 0.84), ("light soy sauce", 0.82),
]

SIZE_TOKEN = re.compile(r"^[\[(]|^\d|^approx|^x$|^(pcs|ct|pack|packs|g|kg|ml|L|cm|sheets|slices|servings)$", re.I)
BRAND_PREFIX = re.compile(r"^(Cymbal( Basic| Select| Organic| Home| Beauty| Pet| Healthcare| Medical| Deli| Premium)?)\s+")


def tok():
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True,
                          check=True).stdout.strip()


TOKEN = None


def call(method, path, body=None, params=""):
    global TOKEN
    TOKEN = TOKEN or tok()
    url = f"{API}/{path}{('?' + params) if params else ''}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                                          "X-Goog-User-Project": PROJECT})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"raw": txt[:2000]}


def save(name, obj):
    os.makedirs(SCRATCH, exist_ok=True)
    with open(f"{SCRATCH}/probe_{name}.json", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- facets
def step_facets():
    st, sc = call("GET", SERVING)
    if st == 404:
        st, sc = call("POST", f"{CATALOG}/servingConfigs", params="servingConfigId=default_search",
                      body={"displayName": "default_search", "solutionTypes": ["SOLUTION_TYPE_SEARCH"],
                            "dynamicFacetSpec": {"mode": "ENABLED"}})
        print("create servingConfig default_search:", st)
    st, sc = call("PATCH", SERVING, params="updateMask=dynamicFacetSpec",
                  body={"dynamicFacetSpec": {"mode": "ENABLED"}})
    print("servingConfig dynamicFacetSpec:", st, sc.get("dynamicFacetSpec"))


# ---------------------------------------------------------------- autocomplete
def short_title(title: str) -> str:
    t = BRAND_PREFIX.sub("", title.strip())
    t = t.split(",")[0]
    toks = t.split()
    while len(toks) > 1 and SIZE_TOKEN.search(toks[-1]):
        toks.pop()
    toks = [x for x in toks if not x.startswith(("[", "（", "("))]
    return " ".join(toks[:3]).strip()


def build_suggestions(path):
    cnt, cats, brands, otc = Counter(), Counter(), Counter(), Counter()
    for line in open(path, encoding="utf-8"):
        p = json.loads(line)
        s = short_title(p["title"])
        s = s.lower()
        if 3 <= len(s) <= 40:
            # v8 OTC / v9 Kansai titles are counted separately so they do not push existing titles out of the top-4000 cut
            base = next((a["value"]["text"][0] for a in p.get("attributes", []) if a["key"] == "base_id"), "")
            (otc if p.get("categories", [""])[0].startswith("Health & Medicine") or base.startswith("k_kansai_")
             else cnt)[s] += 1
        for c in p.get("categories", []):
            for lvl in c.split(" > ")[1:]:
                cats[lvl.strip().lower()] += 1
        for b in p.get("brands", []):
            brands[b] += 1
    rows = {}
    for s, sc in DEMO_QUERIES:
        rows[s] = sc
    for c, n in cats.items():
        rows.setdefault(c, round(min(0.8, 0.5 + n / 400), 3))
    for b, n in brands.items():
        rows.setdefault(b, round(min(0.75, 0.45 + n / 2000), 3))
    for s, n in cnt.most_common(4000):
        rows.setdefault(s, round(min(0.7, 0.2 + n / 20), 3))
    for s, n in otc.most_common():
        rows.setdefault(s, round(min(0.7, 0.2 + n / 20), 3))
    return [{"suggestion": s, "globalScore": float(v), "frequency": None, "languageCode": "en"} for s, v in rows.items()]


def write_local(rows):
    """Local copy for the site / MCP fallback (used when Retail completion data is not available)."""
    with open(os.environ.get("AUTOCOMPLETE_JSONL") or f"{ROOT}/seed/catalog/autocomplete_suggestions.jsonl", "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: -r["globalScore"]):
            f.write(json.dumps({"suggestion": r["suggestion"], "score": r["globalScore"]}, ensure_ascii=False) + "\n")
    print("wrote data/autocomplete_suggestions.jsonl", len(rows))


def step_complete():
    rows = build_suggestions(os.environ.get("CATALOG_JSONL") or f"{ROOT}/seed/catalog/products_retail.jsonl")
    write_local(rows)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in rows:
        tmp.write(json.dumps({k: v for k, v in r.items() if v is not None}, ensure_ascii=False) + "\n")
    tmp.close()
    schema = "suggestion:STRING,globalScore:FLOAT,frequency:INTEGER,languageCode:STRING"
    subprocess.run(["bq", f"--project_id={PROJECT}", f"--location={REGION}", "load", "--replace",
                    "--source_format=NEWLINE_DELIMITED_JSON", f"{PROJECT}:{DATASET}.{SUGGEST_TABLE}", tmp.name, schema],
                   check=True, stdout=subprocess.DEVNULL)
    os.unlink(tmp.name)
    print(f"BQ {DATASET}.{SUGGEST_TABLE}: {len(rows)} suggestions (+ data/autocomplete_suggestions.jsonl)")
    # Retail reads completion data as cloud-retail-customer-data-access@system.gserviceaccount.com, which needs
    # BigQuery Data Viewer on the table. This fails if the org enforces iam.allowedPolicyMemberDomains.
    g = subprocess.run(["bq", f"--project_id={PROJECT}", "add-iam-policy-binding",
                        "--member=serviceAccount:cloud-retail-customer-data-access@system.gserviceaccount.com",
                        "--role=roles/bigquery.dataViewer", f"{PROJECT}:{DATASET}.{SUGGEST_TABLE}"],
                       capture_output=True, text=True)
    print("grant dataViewer to cloud-retail-customer-data-access:", "OK" if g.returncode == 0 else
          ("FAILED " + (g.stderr or g.stdout).strip().replace("\n", " ")[:300]))

    st, cc = call("PATCH", f"{CATALOG}/completionConfig",
                  params="updateMask=matchingOrder,maxSuggestions,minPrefixLength",
                  body={"matchingOrder": "out-of-order", "maxSuggestions": 10, "minPrefixLength": 1})
    print("completionConfig:", st, {k: cc.get(k) for k in ("matchingOrder", "maxSuggestions", "minPrefixLength")}
          if st == 200 else cc)

    st, op = call("POST", f"{CATALOG}/completionData:import", body={
        "inputConfig": {"bigQuerySource": {"projectId": PROJECT, "datasetId": DATASET, "tableId": SUGGEST_TABLE,
                                           "dataSchema": "suggestions"}}})
    if st != 200:
        print("completionData:import FAILED", st, json.dumps(op, ensure_ascii=False)[:800]); return
    name = op["name"]
    print("completion import operation:", name)
    for _ in range(90):
        st, d = call("GET", name)
        if d.get("done"):
            m = d.get("metadata", {})
            print("  done", "error=" + json.dumps(d["error"], ensure_ascii=False) if "error" in d else "",
                  f"success={m.get('successCount')} failure={m.get('failureCount')}")
            return
        time.sleep(10)
    print("  still running (continues in background)")


# ---------------------------------------------------------------- conversational
def step_conv():
    st, r = call("PATCH", f"{CATALOG}/conversationalSearchCustomizationConfig", body={
        "catalog": CATALOG, "retailerDisplayName": "Cymbal Retail"})
    print("conversationalSearchCustomizationConfig:", st, r if st != 200 else r.get("retailerDisplayName"))
    st, r = call("PATCH", f"{CATALOG}/generativeQuestionFeature", params="updateMask=featureEnabled,minimumProducts",
                 body={"catalog": CATALOG, "featureEnabled": True, "minimumProducts": 2})
    print("generativeQuestionFeature enable:", st, r.get("error", {}).get("message", r))
    st, r = call("GET", f"{CATALOG}/generativeQuestions")
    print("generativeQuestions:", st, len(r.get("questions", [])), "questions")
    save("generative_questions", r)


# ---------------------------------------------------------------- probes
SEARCH_BODY = {
    "query": "bento", "visitorId": "probe-visitor", "pageSize": 5, "languageCode": "en",
    "facetSpecs": [
        {"facetKey": {"key": "categories"}, "limit": 10},
        {"facetKey": {"key": "brands"}, "limit": 10},
        {"facetKey": {"key": "attributes.top_category"}},
        {"facetKey": {"key": "attributes.on_sale"}},
        {"facetKey": {"key": "attributes.storage_type"}},
        {"facetKey": {"key": "price", "intervals": [{"maximum": 300}, {"minimum": 300, "maximum": 1000},
                                                     {"minimum": 1000, "maximum": 3000}, {"minimum": 3000}]}},
    ],
    "dynamicFacetSpec": {"mode": "ENABLED"},
    "conversationalSearchSpec": {"followupConversationRequested": True},
    "tileNavigationSpec": {"tileNavigationRequested": True},
    "spellCorrectionSpec": {"mode": "AUTO"},
    "queryExpansionSpec": {"condition": "AUTO", "pinUnexpandedResults": True},
}


def step_probe(queries):
    out = {}
    for q in queries:
        st, r = call("POST", f"{SERVING}:search", body={**SEARCH_BODY, "query": q})
        save(f"search_{q.replace(' ', '_')}", r)
        out[q] = {"status": st, "totalSize": r.get("totalSize"), "keys": sorted(r.keys()),
                  "facets": {f["key"]: len(f.get("values", [])) for f in r.get("facets", [])},
                  "tiles": len(r.get("tileNavigationResult", {}).get("tiles", [])),
                  "conversational": bool(r.get("conversationalSearchResult")),
                  "corrected": r.get("correctedQuery"),
                  "top": [x["id"] for x in r.get("results", [])[:3]],
                  "error": r.get("error", {}).get("message")}
    st, r = call("POST", f"{SERVING}:conversationalSearch", body={
        "query": "I am looking for side dishes for my kids' lunch box", "visitorId": "probe-visitor",
        "conversationalFilteringSpec": {"enableConversationalFiltering": True}})
    save("conversational_search", r)
    final = r[-1] if isinstance(r, list) and r else r
    out["conversationalSearch"] = {"status": st, "keys": sorted(final.keys()) if isinstance(final, dict) else None,
                                   "refinedSearch": [x.get("query") for x in ((final.get("refinedSearch") or [])
                                                     if isinstance(final, dict) else []) if x.get("query")][:6],
                                   "followup": (final.get("followupQuestion") or {}).get("followupQuestion"),
                                   "text": (final.get("conversationalTextResponse") or "")[:160]}
    for q in ["fry", "ben", "dog", "moist", "cold", "eye", "curr", "sal"]:
        for ds in ("user-data", "cloud-retail"):
            st, r = call("GET", f"{CATALOG}:completeQuery",
                         params=urllib.parse.urlencode({"query": q, "visitorId": "probe-visitor", "languageCodes": "en",
                                                        "dataset": ds, "maxSuggestions": 8}))
            out[f"completeQuery {q} [{ds}]"] = [x.get("suggestion") for x in r.get("completionResults", [])] \
                if st == 200 else f"{st} {r.get('error', {}).get('message')}"
    save("summary", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="facets,complete,conv,probe")
    ap.add_argument("--queries", default="bento,frying pan,dog food,moisturizing cream,curry,salmon,cold medicine")
    ap.add_argument("--autocomplete-only", action="store_true", help="= --steps facets,complete")
    ap.add_argument("--local-only", action="store_true", help="only write data/autocomplete_suggestions.jsonl")
    a = ap.parse_args()
    steps = a.steps.split(",")
    if a.local_only:
        write_local(build_suggestions(os.environ.get("CATALOG_JSONL") or f"{ROOT}/seed/catalog/products_retail.jsonl"))
        return
    if a.autocomplete_only:
        steps = ["facets", "complete"]
    if "facets" in steps:
        step_facets()
    if "complete" in steps:
        step_complete()
    if "conv" in steps:
        step_conv()
    if "probe" in steps:
        step_probe(a.queries.split(","))


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    main()
