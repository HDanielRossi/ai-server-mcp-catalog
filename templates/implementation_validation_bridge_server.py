"""Narrow, read-only validation MCP bridge for implementation workers.

This is a repository template for operator installation.  It exposes one MCP
tool, ``validate``, whose operation is a structured enum; callers never supply
an executable, shell command, environment, or cwd.  It deliberately returns
execution evidence only and has no reviewer, Kanban, archive, or commit
semantics.
"""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from mcp.server import MCPServer


ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
MAX_OUTPUT_CHARS = 16_000
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 900
RESULT_SCHEMA = "hermes.implementation-validation/v1"
PYTHON = "/home/hdgr/.hermes/hermes-agent/venv/bin/python3"
AUDIT_SCRIPT = "./scripts/audit-hermes-pipeline-hardening.sh"

ALLOWED_OPERATIONS = frozenset(
    {"pytest_full", "pytest_targeted", "repository_audit", "git_diff_check", "py_compile"}
)


class ValidationBridgeError(Exception):
    """Raised when a structured validation request is unsafe or invalid."""


def _inside(root, candidate):
    return candidate == root or candidate.startswith(str(root) + os.sep)


def validate_workdir(workdir):
    if not isinstance(workdir, str) or not os.path.isabs(workdir):
        raise ValidationBridgeError("workdir must be an absolute path")
    real = Path(workdir).resolve()
    if real == ALLOWED_ROOT or not _inside(ALLOWED_ROOT, str(real)):
        raise ValidationBridgeError("workdir must be a strict descendant of /opt/ai/projects")
    if not real.is_dir() or not (real / ".git").exists():
        raise ValidationBridgeError("workdir must be an existing git worktree")
    return real


def validate_paths(paths, workdir, *, require_tests=False):
    if not isinstance(paths, list) or not paths:
        raise ValidationBridgeError("paths must be a non-empty list")
    result = []
    for raw in paths:
        if not isinstance(raw, str) or not raw or os.path.isabs(raw) or raw.startswith("~"):
            raise ValidationBridgeError("paths must contain non-empty repo-relative strings")
        parts = raw.replace("\\", "/").split("/")
        if ".." in parts or any(part == "" for part in parts):
            raise ValidationBridgeError("paths must be canonical and must not contain '..'")
        if require_tests and not (raw == "tests" or raw.startswith("tests/")):
            raise ValidationBridgeError("targeted pytest paths must be under tests/")
        if raw.endswith("/"):
            raise ValidationBridgeError("paths must not have a trailing slash")
        target = (workdir / raw).resolve()
        if not _inside(workdir, str(target)):
            raise ValidationBridgeError("path escapes worktree")
        if not target.exists():
            raise ValidationBridgeError("path does not exist")
        result.append(raw)
    return result


def build_argv(operation, paths=None):
    if operation == "pytest_full":
        return [PYTHON, "-m", "pytest", "-q"]
    if operation == "pytest_targeted":
        return [PYTHON, "-m", "pytest", "-q", *paths]
    if operation == "repository_audit":
        return ["/bin/bash", AUDIT_SCRIPT, "--repo-only"]
    if operation == "git_diff_check":
        return ["/usr/bin/git", "diff", "--check"]
    if operation == "py_compile":
        script = (
            "import py_compile,sys; "
            "[py_compile.compile(p, cfile='/dev/null', doraise=True) for p in sys.argv[1:]]"
        )
        return [PYTHON, "-c", script, *paths]
    raise ValidationBridgeError(f"unsupported operation: {operation!r}")


def _bounded_reader(stream, limit):
    chunks = []
    size = 0
    truncated = False
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        if size < limit:
            keep = chunk[: limit - size]
            chunks.append(keep)
            size += len(keep)
            if len(keep) != len(chunk):
                truncated = True
        else:
            truncated = True
    return "".join(chunks), truncated


def _execute(argv, cwd, timeout=DEFAULT_TIMEOUT_SECONDS, popen_factory=subprocess.Popen):
    if not isinstance(timeout, int) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValidationBridgeError("timeout is outside the fixed safe bound")
    started = time.monotonic()
    process = popen_factory(
        argv,
        cwd=str(cwd),
        shell=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )
    outputs = {}
    readers = [
        threading.Thread(target=lambda: outputs.__setitem__("stdout", _bounded_reader(process.stdout, MAX_OUTPUT_CHARS)), daemon=True),
        threading.Thread(target=lambda: outputs.__setitem__("stderr", _bounded_reader(process.stderr, MAX_OUTPUT_CHARS)), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=5)
    duration_ms = int((time.monotonic() - started) * 1000)
    stdout, stdout_truncated = outputs.get("stdout", ("", False))
    stderr, stderr_truncated = outputs.get("stderr", ("", False))
    return {
        "schema": RESULT_SCHEMA,
        "command": list(argv),
        "exit_code": None if timed_out else process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "duration_ms": duration_ms,
    }


def validate(operation, workdir, paths=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    if operation not in ALLOWED_OPERATIONS:
        raise ValidationBridgeError(f"unsupported operation: {operation!r}")
    repo = validate_workdir(workdir)
    if operation in {"pytest_targeted", "py_compile"}:
        paths = validate_paths(paths, repo, require_tests=operation == "pytest_targeted")
    elif paths not in (None, []):
        raise ValidationBridgeError("paths are not accepted for this operation")
    argv = build_argv(operation, paths or [])
    result = _execute(argv, repo, timeout=timeout)
    result.update({"operation": operation, "workdir": str(repo), "paths": paths or []})
    return result


mcp_server = MCPServer("implementation-validation-bridge")


@mcp_server.tool(name="validate")
def _tool_validate(operation, workdir, paths=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    return validate(operation, workdir, paths, timeout)


def run_server():
    mcp_server.run()


if __name__ == "__main__":
    run_server()
