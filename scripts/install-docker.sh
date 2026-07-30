#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
require_command claude
require_command npx
require_command docker

docker ps >/dev/null

confirm_replace_mcp docker

claude mcp add   --scope user   docker   --   npx -y mcp-docker-server

echo "Docker MCP instalado."
echo "Advertencia: el acceso a Docker equivale a privilegios elevados sobre el host."
