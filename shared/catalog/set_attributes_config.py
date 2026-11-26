"""Make custom attributes indexable/searchable so the agent can filter (e.g. on_sale, channel)."""
import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request

ATTRS = {
    # key: (indexable, searchable, dynamicFacetable)
    "attributes.on_sale": (True, False, True),
    "attributes.is_seasonal": (True, False, True),
    "attributes.dishes": (True, True, True),
    "attributes.top_category": (True, True, True),
    "attributes.storage_type": (True, False, True),
    "attributes.origin": (True, True, True),
    "attributes.allergens": (True, True, False),
    "attributes.promo_text": (False, True, False),
    "attributes.labels": (True, True, True),
    "attributes.brand_line": (True, True, True),
    "attributes.material": (True, True, True),
    "attributes.color": (True, True, True),
    "attributes.capacity": (True, True, False),
    "attributes.is_food": (True, False, False),
    "attributes.discount_rate": (True, False, False),
    "attributes.price_tax_excluded": (True, False, False),
    # v8 OTC medicines (医薬品・ヘルスケア)
    "attributes.drug_class": (True, True, True),
    "attributes.symptoms": (True, True, True),
    "attributes.abuse_control": (True, False, False),
    "attributes.age_min": (True, False, False),
    # v14 omnichannel (CONTRACT_EN.md)
    "attributes.channel": (True, False, True),
    "attributes.title_ja": (False, False, False),
}


def tok():
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True,
                          check=True).stdout.strip()


def post(url, project, body):
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {tok()}", "Content-Type": "application/json",
                                          "X-Goog-User-Project": project})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    a = ap.parse_args()
    if not a.project:
        raise SystemExit("--project or env PROJECT_ID required (source config.env)")
    base = (f"https://retail.googleapis.com/v2/projects/{a.project}/locations/global/catalogs/"
            f"default_catalog/attributesConfig")
    for key, (idx, srch, facet) in ATTRS.items():
        attr = {"key": key,
                "indexableOption": "INDEXABLE_ENABLED" if idx else "INDEXABLE_DISABLED",
                "searchableOption": "SEARCHABLE_ENABLED" if srch else "SEARCHABLE_DISABLED",
                "dynamicFacetableOption": "DYNAMIC_FACETABLE_ENABLED" if facet else "DYNAMIC_FACETABLE_DISABLED"}
        if key in ("attributes.discount_rate", "attributes.price_tax_excluded", "attributes.age_min"):
            attr["searchableOption"] = "SEARCHABLE_DISABLED"  # numeric attributes cannot be searchable
        for verb in ("replaceCatalogAttribute", "addCatalogAttribute"):
            try:
                post(f"{base}:{verb}", a.project, {"catalogAttribute": attr})
                print(f"{verb:24s} {key}")
                break
            except urllib.error.HTTPError as e:
                msg = e.read().decode()[:300]
                if verb == "addCatalogAttribute":
                    print(f"FAILED {key}: {e.code} {msg}")


if __name__ == "__main__":
    main()
