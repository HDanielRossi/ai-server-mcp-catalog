"""Tests for the repo-only planner_bridge_server template, loaded by absolute path.

Regression coverage: this template is a REAL installable MCP server (unlike
the pure-helper templates in this directory), so these tests verify the
runtime adapter contract in addition to the run() logic.
"""

import importlib.util
import inspect
import os

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/planner_bridge_server.py"

spec = importlib.util.spec_from_file_location("planner_bridge_server", TEMPLATE_PATH)
planner_bridge_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner_bridge_server)


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert planner_bridge_server.__file__ == TEMPLATE_PATH


def test_importing_module_does_not_start_mcp_stdio():
    # Importing (as done above, at collection time) must not have started a
    # server: it must only happen under the __main__ guard. If import had
    # started stdio, module collection itself would already have hung/blocked
    # before reaching this test, so simply getting here is part of the proof.
    assert planner_bridge_server.mcp is not None


def test_mcp_server_object_exists_with_correct_name():
    from mcp.server import MCPServer

    assert isinstance(planner_bridge_server.mcp, MCPServer)
    assert planner_bridge_server.mcp.name == "planner-bridge"


def test_run_is_registered_as_mcp_tool():
    # @mcp.tool() registers the function with the server; the underlying
    # callable must still be reachable as a plain module attribute for tests
    # and for legacy direct-call usage.
    assert hasattr(planner_bridge_server, "run")
    assert callable(planner_bridge_server.run)


def test_run_signature_is_backward_compatible_with_legacy_two_args():
    sig = inspect.signature(planner_bridge_server.run)
    params = list(sig.parameters.values())
    names = [p.name for p in params]

    assert names[0] == "workdir"
    assert names[1] == "prompt"
    assert "context_files" in names

    context_files_param = sig.parameters["context_files"]
    assert context_files_param.default is None


def test_run_is_callable_with_legacy_workdir_prompt_only(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr(planner_bridge_server.subprocess, "run", fake_run)

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(TEMPLATE_PATH)))
    result = planner_bridge_server.run(repo_dir, "plan something")

    assert "exit_code=0" in result
    assert captured["argv"] == [planner_bridge_server.WRAPPER_PATH, repo_dir, "plan something"]
    assert "--context-file" not in captured["argv"]


def test_run_accepts_optional_context_files(monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "ok"

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return FakeCompleted()

    monkeypatch.setattr(planner_bridge_server.subprocess, "run", fake_run)

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(TEMPLATE_PATH)))
    planner_bridge_server.run(repo_dir, "plan something", context_files=["a.txt", "b/c.txt"])

    assert captured["argv"] == [
        planner_bridge_server.WRAPPER_PATH,
        repo_dir,
        "plan something",
        "--context-file",
        "a.txt",
        "--context-file",
        "b/c.txt",
    ]


def test_context_files_rejects_non_list():
    import pytest

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(TEMPLATE_PATH)))
    with pytest.raises(ValueError):
        planner_bridge_server.run(repo_dir, "plan something", context_files="not-a-list")


def test_context_files_rejects_more_than_twelve_entries_before_subprocess(monkeypatch):
    import pytest

    called = {"count": 0}

    def fake_run(argv, **kwargs):
        called["count"] += 1
        raise AssertionError("subprocess must not run when context_files validation fails")

    monkeypatch.setattr(planner_bridge_server.subprocess, "run", fake_run)

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(TEMPLATE_PATH)))
    too_many = [f"file{i}.txt" for i in range(13)]
    with pytest.raises(ValueError):
        planner_bridge_server.run(repo_dir, "plan something", context_files=too_many)

    assert called["count"] == 0


def test_main_guard_and_mcp_run_exist_in_source():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert 'if __name__ == "__main__":' in source
    assert "mcp.run()" in source


def test_subprocess_invocation_uses_argv_list_not_shell_true():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert "shell=True" not in source
    assert "subprocess.run(" in source


def test_workdir_containment_preserved():
    import pytest

    with pytest.raises(ValueError):
        planner_bridge_server.run("/tmp", "plan something")

def test_relative_workdir_is_rejected_before_subprocess(monkeypatch):
    import pytest

    called = {"count": 0}

    def fake_run(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("subprocess must not run for a relative workdir")

    monkeypatch.setattr(planner_bridge_server.subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="workdir must be absolute"):
        planner_bridge_server.run(".", "plan something")

    assert called["count"] == 0
