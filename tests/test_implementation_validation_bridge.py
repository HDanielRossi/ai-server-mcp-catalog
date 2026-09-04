"""Hermetic security and contract tests for the implementation validator."""

import importlib.util
import os
import subprocess
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
    assert argv[:3] == [bridge.PYTHON, "-c", argv[2]]
    assert "py_compile.compile" in argv[2]
    with pytest.raises(bridge.ValidationBridgeError):
        bridge.validate("py_compile", str(worktree), paths=[])


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
