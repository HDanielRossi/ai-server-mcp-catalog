"""Repo-only template for a hardened, budget-bounded Claude CLI bridge.

This module is a template: it is copied and installed by the operator into
its running location. It doubles as a real, executable MCP runtime adapter:
importing it is pure and side-effect free (no subprocess, no ledger writes,
no server startup), but running it as `__main__` (via run_server()) starts
an MCP server (name "claude-bridge") exposing a single `run` tool.

Unlike the other bridge templates in this directory, ``run`` itself
legitimately performs side effects when called: it writes a small JSON
ledger to disk under a lock and spawns the Claude CLI as a subprocess.
Those side effects are the whole point (call-budget enforcement across
process restarts), so they are kept narrow and explicit:

- The only filesystem writes are the ledger file, its lock file, and their
  containing directory (created if missing).
- The only subprocess spawned is the Claude CLI itself, and only through
  the ``_subprocess_run`` injection seam (default: ``subprocess.run``).
- ``run`` never imposes a token cap or a default dollar cap: the
  ``--max-budget-usd`` flag is appended to argv only when the caller passes
  an explicit ``max_budget_usd``.

Call budget accounting is keyed by ``task_id`` and is fail-closed: a
malformed ledger raises ``LedgerCorruptionError`` rather than being reset
or repaired, and a reserved call is always consumed even if the underlying
subprocess call fails. ``task_id`` is optional: when omitted, calls are
accounted against a per-(workdir, prompt) anonymous ledger bucket, keyed by
a deterministic hash (never the raw prompt text) and bounded by both the
usual call-budget threshold and a rolling TTL window (see
``ANONYMOUS_BUDGET_TTL_SECONDS``), so unrelated legacy callers no longer
share or starve each other's budget.

The canonical public contract is ``run(workdir, prompt)``: ``workdir`` and
``prompt`` are the first two positional parameters, and every other
parameter (including ``task_id``) is optional and keyword-capable.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from mcp.server import MCPServer


PROJECTS_ROOT = "/opt/ai/projects"

CALL_BUDGET_THRESHOLD = 4

CALL_TAGS = {
    1: ["normal"],
    2: ["normal"],
    3: ["exceptional", "budget-warning"],
}

REQUIRED_CLAUDE_FLAGS = ["--print", "--output-format", "json", "--no-session-persistence"]

REQUIRED_TELEMETRY_KEYS = (
    "duration_ms",
    "duration_api_ms",
    "num_turns",
    "total_cost_usd",
    "session_id",
    "subtype",
    "is_error",
    "usage",
)

REQUIRED_USAGE_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)

LEDGER_FILENAME = "ledger.json"
LEDGER_LOCK_FILENAME = "ledger.lock"
LEDGER_VERSION = 1

# Prefix for deterministic, per-(workdir, prompt) anonymous ledger keys used
# when task_id is omitted. Never derived from randomness, so the same legacy
# caller identity always maps to the same bounded bucket; distinct callers
# (different workdir or different prompt) get independent buckets.
ANONYMOUS_KEY_PREFIX = "legacy:"

# Rolling TTL window (seconds) for anonymous ledger buckets: once a bucket's
# window has elapsed, its call count resets, so an exhausted legacy caller
# identity is usable again without needing an explicit task_id.
ANONYMOUS_BUDGET_TTL_SECONDS = 3600

RESERVED_PENDING_ERROR = "reserved-pending"

# Time source for anonymous-bucket TTL accounting only. A module attribute
# (not a hardcoded time.monotonic() call) so tests can inject a fake clock.
_now = time.monotonic


class BridgeError(Exception):
    """Raised when bridge inputs fail validation."""


class BudgetExhaustedError(BridgeError):
    """Raised when a task_id has exhausted its call budget."""


class LedgerCorruptionError(BridgeError):
    """Raised when the on-disk ledger fails schema validation. Never auto-repaired."""


def _default_state_dir():
    template_dir = os.path.dirname(os.path.realpath(__file__))
    repo_root = os.path.dirname(template_dir)
    return os.path.join(repo_root, "state", "claude_bridge_ledger")


def _validate_workdir(workdir, root):
    if not workdir:
        raise BridgeError("workdir must be non-empty")
    if not os.path.isabs(workdir):
        raise BridgeError(f"workdir must be an absolute path, got: {workdir}")
    real_root = os.path.realpath(root)
    real_workdir = os.path.realpath(workdir)
    if real_workdir == real_root or not real_workdir.startswith(real_root + os.sep):
        raise BridgeError(f"workdir must be a strict descendant of {real_root}, got {real_workdir}")
    if not os.path.isdir(real_workdir):
        raise BridgeError(f"workdir does not exist or is not a directory: {real_workdir}")
    return real_workdir


def _validate_changed_paths(changed_paths, workdir_real):
    if not changed_paths:
        return []

    normalized = []
    for entry in changed_paths:
        if not isinstance(entry, str) or entry == "":
            raise BridgeError(f"changed path must be a non-empty string: {entry!r}")
        if entry.startswith("/"):
            raise BridgeError(f"changed path must be relative, got absolute path: {entry}")
        parts = re.split(r"[\\/]", entry)
        if ".." in parts:
            raise BridgeError(f"changed path must not contain '..' components: {entry}")
        candidate_real = os.path.realpath(os.path.join(workdir_real, entry))
        if candidate_real != workdir_real and not candidate_real.startswith(workdir_real + os.sep):
            raise BridgeError(f"changed path escapes workdir: {entry}")
        normalized.append(entry)

    return normalized


def _tags_for_call(call_number):
    tags = CALL_TAGS.get(call_number)
    if tags is None:
        raise BridgeError(f"no tags defined for call_number {call_number}")
    return list(tags)


def _load_ledger(ledger_path):
    if not os.path.exists(ledger_path):
        return {"version": LEDGER_VERSION, "tasks": {}}

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise LedgerCorruptionError(f"ledger file is malformed JSON: {ledger_path}") from e

    if not isinstance(data, dict):
        raise LedgerCorruptionError(f"ledger file is not a JSON object: {ledger_path}")
    if data.get("version") != LEDGER_VERSION:
        raise LedgerCorruptionError(f"ledger file missing or invalid 'version': {ledger_path}")
    if not isinstance(data.get("tasks"), dict):
        raise LedgerCorruptionError(f"ledger file missing or invalid 'tasks': {ledger_path}")
    for tid, task_entry in data["tasks"].items():
        if not isinstance(task_entry, dict):
            raise LedgerCorruptionError(f"ledger file has invalid entry for task {tid!r}: {ledger_path}")
        if isinstance(tid, str) and tid.startswith(ANONYMOUS_KEY_PREFIX):
            if not isinstance(task_entry.get("calls"), int) or isinstance(task_entry.get("calls"), bool):
                raise LedgerCorruptionError(f"ledger file has invalid 'calls' for anonymous bucket {tid!r}: {ledger_path}")
            if not isinstance(task_entry.get("window_started_at"), (int, float)):
                raise LedgerCorruptionError(
                    f"ledger file has invalid 'window_started_at' for anonymous bucket {tid!r}: {ledger_path}"
                )
        elif not isinstance(task_entry.get("calls"), list):
            raise LedgerCorruptionError(f"ledger file has invalid 'calls' for task {tid!r}: {ledger_path}")

    return data


def _anonymous_ledger_key(workdir_real, prompt):
    """Deterministic, per-(workdir, prompt) anonymous ledger key.

    Never includes the raw prompt text: only its hash. Distinct workdirs or
    distinct prompts always yield distinct keys, so unrelated legacy callers
    get independent budgets instead of sharing one fixed bucket.
    """
    fp = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    eff = hashlib.sha256("\x00".join((workdir_real, fp)).encode("utf-8")).hexdigest()
    return ANONYMOUS_KEY_PREFIX + eff[:16]


def _atomic_write_json(path, data):
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-ledger-", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


@contextlib.contextmanager
def _ledger_lock(lock_path):
    fd = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


def _validate_telemetry(obj):
    if not isinstance(obj, dict):
        return False
    for key in REQUIRED_TELEMETRY_KEYS:
        if key not in obj:
            return False
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return False
    for key in REQUIRED_USAGE_KEYS:
        if key not in usage:
            return False
    return True


def _failed_entry(call_number, tags, error):
    return {
        "duration_ms": None,
        "duration_api_ms": None,
        "num_turns": None,
        "total_cost_usd": None,
        "session_id": None,
        "subtype": None,
        "is_error": None,
        "iterations": None,
        "modelUsage": None,
        "usage": None,
        "accepted_at_call": call_number,
        "tags": tags,
        "outcome": "failed",
        "error": error,
    }


def _ok_entry(result, call_number, tags):
    usage = result.get("usage") or {}
    return {
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "num_turns": result.get("num_turns"),
        "total_cost_usd": result.get("total_cost_usd"),
        "session_id": result.get("session_id"),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "iterations": result.get("iterations", None),
        "modelUsage": result.get("modelUsage", None),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "iterations": usage.get("iterations", None),
        },
        "accepted_at_call": call_number,
        "tags": tags,
        "outcome": "ok",
        "error": None,
    }


def _build_argv(claude_executable, max_budget_usd, prompt):
    argv = [claude_executable or "claude"] + list(REQUIRED_CLAUDE_FLAGS)
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    argv.append(prompt)
    return argv


def run(
    workdir,
    prompt,
    task_id=None,
    changed_paths=None,
    test_command=None,
    max_budget_usd=None,
    *,
    claude_executable=None,
    state_dir=None,
    projects_root=None,
    _subprocess_run=None,
):
    """Run one budgeted Claude CLI call and return a result dict.

    Canonical contract: ``run(workdir, prompt)``. ``workdir`` and ``prompt``
    are the first two positional parameters; every parameter after them,
    including ``task_id``, is optional and keyword-capable.

    ``task_id`` keys the on-disk call-budget ledger. When omitted (None),
    calls are accounted against a deterministic per-(workdir, prompt)
    anonymous bucket (see ``_anonymous_ledger_key``) bounded by both the call
    budget threshold and a rolling ``ANONYMOUS_BUDGET_TTL_SECONDS`` window, so
    unrelated legacy callers don't share or exhaust each other's budget, and
    an exhausted bucket becomes usable again once its window elapses.

    ``max_budget_usd=None`` means no dollar cap: ``--max-budget-usd`` is only
    appended to argv when a value is explicitly supplied. ``projects_root``
    defaults to ``PROJECTS_ROOT`` when None and exists so tests can inject an
    isolated root without monkeypatching the module constant. ``test_command``
    is accepted for API symmetry with the pipeline bridge but does not affect
    argv construction.
    """
    del test_command

    root = projects_root if projects_root is not None else PROJECTS_ROOT
    workdir_real = _validate_workdir(workdir, root)
    normalized_changed_paths = _validate_changed_paths(changed_paths, workdir_real)
    del normalized_changed_paths  # validated for side effect (raises on violation); not otherwise consumed here

    if task_id is None:
        ledger_key = _anonymous_ledger_key(workdir_real, prompt)
        anonymous = True
    elif not task_id:
        raise BridgeError("task_id must be non-empty when provided")
    else:
        ledger_key = task_id
        anonymous = False

    resolved_state_dir = state_dir if state_dir is not None else _default_state_dir()
    os.makedirs(resolved_state_dir, exist_ok=True)
    ledger_path = os.path.join(resolved_state_dir, LEDGER_FILENAME)
    lock_path = os.path.join(resolved_state_dir, LEDGER_LOCK_FILENAME)

    run_fn = _subprocess_run if _subprocess_run is not None else subprocess.run

    with _ledger_lock(lock_path):
        ledger = _load_ledger(ledger_path)

        if anonymous:
            entry = ledger["tasks"].get(ledger_key)
            now = _now()
            if entry is None:
                entry = {"calls": 0, "window_started_at": now}
                ledger["tasks"][ledger_key] = entry
            elif (now - entry["window_started_at"]) >= ANONYMOUS_BUDGET_TTL_SECONDS:
                entry["calls"] = 0
                entry["window_started_at"] = now

            call_number = entry["calls"] + 1
            if call_number >= CALL_BUDGET_THRESHOLD:
                raise BudgetExhaustedError(
                    f"task_id={ledger_key!r} has reached the call budget threshold of {CALL_BUDGET_THRESHOLD}"
                )

            tags = _tags_for_call(call_number)
            entry["calls"] = call_number
            _atomic_write_json(ledger_path, ledger)
        else:
            task_entry = ledger["tasks"].setdefault(ledger_key, {"calls": []})
            calls = task_entry["calls"]
            call_number = len(calls) + 1

            if call_number >= CALL_BUDGET_THRESHOLD:
                raise BudgetExhaustedError(
                    f"task_id={ledger_key!r} has reached the call budget threshold of {CALL_BUDGET_THRESHOLD}"
                )

            tags = _tags_for_call(call_number)
            calls.append(_failed_entry(call_number, tags, RESERVED_PENDING_ERROR))
            _atomic_write_json(ledger_path, ledger)

    argv = _build_argv(claude_executable, max_budget_usd, prompt)

    outcome = "failed"
    error = None
    parsed_result = None

    try:
        proc = run_fn(argv, cwd=workdir_real, capture_output=True, text=True, timeout=300)
    except Exception:
        error = "subprocess-failed"
    else:
        try:
            parsed_result = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            error = "json-decode-failed"
            parsed_result = None
        else:
            if _validate_telemetry(parsed_result):
                outcome = "ok"
            else:
                error = "telemetry-invalid"
                parsed_result = None

    final_entry = _ok_entry(parsed_result, call_number, tags) if outcome == "ok" else _failed_entry(
        call_number, tags, error
    )

    if not anonymous:
        with _ledger_lock(lock_path):
            ledger = _load_ledger(ledger_path)
            task_entry = ledger["tasks"].setdefault(ledger_key, {"calls": []})
            calls = task_entry["calls"]
            if len(calls) < call_number:
                raise LedgerCorruptionError(
                    f"ledger lost the reservation for task_id={ledger_key!r} call_number={call_number}"
                )
            calls[call_number - 1] = final_entry
            _atomic_write_json(ledger_path, ledger)

    return {
        "call_number": call_number,
        "tags": tags,
        "outcome": outcome,
        "result": parsed_result if outcome == "ok" else None,
        "ledger_entry": final_entry,
        "argv": argv,
        "task_id": ledger_key,
    }


# --- MCP runtime adapter -----------------------------------------------
#
# Instantiating MCPServer and registering the tool below is pure Python
# object construction: it performs no I/O, starts no server, and spawns no
# subprocess. The server only actually starts when run_server() is invoked,
# guarded by the __main__ check below.

mcp_server = MCPServer("claude-bridge")


@mcp_server.tool(name="run")
def _tool_run(
    workdir,
    prompt,
    task_id=None,
    changed_paths=None,
    test_command=None,
    max_budget_usd=None,
):
    """Run one budgeted Claude CLI call and return a result dict.

    Canonical contract: call with just ``{"workdir": ..., "prompt": ...}``.
    ``task_id`` is optional; when omitted, calls are accounted against a
    single fixed anonymous ledger bucket so budget bounds still apply.
    """
    return run(
        workdir,
        prompt,
        task_id=task_id,
        changed_paths=changed_paths,
        test_command=test_command,
        max_budget_usd=max_budget_usd,
    )


def run_server():
    mcp_server.run()


if __name__ == "__main__":
    run_server()
