#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Error: falta el comando '$command_name'." >&2
    exit 1
  fi
}

confirm_replace_mcp() {
  local name="$1"
  if claude mcp get "$name" >/dev/null 2>&1; then
    echo "El MCP '$name' ya existe. Se reemplazará."
    claude mcp remove "$name" --scope user >/dev/null 2>&1 || true
  fi
}
