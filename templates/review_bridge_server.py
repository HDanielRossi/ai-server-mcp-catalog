"""Repo-only template for bounded, read-only review evidence collection.

This module is a template: it is copied and installed by the operator into
its running location. It doubles as a real, executable MCP runtime adapter:
importing it is pure and side-effect free (no subprocess, no sockets, no
server startup), but running it as `__main__` starts an MCP server (name
"review-bridge") exposing a single `collect` tool that gathers bounded,
fresh, read-only review evidence.

The deterministic, pure-logic helpers below (normalize_changed_paths,
validate_test_command, validate_content_window, collect_evidence) remain
side-effect free and are reused by the real `collect` entrypoint for input
validation before any subprocess is spawned. All subprocess invocation is
argv-list only (never shell=True), bounded by
DEFAULT_COLLECT_TIMEOUT_SECONDS, never mutates git state, and never touches
the network.
"""

import re
import shlex
import subprocess
from pathlib import Path

from mcp.server import MCPServer


ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
DEFAULT_COLLECT_TIMEOUT_SECONDS = 60
MAX_CONTENT_WINDOW_BYTES = 20_000
MAX_CAPTURED_OUTPUT_CHARS = 4_000

DEFAULT_TEST_COMMAND = "__skip__"
MAX_CONTENT_WINDOW_LINES = 200
DEFAULT_CHANGED_PATHS = ""
DEFAULT_INCLUDE_DIFF = False
DEFAULT_INCLUDE_REPO_EVIDENCE = True
CONTENT_WINDOW_NOT_REQUESTED = "not-requested"
CONTENT_SKIPPED = "SKIPPED"

ALLOWED_TEST_COMMANDS = frozenset(
    {
        "__skip__",
        "/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q",
        "./scripts/audit-hermes-pipeline-hardening.sh",
    }
)


class ReviewBridgeError(Exception):
    """Raised when review evidence inputs fail validation."""


def normalize_changed_paths(changed_paths):
    """Normalize changed_paths (None, "", list, or comma-separated string) into a list of repo-relative paths."""
    if changed_paths is None or changed_paths == "":
        return []

    if isinstance(changed_paths, str):
        raw_entries = changed_paths.split(",")
    else:
        raw_entries = list(changed_paths)

    normalized = []
    for raw_entry in raw_entries:
        entry = raw_entry.strip()
        if entry == "":
            raise ReviewBridgeError("changed path entry is empty after strip")
        if entry.startswith("/"):
            raise ReviewBridgeError(f"changed path must be repo-relative, got absolute path: {entry}")
        if entry.startswith("~"):
            raise ReviewBridgeError(f"changed path must be repo-relative, got: {entry}")
        parts = re.split(r"[\\/]", entry)
        if ".." in parts:
            raise ReviewBridgeError(f"changed path must not contain '..' components: {entry}")
        normalized.append(entry)

    return normalized


def validate_test_command(test_command):
    """Validate test_command against the allowlist. "__skip__" is a valid explicit no-test request."""
    if test_command not in ALLOWED_TEST_COMMANDS:
        raise ReviewBridgeError(f"test_command is not in the allowlist: {test_command!r}")
    return test_command


def validate_content_window(changed_paths, content_window):
    """Validate a content window request against MAX_CONTENT_WINDOW_LINES and the changed_paths set."""
    if content_window is None:
        return None

    if not isinstance(content_window, dict):
        raise ReviewBridgeError("content_window must be a dict")

    path = content_window.get("path")
    start_line = content_window.get("start_line")
    end_line = content_window.get("end_line")

    if path is None:
        raise ReviewBridgeError("content_window requires exactly one path")
    if isinstance(path, (list, tuple, set)):
        raise ReviewBridgeError("content_window must contain exactly one path, not a collection")
    if path not in changed_paths:
        raise ReviewBridgeError(f"content_window path is not among changed_paths: {path!r}")

    if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
        raise ReviewBridgeError("content_window start_line must be an int >= 1")
    if not isinstance(end_line, int) or isinstance(end_line, bool) or end_line < start_line:
        raise ReviewBridgeError("content_window end_line must be an int >= start_line")

    window_size = end_line - start_line + 1
    if window_size > MAX_CONTENT_WINDOW_LINES:
        raise ReviewBridgeError(
            f"content_window spans {window_size} lines, exceeds MAX_CONTENT_WINDOW_LINES={MAX_CONTENT_WINDOW_LINES}"
        )

    return {"path": path, "start_line": start_line, "end_line": end_line}


def collect_evidence(
    changed_paths=DEFAULT_CHANGED_PATHS,
    include_diff=DEFAULT_INCLUDE_DIFF,
    include_repo_evidence=DEFAULT_INCLUDE_REPO_EVIDENCE,
    test_command=DEFAULT_TEST_COMMAND,
    content_window=None,
):
    """Collect bounded, read-only review evidence deterministically from validated inputs."""
    normalized_paths = normalize_changed_paths(changed_paths)

    if include_diff and not normalized_paths:
        raise ReviewBridgeError("include_diff requires at least one changed path")

    validated_test_command = validate_test_command(test_command)

    validated_window = validate_content_window(normalized_paths, content_window)

    if validated_window is None:
        content_window_field = CONTENT_WINDOW_NOT_REQUESTED
        file_content_field = CONTENT_SKIPPED
    else:
        window_desc = (
            f"{validated_window['path']}:{validated_window['start_line']}-{validated_window['end_line']}"
        )
        content_window_field = f"requested:{window_desc}"
        file_content_field = f"window:{window_desc}"

    return {
        "changed_paths": normalized_paths,
        "include_diff": bool(include_diff),
        "include_repo_evidence": bool(include_repo_evidence),
        "test_command": validated_test_command,
        "content_window": content_window_field,
        "file_content": file_content_field,
        "diff": "requested" if include_diff else "not-requested",
        "status": "ok",
    }


# --- real, read-only evidence collection -----------------------------------


def validate_workdir(workdir):
    """Resolve and validate the reviewed workdir before any subprocess is spawned.

    Mirrors pipeline_bridge_server.validate_workdir: rejects a non-absolute
    path before resolving it, requires the resolved path to exist and be a
    directory, and requires it to remain inside ALLOWED_ROOT (rejecting
    `..`/symlink escapes via Path.resolve()).
    """
    if not isinstance(workdir, str) or not workdir:
        raise ReviewBridgeError("workdir must be a non-empty string")
    if not Path(workdir).is_absolute():
        raise ReviewBridgeError("workdir must be an absolute path")
    resolved = Path(workdir).resolve()
    if not (resolved.exists() and resolved.is_dir()):
        raise ReviewBridgeError(f"workdir does not exist or is not a directory: {resolved}")
    try:
        resolved.relative_to(ALLOWED_ROOT)
    except ValueError:
        raise ReviewBridgeError(f"workdir is outside the allowed root {ALLOWED_ROOT}: {resolved}")
    return resolved


def _resolve_changed_path(relative_changed_path, resolved_workdir):
    """Resolve a single, already-normalized relative changed_path and require it to stay inside resolved_workdir."""
    resolved = (resolved_workdir / relative_changed_path).resolve()
    try:
        resolved.relative_to(resolved_workdir)
    except ValueError:
        raise ReviewBridgeError(f"changed_path escapes workdir: {relative_changed_path}")
    return resolved


def _truncate_output(text, max_chars=MAX_CAPTURED_OUTPUT_CHARS):
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n...[truncated to {max_chars} chars]"


def _run_argv(argv, cwd, timeout=DEFAULT_COLLECT_TIMEOUT_SECONDS):
    """Run argv as a bounded, read-only subprocess (shell=False, no shell interpolation).

    Never raises on timeout or a non-zero exit status: both are captured as a
    structured record (command, exit_code, timed_out, stdout, stderr) so a
    caller can report a structured error instead of crashing.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "command": list(argv),
            "exit_code": None,
            "timed_out": True,
            "stdout": "",
            "stderr": "",
        }
    return {
        "command": list(argv),
        "exit_code": completed.returncode,
        "timed_out": False,
        "stdout": _truncate_output(completed.stdout),
        "stderr": _truncate_output(completed.stderr),
    }


def _verify_git_repo(resolved_workdir):
    """Confirm resolved_workdir is inside a git work tree; fail closed with an explicit error otherwise."""
    record = _run_argv(["git", "rev-parse", "--is-inside-work-tree"], resolved_workdir)
    if record["timed_out"] or record["exit_code"] != 0 or record["stdout"].strip() != "true":
        raise ReviewBridgeError(f"workdir is not a usable git repository: {resolved_workdir}")
    return record


def _git_status(resolved_workdir):
    return _run_argv(["git", "status", "--short"], resolved_workdir)


def _git_diff_name_only(resolved_workdir, relative_changed_path):
    return _run_argv(["git", "diff", "--name-only", "--", relative_changed_path], resolved_workdir)


def _git_diff(resolved_workdir, relative_changed_path):
    return _run_argv(["git", "diff", "--", relative_changed_path], resolved_workdir)


def _git_diff_check(resolved_workdir, relative_changed_path):
    return _run_argv(["git", "diff", "--check", "--", relative_changed_path], resolved_workdir)


def _read_content_window(resolved_path, start_line, end_line):
    """Read a bounded window of resolved_path: at most (end_line - start_line + 1) lines,
    capped overall at MAX_CONTENT_WINDOW_LINES lines and MAX_CONTENT_WINDOW_BYTES bytes.
    Never reads or returns a full-file dump. Returns None if the path is missing or not a file.
    """
    if not resolved_path.exists() or not resolved_path.is_file():
        return None

    lines = []
    total_bytes = 0
    with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            if line_number < start_line:
                continue
            if line_number > end_line:
                break
            total_bytes += len(line.encode("utf-8"))
            if total_bytes > MAX_CONTENT_WINDOW_BYTES:
                lines.append(f"...[truncated at {MAX_CONTENT_WINDOW_BYTES} bytes]")
                break
            lines.append(line)

    return "".join(lines)


def _run_test_command(validated_test_command, resolved_workdir, timeout=DEFAULT_COLLECT_TIMEOUT_SECONDS):
    """Execute an already-allowlist-validated test_command read-only, as an argv list."""
    if validated_test_command == DEFAULT_TEST_COMMAND:
        return {"command": validated_test_command, "skipped": True}

    argv = shlex.split(validated_test_command)
    record = _run_argv(argv, resolved_workdir, timeout=timeout)
    record["skipped"] = False
    return record


def collect(workdir, changed_path=None, test_command=None, content_window=None):
    """Collect bounded, fresh, read-only review evidence for a single review session.

    All inputs (workdir containment, changed_path containment, test_command
    allowlist membership, content_window shape) are validated up front,
    fail-closed, before any subprocess or filesystem read is attempted.

    Evidence gathered (all read-only, argv-list subprocess, shell=False,
    bounded by DEFAULT_COLLECT_TIMEOUT_SECONDS, no git mutation, no network):
      - git repository validation (`git rev-parse --is-inside-work-tree`);
      - `git status --short` for the whole workdir;
      - when changed_path is supplied: a scoped `git diff --name-only`,
        `git diff`, and `git diff --check`, all restricted to changed_path;
      - when changed_path is supplied: a bounded content window of that one
        file, capped at MAX_CONTENT_WINDOW_LINES lines (200) and
        MAX_CONTENT_WINDOW_BYTES bytes (20,000) -- never a full-file or
        full-repository dump, and never more than one file per call;
      - when test_command is supplied: validated against ALLOWED_TEST_COMMANDS
        and, if valid, executed read-only with a bounded exit code and
        truncated stdout/stderr captured as evidence.
    """
    resolved_workdir = validate_workdir(workdir)

    relative_changed_path = None
    resolved_changed_path = None
    validated_window = None
    if changed_path is not None:
        normalized = normalize_changed_paths(changed_path)
        if len(normalized) != 1:
            raise ReviewBridgeError("changed_path must resolve to exactly one path")
        relative_changed_path = normalized[0]
        resolved_changed_path = _resolve_changed_path(relative_changed_path, resolved_workdir)
        if content_window is not None:
            validated_window = validate_content_window([relative_changed_path], content_window)
    elif content_window is not None:
        raise ReviewBridgeError("content_window requires changed_path to be supplied")

    validated_test_command = None
    if test_command is not None:
        validated_test_command = validate_test_command(test_command)

    # All inputs are validated; only now do we touch the filesystem/subprocess.

    repo_check = _verify_git_repo(resolved_workdir)
    status = _git_status(resolved_workdir)

    evidence = {
        "workdir": str(resolved_workdir),
        "repo_check": repo_check,
        "git_status": status,
        "changed_path": relative_changed_path,
        "diff_name_only": None,
        "diff": None,
        "diff_check": None,
        "content_window": None,
        "test_result": None,
        "status": "ok",
    }

    if relative_changed_path is not None:
        evidence["diff_name_only"] = _git_diff_name_only(resolved_workdir, relative_changed_path)
        evidence["diff"] = _git_diff(resolved_workdir, relative_changed_path)
        evidence["diff_check"] = _git_diff_check(resolved_workdir, relative_changed_path)

        if validated_window is not None:
            start_line = validated_window["start_line"]
            end_line = validated_window["end_line"]
        else:
            start_line = 1
            end_line = MAX_CONTENT_WINDOW_LINES

        evidence["content_window"] = {
            "path": relative_changed_path,
            "start_line": start_line,
            "end_line": end_line,
            "content": _read_content_window(resolved_changed_path, start_line, end_line),
        }

    if validated_test_command is not None:
        evidence["test_result"] = _run_test_command(validated_test_command, resolved_workdir)

    return evidence


# --- MCP runtime adapter -----------------------------------------------
#
# Instantiating MCPServer and registering the tool below is pure Python
# object construction: it performs no I/O, starts no server, and spawns no
# subprocess. The server only actually starts when run as __main__.

mcp_server = MCPServer("review-bridge")


@mcp_server.tool(name="collect")
def _tool_collect(workdir, changed_path=None, test_command=None, content_window=None):
    """Return bounded, fresh, read-only review evidence for workdir.

    Read-only: never mutates git state, never touches the network. Evidence
    includes git status, and -- when changed_path is supplied -- a scoped
    diff, diff --check, and a bounded content window of that single file
    (at most MAX_CONTENT_WINDOW_LINES lines / MAX_CONTENT_WINDOW_BYTES
    bytes; never a full-file or full-repository dump). When test_command is
    supplied it must be one of ALLOWED_TEST_COMMANDS and is executed
    read-only with a bounded timeout.
    """
    return collect(workdir, changed_path, test_command, content_window)


if __name__ == "__main__":
    mcp_server.run()
