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

import hashlib
import os
import shutil
import stat
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "audit-hermes-pipeline-hardening.sh")
CLAUDE_BRIDGE_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "claude_bridge_server.py")
REVIEWER_SOUL_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "reviewer-SOUL.md")
PIPELINE_CONTROLLER_MCP_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "pipeline_controller_server.py")
HERMES_PIPELINE_CONTROLLER_PATH = os.path.join(REPO_ROOT, "scripts", "hermes-pipeline-controller.py")
PIPELINE_BRIDGE_TEMPLATE_PATH = os.path.join(REPO_ROOT, "templates", "pipeline_bridge_server.py")

# A7.1: locks the CURRENT SHA-256 of the two frozen A6 files this task must
# never modify, so any drift in either file (this task's or a future one's)
# fails loudly instead of silently invalidating the A7.1 SHA-256 parity
# checks' assumptions.
FROZEN_PIPELINE_CONTROLLER_MCP_SHA256 = (
    "681834f1180145d5ced38e8e34f202a3c25f326a43d99344423c5a9970327eb9"
)
FROZEN_HERMES_PIPELINE_CONTROLLER_SHA256 = (
    "1548c8bdde59d20ccaf02f35e11445d6bdd6576e0bb616ab8ee39744a17f814b"
)

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
    os.path.join("templates", "review_archive_bridge.py"),
    os.path.join("tests", "test_review_archive_bridge_template.py"),
    os.path.join("scripts", "hermes-pipeline-controller.py"),
    os.path.join("tests", "test_hermes_pipeline_controller.py"),
    os.path.join("templates", "pipeline_controller_server.py"),
    os.path.join("tests", "test_pipeline_controller_template.py"),
    ".gitignore",
    os.path.join("docs", "production-repo-onboarding.md"),
    os.path.join("templates", "hermes-repo-contract.md"),
    os.path.join("templates", "default-SOUL.md"),
    os.path.join("tests", "test_default_soul_template.py"),
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

    # Verbatim copy (not hand-written) so the A7.1 bridge-location-rejection
    # check sees a real MCPServer("pipeline-bridge") identity (distinct from
    # "pipeline-controller") and the A4.1-era "installed pipeline_bridge"
    # compliance check still finds its required marker, instead of relying on
    # a stale hand-written stub.
    shutil.copy(PIPELINE_BRIDGE_TEMPLATE_PATH, pipeline_dir / "server.py")
    (review_dir / "server.py").write_text(
        'ALLOWED_TEST_COMMANDS = ["__skip__", "./scripts/audit-hermes-pipeline-hardening.sh"]\n',
        encoding="utf-8",
    )
    # Verbatim copy (not hand-written) so the A4.1 claude static contract
    # audit (REQUIRED_CLAUDE_FLAGS / _tool_run task_id) sees the real,
    # currently-compliant repo template rather than a stale hand-written stub.
    shutil.copy(CLAUDE_BRIDGE_TEMPLATE_PATH, claude_dir / "server.py")
    # A7.1: the real Hermes config shape nests MCP registrations under a
    # top-level "mcp_servers:" mapping; pipeline_controller must be an
    # immediate child of that mapping, never a column-0 top-level key.
    (hermes_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  review_archive_bridge:\n"
        "    enabled: true\n"
        "  pipeline_controller:\n"
        "    enabled: true\n",
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

    # A7.1: coder/coder-claude/planner-codex/sysadmin profile configs are
    # present (mirroring the reviewer profile), none registering the
    # default-only pipeline_controller MCP.
    for profile_name, mcp_entry in (
        ("coder", "claude_bridge"),
        ("coder-claude", "claude_bridge"),
        ("planner-codex", "planner_bridge"),
        ("sysadmin", "review_bridge"),
    ):
        profile_dir = hermes_dir / "profiles" / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.yaml").write_text(
            f"{mcp_entry}:\n  enabled: true\n",
            encoding="utf-8",
        )

    # A7.1: dedicated pipeline-controller-mcp runtime layout + trusted
    # controller binary, both verbatim copies (not hand-written) so the
    # source/runtime SHA-256 parity and exact-roster checks see the real,
    # currently-compliant repo files rather than a stale hand-written stub.
    controller_mcp_dir = root / "usr" / "local" / "lib" / "pipeline-controller-mcp"
    controller_mcp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PIPELINE_CONTROLLER_MCP_TEMPLATE_PATH, controller_mcp_dir / "server.py")

    # A7.1: dedicated virtualenv, modeled structurally (directory + nonempty
    # pyvenv.cfg + executable bin/python3); no real interpreter/site-packages
    # tree is required or created.
    controller_venv_dir = controller_mcp_dir / ".venv"
    controller_venv_bin_dir = controller_venv_dir / "bin"
    controller_venv_bin_dir.mkdir(parents=True, exist_ok=True)
    (controller_venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.11.0\n", encoding="utf-8")
    controller_venv_python3 = controller_venv_bin_dir / "python3"
    controller_venv_python3.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    controller_venv_python3.chmod(0o755)

    controller_bin_dir = root / "usr" / "local" / "bin"
    controller_bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERMES_PIPELINE_CONTROLLER_PATH, controller_bin_dir / "hermes-pipeline-controller")
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
    import re

    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    heredoc_end = None
    heredoc_strip_tabs = False
    heredoc_re = re.compile(
        r"<<(-)?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))"
    )

    for lineno, line in enumerate(lines, start=1):
        if heredoc_end is not None:
            candidate = line.rstrip("\r\n")
            if heredoc_strip_tabs:
                candidate = candidate.lstrip("\t")
            if candidate == heredoc_end:
                heredoc_end = None
                heredoc_strip_tabs = False
            continue

        match = heredoc_re.search(line)
        if match is not None:
            heredoc_strip_tabs = match.group(1) == "-"
            heredoc_end = next(
                value
                for value in match.groups()[1:]
                if value is not None
            )

        found = _suspicious_redirect(line)
        assert found is None, (
            f"possible file-write redirection at line {lineno}: {line!r}"
        )


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


# 14) A5 B3: repo-only hardening audit extensions covering the current A5
# READY_TO_COMMIT contract: review_bridge repository-state/v1, the reviewer
# SOUL A5 fingerprint requirement, review_archive_bridge hermes.review-archive/v2,
# and the hermes-pipeline-controller.py ready-to-commit subcommand. Each
# mutation below corrupts exactly one invariant on a copy of the current,
# real repository file and asserts the audit fails closed with the specific
# stable finding id; a shared positive test proves the unmodified repository
# still passes all four new sections.

def test_repo_only_a5_sections_pass_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "satisfies repository-state/v1 hardening invariants (A5)" in result.stdout
    assert "satisfies A5 repository-state fingerprint invariants" in result.stdout
    assert "satisfies hermes.review-archive/v2 hardening invariants (A5)" in result.stdout
    assert "satisfies ready-to-commit hardening invariants (A5)" in result.stdout


# 14a) review_bridge repository-state/v1 hardening (templates/review_bridge_server.py).

MUT_RB_SCHEMA_OLD = 'REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v1"'
MUT_RB_SCHEMA_NEW = 'REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v0"'

MUT_RB_CANONICAL_OLD = "canonical_workdir = os.path.realpath(str(resolved_workdir))"
MUT_RB_CANONICAL_NEW = "canonical_workdir = str(resolved_workdir)"

MUT_RB_HEAD_OLD = '_run_git_text(["git", "rev-parse", "HEAD"], resolved_workdir).strip()'
MUT_RB_HEAD_NEW = '_run_git_text(["git", "show-ref", "HEAD"], resolved_workdir).strip()'

MUT_RB_CHANGED_PATHS_OLD = "changed_paths = sorted(set(staged_paths) | set(unstaged_paths) | set(untracked_paths))"
MUT_RB_CHANGED_PATHS_NEW = "changed_paths = sorted(staged_paths + unstaged_paths + untracked_paths)"

MUT_RB_ENVELOPE_KEY_OLD = '"staged_patch_sha256": _sha256_bytes(staged_patch),\n'
MUT_RB_ENVELOPE_KEY_NEW = ""

MUT_RB_AGGREGATE_OLD = 'envelope["aggregate_sha256"] = _canonical_json_sha256(envelope)'
MUT_RB_AGGREGATE_NEW = 'envelope["aggregate_digest"] = _canonical_json_sha256(envelope)'

MUT_RB_DOUBLE_CAPTURE_OLD = "    second = _capture_repository_state_once(resolved_workdir, canonical_workdir)\n"
MUT_RB_DOUBLE_CAPTURE_NEW = "    second = first\n"

MUT_RB_STABILITY_OLD = (
    "    if first != second:\n"
    '        raise ReviewBridgeError("repository state changed between consecutive captures (unstable state)")\n'
)
MUT_RB_STABILITY_NEW = "    if first != second:\n        pass\n"

MUT_RB_CONFLICT_OLD = (
    "if code in CONFLICT_STATUS_CODES:\n"
    '            raise ReviewBridgeError(f"unresolved merge conflict detected in git status: {line}")\n'
)
MUT_RB_CONFLICT_NEW = (
    "if False:\n"
    '            raise ReviewBridgeError(f"unresolved merge conflict detected in git status: {line}")\n'
)

MUT_RB_SUBMODULE_OLD = 'if fields and fields[0] == "160000":'
MUT_RB_SUBMODULE_NEW = 'if fields and fields[0] == "999999":'

MUT_RB_SPECIAL_ENTRY_OLD = (
    "                if (\n"
    "                    stat.S_ISREG(mode)\n"
    "                    or stat.S_ISDIR(mode)\n"
    "                    or stat.S_ISLNK(mode)\n"
    "                ):\n"
)
MUT_RB_SPECIAL_ENTRY_NEW = (
    "                if (\n"
    "                    stat.S_ISREG(mode)\n"
    "                    or stat.S_ISDIR(mode)\n"
    "                ):\n"
)

MUT_RB_SHELL_OLD = (
    "    try:\n"
    "        completed = subprocess.run(\n"
    "            argv,\n"
    "            cwd=str(cwd),\n"
    "            shell=False,\n"
    "            capture_output=True,\n"
    "            text=True,\n"
    "            timeout=timeout,\n"
    "            check=False,\n"
    "        )\n"
    "    except subprocess.TimeoutExpired:\n"
)
MUT_RB_SHELL_NEW = MUT_RB_SHELL_OLD.replace("shell=False,", "shell=True,")


def _review_bridge_file(fixture):
    return fixture / "templates" / "review_bridge_server.py"


def test_repo_negative_review_bridge_schema_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_SCHEMA_OLD, MUT_RB_SCHEMA_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_schema_missing" in result.stdout


def test_repo_negative_review_bridge_canonical_workdir_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_CANONICAL_OLD, MUT_RB_CANONICAL_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_canonical_workdir_missing" in result.stdout


def test_repo_negative_review_bridge_head_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_HEAD_OLD, MUT_RB_HEAD_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_head_missing" in result.stdout


def test_repo_negative_review_bridge_changed_paths_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_CHANGED_PATHS_OLD, MUT_RB_CHANGED_PATHS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_changed_paths_missing" in result.stdout


def test_repo_negative_review_bridge_envelope_key_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_ENVELOPE_KEY_OLD, MUT_RB_ENVELOPE_KEY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_envelope_key_missing:staged_patch_sha256" in result.stdout


def test_repo_negative_review_bridge_aggregate_sha256_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_AGGREGATE_OLD, MUT_RB_AGGREGATE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_aggregate_sha256_missing" in result.stdout


def test_repo_negative_review_bridge_double_capture_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_DOUBLE_CAPTURE_OLD, MUT_RB_DOUBLE_CAPTURE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_double_capture_missing" in result.stdout


def test_repo_negative_review_bridge_stability_check_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_STABILITY_OLD, MUT_RB_STABILITY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_stability_check_missing" in result.stdout


def test_repo_negative_review_bridge_conflict_rejection_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_CONFLICT_OLD, MUT_RB_CONFLICT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_conflict_rejection_missing" in result.stdout


def test_repo_negative_review_bridge_submodule_rejection_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_SUBMODULE_OLD, MUT_RB_SUBMODULE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_submodule_rejection_missing" in result.stdout


def test_repo_negative_review_bridge_special_entry_rejection_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_SPECIAL_ENTRY_OLD, MUT_RB_SPECIAL_ENTRY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_special_entry_rejection_missing" in result.stdout


def test_repo_negative_review_bridge_unbounded_or_shell_true_subprocess(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_bridge_file(fixture), MUT_RB_SHELL_OLD, MUT_RB_SHELL_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_bridge_repo_state_unbounded_or_shell_true_subprocess" in result.stdout


# 14b) reviewer SOUL A5 repository-state fingerprint requirement
# (templates/reviewer-SOUL.md).

MUT_SOUL_A5_FINAL_COLLECT_OLD = "one final, successful collect(workdir) call"
MUT_SOUL_A5_FINAL_COLLECT_NEW = "a final collect(workdir) call"

MUT_SOUL_A5_SCOPE_PATHS_OLD = "scope_paths never substitutes for a fingerprint"
MUT_SOUL_A5_SCOPE_PATHS_NEW = "scope_paths is optional documentation"


def _reviewer_soul_file(fixture):
    return fixture / "templates" / "reviewer-SOUL.md"


def test_repo_negative_reviewer_soul_a5_final_collect_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_reviewer_soul_file(fixture), MUT_SOUL_A5_FINAL_COLLECT_OLD, MUT_SOUL_A5_FINAL_COLLECT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "reviewer_soul_a5_missing_invariant:final_collect_required" in result.stdout


def test_repo_negative_reviewer_soul_a5_scope_paths_substitute_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_reviewer_soul_file(fixture), MUT_SOUL_A5_SCOPE_PATHS_OLD, MUT_SOUL_A5_SCOPE_PATHS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "reviewer_soul_a5_missing_invariant:scope_paths_no_substitute" in result.stdout


# 14c) review_archive_bridge hermes.review-archive/v2 hardening
# (templates/review_archive_bridge.py).

MUT_ARC_SCHEMA_V2_OLD = 'REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v2"'
MUT_ARC_SCHEMA_V2_NEW = 'REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v1"'

MUT_ARC_TASKS_TABLE_OLD = '"SELECT * FROM tasks WHERE id=?",'
MUT_ARC_TASKS_TABLE_NEW = '"SELECT * FROM items WHERE id=?",'

MUT_ARC_GUESSED_RUNS_OLD = "        FROM task_runs\n"
MUT_ARC_GUESSED_RUNS_NEW = "        FROM runs\n"

MUT_ARC_GUESSED_TASK_PARENTS_OLD = "        FROM task_events\n"
MUT_ARC_GUESSED_TASK_PARENTS_NEW = "        FROM task_parents\n"

MUT_ARC_IDENTITY_OLD = (
    '    if metadata.get("implementation_task_id") != parent:\n'
    "        raise ArchiveValidationError(\n"
    '            "latest run metadata implementation_task_id "\n'
    '            "does not match task parent"\n'
    "        )\n"
)
MUT_ARC_IDENTITY_NEW = "    pass\n"

MUT_ARC_REPO_STATE_VALIDATION_OLD = (
    "    recomputed = sha256_canonical_excluding(\n"
    "        state,\n"
    '        "aggregate_sha256",\n'
    "    )\n"
)
MUT_ARC_REPO_STATE_VALIDATION_NEW = "    recomputed = aggregate\n"

MUT_ARC_ENVELOPE_SHA_OLD = '    envelope["archive_envelope_sha256"] = (\n'
MUT_ARC_ENVELOPE_SHA_NEW = '    envelope["archive_envelope_digest"] = (\n'

MUT_ARC_AI_DIR_OLD = '    ai_dir = workdir / ".ai"\n'
MUT_ARC_AI_DIR_NEW = '    ai_dir = workdir / ".ai_meta"\n'

MUT_ARC_SYMLINK_OLD = (
    "        if (\n"
    "            not stat.S_ISREG(\n"
    "                file_stat.st_mode\n"
    "            )\n"
    "            or artifact.is_symlink()\n"
    "        ):\n"
)
MUT_ARC_SYMLINK_NEW = (
    "        if (\n"
    "            not stat.S_ISREG(\n"
    "                file_stat.st_mode\n"
    "            )\n"
    "        ):\n"
)

MUT_ARC_READONLY_OLD = '            f"file:{db_path}?mode=ro",\n'
MUT_ARC_READONLY_NEW = '            f"file:{db_path}",\n'

MUT_ARC_SHELL_OLD = "            check=False,\n            shell=False,\n            timeout=GIT_TIMEOUT_SECONDS,\n"
MUT_ARC_SHELL_NEW = "            check=False,\n            shell=True,\n            timeout=GIT_TIMEOUT_SECONDS,\n"

MUT_ARC_GIT_MUTATION_OLD = '["status", "--short"]'
MUT_ARC_GIT_MUTATION_NEW = '["commit", "-am", "wip"]'


def _review_archive_file(fixture):
    return fixture / "templates" / "review_archive_bridge.py"


def test_repo_negative_review_archive_schema_v2_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_SCHEMA_V2_OLD, MUT_ARC_SCHEMA_V2_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_schema_missing" in result.stdout


def test_repo_negative_review_archive_tasks_table_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_TASKS_TABLE_OLD, MUT_ARC_TASKS_TABLE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_tasks_table_missing" in result.stdout


def test_repo_negative_review_archive_guessed_runs_table_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_GUESSED_RUNS_OLD, MUT_ARC_GUESSED_RUNS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_guessed_runs_table_present" in result.stdout


def test_repo_negative_review_archive_guessed_task_parents_table_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_GUESSED_TASK_PARENTS_OLD, MUT_ARC_GUESSED_TASK_PARENTS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_guessed_task_parents_table_present" in result.stdout


def test_repo_negative_review_archive_implementation_identity_check_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_IDENTITY_OLD, MUT_ARC_IDENTITY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_implementation_identity_check_missing" in result.stdout


def test_repo_negative_review_archive_repository_state_validation_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_REPO_STATE_VALIDATION_OLD, MUT_ARC_REPO_STATE_VALIDATION_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_repository_state_validation_missing" in result.stdout


def test_repo_negative_review_archive_envelope_sha256_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_ENVELOPE_SHA_OLD, MUT_ARC_ENVELOPE_SHA_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_archive_envelope_sha256_missing" in result.stdout


def test_repo_negative_review_archive_ai_reviews_path_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_AI_DIR_OLD, MUT_ARC_AI_DIR_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_ai_reviews_path_missing" in result.stdout


def test_repo_negative_review_archive_symlink_or_nonregular_check_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_SYMLINK_OLD, MUT_ARC_SYMLINK_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_symlink_or_nonregular_check_missing" in result.stdout


def test_repo_negative_review_archive_readonly_kanban_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_READONLY_OLD, MUT_ARC_READONLY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_readonly_kanban_missing" in result.stdout


def test_repo_negative_review_archive_shell_false_subprocess_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_SHELL_OLD, MUT_ARC_SHELL_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_shell_false_subprocess_missing" in result.stdout


def test_repo_negative_review_archive_git_mutation_command_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_review_archive_file(fixture), MUT_ARC_GIT_MUTATION_OLD, MUT_ARC_GIT_MUTATION_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "review_archive_v2_git_mutation_command_present:commit" in result.stdout


# 14d) hermes-pipeline-controller.py ready-to-commit hardening
# (scripts/hermes-pipeline-controller.py).

MUT_PC_PARSER_OLD = 'ready_p = subparsers.add_parser(\n        "ready-to-commit", prog="ready-to-commit",\n'
MUT_PC_PARSER_NEW = 'ready_p = subparsers.add_parser(\n        "readytocommit", prog="ready-to-commit",\n'

MUT_PC_FLAGS_OLD = 'ready_p.add_argument("--review_task_id", required=True)'
MUT_PC_FLAGS_NEW = 'ready_p.add_argument("--review_task_id", required=False)'

MUT_PC_SUCCESS_MARKER_OLD = '"phase": "ready-to-commit",\n        "outcome": "ready",\n'
MUT_PC_SUCCESS_MARKER_NEW = '"phase": "ready-to-commit",\n        "outcome": "ok",\n'

MUT_PC_REJECT_MARKER_OLD = '"phase": "ready-to-commit",\n        "outcome": "not-ready",\n'
MUT_PC_REJECT_MARKER_NEW = '"phase": "ready-to-commit",\n        "outcome": "rejected",\n'

MUT_PC_HUMAN_APPROVAL_READY_OLD = (
    '"repository_state_sha256": current_state["aggregate_sha256"],\n        "human_approval_required": True,\n'
)
MUT_PC_HUMAN_APPROVAL_READY_NEW = (
    '"repository_state_sha256": current_state["aggregate_sha256"],\n        "human_approval_required": False,\n'
)

MUT_PC_COMMIT_PERFORMED_REJECT_OLD = (
    '"reason": reason,\n        "human_approval_required": True,\n        "commit_performed": False,\n'
)
MUT_PC_COMMIT_PERFORMED_REJECT_NEW = (
    '"reason": reason,\n        "human_approval_required": True,\n        "commit_performed": True,\n'
)

MUT_PC_PUSH_PERFORMED_READY_OLD = (
    '"commit_performed": False,\n        "push_performed": False,\n    }\n'
    '    print(json.dumps(payload, separators=(",", ":")))\n    return EXIT_OK\n'
)
MUT_PC_PUSH_PERFORMED_READY_NEW = (
    '"commit_performed": False,\n        "push_performed": True,\n    }\n'
    '    print(json.dumps(payload, separators=(",", ":")))\n    return EXIT_OK\n'
)

MUT_PC_REPO_STATE_SCHEMA_OLD = 'REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v1"'
MUT_PC_REPO_STATE_SCHEMA_NEW = 'REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v0"'

MUT_PC_ARCHIVE_V2_SCHEMA_OLD = 'REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v2"'
MUT_PC_ARCHIVE_V2_SCHEMA_NEW = 'REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v1"'

MUT_PC_DOUBLE_CAPTURE_OLD = "    second = _capture_repository_state_once(resolved_workdir, canonical_workdir)\n"
MUT_PC_DOUBLE_CAPTURE_NEW = "    second = first\n"

MUT_PC_STABILITY_OLD = (
    "    if first != second:\n"
    '        raise RepositoryStateError("repository state changed between consecutive captures (unstable state)")\n'
)
MUT_PC_STABILITY_NEW = "    if first != second:\n        pass\n"

MUT_PC_FINGERPRINT_OLD = (
    '    if current_state != envelope.get("repository_state"):\n'
    "        raise ReadyToCommitReject(\n"
    '            "repository_state_mismatch_archive",\n'
    '            "current repository_state does not match the archived review artifact repository_state",\n'
    "        )\n"
)
MUT_PC_FINGERPRINT_NEW = "    pass\n"

MUT_PC_DIFF_CHECK_OLD = '["git", "diff", "--check"], cwd=str(resolved_workdir), shell=False,'
MUT_PC_DIFF_CHECK_NEW = '["git", "diff", "--stat"], cwd=str(resolved_workdir), shell=False,'

MUT_PC_SHELL_BOUND_OLD = (
    '["git", "diff", "--check"], cwd=str(resolved_workdir), shell=False,\n'
    "            capture_output=True, text=True, timeout=GIT_DIFF_CHECK_TIMEOUT_SECONDS,\n"
)
MUT_PC_SHELL_BOUND_NEW = (
    '["git", "diff", "--check"], cwd=str(resolved_workdir), shell=True,\n'
    "            capture_output=True, text=True, timeout=GIT_DIFF_CHECK_TIMEOUT_SECONDS,\n"
)

MUT_PC_READY_PAYLOAD_ANCHOR_OLD = '    payload = {\n        "phase": "ready-to-commit",\n        "outcome": "ready",\n'
MUT_PC_ARCHIVE_HELPER_INSERT_NEW = (
    "    _unused_helper_path = ARCHIVE_HELPER_PATH\n" + MUT_PC_READY_PAYLOAD_ANCHOR_OLD
)
MUT_PC_KANBAN_CREATE_INSERT_NEW = (
    '    _unused_kanban_create = ["hermes", "kanban", "create"]\n' + MUT_PC_READY_PAYLOAD_ANCHOR_OLD
)
MUT_PC_FORBIDDEN_CALL_INSERT_NEW = "    archive_review(args)\n" + MUT_PC_READY_PAYLOAD_ANCHOR_OLD
MUT_PC_GIT_MUTATION_INSERT_NEW = (
    '    _unused_git_add = ["git", "add", "-A"]\n' + MUT_PC_READY_PAYLOAD_ANCHOR_OLD
)
MUT_PC_EXIT4_INSERT_NEW = "    if False:\n        return EXIT_TIMEOUT\n" + MUT_PC_READY_PAYLOAD_ANCHOR_OLD

MUT_PC_REJECT_EXIT_OLD = (
    "    except ReadyToCommitReject as exc:\n"
    "        _emit_ready_to_commit_reject(args, exc.reason_code, exc.reason)\n"
    "        return EXIT_VALIDATION\n"
)
MUT_PC_REJECT_EXIT_NEW = (
    "    except ReadyToCommitReject as exc:\n"
    "        _emit_ready_to_commit_reject(args, exc.reason_code, exc.reason)\n"
    "        return EXIT_TRANSPORT\n"
)

MUT_PC_TRANSPORT_EXIT_OLD = (
    "    except OSError as exc:\n"
    '        sys.stderr.write("transport error: failed to launch hermes: " + str(exc) + "\\n")\n'
    "        return EXIT_TRANSPORT\n"
    "    except CliUsageError as exc:\n"
    '        sys.stderr.write("usage error: " + str(exc) + "\\n")\n'
    "        return EXIT_TRANSPORT\n"
)
MUT_PC_TRANSPORT_EXIT_NEW = (
    "    except OSError as exc:\n"
    '        sys.stderr.write("transport error: failed to launch hermes: " + str(exc) + "\\n")\n'
    "        return EXIT_TRANSPORT\n"
    "    except CliUsageError as exc:\n"
    '        sys.stderr.write("usage error: " + str(exc) + "\\n")\n'
    "        return EXIT_VALIDATION\n"
)


def _pipeline_controller_file(fixture):
    return fixture / "scripts" / "hermes-pipeline-controller.py"


def test_repo_negative_pipeline_controller_parser_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_PARSER_OLD, MUT_PC_PARSER_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_parser_missing" in result.stdout


def test_repo_negative_pipeline_controller_flags_mismatch(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_FLAGS_OLD, MUT_PC_FLAGS_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_flags_mismatch" in result.stdout


def test_repo_negative_pipeline_controller_success_marker_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_SUCCESS_MARKER_OLD, MUT_PC_SUCCESS_MARKER_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_success_marker_missing" in result.stdout


def test_repo_negative_pipeline_controller_reject_marker_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_REJECT_MARKER_OLD, MUT_PC_REJECT_MARKER_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_reject_marker_missing" in result.stdout


def test_repo_negative_pipeline_controller_human_approval_required_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_HUMAN_APPROVAL_READY_OLD, MUT_PC_HUMAN_APPROVAL_READY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_human_approval_required_missing:ready" in result.stdout


def test_repo_negative_pipeline_controller_commit_performed_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _pipeline_controller_file(fixture), MUT_PC_COMMIT_PERFORMED_REJECT_OLD, MUT_PC_COMMIT_PERFORMED_REJECT_NEW
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_commit_performed_missing:reject" in result.stdout


def test_repo_negative_pipeline_controller_push_performed_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_PUSH_PERFORMED_READY_OLD, MUT_PC_PUSH_PERFORMED_READY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_push_performed_missing:ready" in result.stdout


def test_repo_negative_pipeline_controller_repo_state_schema_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_REPO_STATE_SCHEMA_OLD, MUT_PC_REPO_STATE_SCHEMA_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_repo_state_schema_missing" in result.stdout


def test_repo_negative_pipeline_controller_archive_v2_schema_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_ARCHIVE_V2_SCHEMA_OLD, MUT_PC_ARCHIVE_V2_SCHEMA_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_archive_v2_schema_missing" in result.stdout


def test_repo_negative_pipeline_controller_double_capture_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_DOUBLE_CAPTURE_OLD, MUT_PC_DOUBLE_CAPTURE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_double_capture_missing" in result.stdout


def test_repo_negative_pipeline_controller_stability_check_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_STABILITY_OLD, MUT_PC_STABILITY_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_stability_check_missing" in result.stdout


def test_repo_negative_pipeline_controller_fingerprint_equality_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_FINGERPRINT_OLD, MUT_PC_FINGERPRINT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_fingerprint_equality_missing" in result.stdout


def test_repo_negative_pipeline_controller_git_diff_check_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_DIFF_CHECK_OLD, MUT_PC_DIFF_CHECK_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_git_diff_check_missing" in result.stdout


def test_repo_negative_pipeline_controller_unbounded_or_shell_true_subprocess(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_SHELL_BOUND_OLD, MUT_PC_SHELL_BOUND_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_unbounded_or_shell_true_subprocess" in result.stdout


def test_repo_negative_pipeline_controller_archive_helper_invocation_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _pipeline_controller_file(fixture), MUT_PC_READY_PAYLOAD_ANCHOR_OLD, MUT_PC_ARCHIVE_HELPER_INSERT_NEW
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_archive_helper_invocation_present" in result.stdout


def test_repo_negative_pipeline_controller_kanban_create_invocation_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_READY_PAYLOAD_ANCHOR_OLD, MUT_PC_KANBAN_CREATE_INSERT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_kanban_create_invocation_present" in result.stdout


def test_repo_negative_pipeline_controller_forbidden_mutation_call_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_READY_PAYLOAD_ANCHOR_OLD, MUT_PC_FORBIDDEN_CALL_INSERT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_forbidden_mutation_call:archive_review" in result.stdout


def test_repo_negative_pipeline_controller_git_mutation_command_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_READY_PAYLOAD_ANCHOR_OLD, MUT_PC_GIT_MUTATION_INSERT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_git_mutation_command_present:add" in result.stdout


def test_repo_negative_pipeline_controller_exit4_path_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_READY_PAYLOAD_ANCHOR_OLD, MUT_PC_EXIT4_INSERT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_exit4_path_present" in result.stdout


def test_repo_negative_pipeline_controller_reject_exit_code_wrong(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_REJECT_EXIT_OLD, MUT_PC_REJECT_EXIT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_reject_exit_code_wrong" in result.stdout


def test_repo_negative_pipeline_controller_usage_transport_exit_code_wrong(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_file(fixture), MUT_PC_TRANSPORT_EXIT_OLD, MUT_PC_TRANSPORT_EXIT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "ready_to_commit_usage_transport_exit_code_wrong" in result.stdout


# 14b) A5.1 /.ai/reviews/ narrow ignore-scope integration invariant.

def _gitignore_file(fixture):
    return fixture / ".gitignore"


def _controller_test_file(fixture):
    return fixture / "tests" / "test_hermes_pipeline_controller.py"


MUT_A51_GITIGNORE_REMOVE_OLD = "/.ai/reviews/\n"
MUT_A51_GITIGNORE_REMOVE_NEW = ""

MUT_A51_GITIGNORE_BROAD_DOT_AI_OLD = "/.ai/reviews/\n"
MUT_A51_GITIGNORE_BROAD_DOT_AI_NEW = ".ai/\n"

MUT_A51_GITIGNORE_BROAD_ROOTED_DOT_AI_OLD = "/.ai/reviews/\n"
MUT_A51_GITIGNORE_BROAD_ROOTED_DOT_AI_NEW = "/.ai/\n"

MUT_A51_CONTROLLER_FIXTURE_REGRESS_OLD = '(repo / ".gitignore").write_text("/.ai/reviews/\\n")'
MUT_A51_CONTROLLER_FIXTURE_REGRESS_NEW = '(repo / ".gitignore").write_text(".ai/\\n")'


def test_repo_only_a51_section_passes_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert ".gitignore contains the exact rooted rule: /.ai/reviews/" in result.stdout
    assert "controller test fixture (make_rtc_repo) uses the narrow /.ai/reviews/ ignore rule" in result.stdout
    assert (
        "controller tests cover an unrelated untracked path elsewhere under .ai/ "
        "still blocking READY_TO_COMMIT" in result.stdout
    )


def test_repo_negative_a51_gitignore_missing_rooted_reviews_rule(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_gitignore_file(fixture), MUT_A51_GITIGNORE_REMOVE_OLD, MUT_A51_GITIGNORE_REMOVE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert ".gitignore missing the exact rooted rule: /.ai/reviews/" in result.stdout


def test_repo_negative_a51_gitignore_replaced_with_broad_dot_ai(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_gitignore_file(fixture), MUT_A51_GITIGNORE_BROAD_DOT_AI_OLD, MUT_A51_GITIGNORE_BROAD_DOT_AI_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert ".gitignore missing the exact rooted rule: /.ai/reviews/" in result.stdout
    assert ".gitignore must not rely on the overly broad ignore rule: .ai/" in result.stdout


def test_repo_negative_a51_gitignore_replaced_with_broad_rooted_dot_ai(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _gitignore_file(fixture), MUT_A51_GITIGNORE_BROAD_ROOTED_DOT_AI_OLD, MUT_A51_GITIGNORE_BROAD_ROOTED_DOT_AI_NEW
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert ".gitignore missing the exact rooted rule: /.ai/reviews/" in result.stdout
    assert ".gitignore must not rely on the overly broad ignore rule: /.ai/" in result.stdout


def test_repo_negative_a51_controller_fixture_regresses_to_broad_dot_ai(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _controller_test_file(fixture),
        MUT_A51_CONTROLLER_FIXTURE_REGRESS_OLD,
        MUT_A51_CONTROLLER_FIXTURE_REGRESS_NEW,
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert (
        "controller test fixture (make_rtc_repo) must use the narrow /.ai/reviews/ ignore rule, "
        "not a broad .ai/ rule" in result.stdout
    )
    assert (
        "controller test fixture (make_rtc_repo) must not regress to the broad .ai/ ignore rule" in result.stdout
    )


# 15) A6: pipeline_controller_server MCP adapter hardening.


def _pipeline_controller_mcp_file(fixture):
    return fixture / "templates" / "pipeline_controller_server.py"


MUT_A6_SERVER_NAME_OLD = 'mcp_server = MCPServer("pipeline-controller")'
MUT_A6_SERVER_NAME_NEW = 'mcp_server = MCPServer("pipeline-controller-x")'

MUT_A6_TOOL_SURFACE_OLD = '@mcp_server.tool(name="check_task")'
MUT_A6_TOOL_SURFACE_NEW = '@mcp_server.tool(name="check_task_x")'

MUT_A6_CONTROLLER_PATH_ANCHOR = 'CONTROLLER_PATH = "/usr/local/bin/hermes-pipeline-controller"\n'

MUT_A6_CONTROLLER_PATH_OLD = 'CONTROLLER_PATH = "/usr/local/bin/hermes-pipeline-controller"'
MUT_A6_CONTROLLER_PATH_NEW = 'CONTROLLER_PATH = "/usr/local/bin/hermes-pipeline-controller-x"'

MUT_A6_SHELL_FALSE_OLD = "            shell=False,\n"
MUT_A6_SHELL_FALSE_NEW = "            shell=True,\n"

MUT_A6_TIMEOUT_OLD = "            timeout=timeout,\n"
MUT_A6_TIMEOUT_NEW = ""

MUT_A6_ARBITRARY_TOOL_OLD = "def _tool_check_task(task_id: str):"
MUT_A6_ARBITRARY_TOOL_NEW = "def _tool_check_task(task_id: str, argv=None):"

MUT_A6_GIT_TOKEN_NEW = MUT_A6_CONTROLLER_PATH_ANCHOR + '_UNUSED_GIT_TOKEN = "git"\n'

MUT_A6_POLICY_REIMPL_NEW = MUT_A6_CONTROLLER_PATH_ANCHOR + 'ALLOWED_ROOT = "/opt/ai/projects"\n'

MUT_A6_READY_CHAINS_ARCHIVE_OLD = (
    '    return _invoke_controller("ready-to-commit", argv_tail, DEFAULT_READY_TO_COMMIT_TIMEOUT_SECONDS)\n'
)
MUT_A6_READY_CHAINS_ARCHIVE_NEW = (
    '    _invoke_controller("archive-review", argv_tail, DEFAULT_ARCHIVE_REVIEW_TIMEOUT_SECONDS)\n'
    '    return _invoke_controller("ready-to-commit", argv_tail, DEFAULT_READY_TO_COMMIT_TIMEOUT_SECONDS)\n'
)

MUT_A6_ARCHIVE_CHAINS_READY_OLD = (
    '    return _invoke_controller("archive-review", argv_tail, DEFAULT_ARCHIVE_REVIEW_TIMEOUT_SECONDS)\n'
)
MUT_A6_ARCHIVE_CHAINS_READY_NEW = (
    '    _invoke_controller("ready-to-commit", argv_tail, DEFAULT_READY_TO_COMMIT_TIMEOUT_SECONDS)\n'
    '    return _invoke_controller("archive-review", argv_tail, DEFAULT_ARCHIVE_REVIEW_TIMEOUT_SECONDS)\n'
)


def test_repo_only_a6_section_passes_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "pipeline-controller MCP adapter template" in result.stdout
    assert "satisfies pipeline-controller MCP adapter hardening invariants (A6)" in result.stdout


def test_repo_negative_a6_server_name_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_SERVER_NAME_OLD, MUT_A6_SERVER_NAME_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_server_name_missing" in result.stdout


def test_repo_negative_a6_tool_surface_mismatch(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_TOOL_SURFACE_OLD, MUT_A6_TOOL_SURFACE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_tool_surface_mismatch" in result.stdout


def test_repo_negative_a6_controller_path_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_CONTROLLER_PATH_OLD, MUT_A6_CONTROLLER_PATH_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_controller_path_missing" in result.stdout


def test_repo_negative_a6_shell_true_subprocess(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_SHELL_FALSE_OLD, MUT_A6_SHELL_FALSE_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_unbounded_or_shell_true_subprocess" in result.stdout


def test_repo_negative_a6_unbounded_subprocess(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_TIMEOUT_OLD, MUT_A6_TIMEOUT_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_unbounded_or_shell_true_subprocess" in result.stdout


def test_repo_negative_a6_arbitrary_command_tool_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_ARBITRARY_TOOL_OLD, MUT_A6_ARBITRARY_TOOL_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_arbitrary_command_tool_present" in result.stdout


def test_repo_negative_a6_commit_push_staging_subprocess_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_CONTROLLER_PATH_ANCHOR, MUT_A6_GIT_TOKEN_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_commit_push_staging_subprocess_present" in result.stdout


def test_repo_negative_a6_policy_reimplementation_present(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(_pipeline_controller_mcp_file(fixture), MUT_A6_CONTROLLER_PATH_ANCHOR, MUT_A6_POLICY_REIMPL_NEW)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_policy_reimplementation_present" in result.stdout


def test_repo_negative_a6_ready_to_commit_chains_archive_review(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _pipeline_controller_mcp_file(fixture), MUT_A6_READY_CHAINS_ARCHIVE_OLD, MUT_A6_READY_CHAINS_ARCHIVE_NEW
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_ready_to_commit_chains_archive_review" in result.stdout


def test_repo_negative_a6_archive_review_chains_ready_to_commit(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _mutate_file(
        _pipeline_controller_mcp_file(fixture), MUT_A6_ARCHIVE_CHAINS_READY_OLD, MUT_A6_ARCHIVE_CHAINS_READY_NEW
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "pipeline_controller_mcp_archive_review_chains_ready_to_commit" in result.stdout


# 16) A7.1: pipeline-controller MCP future-runtime rollout contract (runtime
# mode) — dedicated runtime layout, trusted controller binary, source/runtime
# SHA-256 parity, virtualenv structural validity, bridge-location rejection,
# and the default-only privilege boundary. All fixtures are plain directories
# under tmp_path; no real venv, no hermes CLI, no network, no service ops.


def test_a7_frozen_source_files_sha256_unchanged():
    """Locks the CURRENT SHA-256 of the two frozen A6 source files this task
    must never modify. If either file drifts (this task's edits or a future
    task's), this fails loudly instead of silently invalidating every A7.1
    SHA-256 parity assertion below, which assumes these exact bytes."""
    with open(PIPELINE_CONTROLLER_MCP_TEMPLATE_PATH, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == FROZEN_PIPELINE_CONTROLLER_MCP_SHA256
    with open(HERMES_PIPELINE_CONTROLLER_PATH, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == FROZEN_HERMES_PIPELINE_CONTROLLER_SHA256


def test_a7_runtime_passes_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert "a7_pipeline_controller_" not in result.stdout


def test_a7_runtime_negative_dedicated_adapter_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    dedicated = runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / "server.py"
    assert dedicated.exists()
    dedicated.unlink()
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "a7_pipeline_controller_dedicated_runtime_missing_or_empty" in result.stdout


def test_a7_runtime_negative_binary_missing(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    controller_bin = runtime_root / "usr" / "local" / "bin" / "hermes-pipeline-controller"
    assert controller_bin.exists()
    controller_bin.unlink()
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "a7_pipeline_controller_binary_missing_or_empty" in result.stdout


def test_a7_runtime_negative_venv_dir_absent(tmp_path):
    """Deletes the .venv directory entirely from beneath AUDIT_RUNTIME_ROOT
    and asserts the marker fires with that exact path: this proves the audit
    inspects the .venv beneath the runtime root (rather than some fixed
    live-host path or a probe silently skipped under a fixture override),
    since the only thing that changed is content under AUDIT_RUNTIME_ROOT."""
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    venv_dir = runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / ".venv"
    assert venv_dir.is_dir()
    shutil.rmtree(venv_dir)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert f"a7_pipeline_controller_venv_missing_or_invalid:{venv_dir}:not_a_directory" in result.stdout


def test_a7_runtime_negative_venv_pyvenv_cfg_empty(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    venv_dir = runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / ".venv"
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    assert pyvenv_cfg.read_text(encoding="utf-8") != ""
    pyvenv_cfg.write_text("", encoding="utf-8")
    assert os.access(venv_dir / "bin" / "python3", os.X_OK)
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert f"a7_pipeline_controller_venv_missing_or_invalid:{venv_dir}:no_pyvenv_cfg" in result.stdout


def test_a7_runtime_negative_venv_no_executable_python(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    venv_dir = runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / ".venv"
    (venv_dir / "bin" / "python3").chmod(0o644)
    assert not (venv_dir / "bin" / "python").exists()
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert f"a7_pipeline_controller_venv_missing_or_invalid:{venv_dir}:no_venv_python_executable" in result.stdout


def test_a7_runtime_negative_bridge_location_rejected(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    bridge_server = runtime_root / "usr" / "local" / "lib" / "pipeline-bridge-mcp" / "server.py"
    impostor_source = (
        '"""Impostor pipeline-controller adapter placed at the pipeline-bridge runtime path."""\n'
        'mcp_server = MCPServer("pipeline-controller")\n'
    )
    assert hashlib.sha256(impostor_source.encode("utf-8")).hexdigest() != FROZEN_PIPELINE_CONTROLLER_MCP_SHA256
    bridge_server.write_text(impostor_source, encoding="utf-8")
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_bridge_location_rejected:{bridge_server}:mcp_identity=pipeline-controller"
        in result.stdout
    )


def test_a7_runtime_negative_adapter_sha256_mismatch(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    dedicated = runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / "server.py"
    with open(dedicated, "a", encoding="utf-8") as f:
        f.write("# a7.1 mutation: byte-level drift only, AST/tool-roster unaffected\n")
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "a7_pipeline_controller_adapter_sha256_mismatch" in result.stdout


def test_a7_runtime_negative_binary_sha256_mismatch(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    controller_bin = runtime_root / "usr" / "local" / "bin" / "hermes-pipeline-controller"
    with open(controller_bin, "a", encoding="utf-8") as f:
        f.write("# a7.1 mutation: byte-level drift only\n")
    result = _run(["--runtime"], cwd=str(fixture), env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)})
    assert result.returncode == 1
    assert "a7_pipeline_controller_binary_sha256_mismatch" in result.stdout


# 17) A7.1 R2b: exact runtime tool roster, default-only registration
# boundary, and A7-specific audit mode behavior. These extend the same
# tmp_path/AUDIT_RUNTIME_ROOT fixture framework used by the R2a tests above.


def _a7_runtime_adapter(runtime_root):
    return runtime_root / "usr" / "local" / "lib" / "pipeline-controller-mcp" / "server.py"


def _a7_default_config(runtime_root):
    return runtime_root / "home" / ".hermes" / "config.yaml"


def _a7_profile_config(runtime_root, profile_name):
    return runtime_root / "home" / ".hermes" / "profiles" / profile_name / "config.yaml"


def _a7_registered_tool_names(path):
    """Return literal @*.tool(name="...") names without importing/executing
    the adapter under test."""
    ast = __import__("ast")
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(path))

    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
            ):
                for kw in dec.keywords:
                    if (
                        kw.arg == "name"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        names.add(kw.value.value)
    return names


def _a7_replace_runtime_tool_name(adapter_path, old_name, new_name):
    """Rename one literal tool registration in a tmp_path adapter copy."""
    re = __import__("re")
    text = adapter_path.read_text(encoding="utf-8")
    pattern = rf'(\bname\s*=\s*)(["\']){re.escape(old_name)}\2'

    def repl(match):
        quote = match.group(2)
        return f"{match.group(1)}{quote}{new_name}{quote}"

    mutated, count = re.subn(pattern, repl, text, count=1)
    assert count == 1, f"tool registration not found: {old_name}"
    assert mutated != text
    adapter_path.write_text(mutated, encoding="utf-8")


A7_EXPECTED_RUNTIME_TOOLS = {
    "check_task",
    "create_implementation",
    "create_review",
    "create_correction",
    "wait_task",
    "archive_review",
    "ready_to_commit",
}


def test_a7_runtime_exact_tool_roster_passes(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    adapter = _a7_runtime_adapter(runtime_root)

    assert _a7_registered_tool_names(PIPELINE_CONTROLLER_MCP_TEMPLATE_PATH) == A7_EXPECTED_RUNTIME_TOOLS
    assert _a7_registered_tool_names(adapter) == A7_EXPECTED_RUNTIME_TOOLS

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 0
    assert "pipeline-controller installed runtime adapter exposes exactly the expected 7-tool roster" in result.stdout
    assert "pipeline-controller MCP installed runtime roster exposes no commit*/push* tool" in result.stdout
    assert "a7_pipeline_controller_tool_roster_mismatch" not in result.stdout
    assert "a7_pipeline_controller_commit_or_push_tool_present" not in result.stdout


def test_a7_runtime_negative_tool_removed(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    adapter = _a7_runtime_adapter(runtime_root)

    _a7_replace_runtime_tool_name(adapter, "check_task", "check_task_removed")

    assert "check_task" not in _a7_registered_tool_names(adapter)
    assert "check_task_removed" in _a7_registered_tool_names(adapter)

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert "a7_pipeline_controller_tool_roster_mismatch" in result.stdout


def test_a7_runtime_negative_eighth_tool(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    adapter = _a7_runtime_adapter(runtime_root)

    with open(adapter, "a", encoding="utf-8") as f:
        f.write(
            '\n\n@mcp_server.tool(name="a7_eighth_probe")\n'
            'def a7_eighth_probe():\n'
            '    return {"ok": True}\n'
        )

    assert _a7_registered_tool_names(adapter) == (
        A7_EXPECTED_RUNTIME_TOOLS | {"a7_eighth_probe"}
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert "a7_pipeline_controller_tool_roster_mismatch" in result.stdout


def test_a7_runtime_negative_commit_push_facade(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    adapter = _a7_runtime_adapter(runtime_root)

    with open(adapter, "a", encoding="utf-8") as f:
        f.write(
            '\n\n@mcp_server.tool(name="push_changes")\n'
            'def push_changes():\n'
            '    return {"forbidden": True}\n'
        )

    assert "push_changes" in _a7_registered_tool_names(adapter)

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert "a7_pipeline_controller_tool_roster_mismatch" in result.stdout
    assert (
        "a7_pipeline_controller_commit_or_push_tool_present:"
        "installed runtime:push_changes"
        in result.stdout
    )


def test_a7_registration_default_and_unrelated_mcp_pass(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    default_cfg = _a7_default_config(runtime_root)

    config = default_cfg.read_text(encoding="utf-8")
    assert "mcp_servers:" in config
    assert "review_archive_bridge:" in config
    assert "pipeline_controller:" in config

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 0
    assert "default/global profile registers pipeline_controller MCP" in result.stdout
    assert "a7_pipeline_controller_registration_missing" not in result.stdout
    assert "a7_pipeline_controller_registration_forbidden" not in result.stdout


def test_a7_registration_missing_from_default_mcp_servers_fails(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    default_cfg = _a7_default_config(runtime_root)

    default_cfg.write_text(
        "mcp_servers:\n"
        "  review_archive_bridge:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_registration_missing:default:{default_cfg}"
        in result.stdout
    )


def test_a7_registration_top_level_key_outside_mcp_servers_does_not_count(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    default_cfg = _a7_default_config(runtime_root)

    default_cfg.write_text(
        "pipeline_controller:\n"
        "  enabled: true\n"
        "mcp_servers:\n"
        "  review_archive_bridge:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_registration_missing:default:{default_cfg}"
        in result.stdout
    )


def test_a7_registration_nested_key_does_not_count(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    default_cfg = _a7_default_config(runtime_root)

    default_cfg.write_text(
        "mcp_servers:\n"
        "  review_archive_bridge:\n"
        "    enabled: true\n"
        "  wrapper:\n"
        "    pipeline_controller:\n"
        "      enabled: true\n",
        encoding="utf-8",
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_registration_missing:default:{default_cfg}"
        in result.stdout
    )


@pytest.mark.parametrize(
    "profile_name",
    ["reviewer", "coder", "coder-claude", "planner-codex", "sysadmin"],
)
def test_a7_registration_forbidden_profile_fails(tmp_path, profile_name):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    profile_cfg = _a7_profile_config(runtime_root, profile_name)

    original = profile_cfg.read_text(encoding="utf-8")
    profile_cfg.write_text(
        original.rstrip()
        + "\n"
        + "mcp_servers:\n"
        + "  harmless_other_mcp:\n"
        + "    enabled: true\n"
        + "  pipeline_controller:\n"
        + "    enabled: true\n",
        encoding="utf-8",
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_registration_forbidden:"
        f"{profile_name}:{profile_cfg}"
        in result.stdout
    )


def test_a7_unrelated_mcp_children_remain_accepted(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    default_cfg = _a7_default_config(runtime_root)

    default_cfg.write_text(
        "mcp_servers:\n"
        "  review_archive_bridge:\n"
        "    enabled: true\n"
        "  pipeline_bridge:\n"
        "    enabled: true\n"
        "  another_unrelated_mcp:\n"
        "    enabled: true\n"
        "  pipeline_controller:\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    result = _run(
        ["--runtime"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 0
    assert "default/global profile registers pipeline_controller MCP" in result.stdout
    assert "a7_pipeline_controller_registration_missing" not in result.stdout


def test_a7_repo_only_does_not_require_pipeline_controller_runtime(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    absent_runtime = tmp_path / "a7_runtime_intentionally_absent"

    assert not absent_runtime.exists()

    result = _run(
        ["--repo-only"],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(absent_runtime)},
    )

    assert result.returncode == 0
    assert "pipeline-controller MCP source roster" in result.stdout
    assert "has exactly 7 registered tools" in result.stdout
    assert "a7_pipeline_controller_dedicated_runtime_missing_or_empty" not in result.stdout
    assert "a7_pipeline_controller_binary_missing_or_empty" not in result.stdout
    assert "a7_pipeline_controller_registration_missing" not in result.stdout


@pytest.mark.parametrize("mode", ["--runtime", "--all"])
def test_a7_runtime_and_all_fail_same_missing_adapter_marker(tmp_path, mode):
    fixture = _build_repo_fixture(tmp_path)
    runtime_root = _build_runtime_fixture(tmp_path, compliant=True)
    dedicated = _a7_runtime_adapter(runtime_root)

    dedicated.unlink()

    result = _run(
        [mode],
        cwd=str(fixture),
        env_overrides={"AUDIT_RUNTIME_ROOT": str(runtime_root)},
    )

    assert result.returncode == 1
    assert (
        f"a7_pipeline_controller_dedicated_runtime_missing_or_empty:{dedicated}"
        in result.stdout
    )


# 15) A8: default-profile Kanban lifecycle ownership invariant audit
# (INV-01..INV-15), verified hermetically against templates/default-SOUL.md,
# templates/hermes-repo-contract.md, and templates/pipeline_controller_server.py.
# One shared positive test proves the unmodified repository passes all 15
# invariants plus the contract cross-check; each negative test corrupts
# exactly one invariant on a copy of the current, real repository file and
# asserts the audit fails closed with the specific stable finding.

A8_SECTION_HEADER = "===== A8: default profile Kanban lifecycle ownership (15 invariants) ====="

# (invariant_id, report_name, exact marker line in templates/default-SOUL.md)
A8_SOUL_MARKER_CASES = [
    ("INV-01", "default-registers-planner_bridge",
     "A8-MANDATE-01: default profile must register planner_bridge (planning only)."),
    ("INV-02", "default-registers-pipeline_controller",
     "A8-MANDATE-02: default profile must register pipeline_controller (sole Kanban lifecycle interface)."),
    ("INV-02", "default-registers-pipeline_controller",
     "A8-OWNERSHIP-01: default is the sole owner of the Kanban graph (create, check, wait, review, correction, archive, READY_TO_COMMIT)."),
    ("INV-03", "default-prohibits-pipeline_bridge",
     "A8-PROHIBIT-01: default profile MUST NOT register or use pipeline_bridge directly."),
    ("INV-04", "default-prohibits-review_archive_bridge",
     "A8-PROHIBIT-02: default profile MUST NOT register or use review_archive_bridge directly."),
    ("INV-05", "reviewer-role-boundary",
     "A8-ROLES-01: reviewer: read-only, uses review_bridge."),
    ("INV-06", "coder-claude-role-boundary",
     "A8-ROLES-02: coder-claude: implementation, uses claude_bridge."),
    ("INV-07", "pipeline_controller-forbidden-profiles",
     "A8-PROHIBIT-03: pipeline_controller is forbidden in profiles: reviewer, coder, coder-claude, planner-codex, sysadmin."),
    ("INV-10", "controller-lifecycle",
     "A8-OWNERSHIP-01: default is the sole owner of the Kanban graph (create, check, wait, review, correction, archive, READY_TO_COMMIT)."),
    ("INV-11", "no-direct-pipeline_bridge-instruction",
     "A8-PROHIBIT-01: default profile MUST NOT register or use pipeline_bridge directly."),
    ("INV-12", "no-direct-review_archive_bridge-instruction",
     "A8-PROHIBIT-02: default profile MUST NOT register or use review_archive_bridge directly."),
    ("INV-13", "planner_bridge-planning-only",
     "A8-PLANNER-01: planner_bridge is planning-only (no lifecycle task creation)."),
    ("INV-14", "dual-approval-commit-push",
     "A8-APPROVAL-01: commit approval requires one explicit human authorization."),
    ("INV-14", "dual-approval-commit-push",
     "A8-APPROVAL-02: push approval requires a separate, explicit human authorization (dual-approval: commit and push are never bundled)."),
]

A8_ALL_INV_IDS = [
    "INV-01", "INV-02", "INV-03", "INV-04", "INV-05", "INV-06", "INV-07",
    "INV-08", "INV-09", "INV-10", "INV-11", "INV-12", "INV-13", "INV-14", "INV-15",
]

A8_CONTRACT_STRINGS = [
    "The default profile must not register or use `pipeline_bridge` directly, "
    "and must not register or use `review_archive_bridge` directly.",
    "`pipeline_controller` exposes exactly seven tools: `check_task`, "
    "`create_implementation`, `create_review`, `create_correction`, `wait_task`, "
    "`archive_review`, `ready_to_commit`.",
    "No MCP tool defined in this repository (`planner_bridge`, `pipeline_bridge`, "
    "`review_archive_bridge`, `pipeline_controller`, `review_bridge`, `claude_bridge`) "
    "is named `commit*`, `push*`, or `staging*`.",
    "`planner_bridge` remains planning-only: it never creates, checks, waits on, "
    "reviews, corrects, archives, or attests readiness for any Kanban task.",
    "`pipeline_controller` itself remains forbidden in the `reviewer`, `coder`, "
    "`coder-claude`, `planner-codex`, and `sysadmin` profiles.",
    "Commit approval requires one explicit human authorization; push approval "
    "requires a second, separate explicit human authorization.",
]


def _default_soul_file(fixture):
    return fixture / "templates" / "default-SOUL.md"


def _hermes_contract_file(fixture):
    return fixture / "templates" / "hermes-repo-contract.md"


def _pipeline_controller_mcp_file(fixture):
    return fixture / "templates" / "pipeline_controller_server.py"


def test_a8_compliant_fixture_passes_all_fifteen_invariants(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "PASS" in result.stdout
    assert A8_SECTION_HEADER in result.stdout
    for inv_id in A8_ALL_INV_IDS:
        assert f"{inv_id} [" in result.stdout
    # every A8 invariant line must report PASS, never FAIL, on the compliant fixture
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(f"{inv_id} [") for inv_id in A8_ALL_INV_IDS):
            assert stripped.endswith("PASS"), f"unexpected A8 finding on compliant fixture: {stripped}"
    for cstr in A8_CONTRACT_STRINGS:
        assert f"A8 contract (hermes-repo-contract.md) contains: {cstr}" in result.stdout


def test_a8_missing_soul_template_fails_all_fifteen_invariants(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _default_soul_file(fixture).unlink()
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    for inv_id in A8_ALL_INV_IDS:
        assert f"{inv_id} [default-SOUL-template] FAIL:" in result.stdout


@pytest.mark.parametrize("inv_id,name,marker_line", A8_SOUL_MARKER_CASES)
def test_a8_soul_negative_missing_marker_line(tmp_path, inv_id, name, marker_line):
    fixture = _build_repo_fixture(tmp_path)
    soul_file = _default_soul_file(fixture)
    _mutate_file(soul_file, marker_line, marker_line + " MUTATED")
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert f"{inv_id} [{name}] FAIL:" in result.stdout


def test_a8_inv08_negative_controller_tool_roster_mismatch(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    controller_file = _pipeline_controller_mcp_file(fixture)
    _mutate_file(controller_file, 'name="ready_to_commit"', 'name="ready_to_commit_v2"')
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "INV-08 [exactly-seven-controller-tools] FAIL:" in result.stdout
    assert "a8_tool_roster_mismatch" in result.stdout
    assert "missing=ready_to_commit" in result.stdout
    assert "unexpected=ready_to_commit_v2" in result.stdout


def test_a8_inv09_negative_forbidden_facade_name_detected(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    controller_file = _pipeline_controller_mcp_file(fixture)
    original = controller_file.read_text(encoding="utf-8")
    controller_file.write_text(
        original
        + "\n\n@mcp_server.tool(name=\"commit_changes\")\n"
        + "def _tool_commit_changes():\n"
        + "    return {}\n",
        encoding="utf-8",
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "INV-09 [no-commit-push-staging-facades] FAIL:" in result.stdout
    assert "a8_forbidden_facade_name" in result.stdout
    assert "commit_changes" in result.stdout


def test_a8_inv15_negative_live_mutation_token_detected(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    script_file = fixture / "scripts" / "audit-hermes-pipeline-hardening.sh"
    original = script_file.read_text(encoding="utf-8")
    script_file.write_text(
        original + "\n# regression probe (never executed): systemctl restart hermes-gateway.service\n",
        encoding="utf-8",
    )
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "INV-15 [no-live-runtime-mutation] FAIL:" in result.stdout
    assert r"a8_inv15_live_mutation_token_present:\bsystemctl\b" in result.stdout


def test_a8_inv15_positive_absent_on_compliant_fixture(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 0
    assert "INV-15 [no-live-runtime-mutation] PASS" in result.stdout
    assert "a8_inv15_live_mutation_token_present" not in result.stdout


@pytest.mark.parametrize("contract_string", A8_CONTRACT_STRINGS)
def test_a8_contract_cross_check_negative(tmp_path, contract_string):
    fixture = _build_repo_fixture(tmp_path)
    contract_file = _hermes_contract_file(fixture)
    _mutate_file(contract_file, contract_string, contract_string + " MUTATED")
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert (
        f"A8 contract (hermes-repo-contract.md) missing required A8 policy text: {contract_string}"
        in result.stdout
    )


def test_a8_contract_missing_file_fails_cross_check(tmp_path):
    fixture = _build_repo_fixture(tmp_path)
    _hermes_contract_file(fixture).unlink()
    result = _run(["--repo-only"], cwd=str(fixture))
    assert result.returncode == 1
    assert "A8 contract cross-check:" in result.stdout
    assert "missing or empty" in result.stdout


def test_a8_soul_override_path_env_var_used_for_hermetic_negative(tmp_path):
    """SOUL_TEMPLATE_PATH lets a caller point run_a8_invariant_audit at an
    arbitrary fixture file without touching templates/default-SOUL.md at all,
    proving the audit's A8 section is driven purely by the given path."""
    fixture = _build_repo_fixture(tmp_path)
    broken_soul = tmp_path / "broken-SOUL.md"
    broken_soul.write_text("not a real SOUL template\n", encoding="utf-8")
    result = _run(
        ["--repo-only"],
        cwd=str(fixture),
        env_overrides={"SOUL_TEMPLATE_PATH": str(broken_soul)},
    )
    assert result.returncode == 1
    assert f"INV-01 [default-registers-planner_bridge] FAIL: {broken_soul} missing exact A8-MANDATE-01 marker line" in result.stdout
    assert f"INV-05 [reviewer-role-boundary] FAIL: {broken_soul} missing exact A8-ROLES-01 marker line" in result.stdout
    # invariants independent of SOUL content (controller roster, facade names,
    # live-mutation token scan) are unaffected by this override and still pass
    assert "INV-08 [exactly-seven-controller-tools] PASS" in result.stdout
    assert "INV-09 [no-commit-push-staging-facades] PASS" in result.stdout
    assert "INV-15 [no-live-runtime-mutation] PASS" in result.stdout
