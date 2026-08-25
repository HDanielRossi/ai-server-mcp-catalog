#!/usr/bin/env bash
# A3.5 hardening invariant audit. Layer A: repository/template invariants. Layer B: installed-runtime invariants (read-only probes). Accumulates all failures.
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
cd "$REPO_DIR"

FAILURES=()

check_ok() {
  echo "OK: $1"
}

check_fail() {
  echo "FAIL: $1"
  FAILURES+=("$1")
}

grep_ok() {
  local file="$1"
  shift
  local needle
  for needle in "$@"; do
    if ! grep -qF -- "$needle" "$file" 2>/dev/null; then
      return 1
    fi
  done
  return 0
}

grep_absent() {
  local file="$1"
  local needle="$2"
  ! grep -qF -- "$needle" "$file" 2>/dev/null
}

require_file_nonempty() {
  local file="$1"
  if [[ -s "$REPO_DIR/$file" ]]; then
    check_ok "$file exists and is non-empty"
  else
    check_fail "$file missing or empty"
  fi
}

echo "===== A3.5 HARDENING AUDIT: repository/template + installed-runtime invariants ====="

echo
echo "1) template and test file existence:"
require_file_nonempty "templates/review_bridge_server.py"
require_file_nonempty "templates/reviewer-SOUL.md"
require_file_nonempty "templates/pipeline_bridge_server.py"
require_file_nonempty "templates/claude_bridge_server.py"
require_file_nonempty "templates/planner_bridge_server.py"
require_file_nonempty "scripts/planner-bridge"
require_file_nonempty "tests/test_review_bridge_template.py"
require_file_nonempty "tests/test_pipeline_bridge_template.py"
require_file_nonempty "tests/test_claude_bridge_template.py"
require_file_nonempty "tests/test_planner_bridge_template.py"
require_file_nonempty "tests/test_planner_bridge_wrapper.py"

echo
echo "2) docs A3.5 marker:"
DOCS_FILE="$REPO_DIR/docs/hermes-pipeline.md"
if [[ -s "$DOCS_FILE" ]] && grep -qF -- "A3.5" "$DOCS_FILE" 2>/dev/null; then
  check_ok "docs/hermes-pipeline.md exists and contains A3.5 marker"
else
  check_fail "docs/hermes-pipeline.md missing or missing A3.5 marker"
fi

echo
echo "3) review template (templates/review_bridge_server.py):"
REVIEW_FILE="$REPO_DIR/templates/review_bridge_server.py"
REVIEW_STRINGS=(
  'DEFAULT_TEST_COMMAND = "__skip__"'
  '__skip__'
  'MAX_CONTENT_WINDOW_LINES = 200'
  'DEFAULT_INCLUDE_DIFF = False'
  'DEFAULT_INCLUDE_REPO_EVIDENCE = True'
  'not-requested'
  'SKIPPED'
  '/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q'
  './scripts/audit-hermes-pipeline-hardening.sh'
  'ALLOWED_TEST_COMMANDS'
  'def collect_evidence'
  'include_diff requires'
  'ReviewBridgeError'
)
for s in "${REVIEW_STRINGS[@]}"; do
  if grep_ok "$REVIEW_FILE" "$s"; then
    check_ok "review template contains: $s"
  else
    check_fail "review template missing: $s"
  fi
done

echo
echo "4) reviewer template (templates/reviewer-SOUL.md):"
REVIEWER_FILE="$REPO_DIR/templates/reviewer-SOUL.md"
REVIEWER_STRINGS=(
  'If test_command is __skip__, do not invent or substitute another command'
  'If tests are required by the acceptance criteria but no valid explicit test command is available, block the task instead of guessing'
  '200 lines'
  'exactly one file'
  'No parallel collects'
  'about 8'
  'ONE deterministic correction'
)
for s in "${REVIEWER_STRINGS[@]}"; do
  if grep_ok "$REVIEWER_FILE" "$s"; then
    check_ok "reviewer template contains: $s"
  else
    check_fail "reviewer template missing: $s"
  fi
done

echo
echo "5) pipeline template (templates/pipeline_bridge_server.py):"
PIPELINE_FILE="$REPO_DIR/templates/pipeline_bridge_server.py"
PIPELINE_STRINGS=(
  'def stable_key'
  '"implementation"'
  'review:'
  'correction:'
  'implementation_task_id'
  'review_task_id'
  'If test_command is __skip__, do not invent or substitute another command'
  'If tests are required by the acceptance criteria but no valid explicit test command is available,'
  'block the task instead of guessing'
  'PipelineBridgeError'
)
for s in "${PIPELINE_STRINGS[@]}"; do
  if grep_ok "$PIPELINE_FILE" "$s"; then
    check_ok "pipeline template contains: $s"
  else
    check_fail "pipeline template missing: $s"
  fi
done

echo
echo "6) claude template (templates/claude_bridge_server.py):"
CLAUDE_FILE="$REPO_DIR/templates/claude_bridge_server.py"
CLAUDE_STRINGS=(
  'CALL_BUDGET_THRESHOLD = 4'
  'PROJECTS_ROOT = "/opt/ai/projects"'
  '--print'
  '--output-format'
  'json'
  '--no-session-persistence'
  '--max-budget-usd'
  'duration_ms'
  'duration_api_ms'
  'num_turns'
  'total_cost_usd'
  'session_id'
  'subtype'
  'is_error'
  'iterations'
  'modelUsage'
  'input_tokens'
  'cache_creation_input_tokens'
  'cache_read_input_tokens'
  'output_tokens'
  'fcntl'
  'flock'
  'os.fsync'
  'os.replace'
  'LedgerCorruptionError'
  'BudgetExhaustedError'
  'task_id'
  'realpath'
)
for s in "${CLAUDE_STRINGS[@]}"; do
  if grep_ok "$CLAUDE_FILE" "$s"; then
    check_ok "claude template contains: $s"
  else
    check_fail "claude template missing: $s"
  fi
done

CLAUDE_ABSENT_STRINGS=(
  'app.py'
  'tests/test_app.py'
  'shell=True'
)
for s in "${CLAUDE_ABSENT_STRINGS[@]}"; do
  if grep_absent "$CLAUDE_FILE" "$s"; then
    check_ok "claude template does not contain: $s"
  else
    check_fail "claude template must not contain: $s"
  fi
done

echo
echo "6a) planner-bridge MCP server template (templates/planner_bridge_server.py) — A3.5.0 bootstrap:"
PLANNER_SERVER_FILE="$REPO_DIR/templates/planner_bridge_server.py"
PLANNER_SERVER_STRINGS=(
  'from mcp.server import MCPServer'
  'MCPServer("planner-bridge")'
  '@mcp.tool()'
  'def run(workdir: str, prompt: str, context_files'
  'context_files: list[str] | None = None'
  'MAX_CONTEXT_FILES'
  '--context-file'
  'if __name__ == "__main__":'
  'mcp.run()'
  'WRAPPER_PATH = "/usr/local/bin/planner-bridge"'
)
for s in "${PLANNER_SERVER_STRINGS[@]}"; do
  if grep_ok "$PLANNER_SERVER_FILE" "$s"; then
    check_ok "planner-bridge server template contains: $s"
  else
    check_fail "planner-bridge server template missing: $s"
  fi
done

PLANNER_SERVER_ABSENT_STRINGS=(
  'shell=True'
)
for s in "${PLANNER_SERVER_ABSENT_STRINGS[@]}"; do
  if grep_absent "$PLANNER_SERVER_FILE" "$s"; then
    check_ok "planner-bridge server template does not contain: $s"
  else
    check_fail "planner-bridge server template must not contain: $s"
  fi
done

echo
echo "6b) planner-bridge wrapper (scripts/planner-bridge) — A3.5.0 explicit-context bootstrap:"
PLANNER_WRAPPER_FILE="$REPO_DIR/scripts/planner-bridge"
PLANNER_WRAPPER_STRINGS=(
  'set -Eeuo pipefail'
  'ALLOWED_ROOT="/opt/ai/projects"'
  '--context-file'
  'MAX_CONTEXT_FILES=12'
  'MAX_CONTEXT_FILE_BYTES=262144'
  'MAX_CONTEXT_TOTAL_BYTES=524288'
  'must be relative'
  'escapes workdir'
  'does not exist'
  'realpath -e'
  '===== EXPLICIT_CONTEXT_BEGIN ====='
  '===== EXPLICIT_CONTEXT_END ====='
  'path=$cf'
  'bytes=$cf_bytes'
  'sha256=$cf_sha'
  'sha256sum'
  'clarify,context_engine,memory'
  'PLANNER_CODEX_PYTHON='
  'sys.argv = ["hermes", "-p", "planner-codex"]'
  'prompt = sys.stdin.read()'
  'from hermes_cli.main import _run_and_exit_oneshot'
  'printf '\''%s'\'' "$PROMPT" | "$PLANNER_CODEX_PYTHON"'
)
for s in "${PLANNER_WRAPPER_STRINGS[@]}"; do
  if grep_ok "$PLANNER_WRAPPER_FILE" "$s"; then
    check_ok "planner-bridge wrapper contains: $s"
  else
    check_fail "planner-bridge wrapper missing: $s"
  fi
done

PLANNER_WRAPPER_ABSENT_STRINGS=(
  '-z "$PROMPT"'
  '--oneshot "$PROMPT"'
  'exec planner-codex'
)

for s in "${PLANNER_WRAPPER_ABSENT_STRINGS[@]}"; do
  if grep_absent "$PLANNER_WRAPPER_FILE" "$s"; then
    check_ok "planner-bridge wrapper does not contain legacy giant-argv transport: $s"
  else
    check_fail "planner-bridge wrapper must not contain legacy giant-argv transport: $s"
  fi
done

if [[ -x "$PLANNER_WRAPPER_FILE" ]]; then
  check_ok "scripts/planner-bridge is executable"
else
  check_fail "scripts/planner-bridge is not executable (expected mode 100755)"
fi

echo
echo "7) docs (docs/hermes-pipeline.md):"
DOCS_STRINGS=(
  'reviewer'
  'A3.5'
  'threshold'
  '__skip__'
  'not-requested'
  'SKIPPED'
  '200'
  '/opt/ai/projects'
  'shell=False'
  'no token cap'
  '/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q'
  'bash scripts/audit-hermes-pipeline-hardening.sh'
  'operator'
)
for s in "${DOCS_STRINGS[@]}"; do
  if grep_ok "$DOCS_FILE" "$s"; then
    check_ok "docs contains: $s"
  else
    check_fail "docs missing: $s"
  fi
done

echo
echo "7a) docs A3.5.0 planner explicit-context bootstrap markers:"
DOCS_A350_STRINGS=(
  'A3.5.0'
  'context_files'
  '--context-file'
  'MAX_CONTEXT_FILES'
  'MAX_CONTEXT_FILE_BYTES'
  'MAX_CONTEXT_TOTAL_BYTES'
  'EXPLICIT_CONTEXT_BEGIN'
  'EXPLICIT_CONTEXT_END'
)
for s in "${DOCS_A350_STRINGS[@]}"; do
  if grep_ok "$DOCS_FILE" "$s"; then
    check_ok "docs contains: $s"
  else
    check_fail "docs missing: $s"
  fi
done

if grep -qiF -- "repo-only" "$DOCS_FILE" 2>/dev/null; then
  check_ok "docs contains: REPO-ONLY (case-insensitive)"
else
  check_fail "docs missing: REPO-ONLY (case-insensitive)"
fi

echo
echo "===== SECTION B: installed runtime invariants (read-only probes) ====="

RUNTIME_FILES=(
  "/usr/local/lib/pipeline-bridge-mcp/server.py"
  "/usr/local/lib/review-bridge-mcp/server.py"
  "$HOME/.hermes/config.yaml"
  "$HOME/.hermes/SOUL.md"
  "$HOME/.hermes/profiles/reviewer/config.yaml"
  "$HOME/.hermes/profiles/reviewer/SOUL.md"
)
RUNTIME_PRESENT=0
for f in "${RUNTIME_FILES[@]}"; do
  if [[ -f "$f" ]]; then
    RUNTIME_PRESENT=1
    break
  fi
done

if [[ ${RUNTIME_PRESENT} -eq 0 ]]; then
  echo "SKIPPED: Hermes runtime not detected on this host (no installed bridge server.py, no ~/.hermes runtime files). Section B not executed."
else
  echo "Runtime present — executing installed-runtime invariant checks (read-only)."

  echo
  echo "8) installed pipeline_bridge review idempotency:"
  if grep -qF -- 'review:{implementation_task_id}' /usr/local/lib/pipeline-bridge-mcp/server.py 2>/dev/null; then
    check_ok "installed pipeline_bridge review idempotency includes implementation_task_id"
  else
    check_fail "installed pipeline_bridge missing review idempotency fix (review:{implementation_task_id})"
  fi

  echo
  echo "9) global/default config must NOT expose review_bridge:"
  if grep -qF -- 'review_bridge' "$HOME/.hermes/config.yaml" 2>/dev/null; then
    check_fail "global/default config exposes review_bridge"
  else
    check_ok "global/default config does not expose review_bridge"
  fi

  echo
  echo "10) reviewer profile must expose review_bridge:"
  if grep -qF -- 'review_bridge' "$HOME/.hermes/profiles/reviewer/config.yaml" 2>/dev/null; then
    check_ok "reviewer profile exposes review_bridge"
  else
    check_fail "reviewer profile does not expose review_bridge"
  fi

  echo
  echo "11) default SOUL async kanban boundary:"
  if grep -qE -- 'Async Kanban Boundary|After creating an implementation task' "$HOME/.hermes/SOUL.md" 2>/dev/null; then
    check_ok "default SOUL contains async kanban boundary"
  else
    check_fail "default SOUL missing async kanban boundary"
  fi

  echo
  echo "12) reviewer SOUL mandatory evidence requirement:"
  if grep -qF -- 'Mandatory Evidence' "$HOME/.hermes/profiles/reviewer/SOUL.md" 2>/dev/null || grep -qF -- 'mcp__review_bridge__collect' "$HOME/.hermes/profiles/reviewer/SOUL.md" 2>/dev/null; then
    check_ok "reviewer SOUL contains mandatory evidence / mcp__review_bridge__collect requirement"
  else
    check_fail "reviewer SOUL missing mandatory evidence requirement"
  fi

  echo
  echo "13) reviewer must NOT expose Memory:"
  REVIEWER_TOOLS_OUT=""
  REVIEWER_TOOLS_RC=0
  REVIEWER_TOOLS_OUT="$(
    timeout -k 5 20 script -qefc 'reviewer tools --summary' /dev/null </dev/null 2>&1
  )" || REVIEWER_TOOLS_RC=$?
  if [[ ${REVIEWER_TOOLS_RC} -eq 124 || ${REVIEWER_TOOLS_RC} -eq 137 ]]; then
    check_fail "reviewer tools --summary timed out (exit ${REVIEWER_TOOLS_RC})"
  elif [[ ${REVIEWER_TOOLS_RC} -ne 0 ]]; then
    check_fail "reviewer tools --summary exited nonzero (exit ${REVIEWER_TOOLS_RC})"
  elif [[ -z "${REVIEWER_TOOLS_OUT//[[:space:]]/}" ]]; then
    check_fail "reviewer tools --summary produced no output (probe failed)"
  elif printf '%s\n' "$REVIEWER_TOOLS_OUT" | grep -q -- 'Memory'; then
    check_fail "reviewer exposes Memory"
  else
    check_ok "reviewer does not expose Memory"
  fi

  echo
  echo "14) global/default must expose review_archive_bridge:"
  if grep -qF -- 'review_archive_bridge:' "$HOME/.hermes/config.yaml" 2>/dev/null; then
    check_ok "global/default exposes review_archive_bridge"
  else
    check_fail "global/default missing review_archive_bridge"
  fi

  echo
  echo "15) reviewer must NOT expose review_archive_bridge:"
  if grep -qF -- 'review_archive_bridge:' "$HOME/.hermes/profiles/reviewer/config.yaml" 2>/dev/null; then
    check_fail "reviewer exposes review_archive_bridge"
  else
    check_ok "reviewer does not expose review_archive_bridge"
  fi

  echo
  echo "16) installed review_bridge ALLOWED_TEST_COMMANDS authorizes the audit test command:"
  python3 - <<'PY'
import ast, sys

PATH = "/usr/local/lib/review-bridge-mcp/server.py"
EXPECTED = "./scripts/audit-hermes-pipeline-hardening.sh"

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError):
    sys.exit(2)

def string_literals(node):
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            out.extend(string_literals(elt))
    elif isinstance(node, ast.Dict):
        for key in node.keys:
            if key is not None:
                out.extend(string_literals(key))
        for val in node.values:
            out.extend(string_literals(val))
    return out

def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value

found = False
for node in ast.walk(tree):
    for target, value in assignment_pairs(node):
        if isinstance(target, ast.Name) and target.id == "ALLOWED_TEST_COMMANDS":
            found = True
            if EXPECTED in string_literals(value):
                sys.exit(0)
if not found:
    sys.exit(2)
sys.exit(1)
PY
  ALLOWED_CHECK_RC=$?
  if [[ ${ALLOWED_CHECK_RC} -eq 0 ]]; then
    check_ok "installed review_bridge authorizes the Hermes pipeline hardening audit test command"
  elif [[ ${ALLOWED_CHECK_RC} -eq 2 ]]; then
    check_fail "cannot read/parse installed review_bridge server.py or ALLOWED_TEST_COMMANDS not found"
  else
    check_fail "installed review_bridge does not authorize the Hermes pipeline hardening audit test command"
  fi

  echo
  echo "17) pipeline repo status/cleanliness:"
  for repo in /opt/ai/projects/agent-pipeline-test /opt/ai/projects/ai-server-mcp-catalog; do
    if [[ -d "$repo/.git" || -f "$repo/.git" ]]; then
      echo "--- git status --short ($repo) ---"
      git -C "$repo" status --short
      check_ok "repository status reported for $repo"
    else
      check_fail "pipeline repository missing: $repo"
    fi
  done
fi

echo
if [[ ${#FAILURES[@]} -eq 0 ]]; then
  echo "PASS"
  exit 0
fi

echo "FAIL"
echo "Failed checks:"
i=0
for f in "${FAILURES[@]}"; do
  i=$((i + 1))
  echo "  $i) $f"
done
echo "Total fails: ${#FAILURES[@]}"
exit 1
