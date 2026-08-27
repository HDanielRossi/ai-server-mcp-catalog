"""Repo-only template for deterministic, idempotent pipeline task creation.

This module is a template: it is copied and installed by the operator into
its running location. It doubles as a real, executable MCP runtime adapter:
importing it is pure and side-effect free (no subprocess, no sockets, no
server startup), but running it as `__main__` starts an MCP server (name
"pipeline-bridge") exposing the three pipeline task-creation operations as
MCP tools, backed by a real `hermes kanban create` subprocess invocation.

Task creation is idempotent: the same (workdir, feature, role) always maps
to the same key, and TaskBackend.existing_id_for_key is always consulted
before TaskBackend.create_task is called. The real Hermes-backed TaskBackend
additionally relies on `hermes kanban create --idempotency-key` to dedup
server-side, so existing_id_for_key is a pure local no-op (returns None) and
never contacts anything itself.
"""

import hashlib
import json
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from mcp.server import MCPServer

ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
DEFAULT_HERMES_TIMEOUT_SECONDS = 60


REVIEW_BODY_REQUIRED_SENTENCES = (
    "If test_command is __skip__, do not invent or substitute another command",
    "If tests are required by the acceptance criteria but no valid explicit test command is available, "
    "block the task instead of guessing",
)

# A3.5 reviewer hardening contract: every review task body must carry this
# language verbatim so the reviewer cannot drift toward stale, fabricated,
# or out-of-session evidence regardless of which profile/SOUL.md is loaded.
REVIEWER_CONTRACT_SENTENCES = (
    "The reviewer is read-only and must not modify anything.",
    "Evidence must be collected fresh in this same review session through mcp__review_bridge__collect.",
    "The reviewer must not use terminal, file, or code execution access to perform the review.",
    "The reviewer must not rely on Memory for review evidence.",
    "A PASS or CHANGES REQUIRED verdict requires a successful fresh collect in this session.",
    "An operational review-bridge failure must be reported as a BLOCK, not papered over with fabricated evidence.",
)


class PipelineBridgeError(Exception):
    """Raised when pipeline task creation inputs fail validation."""


def stable_key(workdir, feature, role):
    """Return a deterministic idempotency key for the (workdir, feature, role) tuple.

    Pure function of its three arguments: identical inputs always produce
    the identical key.
    """
    if not workdir:
        raise PipelineBridgeError("workdir must be non-empty")
    if not feature:
        raise PipelineBridgeError("feature must be non-empty")

    payload = repr((workdir, feature, role))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"key:{digest}:{workdir}|{feature}|{role}"


class TaskBackend(ABC):
    """Injection seam for task creation. The template never contacts a real Kanban."""

    @abstractmethod
    def existing_id_for_key(self, key):
        """Return the existing task id for key, or None if no task exists yet."""
        raise NotImplementedError

    @abstractmethod
    def create_task(self, key, title, body, metadata):
        """Create a task and return its id."""
        raise NotImplementedError


def _common_metadata(role, workdir, feature, acceptance_criteria, changed_paths, test_command):
    return {
        "workdir": workdir,
        "feature": feature,
        "role": role,
        "acceptance_criteria": acceptance_criteria,
        "changed_paths": changed_paths,
        "test_command": test_command,
    }


def _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command):
    return [
        f"workdir: {workdir}",
        f"feature: {feature}",
        f"changed_paths: {changed_paths!r}",
        f"test_command: {test_command!r}",
        "acceptance_criteria:",
        str(acceptance_criteria),
    ]


def create_implementation_task(
    backend, workdir, feature, acceptance_criteria, changed_paths=None, test_command=None
):
    """Idempotently create (or return the existing id for) the implementation task."""
    key = stable_key(workdir, feature, "implementation")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Implementation: {feature}"
    body_lines = [f"Implementation task for feature {feature!r} in {workdir}."]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body = "\n".join(body_lines)
    metadata = _common_metadata("implementation", workdir, feature, acceptance_criteria, changed_paths, test_command)
    return backend.create_task(key, title, body, metadata)


def create_review_task(
    backend,
    workdir,
    feature,
    implementation_task_id,
    acceptance_criteria,
    changed_paths=None,
    test_command=None,
):
    """Idempotently create (or return the existing id for) the review task.

    Rejects if implementation_task_id is missing or empty: a review task
    must always be anchored to a specific implementation task.
    """
    if not implementation_task_id:
        raise PipelineBridgeError("implementation_task_id must be non-empty")

    key = stable_key(workdir, feature, f"review:{implementation_task_id}")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Review: {feature}"
    body_lines = [
        f"Review for implementation task {implementation_task_id}.",
        f"implementation_task_id: {implementation_task_id}",
    ]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body_lines.append("")
    body_lines.extend(REVIEW_BODY_REQUIRED_SENTENCES)
    body_lines.append("")
    body_lines.extend(REVIEWER_CONTRACT_SENTENCES)
    body = "\n".join(body_lines)

    metadata = _common_metadata("review", workdir, feature, acceptance_criteria, changed_paths, test_command)
    metadata["implementation_task_id"] = implementation_task_id
    return backend.create_task(key, title, body, metadata)


def create_correction_task(
    backend,
    workdir,
    feature,
    review_task_id,
    acceptance_criteria,
    changed_paths=None,
    test_command=None,
):
    """Idempotently create (or return the existing id for) the correction task.

    Rejects if review_task_id is missing or empty: a correction task must
    always be anchored to the review task that requested it.
    """
    if not review_task_id:
        raise PipelineBridgeError("review_task_id must be non-empty")

    key = stable_key(workdir, feature, f"correction:{review_task_id}")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Correction: {feature}"
    body_lines = [
        f"Correction for review task {review_task_id}.",
        f"review_task_id: {review_task_id}",
    ]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body = "\n".join(body_lines)

    metadata = _common_metadata("correction", workdir, feature, acceptance_criteria, changed_paths, test_command)
    metadata["review_task_id"] = review_task_id
    return backend.create_task(key, title, body, metadata)


def validate_workdir(workdir):
    """Resolve and validate workdir before any Hermes subprocess is spawned.

    Rejects a non-absolute path before resolving it, requires the resolved
    path to exist and be a directory, and requires it to remain inside
    ALLOWED_ROOT (rejecting `..`/symlink escapes via Path.resolve()).
    """
    if not isinstance(workdir, str) or not workdir:
        raise PipelineBridgeError("workdir must be a non-empty string")
    if not Path(workdir).is_absolute():
        raise PipelineBridgeError("workdir must be an absolute path")
    resolved = Path(workdir).resolve()
    if not (resolved.exists() and resolved.is_dir()):
        raise PipelineBridgeError(f"workdir does not exist or is not a directory: {resolved}")
    try:
        resolved.relative_to(ALLOWED_ROOT)
    except ValueError:
        raise PipelineBridgeError(f"workdir is outside the allowed root {ALLOWED_ROOT}: {resolved}")
    return resolved


_ROLE_KANBAN_PROFILE = {
    "implementation": {"assignee": "coder-claude", "max_retries": "3", "parent_key": None},
    "review": {"assignee": "reviewer", "max_retries": "1", "parent_key": "implementation_task_id"},
    "correction": {"assignee": "coder-claude", "max_retries": "3", "parent_key": "review_task_id"},
}


def _build_kanban_create_argv(key, title, body, metadata, resolved_workdir):
    """Build the canonical `hermes kanban create` argv for one task metadata dict.

    Preserves assignment (coder-claude for implementation/correction, reviewer
    for review), workspace anchoring, created-by, parent linkage, the
    idempotency key, and a role-appropriate max-retries intent.
    """
    role = metadata["role"]
    try:
        profile = _ROLE_KANBAN_PROFILE[role]
    except KeyError:
        raise PipelineBridgeError(f"unknown role for kanban argv: {role!r}")

    argv = [
        "hermes", "kanban", "create",
        title,
        "--body", body,
        "--assignee", profile["assignee"],
    ]
    parent_key = profile["parent_key"]
    if parent_key is not None:
        parent_id = metadata.get(parent_key)
        if parent_id:
            argv += ["--parent", parent_id]
    argv += [
        "--workspace", "dir:" + str(resolved_workdir),
        "--idempotency-key", key,
        "--created-by", "pipeline_bridge",
        "--max-retries", profile["max_retries"],
        "--json",
    ]
    return argv


def _extract_task_id(payload):
    if isinstance(payload, dict):
        candidate = payload
    elif isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        candidate = payload[0]
    else:
        raise PipelineBridgeError("hermes kanban create result has no task id")
    task_id = candidate.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise PipelineBridgeError("hermes kanban create result has no task id")
    return task_id


class HermesKanbanBackend(TaskBackend):
    """Real TaskBackend that shells out to `hermes kanban create`.

    Invokes Hermes via an argv list with shell=False (no shell command
    interpolation) and a bounded subprocess timeout. Idempotency is
    delegated to `hermes kanban create --idempotency-key`, which returns the
    existing task id instead of creating a duplicate, so
    existing_id_for_key never contacts anything and always returns None.
    """

    def __init__(self, resolved_workdir, timeout=DEFAULT_HERMES_TIMEOUT_SECONDS):
        self._resolved_workdir = resolved_workdir
        self._timeout = timeout

    def existing_id_for_key(self, key):
        return None

    def create_task(self, key, title, body, metadata):
        argv = _build_kanban_create_argv(key, title, body, metadata, self._resolved_workdir)
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PipelineBridgeError(f"hermes kanban create timed out after {self._timeout}s: {exc}") from exc
        except OSError as exc:
            raise PipelineBridgeError(f"failed to launch hermes: {exc}") from exc
        if completed.returncode != 0:
            detail = ((completed.stderr or "") + " " + (completed.stdout or "")).strip()
            raise PipelineBridgeError(f"hermes kanban create exited {completed.returncode}: {detail}")
        try:
            payload = json.loads(completed.stdout)
        except (ValueError, TypeError) as exc:
            raise PipelineBridgeError(f"hermes kanban create stdout is not valid JSON: {exc}") from exc
        return _extract_task_id(payload)


# --- MCP runtime adapter -----------------------------------------------
#
# Instantiating MCPServer and registering tools below is pure Python object
# construction: it performs no I/O, starts no server, and spawns no
# subprocess. The server only actually starts when run as __main__.

mcp_server = MCPServer("pipeline-bridge")


@mcp_server.tool(name="create_implementation_task")
def _tool_create_implementation_task(
    workdir, feature, acceptance_criteria, changed_paths=None, test_command=None
):
    """Idempotently create (or return the existing id for) an implementation task in Hermes Kanban."""
    resolved = validate_workdir(workdir)
    backend = HermesKanbanBackend(resolved)
    return create_implementation_task(
        backend, str(resolved), feature, acceptance_criteria, changed_paths, test_command
    )


@mcp_server.tool(name="create_review_task")
def _tool_create_review_task(
    workdir, feature, implementation_task_id, acceptance_criteria, changed_paths=None, test_command=None
):
    """Idempotently create (or return the existing id for) a review task in Hermes Kanban."""
    resolved = validate_workdir(workdir)
    backend = HermesKanbanBackend(resolved)
    return create_review_task(
        backend, str(resolved), feature, implementation_task_id, acceptance_criteria, changed_paths, test_command
    )


@mcp_server.tool(name="create_correction_task")
def _tool_create_correction_task(
    workdir, feature, review_task_id, acceptance_criteria, changed_paths=None, test_command=None
):
    """Idempotently create (or return the existing id for) a correction task in Hermes Kanban."""
    resolved = validate_workdir(workdir)
    backend = HermesKanbanBackend(resolved)
    return create_correction_task(
        backend, str(resolved), feature, review_task_id, acceptance_criteria, changed_paths, test_command
    )


if __name__ == "__main__":
    mcp_server.run()
