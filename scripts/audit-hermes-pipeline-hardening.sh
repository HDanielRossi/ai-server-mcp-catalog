#!/usr/bin/env bash
set -Eeuo pipefail

echo "===== PIPELINE HARDENING AUDIT ====="

echo
echo "1) pipeline_bridge review idempotency:"
if grep -n 'review:{implementation_task_id}' /usr/local/lib/pipeline-bridge-mcp/server.py; then
  echo "OK: review idempotency includes implementation_task_id"
else
  echo "FAIL: missing review idempotency fix"
  exit 1
fi

echo
echo "2) global/default config should NOT expose review_bridge:"
if grep -nA8 -B4 'review_bridge' ~/.hermes/config.yaml; then
  echo "FAIL: global/default config exposes review_bridge"
  exit 1
else
  echo "OK: no global review_bridge found"
fi

echo
echo "3) reviewer profile SHOULD expose review_bridge:"
if grep -nA8 -B4 'review_bridge' ~/.hermes/profiles/reviewer/config.yaml; then
  echo "OK: reviewer has review_bridge"
else
  echo "FAIL: reviewer review_bridge missing"
  exit 1
fi

echo
echo "4) default SOUL async boundary:"
if grep -nA12 -B3 'Async Kanban Boundary\|After creating an implementation task' ~/.hermes/SOUL.md; then
  echo "OK: default async boundary found"
else
  echo "FAIL: default async boundary missing"
  exit 1
fi

echo
echo "5) reviewer SOUL mandatory evidence:"
if grep -nA12 -B3 'Mandatory Evidence Before Verdict\|mcp__review_bridge__collect' ~/.hermes/profiles/reviewer/SOUL.md; then
  echo "OK: reviewer mandatory evidence rule found"
else
  echo "FAIL: reviewer mandatory evidence rule missing"
  exit 1
fi

echo
echo "5b) reviewer should NOT expose Memory:"
if reviewer tools --summary | grep -q 'Memory'; then
  echo "FAIL: reviewer exposes Memory"
  reviewer tools --summary | sed -n '1,120p'
  exit 1
else
  echo "OK: reviewer Memory tool not exposed"
fi

echo
echo "5c) global/default SHOULD expose review_archive_bridge:"
if grep -nA8 -B2 'review_archive_bridge:' ~/.hermes/config.yaml; then
  echo "OK: global/default has review_archive_bridge"
else
  echo "FAIL: global/default missing review_archive_bridge"
  exit 1
fi

echo
echo "5d) reviewer should NOT expose review_archive_bridge:"
if grep -n 'review_archive_bridge:' ~/.hermes/profiles/reviewer/config.yaml; then
  echo "FAIL: reviewer exposes review_archive_bridge"
  exit 1
else
  echo "OK: reviewer does not expose review_archive_bridge"
fi

echo
echo "6) repo cleanliness:"
for repo in /opt/ai/projects/agent-pipeline-test /opt/ai/projects/ai-server-mcp-catalog; do
  echo
  echo "===== $repo ====="
  cd "$repo"
  git status --short
done

echo
echo "PASS: Hermes pipeline hardening audit completed successfully."
