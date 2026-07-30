#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="$HOME/.claude.json"
DEST_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"

[[ -f "$SOURCE" ]] || { echo "No existe $SOURCE" >&2; exit 1; }

mkdir -p "$DEST_DIR"
cp "$SOURCE" "$DEST_DIR/claude-${STAMP}.json"
echo "Respaldo creado en $DEST_DIR/claude-${STAMP}.json"
echo "Nota: backups/ está excluido de Git porque puede contener secretos."
