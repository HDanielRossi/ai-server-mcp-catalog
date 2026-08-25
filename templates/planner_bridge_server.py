#!/usr/bin/env python3
"""Repo-only, versioned copy of the installed planner-bridge MCP server.

This module is a template: it is copied and installed by the operator into
its running location (/usr/local/lib/planner-bridge-mcp/server.py). It is a
real, directly installable MCP server (imports the real ``mcp.server``
runtime adapter), not a pure-helper stand-in.

A3.5.0 bootstrap: extends the original ``run(workdir, prompt)`` API
backward-compatibly with an optional ``context_files`` parameter so callers
can hand the planner explicit repository context instead of relying solely
on the wrapper's fixed, truncated automatic snapshot. Existing callers that
supply only ``workdir`` and ``prompt`` continue to work unchanged.

The wrapper (``/usr/local/bin/planner-bridge``, versioned in this repo at
``scripts/planner-bridge``) remains the filesystem/security enforcement
boundary for explicit context files: this module only validates shape
(None or a list of strings, at most ``MAX_CONTEXT_FILES`` entries) before
handing them to the wrapper as repeated ``--context-file`` argv entries.
"""

import subprocess
from pathlib import Path

from mcp.server import MCPServer

mcp = MCPServer("planner-bridge")

WRAPPER_PATH = "/usr/local/bin/planner-bridge"
ALLOWED_ROOT = Path("/opt/ai/projects")

MAX_CONTEXT_FILES = 12


def _validate_context_files(context_files):
    """Validate context_files is None or a list of at most MAX_CONTEXT_FILES strings."""
    if context_files is None:
        return []

    if not isinstance(context_files, list):
        raise ValueError("context_files must be a list of strings or None")

    for entry in context_files:
        if not isinstance(entry, str):
            raise ValueError(f"context_files entries must be strings, got {entry!r}")

    if len(context_files) > MAX_CONTEXT_FILES:
        raise ValueError(
            f"context_files must not contain more than {MAX_CONTEXT_FILES} entries, "
            f"got {len(context_files)}"
        )

    return context_files


@mcp.tool()
def run(workdir: str, prompt: str, context_files: list[str] | None = None) -> str:
    """
    Run planner-codex through the hardened planner-bridge.

    Args:
        workdir: Absolute project directory under /opt/ai/projects.
        prompt: Planning instruction for planner-codex.
        context_files: Optional list of relative paths (within workdir) whose
            complete contents should be included in the planner prompt, in
            addition to the wrapper's automatic bounded snapshot. At most
            MAX_CONTEXT_FILES entries. The wrapper enforces containment,
            symlink-escape rejection, and size limits.

    Returns:
        planner-bridge output with exit code.
    """
    raw_path = Path(workdir)
    if not raw_path.is_absolute():
        raise ValueError("workdir must be absolute")

    path = raw_path.resolve()
    allowed_root = ALLOWED_ROOT.resolve()

    if not path.exists() or not path.is_dir():
        raise ValueError(f"Invalid workdir: {path}")

    try:
        path.relative_to(allowed_root)
    except ValueError:
        raise ValueError(f"workdir must be inside {allowed_root}")

    validated_context_files = _validate_context_files(context_files)

    argv = [WRAPPER_PATH, str(path), prompt]
    for context_file in validated_context_files:
        argv += ["--context-file", context_file]

    result = subprocess.run(
        argv,
        cwd=str(path),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
        check=False,
    )

    return (
        f"exit_code={result.returncode}\n"
        f"workdir={path}\n"
        f"output:\n{result.stdout}"
    )


if __name__ == "__main__":
    mcp.run()
