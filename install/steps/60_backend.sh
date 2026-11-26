#!/usr/bin/env bash
# 60_backend.sh — deploy the private backends: Commerce MCP server, then the ADK AI Concierge agent
set -euo pipefail
STEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${STEP_DIR}/../lib/common.sh"
load_config

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "  [dry-run] shared/mcp_server/deploy.sh -> ${MCP_SERVICE} (private, min 1)"
  echo "  [dry-run] shared/adk_agent/deploy.sh  -> ${ADK_SERVICE} (private, min 1 / max 1, model ${AGENT_MODEL})"
  exit 0
fi
log_info "[1/2] Commerce MCP server -> ${MCP_SERVICE} (private)"
bash "${REPO_ROOT}/shared/mcp_server/deploy.sh"
MCP_URL="$(service_url "${MCP_SERVICE}")"
[[ -n "${MCP_URL}" ]] || { log_err "could not read the URL of ${MCP_SERVICE}"; exit 1; }
log_ok "MCP server: ${MCP_URL}/mcp"

log_info "[2/2] ADK AI Concierge -> ${ADK_SERVICE} (private, model ${AGENT_MODEL})"
bash "${REPO_ROOT}/shared/adk_agent/deploy.sh"
log_ok "ADK agent: $(service_url "${ADK_SERVICE}")"
