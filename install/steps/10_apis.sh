#!/usr/bin/env bash
# 10_apis.sh — enable the Google Cloud APIs used by the demo (idempotent)
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

APIS=(run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com retail.googleapis.com
      aiplatform.googleapis.com bigquery.googleapis.com storage.googleapis.com iam.googleapis.com
      iamcredentials.googleapis.com speech.googleapis.com texttospeech.googleapis.com logging.googleapis.com
      monitoring.googleapis.com cloudresourcemanager.googleapis.com serviceusage.googleapis.com)
log_info "enabling ${#APIS[@]} APIs on ${PROJECT_ID} (takes 1-2 minutes on a new project)"
run_or_echo retry 3 15 gcloud services enable "${APIS[@]}" --project "${PROJECT_ID}"
log_ok "APIs enabled"
