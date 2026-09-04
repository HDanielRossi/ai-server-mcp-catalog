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
argv-list only (never shell=True), bounded by COLLECT_TIMEOUT_SECONDS,
never mutates git state, and never touches the network.
"""

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path

from mcp.server import MCPServer


ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
COLLECT_TIMEOUT_ENV = "HERMES_REVIEW_BRIDGE_COLLECT_TIMEOUT_SECONDS"
DEFAULT_COLLECT_TIMEOUT_SECONDS = 60
MAX_COLLECT_TIMEOUT_SECONDS = 900
MAX_CONTENT_WINDOW_BYTES = 20_000
MAX_CAPTURED_OUTPUT_CHARS = 4_000
REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v1"
COMMITTED_SCOPE_SCHEMA = "hermes.committed-implementation-scope/v1"
REPO_STATE_TIMEOUT_SECONDS = 30
CONFLICT_STATUS_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})

DEFAULT_TEST_COMMAND = "__skip__"
DEFAULT_TEST_OPERATION = "skip"
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

ALLOWED_TEST_OPERATIONS = frozenset({"skip", "pytest_full", "repository_audit"})
TEST_OPERATION_COMMANDS = {
    "skip": DEFAULT_TEST_COMMAND,
    "pytest_full": "/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q",
    "repository_audit": "./scripts/audit-hermes-pipeline-hardening.sh",
}


class ReviewBridgeError(Exception):
    """Raised when review evidence inputs fail validation."""


def _resolve_collect_timeout_seconds(raw_value=None):
    """Resolve the bounded subprocess timeout for collect test commands.

    Invalid, empty, zero, or negative values fall back to the historical
    default. Values above MAX_COLLECT_TIMEOUT_SECONDS are capped so a runtime
    configuration mistake cannot make reviewer subprocess execution
    effectively unbounded.
    """
    if raw_value is None:
        raw_value = os.environ.get(COLLECT_TIMEOUT_ENV)
    if raw_value is None:
        return DEFAULT_COLLECT_TIMEOUT_SECONDS

    stripped = str(raw_value).strip()
    if stripped == "":
        return DEFAULT_COLLECT_TIMEOUT_SECONDS

    try:
        parsed = int(stripped, 10)
    except ValueError:
        return DEFAULT_COLLECT_TIMEOUT_SECONDS

    if parsed <= 0:
        return DEFAULT_COLLECT_TIMEOUT_SECONDS
    if parsed > MAX_COLLECT_TIMEOUT_SECONDS:
        return MAX_COLLECT_TIMEOUT_SECONDS
    return parsed


COLLECT_TIMEOUT_SECONDS = _resolve_collect_timeout_seconds()


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


def validate_test_operation(test_operation):
    """Validate the structured reviewer operation before any execution."""
    if test_operation not in ALLOWED_TEST_OPERATIONS:
        raise ReviewBridgeError(f"test_operation is not supported: {test_operation!r}")
    return test_operation


def test_operation_for_command(test_command):
    """Map an exact legacy literal to its structured operation."""
    validate_test_command(test_command)
    for operation, command in TEST_OPERATION_COMMANDS.items():
        if command == test_command:
            return operation
    raise ReviewBridgeError(f"no operation mapping for test_command: {test_command!r}")


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
    test_operation=None,
):
    """Collect bounded, read-only review evidence deterministically from validated inputs."""
    normalized_paths = normalize_changed_paths(changed_paths)

    if include_diff and not normalized_paths:
        raise ReviewBridgeError("include_diff requires at least one changed path")

    if test_operation is None:
        validated_test_command = validate_test_command(test_command)
        validated_test_operation = test_operation_for_command(validated_test_command)
    else:
        validated_test_operation = validate_test_operation(test_operation)
        validated_test_command = TEST_OPERATION_COMMANDS[validated_test_operation]

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
        "test_operation": validated_test_operation,
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


def _run_argv(argv, cwd, timeout=COLLECT_TIMEOUT_SECONDS):
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


def _run_test_command(validated_test_command, resolved_workdir, timeout=COLLECT_TIMEOUT_SECONDS):
    """Execute an already-allowlist-validated test_command read-only, as an argv list."""
    if validated_test_command == DEFAULT_TEST_COMMAND:
        return {"command": validated_test_command, "skipped": True}

    argv = shlex.split(validated_test_command)
    record = _run_argv(argv, resolved_workdir, timeout=timeout)
    record["skipped"] = False
    return record


def _run_test_operation(test_operation, resolved_workdir, timeout=COLLECT_TIMEOUT_SECONDS):
    """Execute only the immutable argv mapped from a structured operation."""
    operation = validate_test_operation(test_operation)
    command = TEST_OPERATION_COMMANDS[operation]
    if operation == DEFAULT_TEST_OPERATION:
        return {"operation": operation, "command": command, "skipped": True}
    record = _run_argv(shlex.split(command), resolved_workdir, timeout=timeout)
    record["operation"] = operation
    record["skipped"] = False
    return record


def _run_git_capture(argv, cwd, timeout=REPO_STATE_TIMEOUT_SECONDS):
    """Run a read-only git argv command and return raw stdout bytes; raise ReviewBridgeError on failure.

    Unlike _run_argv, this never swallows a failure into a structured record:
    repository-state capture requires every underlying git command to
    succeed, so a timeout or non-zero exit is raised immediately.
    """
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ReviewBridgeError(f"git command timed out after {timeout}s: {argv!r}")
    if completed.returncode != 0:
        stderr_text = completed.stderr.decode("utf-8", "replace")
        raise ReviewBridgeError(f"git command failed ({completed.returncode}): {argv!r}: {stderr_text}")
    return completed.stdout


def _run_git_text(argv, cwd, timeout=REPO_STATE_TIMEOUT_SECONDS):
    return _run_git_capture(argv, cwd, timeout).decode("utf-8", "replace")


def _git_path_list(argv, cwd):
    """Run a git argv command that prints one path per line and return the non-empty lines."""
    text = _run_git_text(argv, cwd)
    return [line for line in text.split("\n") if line != ""]


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _canonical_json_sha256(obj):
    encoded = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _gitlink_paths(resolved_workdir, paths):
    """Return the subset of paths that are gitlinks (submodules, mode 160000) per `git ls-files -s`."""
    if not paths:
        return set()
    argv = ["git", "ls-files", "-s", "--"] + list(paths)
    text = _run_git_text(argv, resolved_workdir)
    gitlinks = set()
    for line in text.split("\n"):
        if line == "":
            continue
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if fields and fields[0] == "160000":
            gitlinks.add(path)
    return gitlinks


def _reject_conflicts_and_submodules(resolved_workdir, staged_paths, unstaged_paths, untracked_paths):
    """Fail closed on unresolved merge conflicts or changed submodules (gitlinks)."""
    status_text = _run_git_text(["git", "status", "--porcelain"], resolved_workdir)
    for line in status_text.split("\n"):
        if line == "":
            continue
        code = line[:2]
        if code in CONFLICT_STATUS_CODES:
            raise ReviewBridgeError(f"unresolved merge conflict detected in git status: {line}")

    overlap = set(staged_paths) & set(unstaged_paths)
    if overlap:
        raise ReviewBridgeError(
            f"unresolved merge conflict detected (path both staged and unstaged): {sorted(overlap)}"
        )

    all_changed = sorted(set(staged_paths) | set(unstaged_paths) | set(untracked_paths))
    gitlinks = _gitlink_paths(resolved_workdir, all_changed)
    if gitlinks:
        raise ReviewBridgeError(f"changed submodule (gitlink) detected: {sorted(gitlinks)}")

    submodule_status_text = _run_git_text(["git", "submodule", "status"], resolved_workdir)
    for line in submodule_status_text.split("\n"):
        if line == "":
            continue
        if line[0] in ("+", "-"):
            raise ReviewBridgeError(f"changed submodule detected via git submodule status: {line.strip()}")


def _hash_file_bytes(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _describe_untracked_entry(resolved_workdir, relative_path):
    """Build one untracked-entry record; raises ReviewBridgeError for anything but a file or symlink."""
    full_path = resolved_workdir / relative_path
    file_stat = full_path.lstat()

    if stat.S_ISLNK(file_stat.st_mode):
        target_bytes = os.fsencode(os.readlink(full_path))
        return {
            "path": relative_path,
            "type": "symlink",
            "mode": format(stat.S_IMODE(file_stat.st_mode), "04o"),
            "size": len(target_bytes),
            "content_sha256": hashlib.sha256(target_bytes).hexdigest(),
        }

    if stat.S_ISREG(file_stat.st_mode):
        return {
            "path": relative_path,
            "type": "file",
            "mode": format(stat.S_IMODE(file_stat.st_mode), "04o"),
            "size": file_stat.st_size,
            "content_sha256": _hash_file_bytes(full_path),
        }

    raise ReviewBridgeError(f"untracked path is a special file (not a regular file or symlink): {relative_path}")


def _git_path_is_ignored(resolved_workdir, relative_path):
    """Return whether Git excludes relative_path via ignore rules.

    git check-ignore uses exit 0 for ignored and exit 1 for not ignored.
    Any other result is an evidence-channel failure and therefore blocks.
    """
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_path],
            cwd=resolved_workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=REPO_STATE_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewBridgeError(
            f"git check-ignore failed for {relative_path!r}: {exc}"
        ) from exc

    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False

    stderr = result.stderr.decode("utf-8", "replace").strip()
    raise ReviewBridgeError(
        f"git check-ignore failed for {relative_path!r}: "
        f"exit_code={result.returncode} stderr={stderr!r}"
    )


def _reject_untracked_special_entries(resolved_workdir):
    """Reject non-ignored filesystem entries Git cannot fingerprint.

    Git's untracked-file enumeration does not report FIFOs, sockets, devices,
    and similar special filesystem objects.  Silently ignoring one would make
    the repository-state envelope incomplete, so inspect the worktree
    read-only and fail closed when such a non-ignored entry is present.

    Regular files, directories, and symlinks remain handled by the normal
    Git-derived changed-path/untracked machinery.  Git's own .git directory is
    never part of worktree evidence and is pruned from traversal.
    """
    repo_root = Path(resolved_workdir)

    try:
        walker = os.walk(repo_root, topdown=True, followlinks=False)

        for root, dirs, files in walker:
            # Git administrative state is not worktree content.
            dirs[:] = [name for name in dirs if name != ".git"]

            for name in list(dirs) + list(files):
                full_path = Path(root) / name

                try:
                    file_stat = full_path.lstat()
                except OSError as exc:
                    raise ReviewBridgeError(
                        f"cannot inspect repository entry {full_path}: {exc}"
                    ) from exc

                mode = file_stat.st_mode

                if (
                    stat.S_ISREG(mode)
                    or stat.S_ISDIR(mode)
                    or stat.S_ISLNK(mode)
                ):
                    continue

                relative_path = full_path.relative_to(repo_root).as_posix()

                if _git_path_is_ignored(resolved_workdir, relative_path):
                    continue

                raise ReviewBridgeError(
                    "unsupported non-ignored special filesystem entry "
                    f"in worktree: {relative_path}"
                )

    except ReviewBridgeError:
        raise
    except OSError as exc:
        raise ReviewBridgeError(
            f"cannot scan worktree for special filesystem entries: {exc}"
        ) from exc


def _capture_repository_state_once(resolved_workdir, canonical_workdir):
    """Capture one repository-state envelope. Read-only: no git object/index/file writes."""
    head = _run_git_text(["git", "rev-parse", "HEAD"], resolved_workdir).strip()

    staged_paths = _git_path_list(["git", "diff", "--name-only", "--cached"], resolved_workdir)
    unstaged_paths = _git_path_list(["git", "diff", "--name-only"], resolved_workdir)
    untracked_paths = _git_path_list(["git", "ls-files", "--others", "--exclude-standard"], resolved_workdir)

    # Git does not enumerate FIFOs/sockets/devices as ordinary untracked
    # entries. Detect non-ignored special filesystem objects separately so
    # they cannot disappear from the repository-state fingerprint.
    _reject_untracked_special_entries(resolved_workdir)

    _reject_conflicts_and_submodules(resolved_workdir, staged_paths, unstaged_paths, untracked_paths)

    changed_paths = sorted(set(staged_paths) | set(unstaged_paths) | set(untracked_paths))

    staged_patch = _run_git_capture(["git", "diff", "--cached", "--binary", "--full-index"], resolved_workdir)
    unstaged_patch = _run_git_capture(["git", "diff", "--binary", "--full-index"], resolved_workdir)

    untracked_entries = [
        _describe_untracked_entry(resolved_workdir, relative_path) for relative_path in sorted(untracked_paths)
    ]

    envelope = {
        "schema": REPOSITORY_STATE_SCHEMA,
        "workdir": canonical_workdir,
        "head": head,
        "changed_paths": changed_paths,
        "staged_patch_sha256": _sha256_bytes(staged_patch),
        "unstaged_patch_sha256": _sha256_bytes(unstaged_patch),
        "untracked": untracked_entries,
    }
    envelope["aggregate_sha256"] = _canonical_json_sha256(envelope)
    return envelope


def collect_repository_state(workdir):
    """Collect a deterministic, read-only repository-state fingerprint envelope for workdir.

    Captures the envelope twice consecutively and requires exact equality,
    raising ReviewBridgeError on any mismatch, so an in-flight or otherwise
    unstable working tree can never silently produce a fingerprint. Every
    changed/untracked path comes only from git itself -- never from caller
    input. Strictly read-only: never writes git objects, the index, or any
    file.
    """
    resolved_workdir = validate_workdir(workdir)
    _verify_git_repo(resolved_workdir)
    canonical_workdir = os.path.realpath(str(resolved_workdir))

    first = _capture_repository_state_once(resolved_workdir, canonical_workdir)
    second = _capture_repository_state_once(resolved_workdir, canonical_workdir)
    if first != second:
        raise ReviewBridgeError("repository state changed between consecutive captures (unstable state)")

    return first


def collect_committed_implementation_scope(workdir, base_sha, implementation_sha):
    """Compute the exact committed base-to-implementation path set read-only."""
    if not isinstance(base_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ReviewBridgeError("base_sha must be 40 lowercase hex characters")
    if not isinstance(implementation_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", implementation_sha):
        raise ReviewBridgeError("implementation_sha must be 40 lowercase hex characters")
    resolved_workdir = validate_workdir(workdir)
    _verify_git_repo(resolved_workdir)
    for label, sha in (("base_sha", base_sha), ("implementation_sha", implementation_sha)):
        resolved = _run_git_text(
            ["git", "rev-parse", "--verify", "--quiet", sha + "^{commit}"], resolved_workdir
        ).strip()
        if resolved != sha:
            raise ReviewBridgeError("%s does not resolve to the requested commit" % label)
    head = _run_git_text(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"], resolved_workdir
    ).strip()
    if head != implementation_sha:
        raise ReviewBridgeError("implementation_sha does not match repository HEAD")
    raw = _run_git_capture(
        ["git", "diff", "--name-only", "--no-renames", "-z", base_sha, implementation_sha, "--"],
        resolved_workdir,
    )
    paths = []
    for raw_path in raw.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", "surrogateescape")
        parts = path.replace("\\", "/").split("/")
        if path.startswith("/") or ".." in parts:
            raise ReviewBridgeError("committed scope contains unsafe path: %r" % path)
        paths.append(path)
    scope = {
        "schema": COMMITTED_SCOPE_SCHEMA,
        "workdir": os.path.realpath(str(resolved_workdir)),
        "base_sha": base_sha,
        "implementation_sha": implementation_sha,
        "changed_paths": sorted(set(paths)),
    }
    scope["scope_sha256"] = _canonical_json_sha256({k: v for k, v in scope.items() if k != "scope_sha256"})
    return scope


def collect(workdir, changed_path=None, test_command=None, content_window=None, base_sha=None, implementation_sha=None, test_operation=None):
    """Collect bounded, fresh, read-only review evidence for a single review session.

    All inputs (workdir containment, changed_path containment, structured
    test_operation membership, content_window shape) are validated up front,
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
      - when test_operation is supplied: validated against
        ALLOWED_TEST_OPERATIONS and mapped to immutable argv before
        read-only execution with bounded output;
      - every successful call additionally returns a deterministic
        repository-state fingerprint envelope (see collect_repository_state)
        under "repository_state", with its aggregate digest duplicated at
        "repository_state_sha256".
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

    if (base_sha is None) != (implementation_sha is None):
        raise ReviewBridgeError("base_sha and implementation_sha must be supplied together")

    validated_test_command = None
    validated_test_operation = None
    if test_operation is not None and test_command is not None:
        raise ReviewBridgeError("test_operation and legacy test_command are mutually exclusive")
    if test_operation is not None:
        validated_test_operation = validate_test_operation(test_operation)
        validated_test_command = TEST_OPERATION_COMMANDS[validated_test_operation]
    elif test_command is not None:
        validated_test_command = validate_test_command(test_command)
        validated_test_operation = test_operation_for_command(validated_test_command)

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
        "test_operation": validated_test_operation,
        "repository_state": None,
        "repository_state_sha256": None,
        "committed_scope": None,
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

    if validated_test_operation is not None:
        evidence["test_result"] = _run_test_operation(validated_test_operation, resolved_workdir)

    repository_state = collect_repository_state(str(resolved_workdir))
    evidence["repository_state"] = repository_state
    evidence["repository_state_sha256"] = repository_state["aggregate_sha256"]
    if base_sha is not None:
        evidence["committed_scope"] = collect_committed_implementation_scope(
            str(resolved_workdir), base_sha, implementation_sha
        )

    return evidence


# --- MCP runtime adapter -----------------------------------------------
#
# Instantiating MCPServer and registering the tool below is pure Python
# object construction: it performs no I/O, starts no server, and spawns no
# subprocess. The server only actually starts when run as __main__.

mcp_server = MCPServer("review-bridge")


@mcp_server.tool(name="collect")
def _tool_collect(workdir, changed_path=None, test_operation=None, content_window=None, base_sha=None, implementation_sha=None):
    """Return bounded, fresh, read-only review evidence for workdir.

    Read-only: never mutates git state, never touches the network. Evidence
    includes git status, and -- when changed_path is supplied -- a scoped
    diff, diff --check, and a bounded content window of that single file
    (at most MAX_CONTENT_WINDOW_LINES lines / MAX_CONTENT_WINDOW_BYTES
    bytes; never a full-file or full-repository dump). When test_operation is
    supplied it must be one of ALLOWED_TEST_OPERATIONS and is mapped to an
    immutable argv list, then executed read-only with a bounded timeout.
    """
    return collect(workdir, changed_path, content_window=content_window,
                   base_sha=base_sha, implementation_sha=implementation_sha,
                   test_operation=test_operation)


if __name__ == "__main__":
    mcp_server.run()
