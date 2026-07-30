#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"
require_command claude

echo "=== MCP configurados ==="
claude mcp list

echo
echo "=== Detalle individual ==="
for name in filesystem github docker ssh comfyui; do
  if claude mcp get "$name" >/dev/null 2>&1; then
    echo
    echo "--- $name ---"
    claude mcp get "$name"
  fi
done
