#!/usr/bin/env python3

"""Deterministic review archive helper (A5 repository template).

This is the versioned repository counterpart of the installed
/usr/local/bin/review-archive-bridge helper. It preserves the historical A4
human-readable artifact format and adds a machine-verifiable A5 v2 envelope
when the authoritative review run carries hermes.repository-state/v1 metadata.

The only repository write is the review artifact under <workdir>/.ai/reviews.
Kanban access is read-only SQLite. Git subprocesses, when used for historical
human-readable evidence, are read-only argv-list invocations with bounded
timeouts and shell=False.
"""

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ALLOWED_ROOT = Path("/opt/ai/projects").resolve()
KANBAN_DB = Path.home() / ".hermes" / "kanban.db"
GIT_TIMEOUT_SECONDS = 30

REVIEW_ARCHIVE_SCHEMA_V2 = "hermes.review-archive/v2"
REPOSITORY_STATE_SCHEMA = "hermes.repository-state/v1"

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2


class ArchiveValidationError(Exception):
    pass


def canonical_json(obj):
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def sha256_canonical_excluding(obj, excluded_key):
    filtered = {
        key: value
        for key, value in obj.items()
        if key != excluded_key
    }
    return hashlib.sha256(
        canonical_json(filtered).encode("utf-8")
    ).hexdigest()


def fmt_ts(epoch):
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(int(epoch)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def file_ts(epoch):
    if not epoch:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    return datetime.fromtimestamp(int(epoch)).strftime(
        "%Y%m%d_%H%M%S"
    )


def load_json_object(raw, label):
    if not isinstance(raw, str) or not raw:
        raise ArchiveValidationError(
            f"{label} must be non-empty JSON text"
        )

    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ArchiveValidationError(
            f"{label} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise ArchiveValidationError(
            f"{label} must decode to a JSON object"
        )

    return value


def validate_workdir(raw):
    if not isinstance(raw, str) or not raw:
        raise ArchiveValidationError(
            "workdir must be a non-empty string"
        )

    path = Path(raw).expanduser()

    if not path.is_absolute():
        raise ArchiveValidationError(
            "workdir must be absolute"
        )

    normalized = Path(os.path.normpath(str(path)))

    if str(normalized) != str(path):
        raise ArchiveValidationError(
            "workdir must already be normalized"
        )

    resolved = path.resolve()

    if resolved != path:
        raise ArchiveValidationError(
            "workdir must not be or traverse a symlink"
        )

    if (
        resolved == ALLOWED_ROOT
        or ALLOWED_ROOT not in resolved.parents
    ):
        raise ArchiveValidationError(
            f"workdir outside allowed root: {resolved}"
        )

    if not resolved.is_dir():
        raise ArchiveValidationError(
            f"workdir is not a directory: {resolved}"
        )

    if not (resolved / ".git").exists():
        raise ArchiveValidationError(
            f"workdir is not a git repository: {resolved}"
        )

    return resolved


def validate_review_task_id(raw):
    if (
        not isinstance(raw, str)
        or not re.fullmatch(r"t_[0-9a-fA-F]{8,16}", raw)
    ):
        raise ArchiveValidationError(
            f"invalid review task id: {raw!r}"
        )

    return raw


def open_kanban_db_readonly():
    db_path = Path(
        os.environ.get(
            "HERMES_KANBAN_DB",
            str(KANBAN_DB),
        )
    ).expanduser()

    try:
        con = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
        )
    except sqlite3.Error as exc:
        raise ArchiveValidationError(
            f"cannot open Kanban database read-only: {exc}"
        ) from exc

    con.row_factory = sqlite3.Row
    return con


def fetch_task(con, task_id):
    row = con.execute(
        "SELECT * FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()

    if row is None:
        raise ArchiveValidationError(
            f"task not found: {task_id}"
        )

    return row


def fetch_events(con, task_id):
    return con.execute(
        """
        SELECT *
        FROM task_events
        WHERE task_id=?
        ORDER BY id
        """,
        (task_id,),
    ).fetchall()


def select_implementation_parent(events):
    created = [
        event
        for event in events
        if event["kind"] == "created"
    ]

    if len(created) != 1:
        raise ArchiveValidationError(
            "expected exactly one created event, "
            f"found {len(created)}"
        )

    payload = load_json_object(
        created[0]["payload"],
        "created event payload",
    )

    parents = payload.get("parents")

    if not isinstance(parents, list) or len(parents) != 1:
        raise ArchiveValidationError(
            "review task must have exactly one "
            f"implementation parent, got {parents!r}"
        )

    parent = parents[0]

    if (
        not isinstance(parent, str)
        or not re.fullmatch(
            r"t_[0-9a-fA-F]{8,16}",
            parent,
        )
    ):
        raise ArchiveValidationError(
            f"invalid implementation parent id: {parent!r}"
        )

    return parent


def select_latest_run_rows(rows, task_id):
    if not rows:
        raise ArchiveValidationError(
            f"no task_runs found for {task_id}"
        )

    ids = []

    for row in rows:
        run_id = row["id"]

        if (
            isinstance(run_id, bool)
            or not isinstance(run_id, int)
        ):
            raise ArchiveValidationError(
                f"malformed run id: {run_id!r}"
            )

        ids.append(run_id)

    if len(ids) != len(set(ids)):
        raise ArchiveValidationError(
            f"duplicate run ids found for {task_id}"
        )

    max_id = max(ids)

    return next(
        row
        for row in rows
        if row["id"] == max_id
    )


def select_latest_run(con, task_id):
    rows = con.execute(
        """
        SELECT *
        FROM task_runs
        WHERE task_id=?
        """,
        (task_id,),
    ).fetchall()

    return select_latest_run_rows(
        rows,
        task_id,
    )


def validate_task(task, workdir):
    if task["assignee"] != "reviewer":
        raise ArchiveValidationError(
            "task assignee is not reviewer: "
            f"{task['assignee']}"
        )

    if task["status"] != "done":
        raise ArchiveValidationError(
            "task status is not done: "
            f"{task['status']}"
        )

    if task["workspace_kind"] != "dir":
        raise ArchiveValidationError(
            "task workspace_kind is not dir: "
            f"{task['workspace_kind']}"
        )

    workspace_path = task["workspace_path"]

    if not isinstance(workspace_path, str):
        raise ArchiveValidationError(
            "task workspace_path is malformed"
        )

    if Path(workspace_path).resolve() != workdir:
        raise ArchiveValidationError(
            "task workspace mismatch: "
            f"{workspace_path} != {workdir}"
        )

    completed_at = task["completed_at"]

    if (
        isinstance(completed_at, bool)
        or not isinstance(completed_at, int)
        or completed_at <= 0
    ):
        raise ArchiveValidationError(
            "task completed_at must be a positive integer: "
            f"{completed_at!r}"
        )

    return completed_at


def validate_run(run, parent):
    if run["profile"] != "reviewer":
        raise ArchiveValidationError(
            "latest run profile is not reviewer: "
            f"{run['profile']}"
        )

    if run["status"] != "done":
        raise ArchiveValidationError(
            "latest run status is not done: "
            f"{run['status']}"
        )

    if run["outcome"] != "completed":
        raise ArchiveValidationError(
            "latest run outcome is not completed: "
            f"{run['outcome']}"
        )

    metadata = load_json_object(
        run["metadata"],
        "latest run metadata",
    )

    if metadata.get("implementation_task_id") != parent:
        raise ArchiveValidationError(
            "latest run metadata implementation_task_id "
            "does not match task parent"
        )

    return metadata


def classify_verdict(run, metadata):
    verdict = metadata.get("verdict")

    if verdict == "PASS":
        return "PASS", "metadata"

    if verdict == "CHANGES REQUIRED":
        return "CHANGES REQUIRED", "metadata"

    if verdict not in (None, "unknown"):
        raise ArchiveValidationError(
            f"invalid metadata verdict: {verdict!r}"
        )

    summary = run["summary"]

    if not isinstance(summary, str):
        raise ArchiveValidationError(
            "latest run summary must be a string"
        )

    has_changes = "CHANGES REQUIRED" in summary
    has_pass = "PASS" in summary

    if has_changes and not has_pass:
        return "CHANGES REQUIRED", "summary"

    if has_pass and not has_changes:
        return "PASS", "summary"

    raise ArchiveValidationError(
        "ambiguous or unknown summary verdict"
    )


def validate_repository_state(metadata, workdir):
    state = metadata.get("repository_state")

    if not isinstance(state, dict):
        raise ArchiveValidationError(
            "metadata.repository_state must be an object"
        )

    if state.get("schema") != REPOSITORY_STATE_SCHEMA:
        raise ArchiveValidationError(
            "unsupported repository_state schema: "
            f"{state.get('schema')!r}"
        )

    if state.get("workdir") != str(workdir):
        raise ArchiveValidationError(
            "repository_state workdir mismatch: "
            f"{state.get('workdir')!r}"
        )

    aggregate = state.get("aggregate_sha256")
    duplicate = metadata.get(
        "repository_state_sha256"
    )

    if (
        not isinstance(aggregate, str)
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            aggregate,
        )
    ):
        raise ArchiveValidationError(
            "repository_state aggregate_sha256 "
            f"is malformed: {aggregate!r}"
        )

    if duplicate != aggregate:
        raise ArchiveValidationError(
            "metadata.repository_state_sha256 does not "
            "match repository_state.aggregate_sha256"
        )

    recomputed = sha256_canonical_excluding(
        state,
        "aggregate_sha256",
    )

    if recomputed != aggregate:
        raise ArchiveValidationError(
            "repository_state aggregate_sha256 does not "
            "match recomputed digest"
        )

    return state


def build_v2_envelope(
    workdir,
    parent,
    review_task_id,
    run_id,
    completed_at,
    verdict,
    verdict_source,
    state,
):
    envelope = {
        "schema": REVIEW_ARCHIVE_SCHEMA_V2,
        "workdir": str(workdir),
        "implementation_task_id": parent,
        "review_task_id": review_task_id,
        "review_run_id": run_id,
        "review_completed_at": completed_at,
        "verdict": verdict,
        "verdict_source": verdict_source,
        "repository_state": state,
    }

    envelope["archive_envelope_sha256"] = (
        sha256_canonical_excluding(
            envelope,
            "archive_envelope_sha256",
        )
    )

    return envelope


def run_git(workdir, args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=workdir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            shell=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ) as exc:
        return (
            "$ git "
            + " ".join(args)
            + f"\ntransport_error={exc}"
        )

    return (
        "$ git "
        + " ".join(args)
        + "\nexit_code="
        + str(result.returncode)
        + "\n"
        + result.stdout.rstrip()
    )


def render_human_artifact(
    task,
    run,
    metadata,
    events,
    workdir,
    parent,
    verdict,
):
    lines = [
        f"# Review artifact: {task['id']}",
        "",
        "## Summary",
        "",
        f"- Review task: `{task['id']}`",
        f"- Verdict: `{verdict}`",
        f"- Assignee: `{task['assignee']}`",
        f"- Run profile: `{run['profile']}`",
        f"- Task status: `{task['status']}`",
        f"- Run status: `{run['status']}`",
        f"- Run outcome: `{run['outcome']}`",
        f"- Workdir: `{workdir}`",
        f"- Created by: `{task['created_by']}`",
        f"- Created at: `{fmt_ts(task['created_at'])}`",
        f"- Started at: `{fmt_ts(task['started_at'])}`",
        f"- Completed at: `{fmt_ts(task['completed_at'])}`",
        f"- Parent implementation task(s): `{parent}`",
        (
            "- Worker session id: "
            f"`{metadata.get('worker_session_id', 'unknown')}`"
        ),
        "",
        "## Reviewer summary",
        "",
        "```text",
        run["summary"] or "",
        "```",
        "",
        "## Metadata",
        "",
        "```json",
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Git state at archive time",
        "",
        "```text",
        run_git(
            workdir,
            ["status", "--short"],
        ),
        "```",
        "",
        "```text",
        run_git(
            workdir,
            ["log", "--oneline", "-5"],
        ),
        "```",
        "",
        "## Task title",
        "",
        "```text",
        task["title"] or "",
        "```",
        "",
        "## Task body",
        "",
        "```text",
        task["body"] or "",
        "```",
        "",
        "## Event timeline",
        "",
    ]

    if events:
        for event in events:
            payload = event["payload"] or ""

            if len(payload) > 1200:
                payload = (
                    payload[:1200]
                    + "...[TRUNCATED]"
                )

            lines.append(
                f"- id={event['id']} "
                f"kind={event['kind']} "
                f"run_id={event['run_id']} "
                f"created_at={fmt_ts(event['created_at'])}"
            )
            lines.append(
                f"  payload: `{payload}`"
            )
    else:
        lines.append("_No events found._")

    lines += [
        "",
        "## Archive note",
        "",
        (
            "This artifact was written by "
            "`review-archive-bridge`, not by the "
            "`reviewer` profile."
        ),
        "",
        (
            "The reviewer remains read-only and "
            "tool-isolated. The archive bridge persists "
            "Kanban review metadata after the review "
            "has completed."
        ),
        "",
    ]

    return lines


def render_artifact(
    task,
    run,
    metadata,
    events,
    workdir,
    parent,
    verdict,
    verdict_source,
):
    lines = render_human_artifact(
        task,
        run,
        metadata,
        events,
        workdir,
        parent,
        verdict,
    )

    has_state = "repository_state" in metadata
    has_digest = (
        "repository_state_sha256"
        in metadata
    )

    if has_state != has_digest:
        raise ArchiveValidationError(
            "incomplete repository-state metadata"
        )

    if has_state:
        state = validate_repository_state(
            metadata,
            workdir,
        )

        envelope = build_v2_envelope(
            workdir,
            parent,
            task["id"],
            run["id"],
            task["completed_at"],
            verdict,
            verdict_source,
            state,
        )

        lines += [
            "## hermes.review-archive/v2",
            "",
            "```json",
            canonical_json(envelope),
            "```",
            "",
        ]

    return "\n".join(lines).encode(
        "utf-8"
    )


def ensure_reviews_dir(workdir):
    ai_dir = workdir / ".ai"
    reviews_dir = ai_dir / "reviews"

    for directory in (
        ai_dir,
        reviews_dir,
    ):
        try:
            file_stat = directory.lstat()
        except FileNotFoundError:
            directory.mkdir()
            file_stat = directory.lstat()

        if not stat.S_ISDIR(
            file_stat.st_mode
        ):
            raise ArchiveValidationError(
                "archive directory path is not "
                f"a real directory: {directory}"
            )

        if (
            directory.is_symlink()
            or directory.resolve() != directory
        ):
            raise ArchiveValidationError(
                "archive directory path must not "
                f"be a symlink: {directory}"
            )

    if (
        reviews_dir.parent != ai_dir
        or ai_dir.parent != workdir
    ):
        raise ArchiveValidationError(
            "archive directory escaped canonical workdir"
        )

    return reviews_dir


def write_artifact(
    workdir,
    filename,
    content,
):
    reviews_dir = ensure_reviews_dir(
        workdir
    )

    artifact = reviews_dir / filename

    if artifact.parent != reviews_dir:
        raise ArchiveValidationError(
            "artifact path escaped .ai/reviews"
        )

    try:
        file_stat = artifact.lstat()
    except FileNotFoundError:
        file_stat = None

    if file_stat is not None:
        if (
            not stat.S_ISREG(
                file_stat.st_mode
            )
            or artifact.is_symlink()
        ):
            raise ArchiveValidationError(
                "artifact path exists and is not "
                f"a regular file: {artifact}"
            )

        if artifact.read_bytes() == content:
            return artifact, False

        raise ArchiveValidationError(
            "artifact already exists with "
            f"different content: {artifact}"
        )

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
    )

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(
            artifact,
            flags,
            0o644,
        )
    except FileExistsError as exc:
        raise ArchiveValidationError(
            "artifact appeared concurrently and "
            f"was not overwritten: {artifact}"
        ) from exc

    try:
        with os.fdopen(
            fd,
            "wb",
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(
                handle.fileno()
            )
    except Exception:
        try:
            artifact.unlink()
        except OSError:
            pass
        raise

    return artifact, True


def archive_review(
    workdir_arg,
    task_id_arg,
):
    workdir = validate_workdir(
        workdir_arg
    )

    task_id = validate_review_task_id(
        task_id_arg
    )

    con = open_kanban_db_readonly()

    try:
        task = fetch_task(
            con,
            task_id,
        )

        completed_at = validate_task(
            task,
            workdir,
        )

        events = fetch_events(
            con,
            task_id,
        )

        parent = (
            select_implementation_parent(
                events
            )
        )

        run = select_latest_run(
            con,
            task_id,
        )

        metadata = validate_run(
            run,
            parent,
        )

        verdict, verdict_source = (
            classify_verdict(
                run,
                metadata,
            )
        )

    except sqlite3.Error as exc:
        raise ArchiveValidationError(
            f"Kanban database read failed: {exc}"
        ) from exc

    finally:
        con.close()

    content = render_artifact(
        task,
        run,
        metadata,
        events,
        workdir,
        parent,
        verdict,
        verdict_source,
    )

    filename = (
        f"{file_ts(completed_at)}"
        f"-{task_id}.md"
    )

    artifact, created = write_artifact(
        workdir,
        filename,
        content,
    )

    if created:
        print(
            "OK: wrote review artifact"
        )
    else:
        print(
            "OK: artifact already exists: "
            f"{artifact}"
        )

    print(
        f"artifact_path={artifact}"
    )
    print(
        f"verdict={verdict}"
    )
    print(
        f"review_task_id={task_id}"
    )

    return artifact


def main(argv=None):
    argv = (
        sys.argv[1:]
        if argv is None
        else argv
    )

    if len(argv) != 2:
        print(
            "usage: review_archive_bridge.py "
            "<workdir> <review_task_id>",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        archive_review(
            argv[0],
            argv[1],
        )
    except ArchiveValidationError as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return EXIT_VALIDATION

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
