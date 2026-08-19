#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${1:-}"
TEST_COMMAND="${2:-}"

if [[ -z "$REPO" || -z "$TEST_COMMAND" ]]; then
  echo "Usage:"
  echo "  $0 /opt/ai/projects/<repo> '<test command>'"
  exit 2
fi

REAL_REPO="$(realpath "$REPO")"

case "$REAL_REPO" in
  /opt/ai/projects/*) ;;
  *)
    echo "FAIL: repo must be inside /opt/ai/projects"
    echo "repo=$REAL_REPO"
    exit 1
    ;;
esac

if [[ ! -d "$REAL_REPO/.git" ]]; then
  echo "FAIL: not a Git repository: $REAL_REPO"
  exit 1
fi

cd "$REAL_REPO"

echo "===== HERMES REPO PREFLIGHT ====="
echo "repo=$REAL_REPO"

echo
echo "1) Branch:"
git branch --show-current

echo
echo "2) Git status:"
STATUS="$(git status --short)"
if [[ -n "$STATUS" ]]; then
  echo "$STATUS"
  echo "FAIL: working tree is not clean"
  exit 1
fi
echo "OK: working tree clean"

echo
echo "3) Last commits:"
git log --oneline -5

echo
echo "4) Test command:"
echo "$TEST_COMMAND"
bash -lc "$TEST_COMMAND"

echo
echo "5) Hermes hardening audit:"
cd /opt/ai/projects/ai-server-mcp-catalog
./scripts/audit-hermes-pipeline-hardening.sh

echo
echo "PASS: repository is ready for a small Hermes pipeline task."
