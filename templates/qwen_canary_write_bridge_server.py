"""Structured file bridge for the tightly bounded System One Qwen pilot.

It deliberately has no shell, Git, subprocess, arbitrary path, or patch
operation. The fixed S0.5 allowlist is the sole authorized pilot scope.
"""

from pathlib import Path

from mcp.server import MCPServer


ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
ALLOWED_PATHS = frozenset({"app.py", "tests/test_app.py", "README.md", "ARCHITECTURE.md"})
MAX_READ_CHARS = 24_000
MAX_REPLACEMENT_CHARS = 8_000


class CanaryWriteError(Exception):
    pass


def _workdir(workdir):
    if not isinstance(workdir, str) or not Path(workdir).is_absolute():
        raise CanaryWriteError("workdir must be absolute")
    resolved = Path(workdir).resolve()
    if resolved == ALLOWED_ROOT or ALLOWED_ROOT not in resolved.parents or not (resolved / ".git").exists():
        raise CanaryWriteError("workdir must be an existing worktree under /opt/ai/projects")
    return resolved


def _path(workdir, path):
    if path not in ALLOWED_PATHS:
        raise CanaryWriteError("path is outside the fixed canary allowlist")
    target = (workdir / path).resolve()
    if workdir not in target.parents or not target.is_file():
        raise CanaryWriteError("path is not a regular file in the worktree")
    return target


def read_file(workdir, path):
    target = _path(_workdir(workdir), path)
    text = target.read_text(encoding="utf-8")
    if len(text) > MAX_READ_CHARS:
        raise CanaryWriteError("file exceeds fixed read limit")
    return {"path": path, "content": text}


def replace_exact(workdir, path, expected, replacement):
    if not isinstance(expected, str) or not expected:
        raise CanaryWriteError("expected must be a non-empty string")
    if not isinstance(replacement, str) or len(replacement) > MAX_REPLACEMENT_CHARS:
        raise CanaryWriteError("replacement is invalid or too large")
    target = _path(_workdir(workdir), path)
    before = target.read_text(encoding="utf-8")
    if before.count(expected) != 1:
        raise CanaryWriteError("expected text must occur exactly once")
    after = before.replace(expected, replacement, 1)
    target.write_text(after, encoding="utf-8")
    return {"path": path, "replaced": True, "before_chars": len(before), "after_chars": len(after)}


mcp_server = MCPServer("qwen-canary-write-bridge")


@mcp_server.tool(name="read_file")
def _tool_read_file(workdir, path):
    return read_file(workdir, path)


@mcp_server.tool(name="replace_exact")
def _tool_replace_exact(workdir, path, expected, replacement):
    return replace_exact(workdir, path, expected, replacement)


if __name__ == "__main__":
    mcp_server.run()
