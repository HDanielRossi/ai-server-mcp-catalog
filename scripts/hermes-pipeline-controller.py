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
import re
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
    try:
        selected = max(runs, key=lambda r: r["id"])
    except TypeError:
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")

    summary = selected.get("summary")
    if not isinstance(summary, str):
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")

    upper = summary.upper()
    has_cr = "CHANGES REQUIRED" in upper
    has_pass = "PASS" in upper
    if has_cr and not has_pass:
        pass
    elif has_pass and not has_cr:
        raise ValidationBlock(phase, "review verdict was PASS, no correction authorized")
    else:
        raise ValidationBlock(phase, "ambiguous verdict, cannot create correction")

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
        else:
            raise CliUsageError("unknown command: %r" % (args.command,))
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
