"""Trigger Retail API products:import (FULL) from GCS JSONL (default) or BigQuery and poll until done.

GCS is the default because BigQuery cymbal.products carries an extra top-level `channel` column (CONTRACT_EN.md).
"""
import os
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


def token():
    return subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True,
                          check=True).stdout.strip()


def call(method, url, project, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json",
                                          "X-Goog-User-Project": project})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.environ.get("PROJECT_ID"))
    ap.add_argument("--dataset", default=os.environ.get("BQ_DATASET", "cymbal"))
    ap.add_argument("--table", default=os.environ.get("BQ_TABLE", "products"))
    ap.add_argument("--gcs-uri", default="", help="gs://... JSONL in Retail product schema (default source)")
    a = ap.parse_args()
    if not a.project:
        raise SystemExit("--project or env PROJECT_ID required")
    base = f"https://retail.googleapis.com/v2alpha/projects/{a.project}/locations/global/catalogs/default_catalog"
    src = ({"gcsSource": {"inputUris": [a.gcs_uri], "dataSchema": "product"}} if a.gcs_uri else
           {"bigQuerySource": {"projectId": a.project, "datasetId": a.dataset, "tableId": a.table,
                               "dataSchema": "product"}})
    body = {"inputConfig": src,
            "reconciliationMode": "FULL",
            "skipDefaultBranchProtection": True,  # demo catalog is replaced wholesale
            "errorsConfig": {"gcsPrefix": f"gs://{a.project}-catalog/import_errors/"}}
    try:
        op = call("POST", f"{base}/branches/0/products:import", a.project, body)
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode()[:2000]); sys.exit(1)
    name = op["name"]
    print("operation:", name)
    for _ in range(180):
        d = call("GET", f"https://retail.googleapis.com/v2alpha/{name}", a.project)
        m = d.get("metadata", {})
        print(f"  done={d.get('done', False)} success={m.get('successCount', 0)} failure={m.get('failureCount', 0)}")
        if d.get("done"):
            if "error" in d:
                print("ERROR:", d["error"]); sys.exit(1)
            return
        time.sleep(10)
    print("timeout while polling (operation continues in background)")


if __name__ == "__main__":
    main()
