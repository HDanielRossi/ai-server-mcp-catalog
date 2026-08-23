#!/usr/bin/env python3
"""Hermes Kanban pipeline controller.

Read-only structural validation of one Kanban task, using exactly two
authorized `hermes kanban` JSON commands. Fail-closed: any transport
problem, CLI usage problem, or non-exact top-level shape is a hard
failure (exit 3); any structural/content validation failure is an
aggregated, reported failure (exit 2); only a fully valid task is exit 0.
"""

import argparse
import json
import subprocess
import sys

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


class CliUsageError(Exception):
    """A CLI usage problem, mapped to exit code 3."""


class TransportError(Exception):
    """A launch, exit-code, JSON, or top-level-shape failure, mapped to exit code 3."""


class Exit3ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CliUsageError(message)


def build_parser():
    parser = Exit3ArgumentParser(prog="hermes-pipeline-controller.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", prog="check", description="check one Kanban task")
    check.add_argument("task_id")
    return parser


def run_hermes_command(args):
    return subprocess.run(args, shell=False, capture_output=True, text=True, check=False)


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


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except CliUsageError as exc:
        sys.stderr.write("usage error: " + str(exc) + "\n")
        return EXIT_TRANSPORT
    try:
        return check_task(args.task_id)
    except TransportError as exc:
        sys.stderr.write("transport error: " + str(exc) + "\n")
        return EXIT_TRANSPORT
    except OSError as exc:
        sys.stderr.write("transport error: failed to launch hermes: " + str(exc) + "\n")
        return EXIT_TRANSPORT


if __name__ == "__main__":
    raise SystemExit(main())
