#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname "$0")/lib.sh"
load_env

require_command claude
require_command node
require_command npx
require_command curl

COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:8188}"
COMFYUI_FORCE_REMOTE="${COMFYUI_FORCE_REMOTE:-1}"

echo "Verificando ComfyUI en ${COMFYUI_URL}..."
curl --fail --silent "${COMFYUI_URL}/system_stats" >/dev/null

confirm_replace_mcp comfyui

if [[ "$COMFYUI_FORCE_REMOTE" == "1" ]]; then
  claude mcp add \
    --scope user \
    comfyui \
    -e "COMFYUI_URL=${COMFYUI_URL}" \
    -e "COMFYUI_MCP_FORCE_REMOTE=1" \
    -- \
    npx -y comfyui-mcp@latest \
    --comfyui-url "${COMFYUI_URL}" \
    --force-remote
else
  claude mcp add \
    --scope user \
    comfyui \
    -e "COMFYUI_URL=${COMFYUI_URL}" \
    -- \
    npx -y comfyui-mcp@latest \
    --comfyui-url "${COMFYUI_URL}"
fi

echo "ComfyUI MCP instalado."
claude mcp get comfyui
