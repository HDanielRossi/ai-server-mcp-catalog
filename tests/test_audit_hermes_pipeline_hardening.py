"""Tests for the explicit-mode split of scripts/audit-hermes-pipeline-hardening.sh
(A3.5.1d): --repo-only / --runtime / --all, bare == --repo-only, and the
AUDIT_RUNTIME_ROOT override for hermetic runtime-fixture testing.

Hermetic: no dependence on live host runtime state. Repo-only fixtures are
built by copying the real repository files the audit checks (the audit
script itself, the templates, the template test suites, and the docs) into
a temporary directory that mirrors the repository layout. Runtime fixtures
are plain directories pointed to via AUDIT_RUNTIME_ROOT; the two live-host
probes (the `reviewer` CLI and sibling pipeline-repo status) are skipped by
the script itself under an AUDIT_RUNTIME_ROOT override, so no real Hermes
installation is ever required.
"""

import os
import shutil
import stat
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "audit-hermes-pipeline-hardening.sh")
CLAUDE_BRIDGE_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "claude_bridge_server.py")
REVIEWER_SOUL_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "reviewer-SOUL.md")

REPO_ONLY_FILES = [
    os.path.join("templates", "review_bridge_server.py"),
    os.path.join("templates", "reviewer-SOUL.md"),
    os.path.join("templates", "pipeline_bridge_server.py"),
    os.path.join("templates", "claude_bridge_server.py"),
    os.path.join("templates", "planner_bridge_server.py"),
    os.path.join("scripts", "planner-bridge"),
    os.path.join("tests", "test_review_bridge_template.py"),
    os.path.join("tests", "test_pipeline_bridge_template.py"),
    os.path.join("tests", "test_claude_bridge_template.py"),
    os.path.join("tests", "test_planner_bridge_template.py"),
    os.path.join("tests", "test_planner_bridge_wrapper.py"),
    os.path.join("docs", "hermes-pipeline.md"),
]


def _build_repo_fixture(tmp_path, break_marker=False):
    """A temp git repo containing the real audit script plus the real repo
    files it checks (copied verbatim from this repository), mirroring the
    layout the script expects relative to its own location."""
    fixture = tmp_path / "repo"
    fixture.mkdir()

    script_dst = fixture / "scripts" / "audit-hermes-pipeline-hardening.sh"
    script_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT_PATH, script_dst)
    script_dst.chmod(script_dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    for rel in REPO_ONLY_FILES:
        src = os.path.join(REPO_ROOT, rel)
        dst = fixture / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
        if rel == os.path.join("scripts", "planner-bridge"):
            dst.chmod(dst.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if break_marker:
        # Deliberately break one required hardening marker (check 6: the
        # claude bridge template's call-budget threshold constant) so the
        # repo-only suite must catch a genuine template regression.
        claude_file = fixture / "templates" / "claude_bridge_server.py"
        original = claude_file.read_text(encoding="utf-8")
        marker = "CALL_BUDGET_THRESHOLD = 4"
        assert marker in original, "fixture assumption stale: marker not found in real template"
        claude_file.write_text(original.replace(marker, "CALL_BUDGET_THRESHOLD = 99"), encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    return fixture


def _build_runtime_fixture(tmp_path, compliant=True):
    root = tmp_path / "runtime_root"
    pipeline_dir = root / "usr" / "local" / "lib" / "pipeline-bridge-mcp"
    review_dir = root / "usr" / "local" / "lib" / "review-bridge-mcp"
    claude_dir = root / "usr" / "local" / "lib" / "claude-bridge-mcp"
    hermes_dir = root / "home" / ".hermes"
    reviewer_dir = hermes_dir / "profiles" / "reviewer"
    for d in (pipeline_dir, review_dir, claude_dir, reviewer_dir):
        d.mkdir(parents=True, exist_ok=True)

    if not compliant:
        # An empty root: every file-based runtime check must fail closed.
        return root

    (pipeline_dir / "server.py").write_text(
        'key = stable_key(str(path), feature, f"review:{implementation_task_id}")\n',
        encoding="utf-8",
    )
    (review_dir / "server.py").write_text(
        'ALLOWED_TEST_COMMANDS = ["__skip__", "./scripts/audit-hermes-pipeline-hardening.sh"]\n',
        encoding="utf-8",
    )
    # Verbatim copy (not hand-written) so the A4.1 claude static contract
    # audit (REQUIRED_CLAUDE_FLAGS / _tool_run task_id) sees the real,
    # currently-compliant repo template rather than a stale hand-written stub.
    shutil.copy(CLAUDE_BRIDGE_TEMPLATE_PATH, claude_dir / "server.py")
    (hermes_dir / "config.yaml").write_text(
        "review_archive_bridge:\n  enabled: true\n",
        encoding="utf-8",
    )
    (hermes_dir / "SOUL.md").write_text(
        "# Async Kanban Boundary\nAfter creating an implementation task, stop.\n",
        encoding="utf-8",
    )
    (reviewer_dir / "config.yaml").write_text(
        "review_bridge:\n  enabled: true\n",
        encoding="utf-8",
    )
    # Verbatim copy (not hand-written) so the A4.1 reviewer collect protocol
    # invariant audit sees the real, currently-compliant repo SOUL text.
    shutil.copy(REVIEWER_SOUL_TEMPLATE_PATH, reviewer_dir / "SOUL.md")
    return root


def _mutate_file(path, old, new):
    """Exact string-replacement mutation, asserting the target text was
    actually present before replacing it (so a stale assumption about the
    template's current contents fails loudly instead of silently no-op'ing)."""
    text = path.read_text(encoding="utf-8")
    assert old in text, f"mutation assumption stale: {old!r} not found in {path}"
    mutated = text.replace(old, new, 1)
    assert mutated != text
    path.write_text(mutated, encoding="utf-8")


# Mutations shared between repository-fixture negative tests (which mutate a
# copy of templates/claude_bridge_server.py or templates/reviewer-SOUL.md
# under a repo fixture) and runtime-fixture negative tests (which mutate the
# same content under an AUDIT_RUNTIME_ROOT fixture).
MUT_PERMISSION_MODE_FLAGS_OLD = '    "--permission-mode",\n    "acceptEdits",\n'
MUT_PERMISSION_MODE_FLAGS_NEW = ""

MUT_ACCEPT_EDITS_OLD = '"acceptEdits",\n'
MUT_ACCEPT_EDITS_NEW = '"bypassPermissions",\n'

MUT_TASK_ID_REQUIRED_PARAM_OLD = "    task_id,\n"
MUT_TASK_ID_OPTIONAL_PARAM_NEW = "    task_id=None,\n"

MUT_TASK_ID_FORWARDING_OLD = "        task_id=stripped_task_id,\n"
MUT_TASK_ID_FORWARDING_NEW = '        task_id="static",\n'

MUT_SEQUENTIAL_COLLECTS_OLD = "7. collect calls are SEQUENTIAL ONLY, NEVER parallel.\n"
MUT_SEQUENTIAL_COLLECTS_NEW = ""


def _run(args, cwd, env_overrides=None, timeout=60):
    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)
    script = os.path.join(cwd, "scripts", "audit-hermes-pipeline-hardening.sh")
    return subprocess.run(
        ["bash", script, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# 1) bare invocation behaves as --repo-only.
def test_bare_invocation_matches_repo_only(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    bare = _run([], cwd=str(fixture))
    explicit = _run(["--repo-only"], cwd=str(fixture))
    assert bare.returncode == explicit.returncode == 0
    assert bare.stdout == explicit.stdout


def test_bare_invocation_matches_repo_only_on_failure(tmp_path):
    fixture = _build_repo_fixture(tmp_path, break_marker=True)
    bare = _run([], cwd=str(fixture))
    explicit = _run(["--repo-only"], cwd=str(fixture))
    assert bare.returncode == explicit.returncode == 1
    assert bare.stdout == explicit.stdout


# 2) --repo-only accepted (exit 0 on compliant fixture).
def test_repo_only_passes_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout


# 3) --runtime accepted (exit 0 with compliant AUDIT_RUNTIME_ROOT fixture).
def test_runtime_passes_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout
    # The compliant fixture now includes a real claude-bridge-mcp/server.py
    # copy (A4.1); this must not regress to the fail-closed missing-bridge path.
    assert "runtime_claude_bridge_missing" not in result.stdout


# 4) --all accepted (exit 0 when both suites pass under fixtures).
def test_all_passes_when_both_suites_pass(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "runtime_claude_bridge_missing" not in result.stdout


# 5) unknown flag rejected with usage text, exit 2.
def test_unknown_flag_rejected_with_usage(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--bogus"], cwd=str(fixture))
    assert result.returncode == 2
    assert "Usage" in result.stderr


def test_repeated_mode_flag_rejected_with_usage(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only", "--repo-only"], cwd=str(fixture))
    assert result.returncode == 2
    assert "Usage" in result.stderr


def test_two_different_mode_flags_rejected_with_usage(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only", "--runtime"], cwd=str(fixture))
    assert result.returncode == 2
    assert "Usage" in result.stderr


# 6) repo-only does NOT fail merely because live runtime differs/absent.
def test_repo_only_ignores_absent_runtime(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    missing_root = tmp_path / "does_not_exist"
    result = _run(["--repo-only"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(missing_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_repo_only_ignores_noncompliant_runtime(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=False)
    result = _run(["--repo-only"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout


# 7) repo-only catches a representative repo/template hardening failure.
def test_repo_only_catches_broken_template_marker(tmp_path):
    fixture = _build_repo_fixture(tmp_path, break_marker=True)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "claude template missing: CALL_BUDGET_THRESHOLD = 4" in result.stdout


# 8) runtime mode performs runtime checks.
def test_runtime_catches_missing_live_runtime(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=False)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    expected_missing_path = os.path.join(str(runtime_root), "usr", "local", "lib", "pipeline-bridge-mcp", "server.py")
    assert expected_missing_path in result.stdout


# 8a) check 16 (installed review_bridge ALLOWED_TEST_COMMANDS) A3.5.1d.1:
# the static AST probe must see through frozenset({...}) / set({...}) /
# list([...]) / tuple((...)) literal container constructors, since the real
# review_bridge template defines ALLOWED_TEST_COMMANDS as
# frozenset({...}), not a bare set/list/tuple display.
CHECK16_OK_MSG = "installed review_bridge authorizes the Hermes pipeline hardening audit test command"
CHECK16_FAIL_MSG = "installed review_bridge does not authorize the Hermes pipeline hardening audit test command"


def _build_runtime_fixture_with_review_server(tmp_path, review_server_source):
    root = _build_runtime_fixture(tmp_path, compliant=True)
    review_server = root / "usr" / "local" / "lib" / "review-bridge-mcp" / "server.py"
    review_server.write_text(review_server_source, encoding="utf-8")
    return root


def test_check16_authorizes_frozenset_literal_positive(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture_with_review_server(
        tmp_path,
        'ALLOWED_TEST_COMMANDS = frozenset(\n'
        '    {\n'
        '        "__skip__",\n'
        '        "/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q",\n'
        '        "./scripts/audit-hermes-pipeline-hardening.sh",\n'
        '    }\n'
        ')\n',
    )
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert CHECK16_OK_MSG in result.stdout


def test_check16_rejects_frozenset_literal_missing_command_negative(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture_with_review_server(
        tmp_path,
        'ALLOWED_TEST_COMMANDS = frozenset(\n'
        '    {\n'
        '        "__skip__",\n'
        '        "/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q",\n'
        '    }\n'
        ')\n',
    )
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert CHECK16_FAIL_MSG in result.stdout


def test_check16_does_not_unwrap_arbitrary_call_safety(tmp_path):
    """A string appearing inside an arbitrary (non-whitelisted) call's
    literal argument must not be treated as authorized: only frozenset/set/
    list/tuple constructor calls are unwrapped."""
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture_with_review_server(
        tmp_path,
        'ALLOWED_TEST_COMMANDS = some_wrapper(\n'
        '    {\n'
        '        "__skip__",\n'
        '        "./scripts/audit-hermes-pipeline-hardening.sh",\n'
        '    }\n'
        ')\n',
    )
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert CHECK16_FAIL_MSG in result.stdout


# 9) --all includes both repo and runtime sections; either failure fails --all.
def test_all_output_contains_both_section_labels(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert "SECTION: REPO-ONLY" in result.stdout
    assert "SECTION: RUNTIME" in result.stdout


def test_all_fails_when_repo_suite_fails(tmp_path):
    fixture = _build_repo_fixture(tmp_path, break_marker=True)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_all_fails_when_runtime_suite_fails(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=False)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_all_fails_when_both_suites_fail(tmp_path):
    fixture = _build_repo_fixture(tmp_path, break_marker=True)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=False)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "FAIL (repo-only)" in result.stdout
    assert "FAIL (runtime)" in result.stdout


# 10) mode-specific failure propagates non-zero only in the failing mode.
def test_broken_repo_fixture_does_not_fail_runtime_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path, break_marker=True)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    repo_only_result = _run(["--repo-only"], cwd=str(fixture))
    runtime_result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert repo_only_result.returncode == 1
    assert runtime_result.returncode == 0


def test_noncompliant_runtime_does_not_fail_repo_only_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=False)
    runtime_result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    repo_only_result = _run(["--repo-only"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert runtime_result.returncode == 1
    assert repo_only_result.returncode == 0


# 11) the script is read-only: no destructive command tokens anywhere in it.
DESTRUCTIVE_TOKEN_PATTERNS = [
    r"\brm\b",
    r"\bmv\b",
    r"\binstall\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bmkdir\b",
    r"\bdd\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\btee\b",
    r"git\s+commit",
    r"git\s+push",
    r"git\s+reset",
    r"git\s+clean",
    r"git\s+checkout",
]


def test_script_source_contains_no_destructive_tokens():
    import re

    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    for pattern in DESTRUCTIVE_TOKEN_PATTERNS:
        assert not re.search(pattern, source), f"destructive token pattern found: {pattern}"


def _suspicious_redirect(line):
    """A '>' preceded by whitespace/digit/'&'/line-start is a real shell
    redirection context; a '>' glued onto a word (e.g. inside a quoted
    documentation placeholder like "<path>") is not. Flags any real
    redirection that isn't the safe `&N` / `/dev/null` sink."""
    i = 0
    n = len(line)
    while i < n:
        if line[i] == ">":
            j = i
            while j < n and line[j] == ">":
                j += 1
            preceding = line[i - 1] if i > 0 else ""
            is_real_redirect = i == 0 or preceding in (" ", "\t", "&") or preceding.isdigit()
            if is_real_redirect:
                following = line[j:].lstrip()
                if not (following.startswith("&") or following.startswith("/dev/null")):
                    return line[i:j]
            i = j
        else:
            i += 1
    return None


def test_script_source_has_no_file_write_redirection():
    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for lineno, line in enumerate(lines, start=1):
        found = _suspicious_redirect(line)
        assert found is None, f"possible file-write redirection at line {lineno}: {line!r}"


# 12) documentation accuracy.
def test_docs_document_all_modes_and_deployment_disclaimer():
    docs_path = os.path.join(REPO_ROOT, "docs", "hermes-pipeline.md")
    with open(docs_path, "r", encoding="utf-8") as f:
        docs = f.read()
    assert "--repo-only" in docs
    assert "--runtime" in docs
    assert "--all" in docs
    assert "AUDIT_RUNTIME_ROOT" in docs
    assert "alias for" in docs or "== `--repo-only`" in docs or "bare invocation" in docs.lower()
    assert "does NOT imply" in docs or "does not imply" in docs.lower()


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flag_prints_usage_and_exits_zero(tmp_path, flag):
    fixture = _build_repo_fixture(tmp_path)
    result = _run([flag], cwd=str(fixture))
    assert result.returncode == 0
    assert "Usage" in result.stdout


# 13) A4.1 claude-bridge static contract + reviewer collect-protocol
# invariant audit: compliant-fixture positives and single-mutation negatives.

# 13a) compliant fixture (real templates/claude_bridge_server.py and
# templates/reviewer-SOUL.md copied verbatim under AUDIT_RUNTIME_ROOT) passes
# in all three modes.
def test_compliant_claude_bridge_and_reviewer_soul_repo_only_passes(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_compliant_claude_bridge_and_reviewer_soul_runtime_passes(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "runtime_claude_bridge_missing" not in result.stdout


def test_compliant_claude_bridge_and_reviewer_soul_all_passes(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--all"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout


# 13b) repository-fixture negatives: one mutation each on a copy of the
# current repo's templates/claude_bridge_server.py or
# templates/reviewer-SOUL.md, --repo-only must fail with the specific finding.
def test_repo_negative_missing_permission_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    claude_file = fixture / "templates" / "claude_bridge_server.py"
    _mutate_file(claude_file, MUT_PERMISSION_MODE_FLAGS_OLD, MUT_PERMISSION_MODE_FLAGS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "claude_flags_mismatch" in result.stdout


def test_repo_negative_wrong_permission_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    claude_file = fixture / "templates" / "claude_bridge_server.py"
    _mutate_file(claude_file, MUT_ACCEPT_EDITS_OLD, MUT_ACCEPT_EDITS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "claude_flags_mismatch" in result.stdout


def test_repo_negative_task_id_optional(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    claude_file = fixture / "templates" / "claude_bridge_server.py"
    _mutate_file(claude_file, MUT_TASK_ID_REQUIRED_PARAM_OLD, MUT_TASK_ID_OPTIONAL_PARAM_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "claude_task_id_optional" in result.stdout


def test_repo_negative_task_id_forwarding_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    claude_file = fixture / "templates" / "claude_bridge_server.py"
    _mutate_file(claude_file, MUT_TASK_ID_FORWARDING_OLD, MUT_TASK_ID_FORWARDING_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "claude_task_id_forwarding_missing" in result.stdout


def test_repo_negative_reviewer_soul_missing_sequential_invariant(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    soul_file = fixture / "templates" / "reviewer-SOUL.md"
    _mutate_file(soul_file, MUT_SEQUENTIAL_COLLECTS_OLD, MUT_SEQUENTIAL_COLLECTS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "reviewer_soul_missing_invariant:sequential_collects" in result.stdout


# 13c) runtime-fixture negatives: each gets its own AUDIT_RUNTIME_ROOT built
# from the compliant fixture (real bridge + real SOUL), with exactly one
# mutation under test; --runtime must fail with the specific finding.
def test_runtime_negative_bridge_absent(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    claude_server = runtime_root / "usr" / "local" / "lib" / "claude-bridge-mcp" / "server.py"
    assert claude_server.exists()
    claude_server.unlink()
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "runtime_claude_bridge_missing" in result.stdout


def test_runtime_negative_missing_permission_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    claude_server = runtime_root / "usr" / "local" / "lib" / "claude-bridge-mcp" / "server.py"
    _mutate_file(claude_server, MUT_PERMISSION_MODE_FLAGS_OLD, MUT_PERMISSION_MODE_FLAGS_NEW)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "claude_flags_mismatch" in result.stdout


def test_runtime_negative_wrong_permission_mode(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    claude_server = runtime_root / "usr" / "local" / "lib" / "claude-bridge-mcp" / "server.py"
    _mutate_file(claude_server, MUT_ACCEPT_EDITS_OLD, MUT_ACCEPT_EDITS_NEW)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "claude_flags_mismatch" in result.stdout


def test_runtime_negative_task_id_optional(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    claude_server = runtime_root / "usr" / "local" / "lib" / "claude-bridge-mcp" / "server.py"
    _mutate_file(claude_server, MUT_TASK_ID_REQUIRED_PARAM_OLD, MUT_TASK_ID_OPTIONAL_PARAM_NEW)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "claude_task_id_optional" in result.stdout


def test_runtime_negative_task_id_forwarding_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    claude_server = runtime_root / "usr" / "local" / "lib" / "claude-bridge-mcp" / "server.py"
    _mutate_file(claude_server, MUT_TASK_ID_FORWARDING_OLD, MUT_TASK_ID_FORWARDING_NEW)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "claude_task_id_forwarding_missing" in result.stdout


def test_runtime_negative_reviewer_soul_missing_sequential_invariant(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    reviewer_soul = runtime_root / "home" / ".hermes" / "profiles" / "reviewer" / "SOUL.md"
    _mutate_file(reviewer_soul, MUT_SEQUENTIAL_COLLECTS_OLD, MUT_SEQUENTIAL_COLLECTS_NEW)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "reviewer_soul_missing_invariant:sequential_collects" in result.stdout
