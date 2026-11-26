---
name: install-cymbal-demo
description: Safely install the complete Cymbal Retail Agentic Commerce demo (English edition) into a Google Cloud project the user names. It installs 2 private backends, 4 public apps, a 3,000-product AI Commerce Search catalog with Local Inventory, BigQuery data and Cloud Storage buckets. Use when the user asks to install, deploy or set up this demo, or to re-run or resume a failed install. Verifies the result with tests/smoke_install.py and reports the URLs.
---

# Install skill for the Cymbal Retail demo (`install-cymbal-demo`)

Use this skill when the user asks to "deploy this repository to my project" or "install the demo". Run the steps below
in order, and report to the user in English. The manual equivalent is [INSTALL.md](../../../INSTALL.md).

---

## Step 1: Confirm the target

Before you run anything, confirm these values. **Ask the user if any of them is missing.** Never fall back silently to
`gcloud config get-value project`.

1. **`PROJECT_ID` (required)**: the target Google Cloud project. Billing must be enabled, and a dedicated project is
   recommended.
2. **`REGION` (optional)**: default `asia-northeast1`.
3. **`SERVICE_PREFIX` (optional)**: default `cymbal`. Change it only to run a second copy in the same project
   (lowercase, at most 21 characters).

Also check that the user can grant IAM roles and deploy Cloud Run in the project. Owner is simplest. The individual
roles are listed in INSTALL.md section 1.2.

---

## Step 2: Create `install/config.env`, then preflight and dry run

1. Create the user config. Never edit the root `config.env`, which is derived.
   ```bash
   cp install/config.example.env install/config.env
   ```
   Set `PROJECT_ID="<the user's project>"`, plus `REGION` or `SERVICE_PREFIX` if they differ from the defaults. Leave
   the model fields empty to use the verified defaults.
2. Preview the plan and check the environment:
   ```bash
   ./install/install.sh --dry-run
   ./install/install.sh --steps 00_preflight
   ```
   - Show the user the configuration summary (project, region, prefix, buckets, models, the 4 app URLs) and get a
     go-ahead.
   - If `gcloud` is not authenticated, ask the user to run `gcloud auth login`. Do not try to work around it.
   - If billing is disabled, ask the user to link a billing account in the Cloud Console, and stop until they have.

---

## Step 3: Run the installer

```bash
./install/install.sh -y            # first run
./install/install.sh --resume -y   # after an interruption: skip finished steps
./install/install.sh --from <step> -y   # after fixing a failed step
```

A first install takes about **35 to 50 minutes**. Run it in the background and tell the user what is happening at each
step. If a step fails, the installer prints the command to resume it. Read the error, fix the cause, and continue with
`--from <step>`. All steps are idempotent, so you can re-run them safely.

| # | Step | What it does |
|---|---|---|
| 1 | `00_preflight` | Checks the CLIs (`gcloud`, `bq`, `python3` 3.9 or later, `curl`), auth, an ACTIVE project, billing and the seed files |
| 2 | `10_apis` | Enables Run, Cloud Build, Artifact Registry, Retail, Vertex AI, BigQuery, Storage, IAM, Speech, TTS, Logging and Monitoring |
| 3 | `15_models` | `probe_models.py` checks the Gemini models and records fallbacks in `install/.state/models.env` |
| 4 | `20_iam` | Creates the `${SERVICE_PREFIX}-runtime` SA and its roles, and gives the build SAs the source-deploy permissions |
| 5 | `30_retail` | Retail `acceptTerms`, `enrollSolution` (Search), and the Retail service agent with BigQuery read access |
| 6 | `40_data` | Creates 3 buckets, renders the seed for the project, and loads the BigQuery `cymbal` and `cymbal_enrichment` seed |
| 7 | `50_catalog_import` | Loads 3,000 products to BigQuery, runs the AI Commerce Search FULL import, applies attributes and autocomplete, and pushes Local Inventory for S01 to S03 |
| 8 | `60_backend` | Deploys `<prefix>-commerce-mcp`, then `<prefix>-adk-agent` (both private) |
| 9 | `70_apps` | Deploys `<prefix>-site`, `-store-app`, `-store-staff` and `-data-enrichment` in parallel (logs in `install/.state/logs/`) |
| 10 | `80_public` | Makes the 4 apps publicly reachable (see Step 4) |
| 11 | `90_verify` | Runs `tests/smoke_install.py` and prints the URLs |

Useful options: `--project`, `--region`, `--prefix`, `--steps a,b`, `--sequential` (if Cloud Build concurrency is
limited), `--dry-run`.

---

## Step 4: Public access and organization policies (guardrail)

The 4 apps are meant to open in a browser without signing in. `80_public` handles this with documented options only:

1. If the app already answers anonymously, it does nothing.
2. Otherwise it grants `roles/run.invoker` to `allUsers`.
3. If the org's Domain Restricted Sharing policy (`iam.allowedPolicyMemberDomains`) rejects `allUsers`, it sets the
   Cloud Run service to `--no-invoker-iam-check`. The app `deploy.sh` scripts also deploy with this flag.

The MCP server and the ADK agent always stay private.

- **Never** change, disable or override organization policies. Never grant yourself extra roles, and never make the
  backends public.
- If `80_public` still reports blocked apps (for example because the org enforces `run.managed.requireInvokerIam`),
  stop and tell the user the options:
  1. Ask an org admin to allow public access for these services.
  2. Or use **Cloud Console > Cloud Run > `<service>` > Security > Allow public access**, if the org permits it, for:
     - `${SERVICE_PREFIX}-site` (online store)
     - `${SERVICE_PREFIX}-store-app` (Store App)
     - `${SERVICE_PREFIX}-store-staff` (Staff App and `/admin`)
     - `${SERVICE_PREFIX}-data-enrichment` (Enrichment Studio)
  3. Meanwhile, open an app privately:
     `gcloud run services proxy <service> --project <PROJECT_ID> --region <REGION> --port 8080`.
- Verify a private installation with `source config.env && python3 tests/smoke_install.py --id-token`.

---

## Step 5: Verify and report

1. If `90_verify` was not part of the run, or you fixed something afterwards, run the smoke test:
   ```bash
   source config.env && python3 tests/smoke_install.py        # add --id-token if the apps are private
   ```
   It checks: HTTP 200 on all 4 apps, online-store search, `/api/products/<id>/stores` from
   `retail_local_inventory`, 3,000 products and S01 availability from Local Inventory in the store app, the staff
   router and `/admin`, and the 63 seeded Enrichment Studio SKUs.
2. Every check must be `PASS`. If a check fails, use [docs/TROUBLESHOOTING.md](../../../docs/TROUBLESHOOTING.md),
   fix the cause and re-run the affected step.
3. Report to the user as a table:
   - Online store, Store App, Staff App, Manager console (`<staff URL>/admin`) and Enrichment Studio URLs. Read them
     from the `90_verify` output or `gcloud run services describe <service> --format='value(status.url)'`.
   - The models in use (`GEMINI_MODEL`, `AGENT_MODEL`, `ENRICH_GEMINI_MODEL`, `IMAGE_MODEL`), and any fallback
     recorded in `install/.state/models.env`.
   - Public-access status for each app (public, or the action the user must take).
   - Next steps: the demo flow (dinner set with the shrimp-allergy guard, "Available in stores", Store App floor map,
     Staff App shelf photo, Enrichment Studio) and [docs/CUSTOMIZE.md](../../../docs/CUSTOMIZE.md).

Never commit `install/config.env` or `install/.state/`, and never paste project numbers or tokens into tracked files.

---

## Uninstall (only when the user asks)

```bash
./install/uninstall.sh            # delete the 6 Cloud Run services only
./install/uninstall.sh --all      # also BigQuery datasets + AI Commerce Search products (purge), 3 buckets, runtime SA
```

The script asks the user to type the project id. Do not pass `-y` unless the user explicitly asked for it.
