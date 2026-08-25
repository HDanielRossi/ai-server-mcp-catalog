"""Repo-only template for bounded, read-only review evidence collection.

This module is a template: it is copied and installed by the operator into
its running location. It is pure logic with no side effects and imports
nothing that is network-dependent or live-bridge-dependent. Stdlib only
(json, os, re); no subprocess, no sockets, no HTTP clients.

All functions are deterministic: identical inputs always produce identical
outputs. There is no use of time, randomness, network, or subprocess.
"""

import re


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
