#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
load_env
require_command claude
require_command npx

: "${GITHUB_PERSONAL_ACCESS_TOKEN:?Define GITHUB_PERSONAL_ACCESS_TOKEN en .env}"

confirm_replace_mcp github

claude mcp add   --scope user   github   -e "GITHUB_PERSONAL_ACCESS_TOKEN=${GITHUB_PERSONAL_ACCESS_TOKEN}"   --   npx -y @modelcontextprotocol/server-github

echo "GitHub MCP instalado."
