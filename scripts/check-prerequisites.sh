#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/lib.sh"

for cmd in claude node npm npx git docker; do
  require_command "$cmd"
done

echo "Claude Code: $(claude --version)"
echo "Node:       $(node --version)"
echo "npm:        $(npm --version)"
echo "npx:        $(npx --version)"
echo "Git:        $(git --version)"
echo "Docker:     $(docker --version)"
echo
echo "MCP configurados:"
claude mcp list || true
