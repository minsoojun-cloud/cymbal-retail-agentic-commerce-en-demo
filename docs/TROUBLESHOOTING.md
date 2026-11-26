# Troubleshooting (`docs/TROUBLESHOOTING.md`)

Common problems when installing or running the Cymbal Retail Agentic Commerce demo (English edition), with their causes
and fixes. Every installer step is **idempotent**, so after a fix you can re-run the failed step with
`./install/install.sh --from <step>`.

> [!TIP]
> Quick diagnostics:
> ```bash
> source config.env                               # derived settings (never edit config.env itself)
> ./install/install.sh --dry-run                  # resolved project, URLs, buckets, models and the plan
> python3 tests/smoke_install.py                  # end-to-end smoke test (add --id-token for private apps)
> python3 shared/catalog/verify_retail.py         # Retail: product count, Local Inventory, pickup filter, facets
> ls install/.state/ install/.state/logs/         # finished steps (*.done), probed models, deploy logs
> ```

---

## 1. Organization policy, authentication and public access

### Q1. The apps return `403 Forbidden`, or `80_public` says "allUsers binding rejected"

**Cause.** The four apps are meant to be public. `online-store/deploy.sh`, `store-navi/deploy.sh` and
`data-enrichment/deploy.sh` deploy with `--no-invoker-iam-check`, and `80_public` then checks `GET <url>/api/health`
anonymously. Organizations with **Domain Restricted Sharing** (`iam.allowedPolicyMemberDomains`) reject `allUsers`
bindings. Some organizations also enforce `run.managed.requireInvokerIam`, which blocks the `--no-invoker-iam-check`
fallback.

**What the installer does.** For each app that does not answer with HTTP 200, it (1) grants `roles/run.invoker` to
`allUsers` and, if that is rejected, (2) sets `--no-invoker-iam-check`. If the app is still not reachable, it lists the
blocked services and exits with code 1. **It never changes org policies, and you should not change them either.**

**Fix (choose one of the sanctioned options):**
1. Cloud Console → **Cloud Run** → *service* → **Security** → Authentication → **Allow public access** (if your org
   allows it).
2. Ask your organization admin whether a public demo is allowed for this project.
3. Keep the apps private and open them through an authenticated local proxy:
   ```bash
   source config.env
   gcloud run services proxy "${SITE_SERVICE}" --project "${PROJECT_ID}" --region "${REGION}" --port 8080
   # then open http://localhost:8080 (repeat with other ports for STORE_APP_SERVICE / STAFF_SERVICE / ENRICH_SERVICE)
   ```
   Run the smoke test with an identity token: `python3 tests/smoke_install.py --id-token`.

> [!NOTE]
> Cross links between the apps point to the public URLs. When you use the proxy, open each app through its own proxy
> port.

### Q2. `00_preflight` fails: "no active gcloud account", "project is not accessible", or "billing is NOT enabled"

- Run `gcloud auth login`. The installer uses your user credentials with `gcloud`, `bq` and `curl`.
- Check the project id in `install/config.env` (or `--project`). The project must be **ACTIVE**, and you need roughly
  Owner, or the equivalent admin roles (Service Usage, IAM, Cloud Run, Cloud Build, BigQuery, Storage, Retail,
  Vertex AI).
- Link a billing account to the project. If you cannot read the billing status (you lack
  `billing.resourceAssociations.list`), the installer prints a warning and continues.
- `Could not resolve the project number` means `gcloud projects describe <id>` failed, so the cached value in
  `install/.state/project_number.<id>` was never written. Fix your access, then re-run.

### Q3. `30_retail` warns "acceptTerms failed" or "enrollSolution failed"

On a brand-new project the Retail API can take a few minutes to become usable after `10_apis`. The step retries 6 times.
If it still fails, open the Cloud Console → **Search for commerce** → **Get started** once to accept the data-use terms,
then run `./install/install.sh --from 30_retail`. "Already enrolled" responses are treated as success.

---

## 2. Vertex AI / Gemini models

### Q4. A Gemini model returns `404 NOT_FOUND` or "model not available"

Model availability differs by project and over time. Step `15_models` (`install/lib/probe_models.py`) probes each role at
location `global` with a one-line `generateContent`:

| Role (env) | Default | Fallbacks tried in order |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3-flash-preview` | `gemini-3.5-flash`, `gemini-3.8-flash`, `gemini-2.5-flash` |
| `AGENT_MODEL` | `gemini-3.5-flash` | `gemini-3.8-flash`, `gemini-3-flash-preview`, `gemini-2.5-flash` |
| `ENRICH_GEMINI_MODEL` | `gemini-3.8-flash` | `gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-2.5-flash` |
| `IMAGE_MODEL` | `gemini-3.1-flash-image` | `gemini-3-pro-image`, `gemini-2.5-flash-image` |

If a default model does not answer, the first working fallback is written to `install/.state/models.env` as
`PROBED_<ROLE>`, and `config.env` uses it. **A model you pinned in `install/config.env` is never replaced.** If a pinned
model fails, clear it or change it, then run:

```bash
./install/install.sh --steps 15_models,60_backend,70_apps
cat install/.state/models.env
```

### Q5. `429 RESOURCE_EXHAUSTED` during the demo or during enrichment

- Gemini runs at location `global` (dynamic shared quota). Short bursts of 429 errors are retried by the apps. The
  Enrichment Studio falls back to `GEMINI_FALLBACK_MODEL` when the selected model is busy.
- For large enrichment runs, use **Batch & cost** (Vertex AI Batch Prediction) instead of live runs of more than 50 SKUs.
- If 429 errors persist, request more quota, or switch `AGENT_MODEL` / `ENRICH_GEMINI_MODEL` to another probed model and
  redeploy the affected app.

---

## 3. Vertex AI Search for Commerce (Retail API) and Local Inventory

### Q6. Search returns 0 results (or only BigQuery results) right after the install

- A Retail FULL import (`50_catalog_import`, step 3/6) takes 2 to 5 minutes. Indexing for search then needs **another
  few minutes, and sometimes up to an hour on a brand-new project**. In the meantime, the MCP server falls back to a
  BigQuery keyword search and the site falls back to its local search engine, so the demo keeps working.
- Check the state with `source config.env && python3 shared/catalog/verify_retail.py`.
- If the product count in branch 0 is not 3,000, re-run the import with `./install/install.sh --from 50_catalog_import`.
- Facets or attribute filters (`channel`, `on_sale`, `allergens` …) are missing: re-apply the attributes config with
  `python3 shared/catalog/set_attributes_config.py --project "${PROJECT_ID}"`. Changes take a few minutes to apply.
- Autocomplete is empty: re-run `python3 shared/catalog/setup_search_features.py --autocomplete-only`. Completion data
  also needs time to be indexed.

### Q7. "Available in stores" shows no stores, the "Pick up in store" facet is empty, or the badge says BigQuery

- Local Inventory is pushed **after** the FULL import (step 6/6, about 5 to 8 minutes). A FULL import drops existing
  local inventories, so **re-run the push after every re-import**:
  ```bash
  source config.env && python3 store-navi/scripts/push_local_inventory.py --force
  python3 store-navi/scripts/push_local_inventory.py --verify      # spot-check a few products
  ```
- When the Retail call fails or times out, the store APIs fall back to BigQuery `store_inventory`. The response says
  `source: "bigquery"`, and the Store App badge names BigQuery instead of Commerce Search Local Inventory. The demo still
  works. Fix the push, then reload the page.
- A staff registration is saved but Retail is not updated: `addLocalInventories` is best effort and non-fatal (the API
  response reports the result in `local_inventory`). The BigQuery row is still saved, and the shopper app shows the new
  shelf through the BigQuery delta. Check the staff-service logs for `retail addLocalInventories failed`.

---

## 4. Cloud Build / Cloud Run deployments

### Q8. `60_backend` or `70_apps` fails with a permission error during `gcloud run deploy --source`

- Source deploys build with Cloud Build and push to the Artifact Registry repository `cloud-run-source-deploy` in
  `${REGION}`. `20_iam` grants the default compute service account (`<PROJECT_NUMBER>-compute@…`) `run.builder`,
  `storage.objectAdmin`, `artifactregistry.writer` and `logging.logWriter`. It grants the Cloud Build service account
  `cloudbuild.builds.builder`, `run.admin` and `iam.serviceAccountUser`, and lets both act as the runtime service
  account. On a new project these accounts can appear late. Re-run `./install/install.sh --from 20_iam`.
- IAM changes can take a few minutes to propagate. `grant_project_role` retries, but a deploy that starts right after the
  grants can still fail once. Re-run the step.
- Read the full log of each app deploy in `install/.state/logs/<site|store-app|store-staff|data-enrichment>.log`. If
  parallel builds hit a Cloud Build quota, run `./install/install.sh --steps 70_apps --sequential`.

### Q9. Redeploy just one app after a change

```bash
source config.env && bash online-store/deploy.sh                                     # online store
source config.env && SERVICE_NAME="${STORE_APP_SERVICE}" bash store-navi/deploy.sh   # store app (customer)
source config.env && SERVICE_NAME="${STAFF_SERVICE}" bash store-navi/deploy.sh       # staff app + /admin
source config.env && bash data-enrichment/deploy.sh                                  # Enrichment Studio
./install/install.sh --steps 60_backend                                              # MCP server + ADK agent
```

### Q10. The online store chat says it cannot reach the AI Concierge (`502` / `503`), or answers with a scripted reply

- The site's BFF proxies `POST /api/agent/stream` to the **private** ADK agent with an ID token. On `503` (agent
  unreachable), the page falls back to its scripted **mock concierge**. If `ADK_AGENT_URL` was empty at deploy time, or
  the site was deployed with `AGENT_BACKEND=mock`, the site always uses the mock concierge.
- Check that both backends are deployed and **Ready**:
  `gcloud run services list --project "${PROJECT_ID}" --region "${REGION}"`.
- The runtime service account needs `roles/run.invoker` (granted in `20_iam`). Deploy the backends first, then redeploy
  the site so it picks up `shared/adk_agent/DEPLOYMENT.env`:
  `./install/install.sh --steps 60_backend && source config.env && bash online-store/deploy.sh`.
- The first turn after a long idle period is slow: the agent and the MCP server run with `min-instances=1` and warm up
  the Sakura persona at startup. Open each app once before a demo.
- Store App **Ask AI** runs in the store-app process (`AGENT_MODE`). If the live model fails before it produces output,
  the app falls back to a scripted reply. Check `AGENT_MODEL` and the store-app logs.

---

## 5. Voice (microphone input / read-aloud)

### Q11. The microphone button is missing, or speech recognition / read-aloud fails

- Voice is controlled by `VOICE_ENABLED` (default `1`) in `install/config.env`. After changing it, redeploy the site.
- The browser must allow the microphone, and the page must be served over HTTPS (Cloud Run URLs are HTTPS, and so is
  `localhost`). Through `gcloud run services proxy`, use `http://localhost:<port>`.
- Speech-to-Text v2 uses `chirp_2` at `asia-southeast1` with language `en-US`, then falls back to `chirp_3` at `us`.
  Text-to-Speech uses `en-US-Chirp3-HD-Aoede`. Both APIs are enabled in `10_apis` (`speech.googleapis.com`,
  `texttospeech.googleapis.com`). If they were enabled only recently, wait a few minutes and retry.

---

## 6. Re-runs, resume and clean-up

### Q12. The install stopped half-way. Do I start over?

No. Each finished step writes `install/.state/<step>.done`.

```bash
./install/install.sh --resume                  # skip finished steps
./install/install.sh --from 50_catalog_import  # run from one step to the end
./install/install.sh --steps 70_apps,90_verify # run selected steps only
```

Re-running is safe: BigQuery seed tables are reloaded with WRITE_TRUNCATE, empty runtime tables are kept, staff shelf
registrations are preserved, the catalog is FULL-imported again, and Local Inventory is re-pushed with `--force`.

### Q13. I changed `PROJECT_ID`, `REGION` or `SERVICE_PREFIX`

The URLs and the rendered seed are derived from these values. `config.env` re-renders `install/.state/rendered/`
automatically when they change. Run the full installer again for the new target. For a clean slate, delete
`install/.state/` first, because it holds `.done` markers, cached project numbers, probed models and logs. Remove an old
installation with `./install/uninstall.sh` (Cloud Run services only), or add `--data`, `--buckets`, `--sa` or `--all`.
Uninstall asks you to type the project id unless you pass `-y`.

### Q14. The smoke test fails on one check only

`tests/smoke_install.py` checks: HTTP 200 on `/` and `/api/health` for the 4 apps; a search for "curry" that returns
products; `/api/products/<id>/stores` served from Retail Local Inventory; 3,000 products on the store-app Retail engine;
availability at `S01` from `retail_local_inventory`; the staff router and `/admin`; and 63 seeded SKUs in the
Enrichment Studio. Failures on the Retail and Local Inventory checks right after install are usually indexing delays
(see Q6 and Q7). Wait a few minutes, then re-run `./install/install.sh --steps 90_verify`.
