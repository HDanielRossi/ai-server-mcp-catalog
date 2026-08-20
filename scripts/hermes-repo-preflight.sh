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

CONTRACT_FILE=".ai/hermes/repo-contract.md"

echo
echo "1) Hermes repository contract:"
if [[ ! -f "$CONTRACT_FILE" ]]; then
  echo "FAIL: missing Hermes repository contract: .ai/hermes/repo-contract.md"
  exit 1
fi

REQUIRED_SECTIONS=(
  "Repository identity"
  "Allowed paths"
  "Forbidden paths"
  "Test command"
  "Review requirements"
  "Commit policy"
  "Push policy"
  "Rollback procedure"
)

MISSING_COUNT=0
for section in "${REQUIRED_SECTIONS[@]}"; do
  if ! grep -Fqx "## $section" "$CONTRACT_FILE"; then
    echo "FAIL: contract missing required section: $section"
    MISSING_COUNT=$((MISSING_COUNT + 1))
  fi
done

if [[ $MISSING_COUNT -gt 0 ]]; then
  exit 1
fi

echo "OK: Hermes repository contract exists and contains all required sections"

echo
echo "2) Branch:"
git branch --show-current

echo
echo "3) Git status:"
STATUS="$(git status --short)"
if [[ -n "$STATUS" ]]; then
  echo "$STATUS"
  echo "FAIL: working tree is not clean"
  exit 1
fi
echo "OK: working tree clean"

echo
echo "4) Last commits:"
git log --oneline -5

echo
echo "5) Test command:"
echo "$TEST_COMMAND"
bash -lc "$TEST_COMMAND"

echo
echo "6) Hermes hardening audit:"
cd /opt/ai/projects/ai-server-mcp-catalog
./scripts/audit-hermes-pipeline-hardening.sh

echo
echo "PASS: repository is ready for a small Hermes pipeline task."
