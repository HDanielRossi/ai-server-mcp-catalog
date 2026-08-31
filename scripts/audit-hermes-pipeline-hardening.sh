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
                        /usr/local/lib/claude-bridge-mcp/server.py,
                        $HOME/.hermes/...). Intended for hermetic test
                        fixtures. When set, the layout under the override
                        root mirrors:
                          usr/local/lib/pipeline-bridge-mcp/server.py
                          usr/local/lib/review-bridge-mcp/server.py
                          usr/local/lib/claude-bridge-mcp/server.py
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

grep_line_exact() {
  local file="$1"
  local line="$2"
  grep -qxF -- "$line" "$file" 2>/dev/null
}

grep_line_exact_absent() {
  local file="$1"
  local line="$2"
  ! grep -qxF -- "$line" "$file" 2>/dev/null
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
      claude_bridge_server)   echo "$root/usr/local/lib/claude-bridge-mcp/server.py" ;;
      hermes_config)          echo "$root/home/.hermes/config.yaml" ;;
      hermes_soul)            echo "$root/home/.hermes/SOUL.md" ;;
      reviewer_config)        echo "$root/home/.hermes/profiles/reviewer/config.yaml" ;;
      reviewer_soul)          echo "$root/home/.hermes/profiles/reviewer/SOUL.md" ;;
    esac
  else
    case "$kind" in
      pipeline_bridge_server) echo "/usr/local/lib/pipeline-bridge-mcp/server.py" ;;
      review_bridge_server)   echo "/usr/local/lib/review-bridge-mcp/server.py" ;;
      claude_bridge_server)   echo "/usr/local/lib/claude-bridge-mcp/server.py" ;;
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

# --- A4.1: claude-bridge static contract audit (AST probe only; never
# imports/execs the bridge source) --------------------------------------
#
# Verifies, via ast.parse only, that a claude-bridge server.py:
#   - declares REQUIRED_CLAUDE_FLAGS as exactly the expected flag list
#     (claude_flags_mismatch)
#   - defines a _tool_run function requiring task_id with no default
#     (claude_task_id_missing / claude_task_id_optional) that explicitly
#     forwards a local variable (any plain Name, e.g. a stripped/validated
#     copy) to a task_id= keyword call (claude_task_id_forwarding_missing)
claude_static_contract_audit() {
  local label="$1"
  local file="$2"
  local findings
  findings="$(AUDIT_CLAUDE_BRIDGE_PATH="$file" python3 - <<'PY'
import ast
import os
import sys

PATH = os.environ["AUDIT_CLAUDE_BRIDGE_PATH"]

REQUIRED_FLAGS = [
    "--print",
    "--output-format",
    "json",
    "--no-session-persistence",
    "--permission-mode",
    "acceptEdits",
]

findings = []

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError) as e:
    print("claude_probe_error:cannot_parse_source:%s" % (e,))
    sys.exit(1)


def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value


flag_assignments = []
for node in ast.walk(tree):
    for target, value in assignment_pairs(node):
        if isinstance(target, ast.Name) and target.id == "REQUIRED_CLAUDE_FLAGS":
            flag_assignments.append(value)

if len(flag_assignments) != 1:
    findings.append(
        "claude_flags_mismatch:expected_exactly_one_REQUIRED_CLAUDE_FLAGS_assignment_found_%d;missing=%s;unexpected=none"
        % (len(flag_assignments), ",".join(REQUIRED_FLAGS))
    )
else:
    value = flag_assignments[0]
    actual = None
    if isinstance(value, ast.List) and all(
        isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in value.elts
    ):
        actual = [elt.value for elt in value.elts]
    if actual != REQUIRED_FLAGS:
        actual_list = actual if actual is not None else []
        missing = [flag for flag in REQUIRED_FLAGS if flag not in actual_list]
        unexpected = [flag for flag in actual_list if flag not in REQUIRED_FLAGS]
        findings.append(
            "claude_flags_mismatch:missing=%s;unexpected=%s"
            % (",".join(missing) or "none", ",".join(unexpected) or "none")
        )

tool_run_def = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_tool_run":
        tool_run_def = node
        break

if tool_run_def is None:
    findings.append("claude_probe_error:no_tool_run")
else:
    args = tool_run_def.args
    positional = list(args.posonlyargs) + list(args.args)
    kwonly = list(args.kwonlyargs)

    task_id_arg = None
    task_id_in_positional = False
    task_id_index = None
    for i, a in enumerate(positional):
        if a.arg == "task_id":
            task_id_arg = a
            task_id_in_positional = True
            task_id_index = i
            break
    if task_id_arg is None:
        for i, a in enumerate(kwonly):
            if a.arg == "task_id":
                task_id_arg = a
                task_id_index = i
                break

    if task_id_arg is None:
        findings.append("claude_task_id_missing")
    else:
        has_default = False
        if task_id_in_positional:
            first_default_index = len(positional) - len(args.defaults)
            if not (task_id_index < first_default_index):
                has_default = True
        else:
            if args.kw_defaults[task_id_index] is not None:
                has_default = True

        if has_default:
            findings.append("claude_task_id_optional")

        forwarding_found = False
        for node in ast.walk(tool_run_def):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "task_id"
                        and isinstance(kw.value, ast.Name)
                    ):
                        forwarding_found = True
        if not forwarding_found:
            findings.append("claude_task_id_forwarding_missing")

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies claude static contract (REQUIRED_CLAUDE_FLAGS / _tool_run task_id)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A4.1: reviewer collect-protocol invariant audit --------------------
#
# Whitespace-normalized containment check of each required protocol
# invariant's anchor phrase (taken verbatim from the current verified
# reviewer-SOUL.md "Reviewer collect protocol (A4.1)" section) against the
# given SOUL text. Missing invariants are reported as stable, greppable
# reviewer_soul_missing_invariant:<id> findings.
reviewer_soul_invariant_audit() {
  local label="$1"
  local file="$2"
  if [[ ! -s "$file" ]]; then
    check_fail "$label ($file) missing or empty; cannot verify reviewer collect protocol invariants"
    return
  fi
  local findings
  findings="$(AUDIT_REVIEWER_SOUL_PATH="$file" python3 - <<'PY'
import os
import re
import sys

PATH = os.environ["AUDIT_REVIEWER_SOUL_PATH"]

INVARIANTS = [
    ("collect_exact_signature", "collect(workdir, changed_path=None, test_command=None, content_window=None)"),
    ("changed_path_repo_relative", "supplies EXACTLY ONE repo-relative changed_path"),
    ("content_window_requires_changed_path", "content_window may ONLY be used together with a changed_path"),
    ("content_window_path_equals_changed_path", "content_window.path EXACTLY EQUALS changed_path"),
    ("integer_start_end_lines", "start_line and end_line are integers"),
    ("max_window_200_lines", "inclusive content window is <= 200 lines"),
    ("sequential_collects", "collect calls are SEQUENTIAL ONLY, NEVER parallel"),
    ("single_corrected_retry", "perform EXACTLY ONE deterministic corrected retry"),
    ("second_failure_block_stop", "IMMEDIATELY call kanban_block and STOP"),
    ("terminal_complete_or_block", "terminates with EXACTLY ONE of: kanban_complete OR kanban_block"),
    ("reviewer_read_only", "The reviewer remains READ-ONLY"),
    ("collect_sole_evidence_channel", "mcp__review_bridge__collect remains the SOLE evidence channel"),
    ("no_downstream_tasks", "The reviewer creates NO downstream tasks"),
]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
except OSError as e:
    print("reviewer_soul_probe_error:cannot_read_file:%s" % (e,))
    sys.exit(1)

haystack = normalize(source)

findings = []
for invariant_id, anchor in INVARIANTS:
    if normalize(anchor) not in haystack:
        findings.append("reviewer_soul_missing_invariant:%s" % (invariant_id,))

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies all reviewer collect protocol invariants (A4.1)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A5 B3: review_bridge repository-state/v1 hardening (AST probe only;
# never imports/execs the review_bridge source) -------------------------
#
# Verifies, via ast.parse only, that templates/review_bridge_server.py
# implements the deterministic hermes.repository-state/v1 envelope contract:
# schema literal, canonical workdir, HEAD capture, changed_paths union,
# staged/unstaged patch digests, untracked evidence, aggregate digest,
# double-capture stability check, conflict/submodule/special-entry
# rejection, and bounded shell=False git execution.
review_bridge_repository_state_audit() {
  local label="$1"
  local file="$2"
  local findings
  findings="$(AUDIT_REVIEW_BRIDGE_PATH="$file" python3 - <<'PY'
import ast
import os
import sys

PATH = os.environ["AUDIT_REVIEW_BRIDGE_PATH"]

findings = []

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError) as e:
    print("review_bridge_repo_state_probe_error:cannot_parse_source:%s" % (e,))
    sys.exit(1)


def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value


def subscript_key(node):
    s = node.slice
    if hasattr(ast, "Index") and isinstance(s, ast.Index):
        s = s.value
    return s


def find_function(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def has_name(node, name):
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def has_raise(node):
    return any(isinstance(n, ast.Raise) for n in ast.walk(node))


def list_literals(node):
    for n in ast.walk(node):
        if isinstance(n, ast.List):
            elts = []
            ok = True
            for elt in n.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    elts.append(elt.value)
                else:
                    ok = False
                    break
            if ok:
                yield elts


# 1) schema literal
schema_ok = False
for node in ast.walk(tree):
    for target, value in assignment_pairs(node):
        if (
            isinstance(target, ast.Name)
            and target.id == "REPOSITORY_STATE_SCHEMA"
            and isinstance(value, ast.Constant)
            and value.value == "hermes.repository-state/v1"
        ):
            schema_ok = True
if not schema_ok:
    findings.append("review_bridge_repo_state_schema_missing")

# 2) canonical workdir via os.path.realpath inside collect_repository_state
collect_fn = find_function("collect_repository_state")
canonical_ok = False
if collect_fn is not None:
    for node in ast.walk(collect_fn):
        for target, value in assignment_pairs(node):
            if (
                isinstance(target, ast.Name)
                and target.id == "canonical_workdir"
                and isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "realpath"
            ):
                canonical_ok = True
if collect_fn is None or not canonical_ok:
    findings.append("review_bridge_repo_state_canonical_workdir_missing")

# 3) HEAD capture
head_ok = any(elts == ["git", "rev-parse", "HEAD"] for elts in list_literals(tree))
if not head_ok:
    findings.append("review_bridge_repo_state_head_missing")

# 4) changed_paths union of staged/unstaged/untracked
changed_paths_ok = False
for node in ast.walk(tree):
    for target, value in assignment_pairs(node):
        if (
            isinstance(target, ast.Name)
            and target.id == "changed_paths"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "sorted"
        ):
            set_calls = sum(
                1
                for n in ast.walk(value)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "set"
            )
            bitor_ok = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr) for n in ast.walk(value))
            if set_calls >= 3 and bitor_ok:
                changed_paths_ok = True
if not changed_paths_ok:
    findings.append("review_bridge_repo_state_changed_paths_missing")

# 5) envelope dict required keys
envelope_keys = set()
for node in ast.walk(tree):
    for target, value in assignment_pairs(node):
        if isinstance(target, ast.Name) and target.id == "envelope" and isinstance(value, ast.Dict):
            for key in value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    envelope_keys.add(key.value)
for required_key in (
    "schema", "workdir", "head", "changed_paths",
    "staged_patch_sha256", "unstaged_patch_sha256", "untracked",
):
    if required_key not in envelope_keys:
        findings.append("review_bridge_repo_state_envelope_key_missing:%s" % required_key)

# 6) aggregate_sha256: envelope["aggregate_sha256"] = <call>
aggregate_ok = False
for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "envelope"
        ):
            key_node = subscript_key(target)
            if (
                isinstance(key_node, ast.Constant)
                and key_node.value == "aggregate_sha256"
                and isinstance(node.value, ast.Call)
            ):
                aggregate_ok = True
if not aggregate_ok:
    findings.append("review_bridge_repo_state_aggregate_sha256_missing")

# 7) double capture + stability check inside collect_repository_state
if collect_fn is None:
    findings.append("review_bridge_repo_state_double_capture_missing")
    findings.append("review_bridge_repo_state_stability_check_missing")
else:
    capture_calls = sum(
        1
        for n in ast.walk(collect_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_capture_repository_state_once"
    )
    if capture_calls < 2:
        findings.append("review_bridge_repo_state_double_capture_missing")

    stability_ok = False
    for node in ast.walk(collect_fn):
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "first"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.NotEq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Name)
                and test.comparators[0].id == "second"
                and has_raise(node)
            ):
                stability_ok = True
    if not stability_ok:
        findings.append("review_bridge_repo_state_stability_check_missing")

# 8) conflict + submodule rejection
conflict_fn = find_function("_reject_conflicts_and_submodules")
if conflict_fn is None:
    findings.append("review_bridge_repo_state_conflict_rejection_missing")
    findings.append("review_bridge_repo_state_submodule_rejection_missing")
else:
    if not (has_name(conflict_fn, "CONFLICT_STATUS_CODES") and has_raise(conflict_fn)):
        findings.append("review_bridge_repo_state_conflict_rejection_missing")
    submodule_ok = any(isinstance(n, ast.Constant) and n.value == "160000" for n in ast.walk(tree))
    if not submodule_ok:
        findings.append("review_bridge_repo_state_submodule_rejection_missing")

# 9) unsupported special-entry rejection
special_fn = find_function("_reject_untracked_special_entries")
if special_fn is None:
    findings.append("review_bridge_repo_state_special_entry_rejection_missing")
else:
    attrs = {n.attr for n in ast.walk(special_fn) if isinstance(n, ast.Attribute)}
    required_attrs = {"S_ISREG", "S_ISDIR", "S_ISLNK"}
    if not required_attrs.issubset(attrs) or not has_raise(special_fn):
        findings.append("review_bridge_repo_state_special_entry_rejection_missing")

# 10) bounded shell=False subprocess execution (module-wide)
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ):
        shell_kw = next((kw for kw in node.keywords if kw.arg == "shell"), None)
        timeout_kw = next((kw for kw in node.keywords if kw.arg == "timeout"), None)
        if shell_kw is None or not (isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is False):
            findings.append("review_bridge_repo_state_unbounded_or_shell_true_subprocess:line=%d" % node.lineno)
        if timeout_kw is None:
            findings.append("review_bridge_repo_state_unbounded_or_shell_true_subprocess:line=%d" % node.lineno)

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies repository-state/v1 hardening invariants (A5)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A5 B3: reviewer-SOUL.md repository-state fingerprint requirement ---
#
# Whitespace-normalized containment check of each required A5 invariant's
# anchor phrase (taken verbatim from the current verified reviewer-SOUL.md
# "Repository-state fingerprint requirement (A4.2)" section) against the
# given SOUL text. This is the requirement that the final authoritative
# collect/review records the exact repository-state envelope/digest that
# READY_TO_COMMIT later cross-checks.
reviewer_soul_a5_repository_state_invariant_audit() {
  local label="$1"
  local file="$2"
  if [[ ! -s "$file" ]]; then
    check_fail "$label ($file) missing or empty; cannot verify A5 repository-state fingerprint invariants"
    return
  fi
  local findings
  findings="$(AUDIT_REVIEWER_SOUL_A5_PATH="$file" python3 - <<'PY'
import os
import re
import sys

PATH = os.environ["AUDIT_REVIEWER_SOUL_A5_PATH"]

INVARIANTS = [
    ("final_collect_required", "one final, successful collect(workdir) call"),
    ("final_collect_after_every_other", "made after every other collect in the session"),
    ("fingerprint_purpose", "fingerprint the exact repository state the verdict is being issued against"),
    (
        "copy_repository_state_and_digest_verbatim",
        "must copy the repository_state object and the repository_state_sha256 string returned by "
        "that final call verbatim into the completion metadata",
    ),
    ("copy_byte_for_byte", "byte-for-byte, with no summarization, truncation, retyping, or reformatting"),
    ("block_on_missing_or_unstable", "the reviewer must BLOCK the task instead of completing it"),
    ("unstable_capture_blocks", "repository state was unstable across consecutive captures"),
    ("scope_paths_no_substitute", "scope_paths never substitutes for a fingerprint"),
]


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
except OSError as e:
    print("reviewer_soul_a5_probe_error:cannot_read_file:%s" % (e,))
    sys.exit(1)

haystack = normalize(source)

findings = []
for invariant_id, anchor in INVARIANTS:
    if normalize(anchor) not in haystack:
        findings.append("reviewer_soul_a5_missing_invariant:%s" % (invariant_id,))

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies A5 repository-state fingerprint invariants"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A5 B3: review_archive_bridge hermes.review-archive/v2 hardening
# (AST probe only; never imports/execs the archive helper source) -------
review_archive_bridge_v2_audit() {
  local label="$1"
  local file="$2"
  local findings
  findings="$(AUDIT_REVIEW_ARCHIVE_PATH="$file" python3 - <<'PY'
import ast
import os
import sys

PATH = os.environ["AUDIT_REVIEW_ARCHIVE_PATH"]

findings = []

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError) as e:
    print("review_archive_v2_probe_error:cannot_parse_source:%s" % (e,))
    sys.exit(1)


def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value


def subscript_key(node):
    s = node.slice
    if hasattr(ast, "Index") and isinstance(s, ast.Index):
        s = s.value
    return s


def find_function(name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def has_name(node, name):
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def has_raise(node):
    return any(isinstance(n, ast.Raise) for n in ast.walk(node))


def string_constants(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def has_assigned_constant(name, value):
    for node in ast.walk(tree):
        for target, val in assignment_pairs(node):
            if isinstance(target, ast.Name) and target.id == name and isinstance(val, ast.Constant) and val.value == value:
                return True
    return False


all_strings = string_constants(tree)

# 1) schema literals
if not has_assigned_constant("REVIEW_ARCHIVE_SCHEMA_V2", "hermes.review-archive/v2"):
    findings.append("review_archive_v2_schema_missing")
if not has_assigned_constant("REPOSITORY_STATE_SCHEMA", "hermes.repository-state/v1"):
    findings.append("review_archive_v2_repo_state_schema_missing")

# 2) authoritative table use
if not any("FROM tasks" in s for s in all_strings):
    findings.append("review_archive_v2_tasks_table_missing")
if not any("FROM task_runs" in s for s in all_strings):
    findings.append("review_archive_v2_task_runs_table_missing")
if not any("FROM task_events" in s for s in all_strings):
    findings.append("review_archive_v2_task_events_table_missing")

# 3) no guessed tables
if any("FROM runs" in s for s in all_strings):
    findings.append("review_archive_v2_guessed_runs_table_present")
if any("task_parents" in s for s in all_strings):
    findings.append("review_archive_v2_guessed_task_parents_table_present")

# 4) exact review/implementation identity
validate_run_fn = find_function("validate_run")
if validate_run_fn is None or not (
    any("implementation_task_id" in s for s in string_constants(validate_run_fn))
    and has_name(validate_run_fn, "parent")
    and has_raise(validate_run_fn)
):
    findings.append("review_archive_v2_implementation_identity_check_missing")

# 5) repository_state and aggregate digest validation
validate_state_fn = find_function("validate_repository_state")
if validate_state_fn is None or not (
    any("aggregate_sha256" in s for s in string_constants(validate_state_fn))
    and has_name(validate_state_fn, "sha256_canonical_excluding")
    and has_name(validate_state_fn, "REPOSITORY_STATE_SCHEMA")
    and has_raise(validate_state_fn)
):
    findings.append("review_archive_v2_repository_state_validation_missing")

# 6) archive_envelope_sha256
build_envelope_fn = find_function("build_v2_envelope")
archive_sha_ok = False
if build_envelope_fn is not None:
    for node in ast.walk(build_envelope_fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "envelope":
                key_node = subscript_key(target)
                if isinstance(key_node, ast.Constant) and key_node.value == "archive_envelope_sha256":
                    archive_sha_ok = True
if not archive_sha_ok:
    findings.append("review_archive_v2_archive_envelope_sha256_missing")

# 7) direct .ai/reviews artifact handling
ensure_dir_fn = find_function("ensure_reviews_dir")
if ensure_dir_fn is None or not (
    ".ai" in string_constants(ensure_dir_fn) and "reviews" in string_constants(ensure_dir_fn)
):
    findings.append("review_archive_v2_ai_reviews_path_missing")

write_artifact_fn = find_function("write_artifact")
escape_check_ok = False
for fn in (ensure_dir_fn, write_artifact_fn):
    if fn is None:
        continue
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq):
            attrs = [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]
            if "parent" in attrs:
                escape_check_ok = True
if not escape_check_ok:
    findings.append("review_archive_v2_reviews_dir_escape_check_missing")

# 8) symlink / nonregular fail-closed behavior
is_symlink_calls = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "is_symlink")
regular_checks = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("S_ISREG", "S_ISDIR")}
if is_symlink_calls < 2 or not {"S_ISREG", "S_ISDIR"}.issubset(regular_checks):
    findings.append("review_archive_v2_symlink_or_nonregular_check_missing")

# 9) read-only Kanban DB access
readonly_fn = find_function("open_kanban_db_readonly")
if readonly_fn is None or not any("mode=ro" in s for s in string_constants(readonly_fn)):
    findings.append("review_archive_v2_readonly_kanban_missing")

# 10) shell=False bounded subprocesses
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ):
        shell_kw = next((kw for kw in node.keywords if kw.arg == "shell"), None)
        timeout_kw = next((kw for kw in node.keywords if kw.arg == "timeout"), None)
        if shell_kw is None or not (isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is False):
            findings.append("review_archive_v2_shell_false_subprocess_missing:line=%d" % node.lineno)
        if timeout_kw is None:
            findings.append("review_archive_v2_shell_false_subprocess_missing:line=%d" % node.lineno)

# 11) no commit/push/staging mutations: this module's only git execution
# path is run_git(workdir, args) -> subprocess.run(["git", *args], ...), so
# the subcommand to inspect is args[0] at each run_git(...) call site (the
# literal ["git", ...] itself is never fully static because of the *args
# splat, so scanning call-site argument lists is the robust check here).
FORBIDDEN_GIT_SUBCOMMANDS = {
    "add", "commit", "push", "merge", "reset", "restore", "checkout", "clean", "rebase", "update-index",
}
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "run_git":
        if len(node.args) >= 2 and isinstance(node.args[1], ast.List) and node.args[1].elts:
            first = node.args[1].elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                sub = first.value
                if sub in FORBIDDEN_GIT_SUBCOMMANDS:
                    findings.append("review_archive_v2_git_mutation_command_present:%s" % sub)
                if sub == "hash-object" and any(
                    isinstance(e, ast.Constant) and e.value == "-w" for e in node.args[1].elts
                ):
                    findings.append("review_archive_v2_git_mutation_command_present:hash-object-w")

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies hermes.review-archive/v2 hardening invariants (A5)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A5 B3: hermes-pipeline-controller.py ready-to-commit hardening
# (AST probe only; never imports/execs the controller source) -----------
pipeline_controller_ready_to_commit_audit() {
  local label="$1"
  local file="$2"
  local findings
  findings="$(AUDIT_PIPELINE_CONTROLLER_PATH="$file" python3 - <<'PY'
import ast
import os
import sys

PATH = os.environ["AUDIT_PIPELINE_CONTROLLER_PATH"]

findings = []

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError) as e:
    print("ready_to_commit_probe_error:cannot_parse_source:%s" % (e,))
    sys.exit(1)


def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value


def subscript_key(node):
    s = node.slice
    if hasattr(ast, "Index") and isinstance(s, ast.Index):
        s = s.value
    return s


FUNCTIONS = {}
for _node in ast.walk(tree):
    if isinstance(_node, ast.FunctionDef):
        FUNCTIONS[_node.name] = _node


def find_function(name):
    return FUNCTIONS.get(name)


def has_name(node, name):
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def has_raise(node):
    return any(isinstance(n, ast.Raise) for n in ast.walk(node))


def list_literals(node):
    for n in ast.walk(node):
        if isinstance(n, ast.List):
            elts = []
            ok = True
            for elt in n.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    elts.append(elt.value)
                else:
                    ok = False
                    break
            if ok:
                yield elts


def called_function_names(node):
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            names.add(n.func.id)
    return names


def has_assigned_constant(name, value):
    for node in ast.walk(tree):
        for target, val in assignment_pairs(node):
            if isinstance(target, ast.Name) and target.id == name and isinstance(val, ast.Constant) and val.value == value:
                return True
    return False


def dict_has_key_value(node, key, value):
    for n in ast.walk(node):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == key and isinstance(v, ast.Constant) and v.value == value:
                    return True
    return False


ready_fn = find_function("ready_to_commit")

# 1) ready-to-commit parser exists
parser_ok = any(
    isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "add_parser"
    and node.args
    and isinstance(node.args[0], ast.Constant)
    and node.args[0].value == "ready-to-commit"
    for node in ast.walk(tree)
)
if not parser_ok:
    findings.append("ready_to_commit_parser_missing")

# 2) exactly required flags for the ready-to-commit subcommand
build_parser_fn = find_function("build_parser")
ready_var_name = None
if build_parser_fn is not None:
    for node in ast.walk(build_parser_fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "add_parser"
                and value.args
                and isinstance(value.args[0], ast.Constant)
                and value.args[0].value == "ready-to-commit"
            ):
                ready_var_name = node.targets[0].id

flags_ok = False
if build_parser_fn is not None and ready_var_name is not None:
    flag_specs = {}
    for node in ast.walk(build_parser_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == ready_var_name
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            flag_name = node.args[0].value
            required_kw = next((kw for kw in node.keywords if kw.arg == "required"), None)
            required_true = bool(
                required_kw is not None
                and isinstance(required_kw.value, ast.Constant)
                and required_kw.value.value is True
            )
            flag_specs[flag_name] = required_true
    expected = {"--workdir", "--implementation_task_id", "--review_task_id"}
    if set(flag_specs.keys()) == expected and all(flag_specs.values()):
        flags_ok = True
if not flags_ok:
    findings.append("ready_to_commit_flags_mismatch")

# 3) success/reject contract markers
if ready_fn is None or not dict_has_key_value(ready_fn, "outcome", "ready"):
    findings.append("ready_to_commit_success_marker_missing")

reject_fn = find_function("_emit_ready_to_commit_reject")
if reject_fn is None or not dict_has_key_value(reject_fn, "outcome", "not-ready"):
    findings.append("ready_to_commit_reject_marker_missing")

for fn, label_ in ((ready_fn, "ready"), (reject_fn, "reject")):
    if fn is None:
        findings.append("ready_to_commit_human_approval_required_missing:%s" % label_)
        findings.append("ready_to_commit_commit_performed_missing:%s" % label_)
        findings.append("ready_to_commit_push_performed_missing:%s" % label_)
        continue
    if not dict_has_key_value(fn, "human_approval_required", True):
        findings.append("ready_to_commit_human_approval_required_missing:%s" % label_)
    if not dict_has_key_value(fn, "commit_performed", False):
        findings.append("ready_to_commit_commit_performed_missing:%s" % label_)
    if not dict_has_key_value(fn, "push_performed", False):
        findings.append("ready_to_commit_push_performed_missing:%s" % label_)

# 4) repository-state/v1 and archive-v2 schema references
if not has_assigned_constant("REPOSITORY_STATE_SCHEMA", "hermes.repository-state/v1"):
    findings.append("ready_to_commit_repo_state_schema_missing")
if not has_assigned_constant("REVIEW_ARCHIVE_SCHEMA_V2", "hermes.review-archive/v2"):
    findings.append("ready_to_commit_archive_v2_schema_missing")

# 5) double repository capture / stability check
capture_fn = find_function("capture_repository_state")
if capture_fn is None:
    findings.append("ready_to_commit_double_capture_missing")
    findings.append("ready_to_commit_stability_check_missing")
else:
    capture_calls = sum(
        1
        for n in ast.walk(capture_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_capture_repository_state_once"
    )
    if capture_calls < 2:
        findings.append("ready_to_commit_double_capture_missing")
    stability_ok = False
    for node in ast.walk(capture_fn):
        if isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "first"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.NotEq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Name)
                and test.comparators[0].id == "second"
                and has_raise(node)
            ):
                stability_ok = True
    if not stability_ok:
        findings.append("ready_to_commit_stability_check_missing")

# 6) review/Kanban/archive/current fingerprint equality, directly inside ready_to_commit
fingerprint_compare_count = 0
if ready_fn is not None:
    for node in ast.walk(ready_fn):
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == "current_state"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.NotEq)
        ):
            fingerprint_compare_count += 1
if fingerprint_compare_count < 2:
    findings.append("ready_to_commit_fingerprint_equality_missing")

# 7) git diff --check gate
diff_check_ok = any(elts == ["git", "diff", "--check"] for elts in list_literals(tree))
if not diff_check_ok:
    findings.append("ready_to_commit_git_diff_check_missing")

# 8) reachable-set BFS from ready_to_commit: bounded shell=False subprocess
#    execution, forbidden git mutation subcommands, forbidden archive-helper
#    / kanban-create invocations, all scoped to ready-to-commit's own logic.
reachable = set()
frontier = ["ready_to_commit"]
while frontier:
    name = frontier.pop()
    if name in reachable:
        continue
    reachable.add(name)
    fn = find_function(name)
    if fn is None:
        continue
    for callee in called_function_names(fn):
        if callee in FUNCTIONS and callee not in reachable:
            frontier.append(callee)

FORBIDDEN_CALLEES = {"archive_review", "create_implementation", "create_review", "create_correction"}
for name in sorted(reachable & FORBIDDEN_CALLEES):
    findings.append("ready_to_commit_forbidden_mutation_call:%s" % name)

FORBIDDEN_GIT_SUBCOMMANDS = {
    "add", "commit", "push", "merge", "reset", "restore", "checkout", "clean", "rebase", "update-index",
}
archive_helper_ok = True
kanban_create_ok = True
git_mutation_findings = set()
shell_timeout_findings = []

for name in reachable:
    fn = find_function(name)
    if fn is None:
        continue
    if has_name(fn, "ARCHIVE_HELPER_PATH"):
        archive_helper_ok = False
    for elts in list_literals(fn):
        if len(elts) >= 3 and elts[0] == "hermes" and elts[1] == "kanban" and elts[2] == "create":
            kanban_create_ok = False
        if len(elts) >= 2 and elts[0] == "git":
            sub = elts[1]
            if sub in FORBIDDEN_GIT_SUBCOMMANDS:
                git_mutation_findings.add(sub)
            if sub == "hash-object" and "-w" in elts:
                git_mutation_findings.add("hash-object-w")
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ):
            shell_kw = next((kw for kw in node.keywords if kw.arg == "shell"), None)
            timeout_kw = next((kw for kw in node.keywords if kw.arg == "timeout"), None)
            if shell_kw is None or not (isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is False):
                shell_timeout_findings.append("shell:%s:line=%d" % (name, node.lineno))
            if timeout_kw is None:
                shell_timeout_findings.append("timeout:%s:line=%d" % (name, node.lineno))

if not archive_helper_ok:
    findings.append("ready_to_commit_archive_helper_invocation_present")
if not kanban_create_ok:
    findings.append("ready_to_commit_kanban_create_invocation_present")
for sub in sorted(git_mutation_findings):
    findings.append("ready_to_commit_git_mutation_command_present:%s" % sub)
for item in shell_timeout_findings:
    findings.append("ready_to_commit_unbounded_or_shell_true_subprocess:%s" % item)

# 9) exit-code contract: EXIT_VALIDATION=2 for reject, EXIT_TRANSPORT=3 for
#    usage/transport failures, and no ready-to-commit exit-4 (timeout) path.
EXIT_CONST = {}
for node in ast.walk(tree):
    for target, val in assignment_pairs(node):
        if isinstance(target, ast.Name) and target.id in ("EXIT_OK", "EXIT_VALIDATION", "EXIT_TRANSPORT", "EXIT_TIMEOUT"):
            if isinstance(val, ast.Constant) and isinstance(val.value, int):
                EXIT_CONST[target.id] = val.value

if EXIT_CONST.get("EXIT_VALIDATION") != 2:
    findings.append("ready_to_commit_reject_exit_code_wrong")
if EXIT_CONST.get("EXIT_TRANSPORT") != 3:
    findings.append("ready_to_commit_usage_transport_exit_code_wrong")

main_fn = find_function("main")

# A5 B3 host fix: strict all-handler exit mapping
def _handler_exception_names(type_node):
    if isinstance(type_node, ast.Name):
        return {type_node.id}

    if isinstance(type_node, ast.Tuple):
        return {
            elt.id
            for elt in type_node.elts
            if isinstance(elt, ast.Name)
        }

    return set()


def _handler_return_names(handler):
    result = set()

    for stmt in handler.body:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Name)
            ):
                result.add(node.value.id)

    return result


_expected_handler_exits = {
    "ReadyToCommitReject": "EXIT_VALIDATION",
    "TransportError": "EXIT_TRANSPORT",
    "OSError": "EXIT_TRANSPORT",
    "CliUsageError": "EXIT_TRANSPORT",
}

_observed_handler_exits = {
    name: []
    for name in _expected_handler_exits
}

if main_fn is not None:
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.ExceptHandler):
            continue

        if node.type is None:
            continue

        return_names = _handler_return_names(node)

        for exc_name in _handler_exception_names(node.type):
            if exc_name in _observed_handler_exits:
                _observed_handler_exits[exc_name].append(
                    return_names
                )


def _all_handlers_exact(exc_name, expected_exit):
    handlers = _observed_handler_exits.get(exc_name, [])

    return bool(handlers) and all(
        values == {expected_exit}
        for values in handlers
    )


if not _all_handlers_exact(
    "ReadyToCommitReject",
    "EXIT_VALIDATION",
):
    findings.append(
        "ready_to_commit_reject_exit_code_wrong"
    )

if not all(
    _all_handlers_exact(exc_name, "EXIT_TRANSPORT")
    for exc_name in (
        "TransportError",
        "OSError",
        "CliUsageError",
    )
):
    findings.append(
        "ready_to_commit_usage_transport_exit_code_wrong"
    )

reject_mapped = set()
transport_mapped = set()
if main_fn is not None:
    for node in ast.walk(main_fn):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            type_names = set()
            if isinstance(node.type, ast.Name):
                type_names = {node.type.id}
            elif isinstance(node.type, ast.Tuple):
                type_names = {e.id for e in node.type.elts if isinstance(e, ast.Name)}
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            if any(isinstance(r.value, ast.Name) and r.value.id == "EXIT_VALIDATION" for r in returns):
                reject_mapped |= type_names
            if any(isinstance(r.value, ast.Name) and r.value.id == "EXIT_TRANSPORT" for r in returns):
                transport_mapped |= type_names

if "ReadyToCommitReject" not in reject_mapped:
    findings.append("ready_to_commit_reject_exit_code_wrong")
if not {"TransportError", "CliUsageError", "OSError"}.issubset(transport_mapped):
    findings.append("ready_to_commit_usage_transport_exit_code_wrong")

if ready_fn is not None:
    if has_name(ready_fn, "EXIT_TIMEOUT"):
        findings.append("ready_to_commit_exit4_path_present")
    for node in ast.walk(ready_fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value == 4:
            findings.append("ready_to_commit_exit4_path_present")

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies ready-to-commit hardening invariants (A5)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
  fi
}

# --- A6: pipeline_controller_server MCP adapter hardening (AST probe only;
# never imports/execs the MCP adapter source, never starts the MCP server)
pipeline_controller_mcp_audit() {
  local label="$1"
  local file="$2"
  local findings
  findings="$(AUDIT_PIPELINE_CONTROLLER_MCP_PATH="$file" python3 - <<'PY'
import ast
import os
import sys

PATH = os.environ["AUDIT_PIPELINE_CONTROLLER_MCP_PATH"]

findings = []

try:
    with open(PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=PATH)
except (OSError, SyntaxError) as e:
    print("pipeline_controller_mcp_probe_error:cannot_parse_source:%s" % (e,))
    sys.exit(1)


def assignment_pairs(node):
    if isinstance(node, ast.Assign):
        for target in node.targets:
            yield target, node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        yield node.target, node.value


def has_assigned_constant(name, value):
    for node in ast.walk(tree):
        for target, val in assignment_pairs(node):
            if (
                isinstance(target, ast.Name)
                and target.id == name
                and isinstance(val, ast.Constant)
                and val.value == value
            ):
                return True
    return False


FUNCTIONS = {}
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        FUNCTIONS[node.name] = node


def find_function(name):
    return FUNCTIONS.get(name)


def string_constants(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


# 1) MCPServer("pipeline-controller")
server_ok = False
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "MCPServer"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "pipeline-controller"
    ):
        server_ok = True
if not server_ok:
    findings.append("pipeline_controller_mcp_server_name_missing")

# 2) exactly the intended seven-tool surface
EXPECTED_TOOLS = {
    "check_task", "create_implementation", "create_review", "create_correction",
    "wait_task", "archive_review", "ready_to_commit",
}
registered_tools = set()
for node in FUNCTIONS.values():
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool":
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    registered_tools.add(kw.value.value)
if registered_tools != EXPECTED_TOOLS:
    missing = EXPECTED_TOOLS - registered_tools
    unexpected = registered_tools - EXPECTED_TOOLS
    findings.append(
        "pipeline_controller_mcp_tool_surface_mismatch:missing=%s;unexpected=%s"
        % (",".join(sorted(missing)) or "none", ",".join(sorted(unexpected)) or "none")
    )

# 3) fixed controller path constant
if not has_assigned_constant("CONTROLLER_PATH", "/usr/local/bin/hermes-pipeline-controller"):
    findings.append("pipeline_controller_mcp_controller_path_missing")

# 4) shell=False + bounded subprocess (module-wide); at least one real
# subprocess execution point must exist (the controller invocation).
subprocess_run_calls = 0
for node in ast.walk(tree):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ):
        subprocess_run_calls += 1
        shell_kw = next((kw for kw in node.keywords if kw.arg == "shell"), None)
        timeout_kw = next((kw for kw in node.keywords if kw.arg == "timeout"), None)
        if shell_kw is None or not (isinstance(shell_kw.value, ast.Constant) and shell_kw.value.value is False):
            findings.append("pipeline_controller_mcp_unbounded_or_shell_true_subprocess:line=%d" % node.lineno)
        if timeout_kw is None:
            findings.append("pipeline_controller_mcp_unbounded_or_shell_true_subprocess:line=%d" % node.lineno)
if subprocess_run_calls == 0:
    findings.append("pipeline_controller_mcp_no_subprocess_execution_present")

# 5) no arbitrary-command/argv tool: no tool-decorated function may accept a
# parameter literally named argv/command/cmd/shell_command/executable/args.
FORBIDDEN_PARAM_NAMES = {"argv", "command", "cmd", "shell_command", "executable", "args"}
for node in FUNCTIONS.values():
    is_tool = any(
        isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool"
        for dec in node.decorator_list
    )
    if not is_tool:
        continue
    param_names = {a.arg for a in node.args.args} | {a.arg for a in node.args.kwonlyargs}
    if param_names & FORBIDDEN_PARAM_NAMES:
        findings.append("pipeline_controller_mcp_arbitrary_command_tool_present:%s" % node.name)

# 6) no commit/push/staging subprocess: this adapter must never spawn git or
# reference a raw "git" argv token; the controller subprocess is its only
# execution path.
if any(s == "git" for s in string_constants(tree)):
    findings.append("pipeline_controller_mcp_commit_push_staging_subprocess_present")

# 7) no controller-policy reimplementation: none of these controller-owned
# policy symbols may be redefined/duplicated in the adapter.
FORBIDDEN_POLICY_NAMES = {
    "TASK_STATUSES", "RUN_STATUSES", "EVENT_KINDS", "WAIT_TERMINAL_STATUSES",
    "classify_verdict", "select_latest_run", "validate_workdir", "capture_repository_state",
    "REPOSITORY_STATE_SCHEMA", "REVIEW_ARCHIVE_SCHEMA_V2", "ALLOWED_ROOT",
}
defined_names = set(FUNCTIONS.keys())
for node in ast.walk(tree):
    for target, _val in assignment_pairs(node):
        if isinstance(target, ast.Name):
            defined_names.add(target.id)
present_policy_names = defined_names & FORBIDDEN_POLICY_NAMES
if present_policy_names:
    findings.append(
        "pipeline_controller_mcp_policy_reimplementation_present:%s" % ",".join(sorted(present_policy_names))
    )

# 8) no ready_to_commit -> archive_review chaining
ready_fn = find_function("_tool_ready_to_commit")
if ready_fn is None:
    findings.append("pipeline_controller_mcp_ready_to_commit_tool_missing")
else:
    ready_strings = string_constants(ready_fn)
    ready_called = {
        n.func.id for n in ast.walk(ready_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    if "archive-review" in ready_strings or "_tool_archive_review" in ready_called:
        findings.append("pipeline_controller_mcp_ready_to_commit_chains_archive_review")

# 9) no archive_review -> ready_to_commit chaining
archive_fn = find_function("_tool_archive_review")
if archive_fn is None:
    findings.append("pipeline_controller_mcp_archive_review_tool_missing")
else:
    archive_strings = string_constants(archive_fn)
    archive_called = {
        n.func.id for n in ast.walk(archive_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    if "ready-to-commit" in archive_strings or "_tool_ready_to_commit" in archive_called:
        findings.append("pipeline_controller_mcp_archive_review_chains_ready_to_commit")

for finding in findings:
    print(finding)

sys.exit(1 if findings else 0)
PY
)"
  if [[ -z "$findings" ]]; then
    check_ok "$label ($file) satisfies pipeline-controller MCP adapter hardening invariants (A6)"
  else
    local line
    while IFS= read -r line; do
      [[ -n "$line" ]] && check_fail "$label ($file): $line"
    done <<<"$findings"
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
  require_file_nonempty "templates/review_archive_bridge.py"
  require_file_nonempty "tests/test_review_archive_bridge_template.py"
  require_file_nonempty "scripts/hermes-pipeline-controller.py"
  require_file_nonempty "tests/test_hermes_pipeline_controller.py"
  require_file_nonempty "templates/pipeline_controller_server.py"
  require_file_nonempty "tests/test_pipeline_controller_template.py"
  require_file_nonempty ".gitignore"

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
  echo "4a) reviewer template protocol invariants (templates/reviewer-SOUL.md) — A4.1 reviewer collect protocol:"
  reviewer_soul_invariant_audit "reviewer SOUL (repo template)" "$REVIEWER_FILE"

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
  echo "6c) claude bridge static contract audit (templates/claude_bridge_server.py) — A4.1 AST probe:"
  claude_static_contract_audit "claude bridge template" "$CLAUDE_FILE"

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

  echo
  echo "8) review bridge repository-state/v1 hardening (A5 B3 repo-state audit):"
  review_bridge_repository_state_audit "review bridge template" "$REVIEW_FILE"

  echo
  echo "9) reviewer SOUL A5 repository-state fingerprint requirement:"
  reviewer_soul_a5_repository_state_invariant_audit "reviewer SOUL (repo template)" "$REVIEWER_FILE"

  echo
  echo "10) review archive bridge hermes.review-archive/v2 hardening (A5 B3):"
  ARCHIVE_BRIDGE_FILE="$REPO_DIR/templates/review_archive_bridge.py"
  review_archive_bridge_v2_audit "review archive bridge template" "$ARCHIVE_BRIDGE_FILE"

  echo
  echo "11) ready-to-commit controller hardening (A5 B3):"
  PIPELINE_CONTROLLER_FILE="$REPO_DIR/scripts/hermes-pipeline-controller.py"
  pipeline_controller_ready_to_commit_audit "pipeline controller" "$PIPELINE_CONTROLLER_FILE"

  echo
  echo "12) A5.1 /.ai/reviews/ narrow ignore-scope integration invariant:"
  GITIGNORE_FILE="$REPO_DIR/.gitignore"
  if [[ -s "$GITIGNORE_FILE" ]] && grep_line_exact "$GITIGNORE_FILE" "/.ai/reviews/"; then
    check_ok ".gitignore contains the exact rooted rule: /.ai/reviews/"
  else
    check_fail ".gitignore missing the exact rooted rule: /.ai/reviews/"
  fi

  for broad_rule in ".ai/" "/.ai/"; do
    if [[ -s "$GITIGNORE_FILE" ]] && grep_line_exact_absent "$GITIGNORE_FILE" "$broad_rule"; then
      check_ok ".gitignore does not rely on the overly broad ignore rule: $broad_rule"
    else
      check_fail ".gitignore must not rely on the overly broad ignore rule: $broad_rule (only the rooted /.ai/reviews/ rule is permitted for this contract)"
    fi
  done

  CONTROLLER_TEST_FILE="$REPO_DIR/tests/test_hermes_pipeline_controller.py"
  if grep_ok "$CONTROLLER_TEST_FILE" '(repo / ".gitignore").write_text("/.ai/reviews/\n")'; then
    check_ok "controller test fixture (make_rtc_repo) uses the narrow /.ai/reviews/ ignore rule"
  else
    check_fail "controller test fixture (make_rtc_repo) must use the narrow /.ai/reviews/ ignore rule, not a broad .ai/ rule"
  fi

  if grep_absent "$CONTROLLER_TEST_FILE" '(repo / ".gitignore").write_text(".ai/\n")'; then
    check_ok "controller test fixture (make_rtc_repo) does not regress to the broad .ai/ ignore rule"
  else
    check_fail "controller test fixture (make_rtc_repo) must not regress to the broad .ai/ ignore rule"
  fi

  if grep_ok "$CONTROLLER_TEST_FILE" "test_a51_unrelated_untracked_path_under_ai_outside_reviews_blocks_ready_to_commit"; then
    check_ok "controller tests cover an unrelated untracked path elsewhere under .ai/ still blocking READY_TO_COMMIT"
  else
    check_fail "controller tests missing coverage for an unrelated untracked path elsewhere under .ai/ (outside .ai/reviews/) still blocking READY_TO_COMMIT"
  fi

  echo
  echo "13) pipeline-controller MCP adapter hardening (A6 thin MCP facade):"
  PIPELINE_CONTROLLER_MCP_FILE="$REPO_DIR/templates/pipeline_controller_server.py"
  pipeline_controller_mcp_audit "pipeline-controller MCP adapter template" "$PIPELINE_CONTROLLER_MCP_FILE"
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
  echo "12a) reviewer SOUL protocol invariants (installed runtime) — A4.1 reviewer collect protocol:"
  reviewer_soul_invariant_audit "reviewer SOUL (installed runtime)" "$reviewer_soul"

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

LITERAL_CONTAINER_CALLS = ("frozenset", "set", "list", "tuple")

def string_literals(node):
    out = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Call):
        # Narrowly unwrap literal container constructors such as
        # frozenset({...}) / set({...}) / list([...]) / tuple((...)) so
        # their single literal argument can still be inspected statically.
        # Anything else (arbitrary calls) is deliberately left opaque: we
        # do not want a string appearing anywhere inside an arbitrary call
        # to be mistaken for an authorized literal.
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in LITERAL_CONTAINER_CALLS
            and len(node.args) == 1
            and not node.keywords
        ):
            out.extend(string_literals(node.args[0]))
        return out
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
  echo "17a) claude bridge static contract audit (installed runtime) — A4.1 AST probe (fail-closed):"
  local claude_server
  claude_server="$(runtime_path claude_bridge_server)"
  if [[ ! -s "$claude_server" ]]; then
    check_fail "installed claude_bridge ($claude_server): runtime_claude_bridge_missing"
  else
    claude_static_contract_audit "installed claude_bridge" "$claude_server"
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
