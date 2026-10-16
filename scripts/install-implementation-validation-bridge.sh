#!/usr/bin/env bash
# Install the narrow, read-only implementation-validation bridge.
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer as root (for example: sudo $0)." >&2
  exit 1
fi

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE="$REPO_DIR/templates/implementation_validation_bridge_server.py"
BASE=/usr/local/lib/implementation-validation-bridge-mcp
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STAGE="${BASE}.new.${STAMP}"
BACKUP="${BASE}.backup.${STAMP}"
PYTHON=/usr/bin/python3.12

[[ -f "$SOURCE" ]] || { echo "Missing bridge template: $SOURCE" >&2; exit 1; }
[[ -x "$PYTHON" ]] || { echo "Missing required interpreter: $PYTHON" >&2; exit 1; }
[[ ! -e "$STAGE" ]] || { echo "Refusing to overwrite staging path: $STAGE" >&2; exit 1; }

cleanup() {
  if [[ -d "$STAGE" ]]; then
    rm -rf -- "$STAGE"
  fi
}
trap cleanup EXIT

mkdir -p "$STAGE"
"$PYTHON" -m venv "$STAGE/.venv"
"$STAGE/.venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  "mcp==2.0.0" \
  "anyio==4.13.0" \
  "pydantic==2.13.4"
"$STAGE/.venv/bin/python" -m pip check

install -o root -g root -m 0644 "$SOURCE" "$STAGE/server.py"
"$STAGE/.venv/bin/python" -c '
import importlib.util
spec = importlib.util.spec_from_file_location("implementation_validation", "'"$STAGE"'/server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.build_argv("pytest_full") == [
    "/home/hdgr/.hermes/hermes-agent/venv/bin/python3", "-m", "pytest", "-q"
]
'

chown -R root:root "$STAGE"
if [[ -e "$BASE" ]]; then
  mv -- "$BASE" "$BACKUP"
fi
mv -- "$STAGE" "$BASE"
STAGE=""
echo "Installed implementation-validation bridge at $BASE"
if [[ -e "$BACKUP" ]]; then
  echo "Previous runtime preserved at $BACKUP"
fi
