#!/usr/bin/env python3
"""Thin MCP adapter over scripts/hermes-pipeline-controller.py.

This module is a template: it is copied and installed by the operator into
its running location. It doubles as a real, executable MCP runtime adapter:
importing it is pure and side-effect free (no subprocess, no sockets, no
server startup, and no probe of the installed controller path), but running
it as ``__main__`` starts an MCP server (name "pipeline-controller") that
exposes seven MCP tools, each mapping directly onto one
hermes-pipeline-controller.py CLI subcommand: ``check_task`` -> ``check``,
``create_implementation`` -> ``create-implementation``, ``create_review`` ->
``create-review``, ``create_correction`` -> ``create-correction``,
``wait_task`` -> ``wait``, ``archive_review`` -> ``archive-review``, and
``ready_to_commit`` -> ``ready-to-commit``.

The controller remains the sole authority for pipeline policy: workdir
policy, Kanban structural validation, verdict classification,
implementation/review authority, correction policy, repository-state/v1,
archive validation, READY_TO_COMMIT policy, and wait timeout/retry
semantics are all implemented exclusively in
scripts/hermes-pipeline-controller.py and are never reimplemented,
duplicated, or second-guessed here. This adapter only builds argv, invokes
the controller as a bounded subprocess, and reports its exit code and
stdout back unchanged. It never stages, commits, pushes, or infers human
approval; READY_TO_COMMIT remains strictly read-only.
"""

import json
import math
import subprocess

from mcp.server import MCPServer

# Fixed, trusted controller location for later runtime installation. Never
# probed, statted, or otherwise inspected at import time: repository tests
# must be able to import this module without any runtime deployment.
CONTROLLER_PATH = "/usr/local/bin/hermes-pipeline-controller"

RESULT_SCHEMA = "hermes.pipeline-controller-mcp/v1"

DEFAULT_CHECK_TIMEOUT_SECONDS = 60
DEFAULT_CREATE_TIMEOUT_SECONDS = 60
DEFAULT_ARCHIVE_REVIEW_TIMEOUT_SECONDS = 180
DEFAULT_READY_TO_COMMIT_TIMEOUT_SECONDS = 300

# The MCP-level subprocess timeout for `wait` must never race the
# controller's own wait deadline: it is the validated caller timeout plus
# this small, fixed transport grace period, never a replacement for it.
WAIT_TRANSPORT_GRACE_SECONDS = 15
MAX_WAIT_TIMEOUT_SECONDS = 3600

MAX_CAPTURED_STDOUT_CHARS = 1_000_000
MAX_CAPTURED_STDERR_CHARS = 4_000


class PipelineControllerAdapterError(Exception):
    """A deterministic MCP-adapter-level failure.

    Raised for adapter-only numeric input validation failures, subprocess
    launch failures, subprocess timeouts, and malformed/ambiguous
    controller stdout. Never raised to reinterpret a controller exit code:
    a completed controller invocation always reports its exact exit code,
    unchanged, in the result envelope.
    """


def _validate_positive_finite_float(value, name):
    if isinstance(value, bool):
        raise PipelineControllerAdapterError(f"{name} must be a finite positive number, got {value!r}")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise PipelineControllerAdapterError(f"{name} must be a finite positive number, got {value!r}")
    if not math.isfinite(result) or result <= 0:
        raise PipelineControllerAdapterError(f"{name} must be a finite positive number, got {value!r}")
    return result


def _validate_nonnegative_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PipelineControllerAdapterError(f"{name} must be a non-negative integer, got {value!r}")
    if value < 0:
        raise PipelineControllerAdapterError(f"{name} must be a non-negative integer, got {value!r}")
    return value


def _validate_wait_timeout(value):
    timeout = _validate_positive_finite_float(value, "timeout")
    if timeout > MAX_WAIT_TIMEOUT_SECONDS:
        raise PipelineControllerAdapterError(
            f"timeout must not exceed the adapter maximum of {MAX_WAIT_TIMEOUT_SECONDS}s, got {timeout!r}"
        )
    return timeout


def _bound_text(text, limit):
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _parse_controller_stdout(stdout_text):
    """Parse controller stdout into exactly one JSON object, or None if empty.

    Fails closed (raises PipelineControllerAdapterError) on anything other
    than empty stdout or exactly one JSON object: oversized stdout,
    malformed JSON, multiple JSON values, or a JSON value that decodes to
    something other than an object. A well-formed controller response is
    never reinterpreted -- it is returned unchanged as the parsed object.
    """
    if not stdout_text:
        return None
    stripped = stdout_text.strip()
    if stripped == "":
        return None
    if len(stripped) > MAX_CAPTURED_STDOUT_CHARS:
        raise PipelineControllerAdapterError("controller stdout exceeds the bounded capture limit")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(stripped)
    except (ValueError, TypeError) as exc:
        raise PipelineControllerAdapterError(f"controller stdout is not valid JSON: {exc}") from exc
    if stripped[end:].strip() != "":
        raise PipelineControllerAdapterError("controller stdout contains more than one JSON value")
    if not isinstance(value, dict):
        raise PipelineControllerAdapterError("controller stdout JSON is not an object")
    return value


def _invoke_controller(subcommand, argv_tail, timeout):
    """Invoke the controller as [CONTROLLER_PATH, subcommand, *argv_tail].

    Uses an argv list only, shell=False, capture_output=True, text=True,
    and the given bounded subprocess timeout -- no shell interpolation.
    Launch failures and subprocess timeouts are deterministic adapter
    errors and never masquerade as a controller exit code; a completed
    invocation's exit code, and its stdout JSON payload (if any), are
    reported back exactly as the controller produced them.
    """
    argv = [CONTROLLER_PATH, subcommand] + list(argv_tail)
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PipelineControllerAdapterError(
            f"controller {subcommand!r} timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise PipelineControllerAdapterError(
            f"failed to launch controller for {subcommand!r}: {exc}"
        ) from exc

    payload = _parse_controller_stdout(completed.stdout)
    return {
        "schema": RESULT_SCHEMA,
        "command": subcommand,
        "exit_code": completed.returncode,
        "payload": payload,
        "stderr": _bound_text(completed.stderr, MAX_CAPTURED_STDERR_CHARS),
    }


# --- MCP runtime adapter -----------------------------------------------
#
# Instantiating MCPServer and registering tools below is pure Python object
# construction: it performs no I/O, starts no server, spawns no subprocess,
# and never probes CONTROLLER_PATH. The server only actually starts when
# run as __main__.

mcp_server = MCPServer("pipeline-controller")


@mcp_server.tool(name="check_task")
def _tool_check_task(task_id: str):
    """Read-only structural validation of one Kanban task (`check`)."""
    return _invoke_controller("check", [task_id], DEFAULT_CHECK_TIMEOUT_SECONDS)


@mcp_server.tool(name="create_implementation")
def _tool_create_implementation(workdir: str, feature: str, body: str | None = None):
    """Create an implementation task (`create-implementation`)."""
    argv_tail = ["--workdir", workdir, "--feature", feature]
    if body is not None:
        argv_tail += ["--body", body]
    return _invoke_controller("create-implementation", argv_tail, DEFAULT_CREATE_TIMEOUT_SECONDS)


@mcp_server.tool(name="create_review")
def _tool_create_review(
    workdir: str,
    feature: str,
    implementation_task_id: str,
    review_instructions: str | None = None,
):
    """Create a review task (`create-review`)."""
    argv_tail = [
        "--workdir", workdir,
        "--feature", feature,
        "--implementation_task_id", implementation_task_id,
    ]
    if review_instructions is not None:
        argv_tail += ["--review_instructions", review_instructions]
    return _invoke_controller("create-review", argv_tail, DEFAULT_CREATE_TIMEOUT_SECONDS)


@mcp_server.tool(name="create_correction")
def _tool_create_correction(
    workdir: str,
    feature: str,
    implementation_task_id: str,
    review_task_id: str,
    review_summary: str | None = None,
    correction_instructions: str | None = None,
):
    """Create a correction task (`create-correction`)."""
    argv_tail = [
        "--workdir", workdir,
        "--feature", feature,
        "--implementation_task_id", implementation_task_id,
        "--review_task_id", review_task_id,
    ]
    if review_summary is not None:
        argv_tail += ["--review_summary", review_summary]
    if correction_instructions is not None:
        argv_tail += ["--correction_instructions", correction_instructions]
    return _invoke_controller("create-correction", argv_tail, DEFAULT_CREATE_TIMEOUT_SECONDS)


@mcp_server.tool(name="wait_task")
def _tool_wait_task(
    task_id: str,
    timeout: float,
    interval: float = 1.0,
    max_retries: int = 2,
):
    """Poll one Kanban task until terminal or timeout (`wait`).

    The MCP-level subprocess timeout is the validated wait timeout plus a
    small, fixed transport grace period (WAIT_TRANSPORT_GRACE_SECONDS): it
    only bounds this adapter's own subprocess call and never preempts or
    replaces the controller's own exit-4 wait-timeout semantics, which
    operate on the exact requested --timeout value passed through argv.
    """
    validated_timeout = _validate_wait_timeout(timeout)
    validated_interval = _validate_positive_finite_float(interval, "interval")
    validated_max_retries = _validate_nonnegative_int(max_retries, "max_retries")

    argv_tail = [
        task_id,
        "--timeout", str(validated_timeout),
        "--interval", str(validated_interval),
        "--max-retries", str(validated_max_retries),
    ]
    subprocess_timeout = validated_timeout + WAIT_TRANSPORT_GRACE_SECONDS
    return _invoke_controller("wait", argv_tail, subprocess_timeout)


@mcp_server.tool(name="archive_review")
def _tool_archive_review(workdir: str, review_task_id: str):
    """Deterministically archive a completed reviewer task (`archive-review`).

    Invokes archive-review only; does not trigger another controller phase.
    """
    argv_tail = ["--workdir", workdir, "--review_task_id", review_task_id]
    return _invoke_controller("archive-review", argv_tail, DEFAULT_ARCHIVE_REVIEW_TIMEOUT_SECONDS)


@mcp_server.tool(name="ready_to_commit")
def _tool_ready_to_commit(workdir: str, implementation_task_id: str, review_task_id: str):
    """Read-only technical attestation that a workdir is ready to commit
    (`ready-to-commit`).

    Invokes ready-to-commit only; does not trigger another controller phase. Never
    infers or grants human approval -- READY_TO_COMMIT remains strictly
    read-only, exactly as scripts/hermes-pipeline-controller.py implements
    it.
    """
    argv_tail = [
        "--workdir", workdir,
        "--implementation_task_id", implementation_task_id,
        "--review_task_id", review_task_id,
    ]
    return _invoke_controller("ready-to-commit", argv_tail, DEFAULT_READY_TO_COMMIT_TIMEOUT_SECONDS)


if __name__ == "__main__":
    mcp_server.run()
