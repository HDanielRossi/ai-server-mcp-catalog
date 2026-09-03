#!/usr/bin/env python3
"""Hermes Kanban pipeline controller.

Read-only structural validation of one Kanban task, using exactly two
authorized `hermes kanban` JSON commands. Fail-closed: any transport
problem, CLI usage problem, or non-exact top-level shape is a hard
failure (exit 3); any structural/content validation failure is an
aggregated, reported failure (exit 2); only a fully valid task is exit 0.
"""

import argparse
import math
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

ALLOWED_ROOT = Path("/opt/ai/projects").resolve()

SHOW_KEYS = frozenset({
    "task", "latest_summary", "parents", "children", "comments", "events", "runs",
})
TASK_KEYS = frozenset({
    "id", "title", "body", "assignee", "status", "priority", "tenant",
    "workspace_kind", "workspace_path", "branch_name", "project_id", "created_by",
    "created_at", "started_at", "completed_at", "result", "skills", "max_retries",
    "model_override", "provider_override", "session_id", "workflow_template_id",
    "current_step_key",
})
RUN_KEYS = frozenset({
    "id", "profile", "step_key", "status", "outcome", "started_at", "ended_at",
    "summary", "error", "metadata", "worker_pid",
})
EVENT_KINDS = frozenset({
    "created", "claimed", "spawned", "heartbeat", "completed", "blocked",
    "unblocked", "commented", "block_loop_detected", "archived",
})
TASK_STATUSES = frozenset({"done", "archived"})
RUN_STATUSES = frozenset({"done", "blocked"})
EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_TRANSPORT = 3
EXIT_TIMEOUT = 4
WAIT_TERMINAL_STATUSES = frozenset({"done", "archived", "blocked"})
SLEEP = time.sleep
MONOTONIC = time.monotonic
ARCHIVE_HELPER_PATH = "/usr/local/bin/review-archive-bridge"
ARCHIVE_HELPER_TIMEOUT = 120

REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v1"
REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v2"
REPO_STATE_TIMEOUT_SECONDS = 30
KANBAN_READ_TIMEOUT_SECONDS = 30
GIT_DIFF_CHECK_TIMEOUT_SECONDS = 30
CONFLICT_STATUS_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})
REVIEW_ARCHIVE_ENVELOPE_KEYS = frozenset({
    "schema", "workdir", "implementation_task_id", "review_task_id",
    "review_run_id", "review_completed_at", "verdict", "verdict_source",
    "repository_state", "archive_envelope_sha256",
})
REVIEW_ARCHIVE_FILENAME_RE_TEMPLATE = r"^\d{8}_\d{6}-%s\.md$"
REVIEW_ARCHIVE_JSON_BLOCK_RE = re.compile(r"```json\r?\n(.*?)\r?\n```", re.DOTALL)
FORMAL_REVIEW_MARKER = "HERMES_FORMAL_REVIEW_V1"
BOOTSTRAP_PROVENANCE_SCHEMA = "hermes.implementation-provenance/operator-bootstrap/v1"
BOOTSTRAP_REASON = "IMPLEMENTATION_BOOTSTRAP_PROVENANCE_GAP"


class CliUsageError(Exception):
    """A CLI usage problem, mapped to exit code 3."""


class TransportError(Exception):
    """A launch, exit-code, JSON, or top-level-shape failure, mapped to exit code 3."""


class WaitTransportError(Exception):
    """A read-level transport failure in the `wait` round; retryable within the round."""


class WaitStructuralError(Exception):
    """A zero-exit read whose payload shape/id/status violates the fail-closed chain."""


class WorkdirValidationError(Exception):
    """A workdir failed structural or containment validation."""


class ValidationBlock(Exception):
    """A phase-scoped validation failure, mapped to exit code 2."""

    def __init__(self, phase, reason):
        super().__init__(reason)
        self.phase = phase
        self.reason = reason


class VerdictBlock(Exception):
    """A verdict-classification failure; callers map this to a phase-scoped block."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ReadyToCommitReject(Exception):
    """A ready-to-commit structural/content rejection, mapped to exit code 2."""

    def __init__(self, reason_code, reason):
        super().__init__(reason)
        self.reason_code = reason_code
        self.reason = reason


class RepositoryStateError(Exception):
    """A repository-state capture content/stability failure (not a transport failure)."""


class Exit3ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliUsageError(message)


def build_parser():
    parser = Exit3ArgumentParser(prog="hermes-pipeline-controller.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", prog="check", description="check one Kanban task")
    check.add_argument("task_id")

    create_impl = subparsers.add_parser(
        "create-implementation", prog="create-implementation",
        description="create an implementation task",
    )
    create_impl.add_argument("--workdir", required=True)
    create_impl.add_argument("--feature", required=True)
    create_impl.add_argument("--body", default=None)

    bootstrap = subparsers.add_parser(
        "register-bootstrap-implementation", prog="register-bootstrap-implementation",
        description="register an explicitly operator-authored implementation provenance record",
    )
    bootstrap.add_argument("--workdir", required=True)
    bootstrap.add_argument("--feature", required=True)
    bootstrap.add_argument("--reason", required=True)
    bootstrap.add_argument("--base-sha", required=True)
    bootstrap.add_argument("--implementation-sha", required=True)
    bootstrap.add_argument("--validation-evidence", required=True,
                           help="JSON array of structured validation result objects")

    create_review = subparsers.add_parser(
        "create-review", prog="create-review",
        description="create a review task",
    )
    create_review.add_argument("--workdir", required=True)
    create_review.add_argument("--feature", required=True)
    create_review.add_argument("--implementation_task_id", required=True)
    create_review.add_argument("--review_instructions", default=None)

    create_correction = subparsers.add_parser(
        "create-correction", prog="create-correction",
        description="create a correction task",
    )
    create_correction.add_argument("--workdir", required=True)
    create_correction.add_argument("--feature", required=True)
    create_correction.add_argument("--implementation_task_id", required=True)
    create_correction.add_argument("--review_task_id", required=True)
    create_correction.add_argument("--review_summary", default=None)
    create_correction.add_argument("--correction_instructions", default=None)

    wait_p = subparsers.add_parser(
        "wait", prog="wait",
        description="poll one Kanban task until it reaches a terminal status",
    )
    wait_p.add_argument("task_id")
    wait_p.add_argument("--timeout", required=True, type=_type_positive_finite_float)
    wait_p.add_argument("--interval", default=1.0, type=_type_positive_finite_float)
    wait_p.add_argument("--max-retries", default=2, type=_type_nonnegative_int)

    archive_review_p = subparsers.add_parser(
        "archive-review", prog="archive-review",
        description="deterministically archive a completed reviewer task",
    )
    archive_review_p.add_argument("--workdir", required=True)
    archive_review_p.add_argument("--review_task_id", required=True)

    ready_p = subparsers.add_parser(
        "ready-to-commit", prog="ready-to-commit",
        description="read-only attestation that a workdir is ready to commit",
    )
    ready_p.add_argument("--workdir", required=True)
    ready_p.add_argument("--implementation_task_id", required=True)
    ready_p.add_argument("--review_task_id", required=True)

    return parser


def stable_key(workdir, feature, phase):
    raw = ("%s|%s|%s" % (workdir, feature, phase)).lower().strip()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return "pipeline:%s:%s" % (phase, digest)


def validate_workdir(workdir):
    if not isinstance(workdir, str) or not workdir:
        raise WorkdirValidationError("workdir must be a non-empty string")
    if not Path(workdir).is_absolute():
        raise WorkdirValidationError("workdir must be an absolute path")
    resolved = Path(workdir).resolve()
    if not (resolved.exists() and resolved.is_dir()):
        raise WorkdirValidationError(
            "workdir does not exist or is not a directory: " + str(resolved)
        )
    try:
        resolved.relative_to(ALLOWED_ROOT)
    except ValueError:
        raise WorkdirValidationError(
            "workdir is outside the allowed root " + str(ALLOWED_ROOT) + ": " + str(resolved)
        )
    return resolved


def valid_task_id(tid):
    if not isinstance(tid, str) or tid == "":
        return False
    if re.fullmatch(r"t_[a-z0-9]+", tid):
        return True
    if tid.isalnum() and len(tid) >= 3:
        return True
    return False


def classify_verdict(run):
    """Authoritative verdict classification shared by create-correction and archive-review.

    Prefers run.metadata.verdict (exactly "PASS" or "CHANGES REQUIRED") over the narrative
    run.summary. Falls back to summary only when the metadata verdict is absent, None, or
    exactly "unknown"; any other metadata verdict value blocks outright without ever
    consulting summary (an authoritative metadata verdict must not be second-guessed by
    narrative marker mentions).
    """
    metadata = run.get("metadata")
    if isinstance(metadata, dict):
        verdict = metadata.get("verdict")
        if verdict == "PASS":
            return "PASS", "metadata"
        if verdict == "CHANGES REQUIRED":
            return "CHANGES REQUIRED", "metadata"
        if not (verdict is None or verdict == "unknown"):
            raise VerdictBlock("invalid metadata verdict: %r" % (verdict,))

    summary = run.get("summary")
    if not isinstance(summary, str):
        raise VerdictBlock("summary must be a string, got %r" % (summary,))
    has_pass = "PASS" in summary
    has_cr = "CHANGES REQUIRED" in summary
    if has_cr and not has_pass:
        return "CHANGES REQUIRED", "summary"
    if has_pass and not has_cr:
        return "PASS", "summary"
    raise VerdictBlock("ambiguous or unknown summary verdict: %r" % (summary,))


def _is_formal_review_task(task):
    """Identify only reviews created by the formal pipeline contract."""
    return (
        isinstance(task, dict)
        and task.get("assignee") == "reviewer"
        and isinstance(task.get("body"), str)
        and FORMAL_REVIEW_MARKER in task["body"]
    )


def _formal_review_metadata_errors(task, parents, runs):
    """Return fail-closed errors for a marked formal review completion.

    This is deliberately scoped by FORMAL_REVIEW_MARKER so historical and
    unrelated reviewer tasks retain their existing check compatibility.
    """
    errors = []
    if not isinstance(parents, list) or len(parents) != 1 or not valid_task_id(parents[0]):
        errors.append("formal review requires exactly one valid implementation parent")
        return errors
    try:
        latest = select_latest_run("formal-review", runs)
    except ValidationBlock as exc:
        return [exc.reason]
    if latest.get("profile") != "reviewer":
        errors.append("formal review latest run profile is %r, expected 'reviewer'" % latest.get("profile"))
    if latest.get("status") != "done":
        errors.append("formal review latest run status is %r, expected 'done'" % latest.get("status"))
    if latest.get("outcome") != "completed":
        errors.append("formal review latest run outcome is %r, expected 'completed'" % latest.get("outcome"))
    metadata = latest.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("FORMAL_REVIEW_METADATA_INVALID: metadata must be a JSON object")
        return errors
    if metadata.get("implementation_task_id") != parents[0]:
        errors.append("FORMAL_REVIEW_METADATA_INVALID: implementation_task_id mismatch")
    if metadata.get("verdict") not in ("PASS", "CHANGES REQUIRED"):
        errors.append("FORMAL_REVIEW_METADATA_INVALID: verdict must be PASS or CHANGES REQUIRED")
    if metadata.get("mutation_performed") is not False:
        errors.append("FORMAL_REVIEW_METADATA_INVALID: mutation_performed must be exactly false")
    try:
        _validate_review_metadata_repository_state(metadata, task.get("workspace_path"))
    except (ReadyToCommitReject, TypeError) as exc:
        errors.append("FORMAL_REVIEW_METADATA_INVALID: " + str(exc))
    return errors


def select_latest_run(phase, runs):
    """Select the run with the maximum integer id; never fall back on an invalid latest run."""
    if not isinstance(runs, list) or not runs:
        raise ValidationBlock(phase, "runs must be a non-empty array")
    ids = []
    for r in runs:
        if not isinstance(r, dict):
            raise ValidationBlock(phase, "malformed run entry: %r" % (r,))
        rid = r.get("id")
        if isinstance(rid, bool) or not isinstance(rid, int):
            raise ValidationBlock(phase, "malformed or missing run id: %r" % (rid,))
        ids.append(rid)
    if len(ids) != len(set(ids)):
        raise ValidationBlock(phase, "duplicate run ids in runs array")
    max_id = max(ids)
    for r in runs:
        if r["id"] == max_id:
            return r
    raise ValidationBlock(phase, "could not select latest run")


def extract_real_id(payload):
    if isinstance(payload, dict):
        candidate = payload
    elif isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], dict):
        candidate = payload[0]
    else:
        raise TransportError("create result has no task id")
    rid = candidate.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise TransportError("create result has no task id")
    return rid


def emit_success(phase, task_id, key, workdir, feature, parents):
    payload = {
        "phase": phase,
        "task_id": task_id,
        "idempotency_key": key,
        "workdir": workdir,
        "feature": feature,
        "parents": parents,
    }
    print(json.dumps(payload, separators=(",", ":")))


def emit_blocked(phase, reason):
    payload = {
        "phase": phase,
        "blocked": True,
        "reason": reason,
        "task_id": None,
    }
    print(json.dumps(payload, separators=(",", ":")))


def wait_for_task(task_id, timeout, interval, max_retries):
    """Poll task_id until it reaches a terminal status or the deadline expires.

    A poll round is a sequence of read attempts (up to max_retries + 1) that
    ends on either a valid read (terminal or non-terminal) or on exhausting
    the round's transport-retry allowance. The very first read of the whole
    call is unconditional only in the sense that no sleep precedes it; every
    read attempt — including the first — is gated by the deadline check
    first.
    """
    deadline = MONOTONIC() + timeout
    max_attempts = max_retries + 1
    last_status = None
    while True:
        attempts = 0
        while True:
            attempts += 1
            remaining = deadline - MONOTONIC()
            if remaining <= 0:
                _emit_wait_timeout(task_id, last_status, timeout)
                return EXIT_TIMEOUT
            try:
                status_val = read_wait_task(task_id, remaining)
            except subprocess.TimeoutExpired:
                if MONOTONIC() >= deadline:
                    _emit_wait_timeout(task_id, last_status, timeout)
                    return EXIT_TIMEOUT
                if attempts >= max_attempts:
                    _emit_wait_transport(task_id, attempts, "subprocess timeout")
                    return EXIT_TRANSPORT
                continue
            except WaitTransportError as exc:
                if attempts >= max_attempts:
                    _emit_wait_transport(task_id, attempts, str(exc))
                    return EXIT_TRANSPORT
                continue
            except WaitStructuralError as exc:
                _emit_wait_structural(task_id, str(exc))
                return EXIT_VALIDATION
            # successful read
            if status_val in WAIT_TERMINAL_STATUSES:
                _emit_wait_terminal(task_id, status_val)
                return EXIT_OK
            last_status = status_val
            break  # non-terminal success ends the retry round
        # `remaining` is the budget measured for the read that just succeeded.
        if remaining <= 0:
            _emit_wait_timeout(task_id, last_status, timeout)
            return EXIT_TIMEOUT
        SLEEP(min(interval, remaining))
        if remaining <= interval:
            # The sleep consumed the entire remaining budget: we are now at
            # (or past) the deadline, so don't start another read.
            _emit_wait_timeout(task_id, last_status, timeout)
            return EXIT_TIMEOUT


def _emit_wait_terminal(task_id, status):
    print(json.dumps(
        {"outcome": "terminal", "task_id": task_id, "status": status},
        separators=(",", ":"),
    ))


def _emit_wait_timeout(task_id, last_status, timeout_seconds):
    print(json.dumps(
        {
            "outcome": "timeout",
            "task_id": task_id,
            "last_status": last_status,
            "timeout_seconds": timeout_seconds,
        },
        separators=(",", ":"),
    ))


def _emit_wait_structural(task_id, error):
    print(json.dumps(
        {"outcome": "structural_error", "task_id": task_id, "error": error},
        separators=(",", ":"),
    ))


def _emit_wait_transport(task_id, attempts, error):
    sys.stderr.write("transport error: " + (error or "unknown") + "\n")
    print(json.dumps(
        {
            "outcome": "transport_error",
            "task_id": task_id,
            "attempts": attempts,
            "error": error or "unknown",
        },
        separators=(",", ":"),
    ))


def handle_wait(args):
    if not isinstance(args.task_id, str) or not args.task_id.strip():
        raise CliUsageError("task_id must be a non-empty string after stripping")
    timeout = parse_positive_finite_float(args.timeout)
    interval = parse_positive_finite_float(args.interval) if args.interval is not None else 1.0
    max_retries = parse_nonnegative_int(args.max_retries)
    return wait_for_task(args.task_id.strip(), timeout, interval, max_retries)


def validate_phase_inputs(phase, workdir, feature):
    if not isinstance(feature, str) or not feature.strip():
        raise ValidationBlock(phase, "feature must be a non-empty string")
    try:
        return validate_workdir(workdir)
    except WorkdirValidationError as exc:
        raise ValidationBlock(phase, str(exc))


def run_hermes_command(args):
    return subprocess.run(args, shell=False, capture_output=True, text=True, check=False)


def run_hermes_command_with_timeout(argv, budget):
    """Like run_hermes_command but bound by a deadline budget (seconds)."""
    return subprocess.run(
        argv, shell=False, capture_output=True, text=True, check=False,
        timeout=budget,
    )


def parse_positive_finite_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise CliUsageError("%r must be a finite positive float" % (value,))
    if not math.isfinite(result) or result <= 0:
        raise CliUsageError("%r must be a finite positive float > 0" % (value,))
    return result


def parse_nonnegative_int(value):
    try:
        text = str(value).strip()
        if text and text.lstrip("+-").isdigit():
            result = int(text)
        else:
            raise ValueError("not an integer")
    except ValueError:
        raise CliUsageError("%r must be a non-negative integer" % (value,))
    if result < 0:
        raise CliUsageError("%r must be >= 0" % (value,))
    return result


def _type_positive_finite_float(raw):
    try:
        return parse_positive_finite_float(raw)
    except CliUsageError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _type_nonnegative_int(raw):
    try:
        return parse_nonnegative_int(raw)
    except CliUsageError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def parse_json_stdout(completed, label):
    if completed.returncode != 0:
        detail = ((completed.stderr or "").strip() + " " + (completed.stdout or "").strip()).strip()
        raise TransportError(label + " exited " + str(completed.returncode) + ": " + detail)
    try:
        return json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise TransportError(label + " stdout is not valid JSON: " + str(exc))


def validate_exact_keys(value, expected, path, errors):
    if not isinstance(value, dict):
        errors.append(path + " must be a JSON object")
        return
    keys = set(value.keys())
    extra = keys - expected
    missing = expected - keys
    if extra:
        errors.append(
            path + " has unknown keys: " + ", ".join(k for k in sorted(extra) if isinstance(k, str))
        )
    if missing:
        errors.append(
            path + " is missing required keys: " + ", ".join(k for k in sorted(missing) if isinstance(k, str))
        )


def validate_task(task, task_id, errors):
    validate_exact_keys(task, TASK_KEYS, "task", errors)
    if not isinstance(task, dict):
        return
    if task.get("id") != task_id:
        errors.append("task.id is " + repr(task.get("id")) + ", expected " + repr(task_id))
    status = task.get("status")
    if status not in TASK_STATUSES:
        errors.append("task.status is " + repr(status) + ", expected one of done|archived")


def validate_runs(value, path, errors):
    if not isinstance(value, list):
        errors.append(path + " must be a JSON array")
        return
    for i, run in enumerate(value):
        p = "%s[%d]" % (path, i)
        if not isinstance(run, dict):
            errors.append(p + " must be a JSON object")
            continue
        validate_exact_keys(run, RUN_KEYS, p, errors)
        status = run.get("status")
        if status not in RUN_STATUSES:
            errors.append(p + ".status is " + repr(status) + ", expected one of done|blocked")


def validate_events(value, errors):
    if not isinstance(value, list):
        errors.append("events must be a JSON array")
        return
    for i, event in enumerate(value):
        p = "events[%d]" % i
        if not isinstance(event, dict):
            errors.append(p + " must be a JSON object")
            continue
        if "kind" not in event:
            errors.append(p + " is missing required key 'kind'")
            continue
        kind = event["kind"]
        if kind not in EVENT_KINDS:
            errors.append(
                p + ".kind is " + repr(kind) + ", expected one of "
                + ", ".join(sorted(EVENT_KINDS, key=lambda k: str(k)))
            )


def validate_show_top_level(show):
    if not isinstance(show, dict):
        raise TransportError("show top-level value must be a JSON object")
    keys = set(show.keys())
    if keys != SHOW_KEYS:
        raise TransportError("show top-level keys must be exactly " + ", ".join(sorted(SHOW_KEYS)))
    return show


def parse_wait_task(payload, expected_task_id):
    """Validate a `wait` read result; return the authoritative task status string.

    Reuses validate_show_top_level (exact-shape rule preserved); then checks
    task.id and task.status type. On any mismatch, raises WaitStructuralError.
    """
    try:
        show = validate_show_top_level(payload)
    except TransportError as exc:
        raise WaitStructuralError("wait read shape: " + str(exc)) from exc
    task = show.get("task")
    if not isinstance(task, dict):
        raise WaitStructuralError("wait read: show[task] must be a JSON object")
    if task.get("id") != expected_task_id:
        raise WaitStructuralError(
            "wait read: task.id is %r, expected %r" % (task.get("id"), expected_task_id)
        )
    status = task.get("status")
    if not isinstance(status, str) or status == "":
        raise WaitStructuralError(
            "wait read: task.status is %r (must be a non-empty string)" % (status,)
        )
    return status


def read_wait_task(task_id, budget):
    """Perform one read of task_id within subprocess timeout `budget` seconds.

    Returns the authoritative status string on success, or raises
    WaitTransportError / WaitStructuralError, or propagates subprocess.TimeoutExpired.
    """
    argv = ["hermes", "kanban", "show", task_id, "--json"]
    try:
        completed = run_hermes_command_with_timeout(argv, budget)
    except subprocess.TimeoutExpired:
        raise
    except OSError as exc:
        raise WaitTransportError(
            "hermes kanban show %s raised OSError: %s" % (task_id, exc)
        ) from exc
    if completed.returncode != 0:
        detail = ((completed.stderr or "") + " " + (completed.stdout or "")).strip()
        msg = "hermes kanban show %s exited %s" % (task_id, completed.returncode)
        if detail:
            msg += ": " + detail
        raise WaitTransportError(msg)
    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise WaitTransportError(
            "hermes kanban show %s stdout is not valid JSON: %s" % (task_id, exc)
        ) from exc
    return parse_wait_task(payload, task_id)


def validate_payloads(task_id, show, runs):
    errors = []
    validate_task(show.get("task"), task_id, errors)
    validate_runs(show.get("runs"), "show.runs", errors)
    validate_runs(runs, "runs", errors)
    validate_events(show.get("events"), errors)
    if not isinstance(show.get("runs"), list) or not isinstance(runs, list):
        errors.append("show.runs and runs must both be arrays to compare equality")
    elif show["runs"] != runs:
        errors.append("show.runs and standalone runs are not equal as parsed JSON")
    return errors


def emit_result(task_id, show_exit_code, runs_exit_code, valid, errors):
    payload = {
        "task_id": task_id,
        "show_exit_code": show_exit_code,
        "runs_exit_code": runs_exit_code,
        "valid": valid,
        "errors": errors,
    }
    print(json.dumps(payload, separators=(",", ":")))


def check_task(task_id):
    spec = "hermes kanban show " + task_id + " --json"
    show_completed = run_hermes_command(["hermes", "kanban", "show", task_id, "--json"])
    spec2 = "hermes kanban runs " + task_id + " --json"
    runs_completed = run_hermes_command(["hermes", "kanban", "runs", task_id, "--json"])
    show = parse_json_stdout(show_completed, spec)
    runs = parse_json_stdout(runs_completed, spec2)
    show = validate_show_top_level(show)
    errors = validate_payloads(task_id, show, runs)
    task = show.get("task")
    if not errors and _is_formal_review_task(task):
        errors.extend(_formal_review_metadata_errors(task, show.get("parents"), show.get("runs")))
    valid = not errors
    emit_result(task_id, show_completed.returncode, runs_completed.returncode, valid, errors)
    return EXIT_OK if valid else EXIT_VALIDATION


def create_implementation(args):
    phase = "implementation"
    resolved = validate_phase_inputs(phase, args.workdir, args.feature)
    feature = args.feature

    key = stable_key(str(resolved), feature, "implementation")
    body = args.body.strip() if args.body else ""
    if not body:
        body = (
            "Implement %s in workspace %s, staying within the scope of this workdir; "
            "do not authorize changes outside it." % (feature, str(resolved))
        )

    argv = [
        "hermes", "kanban", "create",
        "Implement %s in %s" % (feature, resolved.name),
        "--body", body,
        "--assignee", "coder-claude",
        "--workspace", "dir:" + str(resolved),
        "--idempotency-key", key,
        "--created-by", "pipeline_bridge",
        "--max-retries", "3",
        "--json",
    ]
    completed = run_hermes_command(argv)
    payload = parse_json_stdout(completed, "hermes kanban create")
    task_id = extract_real_id(payload)
    emit_success(phase, task_id, key, str(resolved), feature, [])
    return EXIT_OK


def register_bootstrap_implementation(args):
    """Register operator-authored provenance without impersonating a worker."""
    phase = "register-bootstrap-implementation"
    resolved = validate_phase_inputs(phase, args.workdir, args.feature)
    if args.reason != BOOTSTRAP_REASON:
        raise ValidationBlock(phase, "unsupported bootstrap reason: %r" % args.reason)
    sha_re = r"[0-9a-f]{40}"
    if not re.fullmatch(sha_re, args.base_sha) or not re.fullmatch(sha_re, args.implementation_sha):
        raise ValidationBlock(phase, "base-sha and implementation-sha must be 40 lowercase hex characters")
    try:
        evidence = json.loads(args.validation_evidence)
    except (TypeError, ValueError) as exc:
        raise ValidationBlock(phase, "validation-evidence must be valid JSON: %s" % exc)
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, dict) for item in evidence):
        raise ValidationBlock(phase, "validation-evidence must be a non-empty JSON array of objects")
    required_ops = {"pytest_full", "audit", "git_diff_check"}
    seen = set()
    for item in evidence:
        if set(item) != {"operation", "exit_code", "status"}:
            raise ValidationBlock(phase, "each validation record must contain exactly operation, exit_code, status")
        if not isinstance(item["operation"], str) or item["operation"] in seen:
            raise ValidationBlock(phase, "validation operations must be unique strings")
        if item["status"] != "PASS" or item["exit_code"] != 0:
            raise ValidationBlock(phase, "all validation operations must have status PASS and exit_code 0")
        seen.add(item["operation"])
    if not required_ops.issubset(seen):
        raise ValidationBlock(phase, "missing mandatory validation operations: %s" % sorted(required_ops - seen))
    try:
        state = capture_repository_state(resolved)
    except (RepositoryStateError, TransportError) as exc:
        raise ValidationBlock(phase, "cannot capture repository state: %s" % exc)
    if state["changed_paths"]:
        raise ValidationBlock(phase, "bootstrap provenance requires a clean worktree")
    head = state.get("head")
    if head != args.implementation_sha:
        raise ValidationBlock(phase, "implementation-sha does not match repository HEAD")
    provenance = {
        "schema": BOOTSTRAP_PROVENANCE_SCHEMA,
        "implementation_provenance": "operator-bootstrap",
        "implementation_task_id": "PENDING",
        "reason": args.reason,
        "workdir": os.path.realpath(str(resolved)),
        "base_sha": args.base_sha,
        "implementation_sha": args.implementation_sha,
        "repository_state": state,
        "repository_state_sha256": state["aggregate_sha256"],
        "changed_paths": [],
        "mutation_scope": "operator-authored repository implementation only; no worker run",
        "validation": evidence,
        "coder_worker_run": False,
    }
    key = stable_key(str(resolved), args.feature, "bootstrap-implementation")
    body = "Operator bootstrap implementation for %s. Explicit provenance; no coder worker run." % args.feature
    create = run_hermes_command([
        "hermes", "kanban", "create", "Bootstrap %s in %s" % (args.feature, resolved.name),
        "--body", body, "--workspace", "dir:" + str(resolved),
        "--idempotency-key", key, "--created-by", "pipeline_controller",
        "--max-retries", "0", "--json",
    ])
    task_id = extract_real_id(parse_json_stdout(create, "hermes kanban create"))
    provenance["implementation_task_id"] = task_id
    complete = run_hermes_command([
        "hermes", "kanban", "complete", task_id,
        "--summary", "operator-bootstrap provenance registered",
        "--metadata", json.dumps(provenance, separators=(",", ":")),
    ])
    if complete.returncode != 0:
        raise TransportError("hermes kanban complete failed: %s" % (complete.stderr or complete.stdout).strip())
    print(json.dumps({"phase": phase, "task_id": task_id, "workdir": str(resolved),
                      "implementation_provenance": "operator-bootstrap", "repository_state_sha256": state["aggregate_sha256"]},
                     separators=(",", ":")))
    return EXIT_OK


def create_review(args):
    phase = "review"
    resolved = validate_phase_inputs(phase, args.workdir, args.feature)
    feature = args.feature

    impl_id = args.implementation_task_id
    if not valid_task_id(impl_id):
        raise ValidationBlock(phase, "implementation_task_id is invalid: %r" % (impl_id,))

    key = stable_key(str(resolved), feature, "review:" + impl_id)
    body = args.review_instructions.strip() if args.review_instructions else ""
    if not body:
        body = "Review %s in workspace %s (implementation task %s)." % (
            feature, str(resolved), impl_id,
        )
    body = FORMAL_REVIEW_MARKER + "\n" + body

    argv = [
        "hermes", "kanban", "create",
        "Review %s in %s" % (feature, resolved.name),
        "--body", body,
        "--assignee", "reviewer",
        "--parent", impl_id,
        "--workspace", "dir:" + str(resolved),
        "--idempotency-key", key,
        "--created-by", "pipeline_bridge",
        "--max-retries", "1",
        "--json",
    ]
    completed = run_hermes_command(argv)
    payload = parse_json_stdout(completed, "hermes kanban create")
    task_id = extract_real_id(payload)
    emit_success(phase, task_id, key, str(resolved), feature, [impl_id])
    return EXIT_OK


def create_correction(args):
    phase = "correction"
    resolved = validate_phase_inputs(phase, args.workdir, args.feature)
    feature = args.feature

    impl_id = args.implementation_task_id
    if not valid_task_id(impl_id):
        raise ValidationBlock(phase, "implementation_task_id is invalid: %r" % (impl_id,))
    review_id = args.review_task_id
    if not valid_task_id(review_id):
        raise ValidationBlock(phase, "review_task_id is invalid: %r" % (review_id,))

    show_completed = run_hermes_command(["hermes", "kanban", "show", review_id, "--json"])
    show = parse_json_stdout(show_completed, "hermes kanban show " + review_id)
    show = validate_show_top_level(show)

    task = show.get("task")
    if not isinstance(task, dict) or task.get("id") != review_id:
        raise ValidationBlock(phase, "inconsistent review payload")
    status = task.get("status")
    if status != "done":
        raise ValidationBlock(phase, "review task is not done (status=%r)" % (status,))

    runs = show.get("runs")
    if not isinstance(runs, list) or not runs or not all(isinstance(r, dict) for r in runs):
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")
    if not all(isinstance(r.get("id"), (int, str)) for r in runs):
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")
    if _is_formal_review_task(task):
        formal_errors = _formal_review_metadata_errors(task, show.get("parents"), runs)
        if formal_errors:
            raise ValidationBlock(phase, "; ".join(formal_errors))
    try:
        selected = max(runs, key=lambda r: r["id"])
    except TypeError:
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")

    try:
        verdict, _source = classify_verdict(selected)
    except VerdictBlock as exc:
        raise ValidationBlock(
            phase, "ambiguous verdict, cannot create correction: %s" % exc.reason
        )
    if verdict == "PASS":
        raise ValidationBlock(phase, "review verdict was PASS, no correction authorized")

    summary = selected.get("summary")

    key = stable_key(str(resolved), feature, "correction:" + review_id)
    body_parts = [
        "Correct %s in workspace %s, staying within the scope of this workdir; "
        "do not authorize changes outside it." % (feature, str(resolved)),
        "Implementation task: %s. Review task: %s." % (impl_id, review_id),
        "Review verdict: %s" % summary,
    ]
    if args.correction_instructions and args.correction_instructions.strip():
        body_parts.append(args.correction_instructions.strip())
    body = " ".join(body_parts)

    argv = [
        "hermes", "kanban", "create",
        "Correct %s in %s" % (feature, resolved.name),
        "--body", body,
        "--assignee", "coder-claude",
        "--parent", impl_id,
        "--parent", review_id,
        "--workspace", "dir:" + str(resolved),
        "--idempotency-key", key,
        "--created-by", "pipeline_bridge",
        "--max-retries", "3",
        "--json",
    ]
    completed = run_hermes_command(argv)
    payload = parse_json_stdout(completed, "hermes kanban create")
    task_id = extract_real_id(payload)
    emit_success(phase, task_id, key, str(resolved), feature, [impl_id, review_id])
    return EXIT_OK


def archive_review(args):
    phase = "archive-review"

    try:
        resolved = validate_workdir(args.workdir)
    except WorkdirValidationError as exc:
        raise ValidationBlock(phase, str(exc))

    review_id = args.review_task_id
    if not valid_task_id(review_id):
        raise ValidationBlock(phase, "review_task_id is invalid: %r" % (review_id,))

    show_completed = run_hermes_command(["hermes", "kanban", "show", review_id, "--json"])
    show = parse_json_stdout(show_completed, "hermes kanban show " + review_id)
    show = validate_show_top_level(show)

    task = show.get("task")
    if not isinstance(task, dict):
        raise ValidationBlock(phase, "show.task must be a JSON object")
    if task.get("id") != review_id:
        raise ValidationBlock(
            phase, "task.id is %r, expected %r" % (task.get("id"), review_id)
        )

    if task.get("workspace_kind") != "dir":
        raise ValidationBlock(
            phase,
            "task.workspace_kind is %r, expected 'dir'" % (task.get("workspace_kind"),),
        )
    workspace_path = task.get("workspace_path")
    if (
        not isinstance(workspace_path, str)
        or not workspace_path
        or not Path(workspace_path).is_absolute()
    ):
        raise ValidationBlock(
            phase, "task.workspace_path is missing or malformed: %r" % (workspace_path,)
        )
    if Path(workspace_path).resolve() != resolved:
        raise ValidationBlock(
            phase,
            "task workspace %r does not match requested workdir %r"
            % (workspace_path, str(resolved)),
        )

    if task.get("assignee") != "reviewer":
        raise ValidationBlock(
            phase, "task.assignee is %r, expected 'reviewer'" % (task.get("assignee"),)
        )
    if task.get("status") != "done":
        raise ValidationBlock(
            phase, "task.status is %r, expected 'done'" % (task.get("status"),)
        )

    completed_at = task.get("completed_at")
    if isinstance(completed_at, bool) or not isinstance(completed_at, int) or completed_at <= 0:
        raise ValidationBlock(
            phase, "task.completed_at must be a positive int, got %r" % (completed_at,)
        )

    latest_run = select_latest_run(phase, show.get("runs"))

    if _is_formal_review_task(task):
        formal_errors = _formal_review_metadata_errors(task, show.get("parents"), show.get("runs"))
        if formal_errors:
            raise ValidationBlock(phase, "; ".join(formal_errors))

    if latest_run.get("profile") != "reviewer":
        raise ValidationBlock(
            phase,
            "latest run profile is %r, expected 'reviewer'" % (latest_run.get("profile"),),
        )
    if latest_run.get("status") != "done":
        raise ValidationBlock(
            phase, "latest run status is %r, expected 'done'" % (latest_run.get("status"),)
        )
    if latest_run.get("outcome") != "completed":
        raise ValidationBlock(
            phase,
            "latest run outcome is %r, expected 'completed'" % (latest_run.get("outcome"),),
        )

    try:
        verdict, _source = classify_verdict(latest_run)
    except VerdictBlock as exc:
        raise ValidationBlock(phase, "verdict blocked, cannot archive review: %s" % exc.reason)

    helper_argv = [ARCHIVE_HELPER_PATH, str(resolved), review_id]
    try:
        helper_completed = subprocess.run(
            helper_argv, shell=False, capture_output=True, text=True, check=False,
            timeout=ARCHIVE_HELPER_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError("review-archive-bridge timed out: %s" % exc)
    except OSError as exc:
        raise TransportError("review-archive-bridge failed to launch: %s" % exc)

    if helper_completed.returncode != 0:
        detail = (
            (helper_completed.stderr or "").strip() + " " + (helper_completed.stdout or "").strip()
        ).strip()
        raise TransportError(
            "review-archive-bridge exited %s: %s" % (helper_completed.returncode, detail)
        )

    payload = {
        "phase": phase,
        "outcome": "archive-succeeded",
        "review_task_id": review_id,
        "workdir": str(resolved),
        "verdict": verdict,
        "completed_at": completed_at,
    }
    print(json.dumps(payload, separators=(",", ":")))
    return EXIT_OK


# --- ready-to-commit: read-only attestation ---------------------------------
#
# Everything below is strictly read-only: it never authorizes or performs a
# commit, push, staging, filesystem write, or Kanban write. It only proves --
# via two authorized `hermes kanban` JSON reads, a git diff --check, and a
# deterministic repository-state fingerprint capture -- that a workdir's
# implementation/review pair is in a state a human could safely commit.

GIT_ENV_OVERRIDES = {
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def _deterministic_git_env():
    env = dict(os.environ)
    env.update(GIT_ENV_OVERRIDES)
    return env


def _sha256_canonical_excluding(obj, excluded_key):
    filtered = {k: v for k, v in obj.items() if k != excluded_key}
    encoded = json.dumps(filtered, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _hash_file_bytes(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _run_git_capture(argv, cwd, timeout=REPO_STATE_TIMEOUT_SECONDS):
    """Run a read-only git argv command; raise TransportError on launch/timeout, RepositoryStateError on failure exit."""
    try:
        completed = subprocess.run(
            argv, cwd=str(cwd), shell=False, capture_output=True,
            timeout=timeout, check=False, env=_deterministic_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError("git command timed out after %ss: %r" % (timeout, argv)) from exc
    except OSError as exc:
        raise TransportError("git command failed to launch: %r: %s" % (argv, exc)) from exc
    if completed.returncode != 0:
        stderr_text = completed.stderr.decode("utf-8", "replace")
        raise RepositoryStateError("git command failed (%s): %r: %s" % (completed.returncode, argv, stderr_text))
    return completed.stdout


def _run_git_text(argv, cwd, timeout=REPO_STATE_TIMEOUT_SECONDS):
    return _run_git_capture(argv, cwd, timeout).decode("utf-8", "replace")


def _git_path_list(argv, cwd):
    text = _run_git_text(argv, cwd)
    return [line for line in text.split("\n") if line != ""]


def _verify_git_repo_worktree(resolved_workdir):
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=str(resolved_workdir),
            shell=False, capture_output=True, text=True, timeout=REPO_STATE_TIMEOUT_SECONDS,
            check=False, env=_deterministic_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError("git rev-parse timed out: %s" % exc) from exc
    except OSError as exc:
        raise TransportError("git rev-parse failed to launch: %s" % exc) from exc
    if completed.returncode != 0 or completed.stdout.strip() != "true":
        raise RepositoryStateError("workdir is not a usable git repository: %s" % resolved_workdir)


def _git_path_is_ignored(resolved_workdir, relative_path):
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_path],
            cwd=str(resolved_workdir), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=False, timeout=REPO_STATE_TIMEOUT_SECONDS, shell=False, env=_deterministic_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError("git check-ignore timed out for %r: %s" % (relative_path, exc)) from exc
    except OSError as exc:
        raise TransportError("git check-ignore failed to launch for %r: %s" % (relative_path, exc)) from exc
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    stderr = result.stderr.decode("utf-8", "replace").strip()
    raise RepositoryStateError(
        "git check-ignore failed for %r: exit_code=%s stderr=%r" % (relative_path, result.returncode, stderr)
    )


def _reject_untracked_special_entries(resolved_workdir):
    """Fail closed on non-ignored filesystem entries Git cannot fingerprint (FIFOs, sockets, devices, ...)."""
    repo_root = Path(resolved_workdir)
    try:
        walker = os.walk(repo_root, topdown=True, followlinks=False)
        for root, dirs, files in walker:
            dirs[:] = [name for name in dirs if name != ".git"]
            for name in list(dirs) + list(files):
                full_path = Path(root) / name
                try:
                    file_stat = full_path.lstat()
                except OSError as exc:
                    raise RepositoryStateError("cannot inspect repository entry %s: %s" % (full_path, exc)) from exc
                mode = file_stat.st_mode
                if stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
                    continue
                relative_path = full_path.relative_to(repo_root).as_posix()
                if _git_path_is_ignored(resolved_workdir, relative_path):
                    continue
                raise RepositoryStateError(
                    "unsupported non-ignored special filesystem entry in worktree: %s" % relative_path
                )
    except RepositoryStateError:
        raise
    except OSError as exc:
        raise RepositoryStateError("cannot scan worktree for special filesystem entries: %s" % exc)


def _gitlink_paths(resolved_workdir, paths):
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
    status_text = _run_git_text(["git", "status", "--porcelain"], resolved_workdir)
    for line in status_text.split("\n"):
        if line == "":
            continue
        code = line[:2]
        if code in CONFLICT_STATUS_CODES:
            raise RepositoryStateError("unresolved merge conflict detected in git status: %s" % line)

    overlap = set(staged_paths) & set(unstaged_paths)
    if overlap:
        raise RepositoryStateError(
            "unresolved merge conflict detected (path both staged and unstaged): %s" % sorted(overlap)
        )

    all_changed = sorted(set(staged_paths) | set(unstaged_paths) | set(untracked_paths))
    gitlinks = _gitlink_paths(resolved_workdir, all_changed)
    if gitlinks:
        raise RepositoryStateError("changed submodule (gitlink) detected: %s" % sorted(gitlinks))

    submodule_status_text = _run_git_text(["git", "submodule", "status"], resolved_workdir)
    for line in submodule_status_text.split("\n"):
        if line == "":
            continue
        if line[0] in ("+", "-"):
            raise RepositoryStateError("changed submodule detected via git submodule status: %s" % line.strip())


def _describe_untracked_entry(resolved_workdir, relative_path):
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

    raise RepositoryStateError(
        "untracked path is a special file (not a regular file or symlink): %s" % relative_path
    )


def _capture_repository_state_once(resolved_workdir, canonical_workdir):
    """Capture one repository-state/v1 envelope. Read-only: no git object/index/file writes."""
    head = _run_git_text(["git", "rev-parse", "HEAD"], resolved_workdir).strip()

    staged_paths = _git_path_list(["git", "diff", "--name-only", "--cached"], resolved_workdir)
    unstaged_paths = _git_path_list(["git", "diff", "--name-only"], resolved_workdir)
    untracked_paths = _git_path_list(["git", "ls-files", "--others", "--exclude-standard"], resolved_workdir)

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
    envelope["aggregate_sha256"] = _sha256_canonical_excluding(envelope, "aggregate_sha256")
    return envelope


def capture_repository_state(resolved_workdir):
    """Capture a deterministic repository-state/v1 envelope, twice, requiring exact equality."""
    _verify_git_repo_worktree(resolved_workdir)
    canonical_workdir = os.path.realpath(str(resolved_workdir))
    first = _capture_repository_state_once(resolved_workdir, canonical_workdir)
    second = _capture_repository_state_once(resolved_workdir, canonical_workdir)
    if first != second:
        raise RepositoryStateError("repository state changed between consecutive captures (unstable state)")
    return first


def _run_git_diff_check_gate(resolved_workdir):
    try:
        completed = subprocess.run(
            ["git", "diff", "--check"], cwd=str(resolved_workdir), shell=False,
            capture_output=True, text=True, timeout=GIT_DIFF_CHECK_TIMEOUT_SECONDS,
            check=False, env=_deterministic_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError("git diff --check timed out: %s" % exc) from exc
    except OSError as exc:
        raise TransportError("git diff --check failed to launch: %s" % exc) from exc
    if completed.returncode != 0:
        detail = ((completed.stdout or "").strip() + " " + (completed.stderr or "").strip()).strip()
        raise ReadyToCommitReject("git_diff_check_failed", "git diff --check reported issues: %s" % detail)


def _run_kanban_json(argv, label):
    try:
        completed = run_hermes_command_with_timeout(argv, KANBAN_READ_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise TransportError("%s timed out after %ss" % (label, KANBAN_READ_TIMEOUT_SECONDS)) from exc
    except OSError as exc:
        raise TransportError("%s failed to launch: %s" % (label, exc)) from exc
    return parse_json_stdout(completed, label)


def _fetch_show_and_runs(task_id):
    show = _run_kanban_json(["hermes", "kanban", "show", task_id, "--json"], "hermes kanban show " + task_id)
    runs = _run_kanban_json(["hermes", "kanban", "runs", task_id, "--json"], "hermes kanban runs " + task_id)
    show = validate_show_top_level(show)
    return show, runs


def _require_show_runs_match(prefix, show, runs):
    show_runs = show.get("runs")
    if not isinstance(show_runs, list) or not isinstance(runs, list):
        raise ReadyToCommitReject(prefix + "_runs_shape", prefix + ": show.runs and runs must both be arrays")
    if show_runs != runs:
        raise ReadyToCommitReject(
            prefix + "_runs_mismatch", prefix + ": show.runs and standalone runs are not equal as parsed JSON"
        )


def _select_latest_run_reject(reason_code, runs):
    try:
        return select_latest_run("ready-to-commit", runs)
    except ValidationBlock as exc:
        raise ReadyToCommitReject(reason_code, exc.reason)


def _validate_implementation_task(task, task_id, canonical_workdir):
    if not isinstance(task, dict):
        raise ReadyToCommitReject("implementation_task_shape", "implementation task must be a JSON object")
    if task.get("id") != task_id:
        raise ReadyToCommitReject(
            "implementation_task_id_mismatch",
            "implementation task.id is %r, expected %r" % (task.get("id"), task_id),
        )
    if task.get("workspace_kind") != "dir":
        raise ReadyToCommitReject(
            "implementation_workspace_kind_mismatch",
            "implementation task.workspace_kind is %r, expected 'dir'" % (task.get("workspace_kind"),),
        )
    workspace_path = task.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path or not Path(workspace_path).is_absolute():
        raise ReadyToCommitReject(
            "implementation_workspace_path_invalid",
            "implementation task.workspace_path is missing or malformed: %r" % (workspace_path,),
        )
    if os.path.realpath(workspace_path) != canonical_workdir:
        raise ReadyToCommitReject(
            "implementation_workspace_mismatch",
            "implementation task workspace %r does not match requested workdir %r"
            % (workspace_path, canonical_workdir),
        )
    if task.get("assignee") not in ("coder-claude", None):
        raise ReadyToCommitReject(
            "implementation_assignee_mismatch",
            "implementation task.assignee is %r, expected 'coder-claude' or null" % (task.get("assignee"),),
        )
    if task.get("status") != "done":
        raise ReadyToCommitReject(
            "implementation_status_mismatch",
            "implementation task.status is %r, expected 'done'" % (task.get("status"),),
        )
    completed_at = task.get("completed_at")
    if isinstance(completed_at, bool) or not isinstance(completed_at, int) or completed_at <= 0:
        raise ReadyToCommitReject(
            "implementation_completed_at_invalid",
            "implementation task.completed_at must be a positive int, got %r" % (completed_at,),
        )
    return completed_at


def _validate_implementation_latest_run(run):
    if run.get("profile") == "coder-claude":
        if run.get("outcome") != "completed":
            raise ReadyToCommitReject(
                "implementation_run_outcome_mismatch",
                "implementation latest run outcome is %r, expected 'completed'" % (run.get("outcome"),),
            )
        if run.get("status") not in ("done", "completed"):
            raise ReadyToCommitReject(
                "implementation_run_status_mismatch",
                "implementation latest run status is %r, expected 'done' or 'completed'" % (run.get("status"),),
            )
        return "coder-worker"
    metadata = run.get("metadata")
    if isinstance(metadata, dict) and metadata.get("schema") == BOOTSTRAP_PROVENANCE_SCHEMA:
        if run.get("outcome") != "completed" or run.get("status") not in ("done", "completed"):
            raise ReadyToCommitReject("bootstrap_run_status_invalid", "bootstrap provenance run is not completed")
        if metadata.get("implementation_provenance") != "operator-bootstrap":
            raise ReadyToCommitReject("bootstrap_provenance_mode_invalid", "bootstrap provenance mode is invalid")
        if metadata.get("coder_worker_run") is not False:
            raise ReadyToCommitReject("bootstrap_coder_run_invalid", "bootstrap provenance must state no coder worker run")
        return "operator-bootstrap"
    if run.get("profile") != "coder-claude":
        raise ReadyToCommitReject(
            "implementation_run_profile_mismatch",
            "implementation latest run profile is %r, expected 'coder-claude'" % (run.get("profile"),),
        )
    raise AssertionError("unreachable")


def _validate_bootstrap_provenance(run, task, task_id, canonical_workdir):
    metadata = run.get("metadata")
    if not isinstance(metadata, dict):
        raise ReadyToCommitReject("bootstrap_provenance_shape", "bootstrap provenance metadata must be an object")
    if task.get("assignee") is not None or task.get("created_by") != "pipeline_controller":
        raise ReadyToCommitReject("bootstrap_task_identity_invalid", "bootstrap task must be operator-owned and unassigned")
    exact = {
        "schema", "implementation_provenance", "implementation_task_id", "reason", "workdir",
        "base_sha", "implementation_sha", "repository_state", "repository_state_sha256",
        "changed_paths", "mutation_scope", "validation", "coder_worker_run",
    }
    if set(metadata) != exact:
        raise ReadyToCommitReject("bootstrap_provenance_keys", "bootstrap provenance keys are not exact")
    if metadata["implementation_task_id"] != task_id or metadata["reason"] != BOOTSTRAP_REASON:
        raise ReadyToCommitReject("bootstrap_provenance_identity", "bootstrap provenance identity/reason mismatch")
    if metadata["workdir"] != canonical_workdir or metadata["changed_paths"] != []:
        raise ReadyToCommitReject("bootstrap_provenance_scope", "bootstrap workdir or changed paths invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["base_sha"]) or not re.fullmatch(r"[0-9a-f]{40}", metadata["implementation_sha"]):
        raise ReadyToCommitReject("bootstrap_provenance_sha", "bootstrap base/implementation SHA malformed")
    state = metadata["repository_state"]
    if not isinstance(state, dict) or state.get("schema") != REPOSITORY_STATE_SCHEMA:
        raise ReadyToCommitReject("bootstrap_repository_state_invalid", "bootstrap repository_state is invalid")
    if metadata["repository_state_sha256"] != state.get("aggregate_sha256") or _sha256_canonical_excluding(state, "aggregate_sha256") != state.get("aggregate_sha256"):
        raise ReadyToCommitReject("bootstrap_repository_state_hash", "bootstrap repository state hash mismatch")
    validation = metadata["validation"]
    if not isinstance(validation, list) or not validation or any(not isinstance(item, dict) or item.get("status") != "PASS" or item.get("exit_code") != 0 for item in validation):
        raise ReadyToCommitReject("bootstrap_validation_invalid", "bootstrap validation evidence is invalid")
    return state


def _validate_review_task(task, task_id, canonical_workdir):
    if not isinstance(task, dict):
        raise ReadyToCommitReject("review_task_shape", "review task must be a JSON object")
    if task.get("id") != task_id:
        raise ReadyToCommitReject(
            "review_task_id_mismatch", "review task.id is %r, expected %r" % (task.get("id"), task_id)
        )
    if task.get("workspace_kind") != "dir":
        raise ReadyToCommitReject(
            "review_workspace_kind_mismatch",
            "review task.workspace_kind is %r, expected 'dir'" % (task.get("workspace_kind"),),
        )
    workspace_path = task.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path or not Path(workspace_path).is_absolute():
        raise ReadyToCommitReject(
            "review_workspace_path_invalid",
            "review task.workspace_path is missing or malformed: %r" % (workspace_path,),
        )
    if os.path.realpath(workspace_path) != canonical_workdir:
        raise ReadyToCommitReject(
            "review_workspace_mismatch",
            "review task workspace %r does not match requested workdir %r" % (workspace_path, canonical_workdir),
        )
    if task.get("assignee") != "reviewer":
        raise ReadyToCommitReject(
            "review_assignee_mismatch", "review task.assignee is %r, expected 'reviewer'" % (task.get("assignee"),)
        )
    if task.get("status") != "done":
        raise ReadyToCommitReject(
            "review_status_mismatch", "review task.status is %r, expected 'done'" % (task.get("status"),)
        )
    completed_at = task.get("completed_at")
    if isinstance(completed_at, bool) or not isinstance(completed_at, int) or completed_at <= 0:
        raise ReadyToCommitReject(
            "review_completed_at_invalid",
            "review task.completed_at must be a positive int, got %r" % (completed_at,),
        )
    return completed_at


def _validate_review_parents(parents, implementation_task_id):
    if parents != [implementation_task_id]:
        raise ReadyToCommitReject(
            "review_parents_mismatch",
            "review parents is %r, expected [%r]" % (parents, implementation_task_id),
        )


def _validate_review_latest_run(run, implementation_task_id):
    if run.get("profile") != "reviewer":
        raise ReadyToCommitReject(
            "review_run_profile_mismatch",
            "review latest run profile is %r, expected 'reviewer'" % (run.get("profile"),),
        )
    if run.get("status") != "done":
        raise ReadyToCommitReject(
            "review_run_status_mismatch", "review latest run status is %r, expected 'done'" % (run.get("status"),)
        )
    if run.get("outcome") != "completed":
        raise ReadyToCommitReject(
            "review_run_outcome_mismatch",
            "review latest run outcome is %r, expected 'completed'" % (run.get("outcome"),),
        )
    metadata = run.get("metadata")
    if not isinstance(metadata, dict):
        raise ReadyToCommitReject(
            "review_metadata_shape", "review latest run metadata must be a JSON object, got %r" % (metadata,)
        )
    if metadata.get("implementation_task_id") != implementation_task_id:
        raise ReadyToCommitReject(
            "review_metadata_implementation_task_id_mismatch",
            "review latest run metadata.implementation_task_id is %r, expected %r"
            % (metadata.get("implementation_task_id"), implementation_task_id),
        )
    if metadata.get("mutation_performed") is not False:
        raise ReadyToCommitReject(
            "review_metadata_mutation_performed_invalid",
            "review latest run metadata.mutation_performed is %r, expected exactly false"
            % (metadata.get("mutation_performed"),),
        )
    return metadata


def _validate_review_metadata_repository_state(metadata, canonical_workdir):
    state = metadata.get("repository_state")
    if not isinstance(state, dict):
        raise ReadyToCommitReject(
            "review_metadata_repository_state_shape",
            "review metadata.repository_state must be a JSON object, got %r" % (state,),
        )
    if state.get("schema") != REPOSITORY_STATE_SCHEMA:
        raise ReadyToCommitReject(
            "review_metadata_repository_state_schema_mismatch",
            "unsupported repository_state schema: %r" % (state.get("schema"),),
        )
    if state.get("workdir") != canonical_workdir:
        raise ReadyToCommitReject(
            "review_metadata_repository_state_workdir_mismatch",
            "repository_state workdir is %r, expected %r" % (state.get("workdir"), canonical_workdir),
        )
    aggregate = state.get("aggregate_sha256")
    duplicate = metadata.get("repository_state_sha256")
    if not isinstance(aggregate, str) or not re.fullmatch(r"[0-9a-f]{64}", aggregate):
        raise ReadyToCommitReject(
            "review_metadata_repository_state_sha256_malformed",
            "repository_state aggregate_sha256 is malformed: %r" % (aggregate,),
        )
    if duplicate != aggregate:
        raise ReadyToCommitReject(
            "review_metadata_repository_state_sha256_mismatch",
            "metadata.repository_state_sha256 does not match repository_state.aggregate_sha256",
        )
    recomputed = _sha256_canonical_excluding(state, "aggregate_sha256")
    if recomputed != aggregate:
        raise ReadyToCommitReject(
            "review_metadata_repository_state_sha256_invalid",
            "repository_state aggregate_sha256 does not match recomputed digest",
        )
    return state


def _find_review_archive_artifact(resolved_workdir, review_task_id):
    ai_dir = resolved_workdir / ".ai"
    reviews_dir = ai_dir / "reviews"

    for directory in (ai_dir, reviews_dir):
        try:
            dir_stat = directory.lstat()
        except OSError as exc:
            raise ReadyToCommitReject(
                "review_archive_missing", "review archive directory missing: %s (%s)" % (directory, exc)
            )
        if not stat.S_ISDIR(dir_stat.st_mode) or directory.is_symlink():
            raise ReadyToCommitReject(
                "review_archive_invalid_directory",
                "review archive directory is not a real directory: %s" % directory,
            )

    if reviews_dir.parent != ai_dir or ai_dir.parent != resolved_workdir:
        raise ReadyToCommitReject(
            "review_archive_invalid_directory", "review archive directory escaped canonical workdir"
        )

    pattern = re.compile(REVIEW_ARCHIVE_FILENAME_RE_TEMPLATE % re.escape(review_task_id))
    try:
        entries = list(reviews_dir.iterdir())
    except OSError as exc:
        raise ReadyToCommitReject("review_archive_unreadable", "cannot list review archive directory: %s" % exc)

    candidates = [entry for entry in entries if pattern.fullmatch(entry.name)]
    if len(candidates) != 1:
        raise ReadyToCommitReject(
            "review_archive_ambiguous",
            "expected exactly one review archive artifact for %s, found %d" % (review_task_id, len(candidates)),
        )

    artifact = candidates[0]
    if artifact.parent != reviews_dir:
        raise ReadyToCommitReject(
            "review_archive_invalid_directory", "review archive artifact escaped reviews directory"
        )
    artifact_stat = artifact.lstat()
    if artifact.is_symlink() or not stat.S_ISREG(artifact_stat.st_mode):
        raise ReadyToCommitReject(
            "review_archive_not_regular",
            "review archive artifact is not a regular non-symlink file: %s" % artifact,
        )
    return artifact


def _parse_review_archive_envelope(artifact_path, review_task_id):
    try:
        raw = artifact_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReadyToCommitReject("review_archive_unreadable", "cannot read review archive artifact: %s" % exc)

    envelopes = []
    for block in REVIEW_ARCHIVE_JSON_BLOCK_RE.findall(raw):
        try:
            parsed = json.loads(block)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed.get("schema") == REVIEW_ARCHIVE_SCHEMA_V2:
            envelopes.append(parsed)

    if len(envelopes) != 1:
        raise ReadyToCommitReject(
            "review_archive_envelope_ambiguous",
            "expected exactly one %s JSON section, found %d" % (REVIEW_ARCHIVE_SCHEMA_V2, len(envelopes)),
        )
    envelope = envelopes[0]

    errors = []
    validate_exact_keys(envelope, REVIEW_ARCHIVE_ENVELOPE_KEYS, "review_archive_envelope", errors)
    if errors:
        raise ReadyToCommitReject("review_archive_envelope_shape", "; ".join(errors))

    if envelope.get("review_task_id") != review_task_id:
        raise ReadyToCommitReject(
            "review_archive_envelope_mismatch",
            "review archive envelope.review_task_id is %r, expected %r"
            % (envelope.get("review_task_id"), review_task_id),
        )

    recomputed = _sha256_canonical_excluding(envelope, "archive_envelope_sha256")
    if recomputed != envelope.get("archive_envelope_sha256"):
        raise ReadyToCommitReject(
            "review_archive_envelope_sha256_mismatch",
            "recomputed archive_envelope_sha256 does not match review archive artifact",
        )

    return envelope


def _require_envelope_matches_kanban(
    envelope, canonical_workdir, implementation_task_id, review_task_id,
    review_run_id, review_completed_at, verdict, verdict_source, review_state,
):
    checks = (
        ("workdir", envelope.get("workdir"), canonical_workdir),
        ("implementation_task_id", envelope.get("implementation_task_id"), implementation_task_id),
        ("review_task_id", envelope.get("review_task_id"), review_task_id),
        ("review_run_id", envelope.get("review_run_id"), review_run_id),
        ("review_completed_at", envelope.get("review_completed_at"), review_completed_at),
        ("verdict", envelope.get("verdict"), verdict),
        ("verdict_source", envelope.get("verdict_source"), verdict_source),
    )
    for field, actual, expected in checks:
        if actual != expected:
            raise ReadyToCommitReject(
                "review_archive_envelope_mismatch",
                "review archive envelope.%s is %r, expected %r" % (field, actual, expected),
            )
    if envelope.get("repository_state") != review_state:
        raise ReadyToCommitReject(
            "review_archive_repository_state_mismatch",
            "review archive envelope.repository_state does not match authoritative review metadata repository_state",
        )


def _emit_ready_to_commit_reject(args, reason_code, reason):
    payload = {
        "phase": "ready-to-commit",
        "outcome": "not-ready",
        "workdir": args.workdir,
        "implementation_task_id": args.implementation_task_id,
        "review_task_id": args.review_task_id,
        "reason_code": reason_code,
        "reason": reason,
        "human_approval_required": True,
        "commit_performed": False,
        "push_performed": False,
    }
    print(json.dumps(payload, separators=(",", ":")))


def ready_to_commit(args):
    """Strictly read-only technical attestation that a workdir is ready to commit.

    Never authorizes or performs commit/push/staging/filesystem writes/Kanban
    writes. Validates the implementation and review tasks via two authorized
    `hermes kanban` JSON reads each, requires an exact PASS verdict, requires
    the deterministically archived review artifact envelope to match the
    authoritative Kanban review metadata exactly, and requires a fresh,
    doubly-captured repository-state fingerprint to match both the
    authoritative review metadata and the archived envelope exactly.
    """
    implementation_task_id = args.implementation_task_id
    review_task_id = args.review_task_id

    try:
        resolved = validate_workdir(args.workdir)
    except WorkdirValidationError as exc:
        raise ReadyToCommitReject("invalid_workdir", str(exc))

    if not valid_task_id(implementation_task_id):
        raise ReadyToCommitReject(
            "invalid_implementation_task_id", "implementation_task_id is invalid: %r" % (implementation_task_id,)
        )
    if not valid_task_id(review_task_id):
        raise ReadyToCommitReject("invalid_review_task_id", "review_task_id is invalid: %r" % (review_task_id,))

    canonical_workdir = os.path.realpath(str(resolved))

    # --- implementation task ---
    impl_show, impl_runs = _fetch_show_and_runs(implementation_task_id)
    _require_show_runs_match("implementation", impl_show, impl_runs)
    impl_task = impl_show.get("task")
    _validate_implementation_task(impl_task, implementation_task_id, canonical_workdir)
    impl_latest = _select_latest_run_reject("implementation_run_selection_invalid", impl_show.get("runs"))
    implementation_provenance = _validate_implementation_latest_run(impl_latest)
    bootstrap_state = None
    if implementation_provenance == "operator-bootstrap":
        bootstrap_state = _validate_bootstrap_provenance(
            impl_latest, impl_task, implementation_task_id, canonical_workdir,
        )

    # --- review task ---
    review_show, review_runs = _fetch_show_and_runs(review_task_id)
    _require_show_runs_match("review", review_show, review_runs)
    review_completed_at = _validate_review_task(review_show.get("task"), review_task_id, canonical_workdir)
    _validate_review_parents(review_show.get("parents"), implementation_task_id)
    review_latest = _select_latest_run_reject("review_run_selection_invalid", review_show.get("runs"))
    metadata = _validate_review_latest_run(review_latest, implementation_task_id)

    try:
        verdict, verdict_source = classify_verdict(review_latest)
    except VerdictBlock as exc:
        raise ReadyToCommitReject("verdict_blocked", exc.reason)
    if verdict != "PASS":
        raise ReadyToCommitReject("verdict_not_pass", "review verdict is %r, expected PASS" % (verdict,))

    review_state = _validate_review_metadata_repository_state(metadata, canonical_workdir)

    # --- archived review artifact ---
    artifact_path = _find_review_archive_artifact(resolved, review_task_id)
    envelope = _parse_review_archive_envelope(artifact_path, review_task_id)
    review_run_id = review_latest.get("id")
    _require_envelope_matches_kanban(
        envelope, canonical_workdir, implementation_task_id, review_task_id,
        review_run_id, review_completed_at, verdict, verdict_source, review_state,
    )

    # --- working tree gates ---
    _run_git_diff_check_gate(resolved)

    try:
        current_state = capture_repository_state(resolved)
    except RepositoryStateError as exc:
        raise ReadyToCommitReject("repository_state_invalid", str(exc))

    if current_state != review_state:
        raise ReadyToCommitReject(
            "repository_state_mismatch_kanban",
            "current repository_state does not match authoritative review metadata repository_state",
        )
    if current_state != envelope.get("repository_state"):
        raise ReadyToCommitReject(
            "repository_state_mismatch_archive",
            "current repository_state does not match the archived review artifact repository_state",
        )
    if bootstrap_state is not None and current_state != bootstrap_state:
        raise ReadyToCommitReject(
            "bootstrap_repository_state_mismatch",
            "current repository_state does not match bootstrap provenance",
        )

    payload = {
        "phase": "ready-to-commit",
        "outcome": "ready",
        "workdir": args.workdir,
        "implementation_task_id": implementation_task_id,
        "review_task_id": review_task_id,
        "review_run_id": review_run_id,
        "verdict": "PASS",
        "verdict_source": verdict_source,
        "review_archived": True,
        "repository_state_sha256": current_state["aggregate_sha256"],
        "human_approval_required": True,
        "commit_performed": False,
        "push_performed": False,
    }
    if implementation_provenance == "operator-bootstrap":
        payload["implementation_provenance"] = implementation_provenance
    print(json.dumps(payload, separators=(",", ":")))
    return EXIT_OK


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except CliUsageError as exc:
        sys.stderr.write("usage error: " + str(exc) + "\n")
        return EXIT_TRANSPORT
    try:
        if args.command == "wait":
            return handle_wait(args)
        if args.command == "check":
            return check_task(args.task_id)
        elif args.command == "create-implementation":
            return create_implementation(args)
        elif args.command == "create-review":
            return create_review(args)
        elif args.command == "create-correction":
            return create_correction(args)
        elif args.command == "archive-review":
            return archive_review(args)
        elif args.command == "ready-to-commit":
            return ready_to_commit(args)
        else:
            raise CliUsageError("unknown command: %r" % (args.command,))
    except ReadyToCommitReject as exc:
        _emit_ready_to_commit_reject(args, exc.reason_code, exc.reason)
        return EXIT_VALIDATION
    except ValidationBlock as exc:
        emit_blocked(exc.phase, exc.reason)
        return EXIT_VALIDATION
    except TransportError as exc:
        sys.stderr.write("transport error: " + str(exc) + "\n")
        return EXIT_TRANSPORT
    except OSError as exc:
        sys.stderr.write("transport error: failed to launch hermes: " + str(exc) + "\n")
        return EXIT_TRANSPORT
    except CliUsageError as exc:
        sys.stderr.write("usage error: " + str(exc) + "\n")
        return EXIT_TRANSPORT


if __name__ == "__main__":
    raise SystemExit(main())
