#!/usr/bin/env bash
# 20_iam.sh — runtime service account, its roles, and Cloud Build / source-deploy permissions (idempotent)
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

log_info "runtime service account ${RUNTIME_SA}"
if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  run_or_echo gcloud iam service-accounts create "${RUNTIME_SA_NAME}" --project "${PROJECT_ID}" \
    --display-name "Cymbal Retail demo runtime (all Cloud Run services)"
  [[ "${DRY_RUN:-0}" == "1" ]] || retry 6 5 gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1
else
  log_ok "exists"
fi

for role in roles/retail.editor roles/bigquery.dataEditor roles/bigquery.jobUser roles/storage.objectAdmin \
            roles/aiplatform.user roles/run.invoker roles/logging.logWriter roles/monitoring.metricWriter \
            roles/cloudtrace.agent roles/iam.serviceAccountTokenCreator roles/serviceusage.serviceUsageConsumer; do
  grant_project_role "serviceAccount:${RUNTIME_SA}" "${role}"
done

log_info "Cloud Build / Cloud Run source-deploy permissions"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
for role in roles/run.builder roles/storage.objectAdmin roles/artifactregistry.writer roles/logging.logWriter; do
  grant_project_role "serviceAccount:${COMPUTE_SA}" "${role}"
done
for role in roles/cloudbuild.builds.builder roles/run.admin roles/iam.serviceAccountUser; do
  grant_project_role "serviceAccount:${CB_SA}" "${role}"
done
for sa in "${COMPUTE_SA}" "${CB_SA}"; do   # builders must be able to actAs the runtime SA
  run_or_echo gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" --project "${PROJECT_ID}" \
    --member "serviceAccount:${sa}" --role roles/iam.serviceAccountUser --quiet >/dev/null 2>&1 \
    || log_warn "actAs binding for ${sa} failed (the account may not exist yet)"
done

if ! gcloud artifacts repositories describe cloud-run-source-deploy --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  run_or_echo gcloud artifacts repositories create cloud-run-source-deploy --repository-format docker \
    --location "${REGION}" --project "${PROJECT_ID}" --description "Cloud Run source deployments" || true
fi
log_ok "IAM ready"
