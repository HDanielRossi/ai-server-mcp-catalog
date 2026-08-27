#!/usr/bin/env bash
# A3.5 hardening invariant audit, split into explicit modes.
# --repo-only : repository/template invariants only (no live runtime dependence).
# --runtime   : installed/live runtime invariants only (read-only probes).
# --all       : both suites, clearly labeled, fails if either suite fails.
# Bare invocation (no flags) is an alias for --repo-only.
#
# Deliberately does NOT use `set -e`: several checks below intentionally
# inspect the nonzero exit status of a probe command (e.g. the
# ALLOWED_TEST_COMMANDS python3 probe, the reviewer CLI probe) instead of
# aborting on it, so the audit can accumulate and report every failing check
# in one run rather than stopping at the first one.
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"
cd "$REPO_DIR"

usage() {
  cat <<'EOF'
Usage: audit-hermes-pipeline-hardening.sh [--repo-only|--runtime|--all|-h|--help]

Modes (exactly one may be given):
  --repo-only   Repository/template hardening invariants only. Does not read
                or require any installed/live runtime; never fails merely
                because live runtime state differs from or is missing
                relative to the repository templates. This is the default
                when no mode flag is given (bare invocation == --repo-only).
  --runtime     Installed/live runtime hardening invariants only (read-only
                probes). Fails if the live runtime is missing or
                non-compliant.
  --all         Runs --repo-only then --runtime, with clearly labeled
                sections for both. Fails if either suite fails.

  -h, --help    Print this usage text and exit 0.

Environment:
  AUDIT_RUNTIME_ROOT   Overrides the root directory used to locate live
                        runtime files in --runtime/--all mode. Unset means
                        the real documented live paths
                        (/usr/local/lib/pipeline-bridge-mcp/server.py,
                        /usr/local/lib/review-bridge-mcp/server.py,
                        $HOME/.hermes/...). Intended for hermetic test
                        fixtures. When set, the layout under the override
                        root mirrors:
                          usr/local/lib/pipeline-bridge-mcp/server.py
                          usr/local/lib/review-bridge-mcp/server.py
                          home/.hermes/config.yaml
                          home/.hermes/SOUL.md
                          home/.hermes/profiles/reviewer/config.yaml
                          home/.hermes/profiles/reviewer/SOUL.md

Exit codes: 0 = pass, 1 = genuine audit failure, 2 = usage error.
EOF
}

MODE=""
MODE_COUNT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --repo-only|--runtime|--all)
      MODE_COUNT=$((MODE_COUNT + 1))
      MODE="${1#--}"
      shift
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ $MODE_COUNT -gt 1 ]]; then
  echo "Error: only one mode flag may be given (--repo-only, --runtime, --all)" >&2
  usage >&2
  exit 2
fi

if [[ -z "$MODE" ]]; then
  MODE="repo-only"
fi

REPO_FAILURES=()
RUNTIME_FAILURES=()
ACTIVE_FAILURES_ARR="REPO_FAILURES"

check_ok() {
  echo "OK: $1"
}

check_fail() {
  local msg="$1"
  echo "FAIL: $msg"
  local -n arr_ref="$ACTIVE_FAILURES_ARR"
  arr_ref+=("$msg")
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

# Resolves a live-runtime path under AUDIT_RUNTIME_ROOT (if set), falling
# back to the real documented live path otherwise. See usage() for the
# fixture layout mirrored under the override root.
runtime_path() {
  local kind="$1"
  local root="${AUDIT_RUNTIME_ROOT:-}"
  if [[ -n "$root" ]]; then
    case "$kind" in
      pipeline_bridge_server) echo "$root/usr/local/lib/pipeline-bridge-mcp/server.py" ;;
      review_bridge_server)   echo "$root/usr/local/lib/review-bridge-mcp/server.py" ;;
      hermes_config)          echo "$root/home/.hermes/config.yaml" ;;
      hermes_soul)            echo "$root/home/.hermes/SOUL.md" ;;
      reviewer_config)        echo "$root/home/.hermes/profiles/reviewer/config.yaml" ;;
      reviewer_soul)          echo "$root/home/.hermes/profiles/reviewer/SOUL.md" ;;
    esac
  else
    case "$kind" in
      pipeline_bridge_server) echo "/usr/local/lib/pipeline-bridge-mcp/server.py" ;;
      review_bridge_server)   echo "/usr/local/lib/review-bridge-mcp/server.py" ;;
      hermes_config)          echo "$HOME/.hermes/config.yaml" ;;
      hermes_soul)            echo "$HOME/.hermes/SOUL.md" ;;
      reviewer_config)        echo "$HOME/.hermes/profiles/reviewer/config.yaml" ;;
      reviewer_soul)          echo "$HOME/.hermes/profiles/reviewer/SOUL.md" ;;
    esac
  fi
}

require_runtime_file_compliant() {
  local label="$1"
  local file="$2"
  shift 2
  if [[ ! -s "$file" ]]; then
    check_fail "$label missing or empty at $file"
    return
  fi
  if grep_ok "$file" "$@"; then
    check_ok "$label ($file) is compliant"
  else
    check_fail "$label ($file) is missing required hardening marker(s)"
  fi
}

run_repo_only() {
  ACTIVE_FAILURES_ARR="REPO_FAILURES"

  echo "===== SECTION: REPO-ONLY (repository/template invariants) ====="

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
  echo "7b) docs document explicit audit modes (A3.5.1d):"
  DOCS_MODES_STRINGS=(
    '--repo-only'
    '--runtime'
    '--all'
    'AUDIT_RUNTIME_ROOT'
  )
  for s in "${DOCS_MODES_STRINGS[@]}"; do
    if grep_ok "$DOCS_FILE" "$s"; then
      check_ok "docs contains: $s"
    else
      check_fail "docs missing: $s"
    fi
  done
}

run_runtime() {
  ACTIVE_FAILURES_ARR="RUNTIME_FAILURES"
  local override_root="${AUDIT_RUNTIME_ROOT:-}"

  echo "===== SECTION: RUNTIME (installed/live runtime invariants) ====="
  echo "Runtime root: ${override_root:-<real live paths>}"

  echo
  echo "8) installed pipeline_bridge review idempotency:"
  local pipeline_server
  pipeline_server="$(runtime_path pipeline_bridge_server)"
  require_runtime_file_compliant "installed pipeline_bridge" "$pipeline_server" 'review:{implementation_task_id}'

  echo
  echo "9) global/default config must NOT expose review_bridge:"
  local hermes_config
  hermes_config="$(runtime_path hermes_config)"
  if [[ ! -s "$hermes_config" ]]; then
    check_fail "global/default config missing or empty at $hermes_config"
  elif grep -qF -- 'review_bridge' "$hermes_config" 2>/dev/null; then
    check_fail "global/default config exposes review_bridge"
  else
    check_ok "global/default config does not expose review_bridge"
  fi

  echo
  echo "10) reviewer profile must expose review_bridge:"
  local reviewer_config
  reviewer_config="$(runtime_path reviewer_config)"
  require_runtime_file_compliant "reviewer profile config" "$reviewer_config" 'review_bridge'

  echo
  echo "11) default SOUL async kanban boundary:"
  local hermes_soul
  hermes_soul="$(runtime_path hermes_soul)"
  if [[ ! -s "$hermes_soul" ]]; then
    check_fail "default SOUL missing or empty at $hermes_soul"
  elif grep -qE -- 'Async Kanban Boundary|After creating an implementation task' "$hermes_soul" 2>/dev/null; then
    check_ok "default SOUL contains async kanban boundary"
  else
    check_fail "default SOUL missing async kanban boundary"
  fi

  echo
  echo "12) reviewer SOUL mandatory evidence requirement:"
  local reviewer_soul
  reviewer_soul="$(runtime_path reviewer_soul)"
  if [[ ! -s "$reviewer_soul" ]]; then
    check_fail "reviewer SOUL missing or empty at $reviewer_soul"
  elif grep -qF -- 'Mandatory Evidence' "$reviewer_soul" 2>/dev/null || grep -qF -- 'mcp__review_bridge__collect' "$reviewer_soul" 2>/dev/null; then
    check_ok "reviewer SOUL contains mandatory evidence / mcp__review_bridge__collect requirement"
  else
    check_fail "reviewer SOUL missing mandatory evidence requirement"
  fi

  echo
  echo "13) reviewer must NOT expose Memory:"
  if [[ -n "$override_root" ]]; then
    echo "SKIPPED: 'reviewer tools --summary' is a live host CLI probe (not a file under AUDIT_RUNTIME_ROOT); not applicable to a fixture root."
  else
    local reviewer_tools_out=""
    local reviewer_tools_rc=0
    reviewer_tools_out="$(
      timeout -k 5 20 script -qefc 'reviewer tools --summary' /dev/null </dev/null 2>&1
    )" || reviewer_tools_rc=$?
    if [[ ${reviewer_tools_rc} -eq 124 || ${reviewer_tools_rc} -eq 137 ]]; then
      check_fail "reviewer tools --summary timed out (exit ${reviewer_tools_rc})"
    elif [[ ${reviewer_tools_rc} -ne 0 ]]; then
      check_fail "reviewer tools --summary exited nonzero (exit ${reviewer_tools_rc})"
    elif [[ -z "${reviewer_tools_out//[[:space:]]/}" ]]; then
      check_fail "reviewer tools --summary produced no output (probe failed)"
    elif printf '%s\n' "$reviewer_tools_out" | grep -q -- 'Memory'; then
      check_fail "reviewer exposes Memory"
    else
      check_ok "reviewer does not expose Memory"
    fi
  fi

  echo
  echo "14) global/default must expose review_archive_bridge:"
  if [[ ! -s "$hermes_config" ]]; then
    check_fail "global/default config missing or empty at $hermes_config"
  elif grep -qF -- 'review_archive_bridge:' "$hermes_config" 2>/dev/null; then
    check_ok "global/default exposes review_archive_bridge"
  else
    check_fail "global/default missing review_archive_bridge"
  fi

  echo
  echo "15) reviewer must NOT expose review_archive_bridge:"
  if [[ ! -s "$reviewer_config" ]]; then
    check_fail "reviewer profile config missing or empty at $reviewer_config"
  elif grep -qF -- 'review_archive_bridge:' "$reviewer_config" 2>/dev/null; then
    check_fail "reviewer exposes review_archive_bridge"
  else
    check_ok "reviewer does not expose review_archive_bridge"
  fi

  echo
  echo "16) installed review_bridge ALLOWED_TEST_COMMANDS authorizes the audit test command:"
  local review_server
  review_server="$(runtime_path review_bridge_server)"
  local allowed_check_rc=0
  AUDIT_REVIEW_SERVER_PATH="$review_server" python3 - <<'PY' || allowed_check_rc=$?
import ast
import os
import sys

PATH = os.environ["AUDIT_REVIEW_SERVER_PATH"]
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
  if [[ ${allowed_check_rc} -eq 0 ]]; then
    check_ok "installed review_bridge authorizes the Hermes pipeline hardening audit test command"
  elif [[ ${allowed_check_rc} -eq 2 ]]; then
    check_fail "cannot read/parse installed review_bridge server.py or ALLOWED_TEST_COMMANDS not found at $review_server"
  else
    check_fail "installed review_bridge does not authorize the Hermes pipeline hardening audit test command"
  fi

  echo
  echo "17) pipeline repo status/cleanliness:"
  if [[ -n "$override_root" ]]; then
    echo "SKIPPED: pipeline repository reporting inspects fixed sibling project directories on the real host, not files under AUDIT_RUNTIME_ROOT; not applicable to a fixture root."
  else
    local repo
    for repo in /opt/ai/projects/agent-pipeline-test /opt/ai/projects/ai-server-mcp-catalog; do
      if [[ -d "$repo/.git" || -f "$repo/.git" ]]; then
        echo "--- git status --short ($repo) ---"
        git -C "$repo" status --short || true
        check_ok "repository status reported for $repo"
      else
        check_fail "pipeline repository missing: $repo"
      fi
    done
  fi
}

report_suite() {
  local -n failures_ref="$1"
  local label="$2"
  echo
  if [[ ${#failures_ref[@]} -eq 0 ]]; then
    echo "PASS ($label)"
    return 0
  fi
  echo "FAIL ($label)"
  echo "Failed checks ($label):"
  local i=0
  local f
  for f in "${failures_ref[@]}"; do
    i=$((i + 1))
    echo "  $i) $f"
  done
  echo "Total fails ($label): ${#failures_ref[@]}"
  return 1
}

case "$MODE" in
  repo-only)
    echo "===== A3.5.1d HARDENING AUDIT: mode=repo-only ====="
    run_repo_only
    if report_suite REPO_FAILURES "repo-only"; then
      echo
      echo "PASS"
      exit 0
    fi
    echo
    echo "FAIL"
    exit 1
    ;;
  runtime)
    echo "===== A3.5.1d HARDENING AUDIT: mode=runtime ====="
    run_runtime
    if report_suite RUNTIME_FAILURES "runtime"; then
      echo
      echo "PASS"
      exit 0
    fi
    echo
    echo "FAIL"
    exit 1
    ;;
  all)
    echo "===== A3.5.1d HARDENING AUDIT: mode=all ====="
    run_repo_only
    run_runtime
    REPO_RC=0
    RUNTIME_RC=0
    report_suite REPO_FAILURES "repo-only" || REPO_RC=1
    report_suite RUNTIME_FAILURES "runtime" || RUNTIME_RC=1
    echo
    if [[ ${REPO_RC} -eq 0 && ${RUNTIME_RC} -eq 0 ]]; then
      echo "PASS"
      exit 0
    fi
    echo "FAIL"
    exit 1
    ;;
esac
