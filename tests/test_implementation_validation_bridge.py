"""Hermetic security and contract tests for the implementation validator."""

import importlib.util
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("implementation_validation", ROOT / "templates" / "implementation_validation_bridge_server.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    (path / ".git").mkdir()
    (path / "app.py").write_text("VALUE = 1\n")
    (path / "tests").mkdir()
    (path / "tests" / "test_ok.py").write_text("assert True\n")
    return path


def test_operation_allowlist_and_fixed_argv():
    assert bridge.build_argv("pytest_full") == [bridge.PYTHON, "-m", "pytest", "-q"]
    assert bridge.build_argv("repository_audit") == ["/bin/bash", bridge.AUDIT_SCRIPT, "--repo-only"]
    assert bridge.build_argv("git_diff_check") == ["/usr/bin/git", "diff", "--check"]
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.build_argv("echo pwned")


@pytest.mark.parametrize("value", ["../tests/test_ok.py", "/etc/passwd", "tests/../x", "tests;id"])
def test_target_paths_reject_unsafe_values(tmp_path, value):
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.validate_paths([value], repo(tmp_path))


def test_targeted_pytest_is_repo_relative_and_test_confined(tmp_path):
    worktree = repo(tmp_path)
    assert bridge.validate_paths(["tests/test_ok.py"], worktree, require_tests=True) == ["tests/test_ok.py"]
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.validate_paths(["pyproject.toml"], worktree, require_tests=True)


def test_symlink_escape_rejected(tmp_path):
    worktree = repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("assert True\n")
    (worktree / "tests" / "escape.py").symlink_to(outside)
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.validate_paths(["tests/escape.py"], worktree, require_tests=True)


def test_paths_required_for_compile_and_compile_is_fixed(tmp_path):
    worktree = repo(tmp_path)
    argv = bridge.build_argv("py_compile", ["tests/test_ok.py"])
    assert argv[:3] == [bridge.PYTHON, "-B", "-c"]
    assert "py_compile.compile" in argv[3]
    assert "TemporaryDirectory" in argv[3]
    assert "dir='/tmp'" in argv[3]
    assert "/dev/null" not in argv[3]
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.validate("py_compile", str(worktree), paths=[])


def test_compile_succeeds_without_writing_bytecode_to_worktree(tmp_path, monkeypatch):
    worktree = repo(tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", tmp_path.resolve())
    result = bridge.validate("py_compile", str(worktree), paths=["tests/test_ok.py"])
    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert not list(worktree.rglob("*.pyc"))


def test_compile_failure_is_reported_without_writing_bytecode_to_worktree(tmp_path, monkeypatch):
    worktree = repo(tmp_path)
    (worktree / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", tmp_path.resolve())
    result = bridge.validate("py_compile", str(worktree), paths=["bad.py"])
    assert result["exit_code"] != 0
    assert "SyntaxError" in result["stderr"]
    assert not list(worktree.rglob("*.pyc"))


def test_qwen_canary_serial_validation_uses_fixed_order_and_stops_on_failure(tmp_path, monkeypatch):
    worktree = repo(tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", tmp_path.resolve())
    calls = []

    def fake_validate(operation, workdir, paths=None, timeout=bridge.DEFAULT_TIMEOUT_SECONDS):
        calls.append((operation, paths or []))
        return {
            "schema": bridge.RESULT_SCHEMA,
            "operation": operation,
            "workdir": workdir,
            "paths": paths or [],
            "exit_code": 1 if operation == "py_compile" else 0,
            "timed_out": False,
        }

    monkeypatch.setattr(bridge, "validate", fake_validate)
    result = bridge.validate_qwen_canary_serial(
        str(worktree), ["app.py", "tests/test_ok.py"], ["tests/test_ok.py"]
    )

    assert calls == [
        ("git_diff_check", []),
        ("py_compile", ["app.py", "tests/test_ok.py"]),
    ]
    assert result["schema"] == bridge.QWEN_CANARY_RESULT_SCHEMA
    assert result["passed"] is False
    assert result["failed_operation"] == "py_compile"
    assert [step["operation"] for step in result["steps"]] == ["git_diff_check", "py_compile"]


def test_qwen_canary_serial_validation_returns_full_passed_envelope(tmp_path, monkeypatch):
    worktree = repo(tmp_path)
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", tmp_path.resolve())

    def fake_validate(operation, workdir, paths=None, timeout=bridge.DEFAULT_TIMEOUT_SECONDS):
        return {
            "schema": bridge.RESULT_SCHEMA,
            "operation": operation,
            "workdir": workdir,
            "paths": paths or [],
            "exit_code": 0,
            "timed_out": False,
        }

    monkeypatch.setattr(bridge, "validate", fake_validate)
    result = bridge.validate_qwen_canary_serial(
        str(worktree), ["app.py", "tests/test_ok.py"], ["tests/test_ok.py"]
    )

    assert result["passed"] is True
    assert result["failed_operation"] is None
    assert [step["operation"] for step in result["steps"]] == [
        "git_diff_check", "py_compile", "pytest_targeted", "pytest_full",
    ]


def test_qwen_canary_serial_validation_rejects_non_python_compile_paths(tmp_path, monkeypatch):
    worktree = repo(tmp_path)
    (worktree / "README.md").write_text("docs\n")
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", tmp_path.resolve())
    with pytest.raises(bridge.ValidationBridgeError, match="must end in .py"):
        bridge.validate_qwen_canary_serial(str(worktree), ["README.md"], ["tests/test_ok.py"])


def test_qwen_only_server_mode_exposes_only_the_qwen_server(monkeypatch):
    called = []
    monkeypatch.setattr(bridge.mcp_server, "run", lambda: called.append("generic"))
    monkeypatch.setattr(bridge.qwen_canary_mcp_server, "run", lambda: called.append("qwen"))
    monkeypatch.setattr(sys, "argv", ["server.py", "--qwen-canary-only"])

    bridge.run_server()

    assert called == ["qwen"]


def test_qwen_serial_tool_declares_native_array_arguments():
    params = inspect.signature(bridge._tool_validate_qwen_canary_serial).parameters
    assert params["workdir"].annotation is str
    assert params["python_paths"].annotation == list[str]
    assert params["test_paths"].annotation == list[str]
    assert params["timeout"].annotation is int


def test_qwen_serial_tool_consumes_its_single_invocation(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge, "_qwen_canary_validation_consumed", False)
    monkeypatch.setattr(
        bridge,
        "validate_qwen_canary_serial",
        lambda *args: calls.append(args) or {"passed": True},
    )

    assert bridge._tool_validate_qwen_canary_serial(
        "/opt/ai/projects/example", ["app.py"], ["tests/test_app.py"]
    ) == {"passed": True}
    with pytest.raises(bridge.ValidationBridgeError, match="only once"):
        bridge._tool_validate_qwen_canary_serial(
            "/opt/ai/projects/example", ["app.py"], ["tests/test_app.py"]
        )
    assert len(calls) == 1


def test_no_caller_command_or_environment_and_read_only_git():
    assert "shell=True" not in (ROOT / "templates" / "implementation_validation_bridge_server.py").read_text()
    assert bridge.build_argv("git_diff_check")[-2:] == ["diff", "--check"]
    assert "commit" not in " ".join(bridge.build_argv("git_diff_check"))


def test_timeout_kills_process_group_and_result_is_structured(monkeypatch, tmp_path):
    worktree = repo(tmp_path)
    class Fake:
        pid = 77
        returncode = -9
        stdout = type("S", (), {"read": lambda self, n: ""})()
        stderr = type("S", (), {"read": lambda self, n: ""})()
        def wait(self, timeout=None):
            if timeout == 1:
                raise subprocess.TimeoutExpired("fixed", timeout)
    killed = []
    monkeypatch.setattr(bridge.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    result = bridge._execute(["/bin/true"], worktree, timeout=1, popen_factory=lambda *a, **k: Fake())
    assert killed == [(77, bridge.signal.SIGKILL)]
    assert result["timed_out"] is True
    assert result["schema"] == bridge.RESULT_SCHEMA
    assert set(("stdout", "stderr", "stdout_truncated", "stderr_truncated")) <= result.keys()


def test_output_is_bounded():
    class Stream:
        def read(self, _n):
            if hasattr(self, "done"):
                return ""
            self.done = True
            return "x" * (bridge.MAX_OUTPUT_CHARS * 2)
    output, truncated = bridge._bounded_reader(Stream(), bridge.MAX_OUTPUT_CHARS)
    assert len(output) == bridge.MAX_OUTPUT_CHARS
    assert truncated is True


def test_profile_fragment_is_coder_only():
    text = (ROOT / "templates" / "coder-claude-implementation-validation.yaml").read_text()
    assert "implementation_validation_bridge" in text
    assert "review_bridge" not in text
    assert "pipeline_controller" not in text
    assert "ready_to_commit" not in text
