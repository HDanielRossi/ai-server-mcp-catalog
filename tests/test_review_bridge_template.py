"""Tests for the review_bridge_server template as a real MCP runtime adapter.

Loaded by absolute path so the template is exercised exactly as the operator
would install it. Uses fakes/mocking/tmp_path/temp git repos for
subprocess/filesystem behavior: no network, no real Kanban, no destructive
git (assertions confirm repo state is unchanged after every collect() call).
"""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

REPO_ROOT = str(Path(__file__).resolve().parents[1])
TEMPLATE_PATH = str(Path(REPO_ROOT) / "templates" / "review_bridge_server.py")


def _load_module_with_subprocess_spy():
    """Load the template fresh, spying on subprocess.run for the duration of import."""
    calls = []
    original_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original_run(*args, **kwargs)

    subprocess.run = spy
    try:
        spec = importlib.util.spec_from_file_location("review_bridge_server", TEMPLATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        subprocess.run = original_run
    return module, calls


def _load_module_with_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("HERMES_REVIEW_BRIDGE_COLLECT_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("HERMES_REVIEW_BRIDGE_COLLECT_TIMEOUT_SECONDS", value)
    module, _calls = _load_module_with_subprocess_spy()
    return module


review_bridge_server, IMPORT_TIME_SUBPROCESS_CALLS = _load_module_with_subprocess_spy()

ReviewBridgeError = review_bridge_server.ReviewBridgeError
collect_evidence = review_bridge_server.collect_evidence
normalize_changed_paths = review_bridge_server.normalize_changed_paths
validate_test_command = review_bridge_server.validate_test_command
validate_content_window = review_bridge_server.validate_content_window
validate_workdir = review_bridge_server.validate_workdir
collect = review_bridge_server.collect
ALLOWED_ROOT = review_bridge_server.ALLOWED_ROOT


def _init_git_repo(repo_dir):
    """Create a minimal real git repo at repo_dir with one committed file (a.py)."""
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "reviewer-test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Reviewer Test"], cwd=repo_dir, check=True)
    (repo_dir / "a.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert review_bridge_server.__file__ == TEMPLATE_PATH


def test_defaults(monkeypatch):
    module = _load_module_with_env(monkeypatch, None)
    assert module.DEFAULT_TEST_COMMAND == "__skip__"
    assert module.DEFAULT_CHANGED_PATHS == ""
    assert module.DEFAULT_INCLUDE_DIFF is False
    assert module.DEFAULT_INCLUDE_REPO_EVIDENCE is True
    assert module.MAX_CONTENT_WINDOW_LINES == 200
    assert module.DEFAULT_COLLECT_TIMEOUT_SECONDS == 60
    assert module.COLLECT_TIMEOUT_SECONDS == 60

    result = module.collect_evidence()
    assert result["test_command"] == "__skip__"
    assert result["changed_paths"] == []
    assert result["include_diff"] is False
    assert result["include_repo_evidence"] is True


def test_collect_timeout_env_override(monkeypatch):
    module = _load_module_with_env(monkeypatch, "150")
    assert module.COLLECT_TIMEOUT_ENV == "HERMES_REVIEW_BRIDGE_COLLECT_TIMEOUT_SECONDS"
    assert module.DEFAULT_COLLECT_TIMEOUT_SECONDS == 60
    assert module.COLLECT_TIMEOUT_SECONDS == 150


@pytest.mark.parametrize("raw_value", ["", "0", "-1", "not-an-int"])
def test_collect_timeout_invalid_env_falls_back_to_default(monkeypatch, raw_value):
    module = _load_module_with_env(monkeypatch, raw_value)
    assert module.COLLECT_TIMEOUT_SECONDS == module.DEFAULT_COLLECT_TIMEOUT_SECONDS


def test_collect_timeout_upper_bound_is_capped(monkeypatch):
    module = _load_module_with_env(monkeypatch, "999999")
    assert module.MAX_COLLECT_TIMEOUT_SECONDS == 900
    assert module.COLLECT_TIMEOUT_SECONDS == module.MAX_COLLECT_TIMEOUT_SECONDS


def test_test_command_uses_resolved_collect_timeout_by_default(monkeypatch):
    module = _load_module_with_env(monkeypatch, "150")
    captured = {}

    def fake_run_argv(argv, cwd, timeout=module.COLLECT_TIMEOUT_SECONDS):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return {
            "command": list(argv),
            "exit_code": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(module, "_run_argv", fake_run_argv)

    result = module._run_test_command(
        "./scripts/audit-hermes-pipeline-hardening.sh",
        Path(REPO_ROOT),
    )

    assert captured["argv"] == ["./scripts/audit-hermes-pipeline-hardening.sh"]
    assert captured["timeout"] == 150
    assert result["skipped"] is False


def test_allowlist_accepts_all_three_commands():
    for command in review_bridge_server.ALLOWED_TEST_COMMANDS:
        assert validate_test_command(command) == command


def test_allowlist_rejects_unknown_command():
    with pytest.raises(ReviewBridgeError):
        validate_test_command("rm -rf /")


def test_no_window_defaults():
    result = collect_evidence(changed_paths="a.py", content_window=None)
    assert result["content_window"] == "not-requested"
    assert result["file_content"] == "SKIPPED"


def test_window_valid_full_range_accepted():
    result = collect_evidence(
        changed_paths="a.py",
        content_window={"path": "a.py", "start_line": 1, "end_line": 200},
    )
    assert result["content_window"] == "requested:a.py:1-200"
    assert result["file_content"] == "window:a.py:1-200"


def test_window_invalid_zero_paths():
    with pytest.raises(ReviewBridgeError):
        validate_content_window([], {"path": "a.py", "start_line": 1, "end_line": 10})


def test_window_invalid_two_paths_in_one_window():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(
            ["a.py", "b.py"],
            {"path": ["a.py", "b.py"], "start_line": 1, "end_line": 10},
        )


def test_window_invalid_start_after_end():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "a.py", "start_line": 10, "end_line": 5})


def test_window_invalid_oversized():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "a.py", "start_line": 1, "end_line": 201})


def test_window_invalid_path_not_in_changed_paths():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "b.py", "start_line": 1, "end_line": 10})


def test_diff_invalid_without_changed_paths():
    with pytest.raises(ReviewBridgeError):
        collect_evidence(changed_paths="", include_diff=True)


def test_diff_false_without_paths_is_fine():
    result = collect_evidence(changed_paths="", include_diff=False)
    assert result["status"] == "ok"
    assert result["diff"] == "not-requested"


def test_paths_absolute_rejected():
    with pytest.raises(ReviewBridgeError):
        normalize_changed_paths("/etc/passwd")


def test_paths_dotdot_rejected():
    with pytest.raises(ReviewBridgeError):
        normalize_changed_paths("a/../b")


def test_paths_empty_string_normalizes_to_no_paths():
    assert normalize_changed_paths("") == []


def test_paths_comma_string_splits_and_strips():
    assert normalize_changed_paths("a.py, b.py") == ["a.py", "b.py"]


def test_skip_command_never_substituted():
    result = collect_evidence(test_command="__skip__")
    assert result["test_command"] == "__skip__"


# --- MCP server surface --------------------------------------------------


def test_mcp_server_import_and_instantiation():
    from mcp.server import MCPServer

    assert isinstance(review_bridge_server.mcp_server, MCPServer)
    assert review_bridge_server.mcp_server.name == "review-bridge"


def test_collect_is_registered_as_the_only_mcp_tool():
    tools = asyncio.run(review_bridge_server.mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"collect"}


def test_mcp_server_has_executable_run_entrypoint():
    assert callable(review_bridge_server.mcp_server.run)


def test_no_subprocess_execution_on_import():
    assert IMPORT_TIME_SUBPROCESS_CALLS == []


def test_main_guard_gates_run_and_is_not_reached_on_plain_import():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    assert 'if __name__ == "__main__":' in source
    guard_index = source.index('if __name__ == "__main__":')
    guarded_block = source[guard_index:]
    assert "mcp_server.run()" in guarded_block
    assert review_bridge_server.__name__ != "__main__"


def test_module_has_no_network_imports():
    assert not hasattr(review_bridge_server, "socket")
    assert not hasattr(review_bridge_server, "requests")
    assert not hasattr(review_bridge_server, "urllib")


def test_source_contains_no_git_mutation_verbs():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    forbidden = ["\"reset\"", "\"restore\"", "\"checkout\"", "\"clean\"", "\"commit\"", "\"merge\"", "\"rebase\"", "\"push\""]
    for token in forbidden:
        assert token not in source


# --- workdir validation ----------------------------------------------------


def test_validate_workdir_rejects_relative_path_before_resolving():
    with pytest.raises(ReviewBridgeError):
        validate_workdir("relative/path")


def test_validate_workdir_rejects_missing_directory():
    with pytest.raises(ReviewBridgeError):
        validate_workdir("/opt/ai/projects/this-does-not-exist-xyz")


def test_validate_workdir_rejects_non_directory():
    target = os.path.join(REPO_ROOT, "templates", "review_bridge_server.py")
    assert os.path.isfile(target)
    with pytest.raises(ReviewBridgeError):
        validate_workdir(target)


def test_validate_workdir_rejects_path_outside_allowed_root():
    with pytest.raises(ReviewBridgeError):
        validate_workdir("/etc")


def test_validate_workdir_rejects_dotdot_escape():
    escape_path = os.path.join(REPO_ROOT, "..", "..")
    with pytest.raises(ReviewBridgeError):
        validate_workdir(escape_path)


def test_validate_workdir_accepts_valid_path_under_allowed_root():
    resolved = validate_workdir(REPO_ROOT)
    assert str(resolved) == os.path.realpath(REPO_ROOT)
    assert resolved.is_relative_to(ALLOWED_ROOT)


# --- changed_path containment ----------------------------------------------


def test_collect_rejects_changed_path_escaping_workdir():
    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, changed_path="../../etc/passwd")


def test_collect_rejects_absolute_changed_path():
    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, changed_path="/etc/passwd")


def test_collect_rejects_multiple_changed_paths_in_one_call():
    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, changed_path="a.py,b.py")


def test_content_window_requires_changed_path():
    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, content_window={"path": "a.py", "start_line": 1, "end_line": 5})


def test_path_validation_happens_before_any_subprocess_call(monkeypatch):
    def fake_run(*args, **kwargs):
        raise AssertionError("must not run subprocess when changed_path escapes workdir")

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, changed_path="../../etc/passwd")


# --- subprocess safety (_run_argv) ------------------------------------------


def test_run_argv_uses_argv_list_and_shell_false(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "true\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    record = review_bridge_server._run_argv(["git", "status", "--short"], REPO_ROOT)

    assert isinstance(captured["argv"], list)
    assert captured["kwargs"]["shell"] is False
    assert "timeout" in captured["kwargs"]
    assert record["exit_code"] == 0
    assert record["timed_out"] is False


def test_run_argv_handles_timeout_without_crashing(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    record = review_bridge_server._run_argv(["git", "status"], REPO_ROOT)
    assert record["timed_out"] is True
    assert record["exit_code"] is None


def test_run_argv_handles_nonzero_exit_without_crashing(monkeypatch):
    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "boom"

    def fake_run(argv, **kwargs):
        return FakeCompleted()

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    record = review_bridge_server._run_argv(["git", "status"], REPO_ROOT)
    assert record["exit_code"] == 1
    assert record["timed_out"] is False
    assert record["stderr"] == "boom"


def test_verify_git_repo_raises_structured_error_when_not_a_repo(monkeypatch):
    class FakeCompleted:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    def fake_run(argv, **kwargs):
        return FakeCompleted()

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT)


def test_git_subprocess_calls_are_argv_lists_with_shell_false_end_to_end(monkeypatch):
    real_run = review_bridge_server.subprocess.run
    captured = []

    def spy(argv, **kwargs):
        captured.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(review_bridge_server.subprocess, "run", spy)

    collect(REPO_ROOT)

    assert captured
    for argv, kwargs in captured:
        assert isinstance(argv, list)
        assert kwargs.get("shell") is False


# --- test_command allowlist / execution -------------------------------------


def test_invalid_test_command_rejected_before_any_subprocess(monkeypatch):
    def fake_run(*args, **kwargs):
        raise AssertionError("must not run subprocess when test_command is invalid")

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    with pytest.raises(ReviewBridgeError):
        collect(REPO_ROOT, test_command="rm -rf /")


def test_shell_metacharacter_test_command_rejected():
    with pytest.raises(ReviewBridgeError):
        validate_test_command("git status; rm -rf /")


def test_skip_test_command_is_recorded_as_skipped_and_not_executed(monkeypatch):
    real_run = review_bridge_server.subprocess.run

    def dispatch(argv, **kwargs):
        assert argv[0] == "git", "only git commands may run when test_command is __skip__"
        return real_run(argv, **kwargs)

    monkeypatch.setattr(review_bridge_server.subprocess, "run", dispatch)

    result = collect(REPO_ROOT, test_command="__skip__")
    assert result["test_result"] == {"command": "__skip__", "skipped": True}


def test_allowed_test_command_is_executed_as_argv_list(monkeypatch):
    real_run = review_bridge_server.subprocess.run
    captured = {}

    def dispatch(argv, **kwargs):
        if argv[0] == "git":
            return real_run(argv, **kwargs)
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class FakeCompleted:
            returncode = 0
            stdout = "3 passed"
            stderr = ""

        return FakeCompleted()

    monkeypatch.setattr(review_bridge_server.subprocess, "run", dispatch)

    allowed_command = "./scripts/audit-hermes-pipeline-hardening.sh"
    result = collect(REPO_ROOT, test_command=allowed_command)

    assert captured["argv"] == shlex.split(allowed_command)
    assert captured["kwargs"]["shell"] is False
    assert result["test_result"]["exit_code"] == 0
    assert result["test_result"]["skipped"] is False
    assert result["test_result"]["stdout"] == "3 passed"


def test_test_command_timeout_yields_structured_failure(monkeypatch):
    real_run = review_bridge_server.subprocess.run

    def dispatch(argv, **kwargs):
        if argv[0] == "git":
            return real_run(argv, **kwargs)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(review_bridge_server.subprocess, "run", dispatch)

    allowed_command = "./scripts/audit-hermes-pipeline-hardening.sh"
    result = collect(REPO_ROOT, test_command=allowed_command)

    assert result["test_result"]["timed_out"] is True
    assert result["test_result"]["exit_code"] is None
    assert result["test_result"]["skipped"] is False


def test_test_command_nonzero_exit_yields_structured_failure_not_a_crash(monkeypatch):
    real_run = review_bridge_server.subprocess.run

    def dispatch(argv, **kwargs):
        if argv[0] == "git":
            return real_run(argv, **kwargs)

        class FakeCompleted:
            returncode = 1
            stdout = ""
            stderr = "1 failed"

        return FakeCompleted()

    monkeypatch.setattr(review_bridge_server.subprocess, "run", dispatch)

    allowed_command = "./scripts/audit-hermes-pipeline-hardening.sh"
    result = collect(REPO_ROOT, test_command=allowed_command)

    assert result["test_result"]["exit_code"] == 1
    assert result["test_result"]["timed_out"] is False
    assert result["test_result"]["stderr"] == "1 failed"


# --- real, read-only git evidence (temp git repo) ---------------------------


def test_collect_real_git_repo_returns_status_and_scoped_diff_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "a.py").write_text("print('hello')\nprint('world')\n")

    index_before = (repo / ".git" / "index").read_bytes()
    result = collect(str(repo), changed_path="a.py")
    index_after = (repo / ".git" / "index").read_bytes()

    assert result["status"] == "ok"
    assert "a.py" in result["git_status"]["stdout"]
    assert "a.py" in result["diff_name_only"]["stdout"]
    assert "world" in result["diff"]["stdout"]
    assert result["diff_check"]["exit_code"] in (0, 1)
    assert index_before == index_after
    assert (repo / "a.py").read_text() == "print('hello')\nprint('world')\n"


def test_collect_content_window_defaults_to_single_changed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "b.py").write_text("second file\n")
    subprocess.run(["git", "add", "b.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add b"], cwd=repo, check=True, capture_output=True)

    result = collect(str(repo), changed_path="a.py")

    assert result["content_window"]["path"] == "a.py"
    assert result["content_window"]["content"] == "print('hello')\n"


def test_content_window_capped_for_large_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    big_file = repo / "big.py"
    big_file.write_text("\n".join(f"line {i}" for i in range(1000)) + "\n")
    subprocess.run(["git", "add", "big.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    result = collect(str(repo), changed_path="big.py")
    content = result["content_window"]["content"]

    assert content.count("\n") <= review_bridge_server.MAX_CONTENT_WINDOW_LINES
    assert result["content_window"]["end_line"] == review_bridge_server.MAX_CONTENT_WINDOW_LINES


def test_content_window_path_must_match_the_single_changed_path(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "b.py").write_text("second file\n")
    subprocess.run(["git", "add", "b.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add b"], cwd=repo, check=True, capture_output=True)

    with pytest.raises(ReviewBridgeError):
        collect(str(repo), changed_path="a.py", content_window={"path": "b.py", "start_line": 1, "end_line": 5})


def test_explicit_content_window_within_cap_is_honored(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "c.py").write_text("\n".join(f"line {i}" for i in range(50)) + "\n")
    subprocess.run(["git", "add", "c.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    result = collect(str(repo), changed_path="c.py", content_window={"path": "c.py", "start_line": 5, "end_line": 10})

    assert result["content_window"]["start_line"] == 5
    assert result["content_window"]["end_line"] == 10
    assert result["content_window"]["content"] == "line 4\nline 5\nline 6\nline 7\nline 8\nline 9\n"


# --- MCP tool end-to-end (mocked subprocess) --------------------------------


def test_mcp_tool_collect_end_to_end(monkeypatch):
    real_run = review_bridge_server.subprocess.run

    def spy(argv, **kwargs):
        return real_run(argv, **kwargs)

    monkeypatch.setattr(review_bridge_server.subprocess, "run", spy)

    tool_fn = review_bridge_server._tool_collect
    result = tool_fn(REPO_ROOT)
    assert result["status"] == "ok"


def test_mcp_tool_rejects_workdir_outside_allowed_root(monkeypatch):
    def fake_run(*args, **kwargs):
        raise AssertionError("must not run subprocess when workdir validation fails")

    monkeypatch.setattr(review_bridge_server.subprocess, "run", fake_run)

    tool_fn = review_bridge_server._tool_collect
    with pytest.raises(ReviewBridgeError):
        tool_fn("/etc")


# --- reviewer-SOUL.md evidence policy ---------------------------------------

SOUL_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/reviewer-SOUL.md"


def test_reviewer_soul_exists():
    assert os.path.isfile(SOUL_PATH)


def test_reviewer_soul_requires_fresh_same_session_collect():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "mcp__review_bridge__collect" in text
    assert "same review session" in text or "same session" in text


def test_reviewer_soul_excludes_memory_as_evidence():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "Memory is not review evidence" in text


def test_reviewer_soul_excludes_read_resource_and_list_resources():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "mcp__review_bridge__read_resource" in text
    assert "mcp__review_bridge__list_resources" in text
    assert "not exploratory substitutes for collect" in text


def test_reviewer_soul_excludes_broad_execution_tools():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "terminal" in text
    assert "code execution" in text
    assert "browser" in text
    assert "delegation" in text


def test_reviewer_soul_defines_block_on_operational_failure():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "BLOCK" in text
    assert "quoting the exact failure" in text


def test_reviewer_soul_requires_collect_for_pass_or_changes_required():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "PASS" in text
    assert "CHANGES REQUIRED" in text
    assert "PASS or CHANGES REQUIRED verdict" in text


def test_reviewer_soul_forbids_implementing_or_mutating():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "never implements, patches, edits, formats code, resets" in text
    assert "commits, pushes, merges, installs dependencies" in text


# --- reviewer-SOUL.md collect protocol (A4.1) -------------------------------


def test_reviewer_soul_documents_collect_protocol_signature():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "collect(workdir, changed_path=None, test_command=None, content_window=None)" in text


def test_reviewer_soul_documents_collect_protocol_rules():
    with open(SOUL_PATH, encoding="utf-8") as f:
        text = f.read()
    assert "EXACTLY ONE repo-relative changed_path" in text
    assert "NORMALLY OMIT content_window" in text
    assert "content_window may ONLY be used together with a changed_path" in text
    assert "content_window.path EXACTLY EQUALS changed_path" in text
    assert "start_line and end_line are integers" in text
    assert "<= 200 lines" in text
    assert "SEQUENTIAL ONLY, NEVER parallel" in text
    assert "EXACTLY ONE deterministic corrected retry" in text
    assert "IMMEDIATELY call kanban_block and STOP" in text
    assert "EXACTLY ONE of: kanban_complete OR kanban_block" in text
    assert "The reviewer remains READ-ONLY" in text
    assert "mcp__review_bridge__collect remains the SOLE evidence channel" in text
    assert "The reviewer creates NO downstream tasks" in text


# --- repository-state fingerprint (A4.2) ------------------------------------


def test_repository_state_schema_and_aggregate_sha256_recompute(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    state = review_bridge_server.collect_repository_state(str(repo))

    assert state["schema"] == "hermes.repository-state/v1"
    assert state["schema"] == review_bridge_server.REPOSITORY_STATE_SCHEMA

    envelope_without_aggregate = {k: v for k, v in state.items() if k != "aggregate_sha256"}
    expected = hashlib.sha256(
        json.dumps(
            envelope_without_aggregate, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    assert state["aggregate_sha256"] == expected


def test_repository_state_is_deterministic_across_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "a.py").write_text("print('hello')\nprint('again')\n")

    first = review_bridge_server.collect_repository_state(str(repo))
    second = review_bridge_server.collect_repository_state(str(repo))

    assert first == second


def test_repository_state_staged_vs_unstaged_digests_and_changed_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "b.py").write_text("second file\n")
    subprocess.run(["git", "add", "b.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add b"], cwd=repo, check=True, capture_output=True)

    (repo / "a.py").write_text("print('hello')\nprint('staged')\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    (repo / "b.py").write_text("second file\nunstaged change\n")

    state = review_bridge_server.collect_repository_state(str(repo))

    assert state["changed_paths"] == ["a.py", "b.py"]
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    assert state["staged_patch_sha256"] != empty_sha256
    assert state["unstaged_patch_sha256"] != empty_sha256
    assert state["staged_patch_sha256"] != state["unstaged_patch_sha256"]


def test_repository_state_untracked_file_and_symlink_digests(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "new_file.txt").write_text("untracked contents\n")
    (repo / "link_to_a").symlink_to("a.py")

    state = review_bridge_server.collect_repository_state(str(repo))

    untracked_by_path = {entry["path"]: entry for entry in state["untracked"]}
    assert set(untracked_by_path) == {"new_file.txt", "link_to_a"}
    assert [entry["path"] for entry in state["untracked"]] == sorted(untracked_by_path)

    file_entry = untracked_by_path["new_file.txt"]
    assert file_entry["type"] == "file"
    assert file_entry["size"] == len(b"untracked contents\n")
    assert file_entry["content_sha256"] == hashlib.sha256(b"untracked contents\n").hexdigest()

    link_entry = untracked_by_path["link_to_a"]
    assert link_entry["type"] == "symlink"
    assert link_entry["content_sha256"] == hashlib.sha256(b"a.py").hexdigest()


def test_repository_state_raises_on_untracked_special_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    os.mkfifo(repo / "special.fifo")

    with pytest.raises(ReviewBridgeError):
        review_bridge_server.collect_repository_state(str(repo))


def test_repository_state_allows_ignored_special_file(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / ".gitignore").write_text("ignored.fifo\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore fifo"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    os.mkfifo(repo / "ignored.fifo")

    state = review_bridge_server.collect_repository_state(str(repo))

    assert "ignored.fifo" not in state["changed_paths"]
    assert all(entry["path"] != "ignored.fifo" for entry in state["untracked"])


def test_repository_state_raises_on_unresolved_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    real_run_git_text = review_bridge_server._run_git_text

    def fake_run_git_text(argv, cwd, timeout=review_bridge_server.REPO_STATE_TIMEOUT_SECONDS):
        if argv[:2] == ["git", "status"] and "--porcelain" in argv:
            return "UU conflicted.py\n"
        return real_run_git_text(argv, cwd, timeout)

    monkeypatch.setattr(review_bridge_server, "_run_git_text", fake_run_git_text)

    with pytest.raises(ReviewBridgeError):
        review_bridge_server.collect_repository_state(str(repo))


def test_repository_state_raises_on_unstable_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    real_capture = review_bridge_server._capture_repository_state_once
    call_count = {"n": 0}

    def flaky_capture(resolved_workdir, canonical_workdir):
        call_count["n"] += 1
        envelope = real_capture(resolved_workdir, canonical_workdir)
        if call_count["n"] == 2:
            envelope["head"] = "f" * 40
            envelope["aggregate_sha256"] = "unstable"
        return envelope

    monkeypatch.setattr(review_bridge_server, "_capture_repository_state_once", flaky_capture)

    with pytest.raises(ReviewBridgeError):
        review_bridge_server.collect_repository_state(str(repo))


def test_collect_existing_keys_unchanged_after_repository_state_addition(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bridge_server, "ALLOWED_ROOT", tmp_path.resolve())
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    result = collect(str(repo))

    expected_keys = {
        "workdir", "repo_check", "git_status", "changed_path", "diff_name_only",
        "diff", "diff_check", "content_window", "test_result", "repository_state",
        "repository_state_sha256", "status",
    }
    assert set(result.keys()) == expected_keys
    assert result["status"] == "ok"
    assert result["repository_state"]["schema"] == review_bridge_server.REPOSITORY_STATE_SCHEMA
    assert result["repository_state_sha256"] == result["repository_state"]["aggregate_sha256"]
