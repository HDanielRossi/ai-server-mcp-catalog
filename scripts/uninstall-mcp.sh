#!/usr/bin/env bash
set -Eeuo pipefail

NAME="${1:-}"
if [[ -z "$NAME" ]]; then
  echo "Uso: $0 <nombre-mcp>" >&2
  exit 1
fi

claude mcp remove "$NAME" --scope user
