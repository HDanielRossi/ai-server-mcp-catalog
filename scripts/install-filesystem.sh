#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
load_env
require_command claude
require_command npx

P1="${FILESYSTEM_PATH_1:-/opt/ai}"
P2="${FILESYSTEM_PATH_2:-/mnt/ai-storage/comfyui}"
P3="${FILESYSTEM_PATH_3:-/mnt/ai-storage/models}"

for path in "$P1" "$P2" "$P3"; do
  [[ -d "$path" ]] || { echo "Error: no existe $path" >&2; exit 1; }
done

confirm_replace_mcp filesystem

claude mcp add   --scope user   filesystem   --   npx -y @modelcontextprotocol/server-filesystem   "$P1" "$P2" "$P3"

echo "Filesystem MCP instalado."
