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
    os.path.join("templates", "review_archive_bridge.py"),
    os.path.join("tests", "test_review_archive_bridge_template.py"),
    os.path.join("scripts", "hermes-pipeline-controller.py"),
    os.path.join("tests", "test_hermes_pipeline_controller.py"),
    ".gitignore",
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
