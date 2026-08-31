import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "templates"
    / "review_archive_bridge.py"
)

SPEC = importlib.util.spec_from_file_location(
    "review_archive_bridge",
    MODULE_PATH,
)

archive = importlib.util.module_from_spec(
    SPEC
)

SPEC.loader.exec_module(
    archive
)

ArchiveValidationError = (
    archive.ArchiveValidationError
)

REVIEW_ID = "t_11111111"
IMPLEMENTATION_ID = "t_22222222"


def make_repo(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "projects"
    root.mkdir()

    repo = root / "repo"
    repo.mkdir()

    (repo / ".git").mkdir()

    monkeypatch.setattr(
        archive,
        "ALLOWED_ROOT",
        root.resolve(),
    )

    return repo.resolve()


def make_db(
    tmp_path,
    monkeypatch,
):
    db = tmp_path / "kanban.db"

    con = sqlite3.connect(db)

    con.executescript(
        """
        CREATE TABLE tasks(
            id TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            assignee TEXT,
            status TEXT,
            created_by TEXT,
            created_at INTEGER,
            started_at INTEGER,
            completed_at INTEGER,
            workspace_kind TEXT,
            workspace_path TEXT
        );

        CREATE TABLE task_runs(
            id INTEGER PRIMARY KEY,
            task_id TEXT,
            profile TEXT,
            status TEXT,
            outcome TEXT,
            summary TEXT,
            metadata TEXT
        );

        CREATE TABLE task_events(
            id INTEGER PRIMARY KEY,
            task_id TEXT,
            run_id INTEGER,
            kind TEXT,
            payload TEXT,
            created_at INTEGER
        );
        """
    )

    con.close()

    monkeypatch.setenv(
        "HERMES_KANBAN_DB",
        str(db),
    )

    return db


def repository_state(repo):
    empty_sha = hashlib.sha256(
        b""
    ).hexdigest()

    state = {
        "schema":
            "hermes.repository-state/v1",
        "workdir":
            str(repo),
        "head":
            "a" * 40,
        "changed_paths":
            [],
        "staged_patch_sha256":
            empty_sha,
        "unstaged_patch_sha256":
            empty_sha,
        "untracked":
            [],
    }

    state["aggregate_sha256"] = (
        archive.sha256_canonical_excluding(
            state,
            "aggregate_sha256",
        )
    )

    return state


def insert_review(
    db,
    repo,
    *,
    verdict="PASS",
    summary="PASS ok",
    state=True,
    parent=IMPLEMENTATION_ID,
    metadata_implementation_id=IMPLEMENTATION_ID,
    run_id=10,
    completed_at=1700000000,
    metadata_extra=None,
):
    metadata = {
        "implementation_task_id":
            metadata_implementation_id,
        "verdict":
            verdict,
        "mutation_performed":
            False,
    }

    if state:
        repo_state = repository_state(
            repo
        )

        metadata["repository_state"] = (
            repo_state
        )

        metadata[
            "repository_state_sha256"
        ] = repo_state["aggregate_sha256"]

    if metadata_extra:
        metadata.update(
            metadata_extra
        )

    con = sqlite3.connect(db)

    con.execute(
        """
        INSERT INTO tasks
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            REVIEW_ID,
            "title",
            "body",
            "reviewer",
            "done",
            "pipeline_bridge",
            1699999900,
            1699999950,
            completed_at,
            "dir",
            str(repo),
        ),
    )

    con.execute(
        """
        INSERT INTO task_events
        VALUES(?,?,?,?,?,?)
        """,
        (
            1,
            REVIEW_ID,
            None,
            "created",
            json.dumps(
                {
                    "parents": [
                        parent
                    ]
                }
            ),
            1699999900,
        ),
    )

    con.execute(
        """
        INSERT INTO task_runs
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            run_id,
            REVIEW_ID,
            "reviewer",
            "done",
            "completed",
            summary,
            json.dumps(
                metadata
            ),
        ),
    )

    con.commit()
    con.close()

    return metadata


def artifact_path(
    repo,
    completed_at=1700000000,
):
    return (
        repo
        / ".ai"
        / "reviews"
        / (
            f"{archive.file_ts(completed_at)}"
            f"-{REVIEW_ID}.md"
        )
    )


def read_v2_envelope(path):
    text = path.read_text()

    assert (
        text.count(
            "## hermes.review-archive/v2"
        )
        == 1
    )

    tail = text.split(
        "## hermes.review-archive/v2",
        1,
    )[1]

    assert tail.count(
        "```json"
    ) == 1

    raw = (
        tail.split(
            "```json",
            1,
        )[1]
        .split(
            "```",
            1,
        )[0]
        .strip()
    )

    return json.loads(raw), text


def test_valid_pass_v2_archive_and_digest(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    metadata = insert_review(
        db,
        repo,
    )

    path = archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    envelope, _ = read_v2_envelope(
        path
    )

    assert envelope["schema"] == (
        "hermes.review-archive/v2"
    )

    assert envelope["verdict"] == "PASS"
    assert envelope["verdict_source"] == (
        "metadata"
    )

    assert (
        envelope["implementation_task_id"]
        == IMPLEMENTATION_ID
    )

    assert (
        envelope["review_task_id"]
        == REVIEW_ID
    )

    assert envelope["review_run_id"] == 10

    assert envelope["repository_state"] == (
        metadata["repository_state"]
    )

    assert (
        envelope[
            "archive_envelope_sha256"
        ]
        == archive.sha256_canonical_excluding(
            envelope,
            "archive_envelope_sha256",
        )
    )


def test_valid_changes_required_v2(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        verdict="CHANGES REQUIRED",
        summary="PASS narrative conflict",
    )

    envelope, _ = read_v2_envelope(
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )
    )

    assert envelope["verdict"] == (
        "CHANGES REQUIRED"
    )

    assert envelope["verdict_source"] == (
        "metadata"
    )


def test_max_integer_run_selected(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        run_id=5,
        verdict="CHANGES REQUIRED",
    )

    state = repository_state(
        repo
    )

    metadata = {
        "implementation_task_id":
            IMPLEMENTATION_ID,
        "verdict":
            "PASS",
        "repository_state":
            state,
        "repository_state_sha256":
            state["aggregate_sha256"],
    }

    con = sqlite3.connect(db)

    con.execute(
        """
        INSERT INTO task_runs
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            99,
            REVIEW_ID,
            "reviewer",
            "done",
            "completed",
            "PASS newest",
            json.dumps(metadata),
        ),
    )

    con.commit()
    con.close()

    envelope, _ = read_v2_envelope(
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )
    )

    assert envelope["review_run_id"] == 99
    assert envelope["verdict"] == "PASS"


def test_latest_run_rejects_duplicate_and_boolean_ids():
    with pytest.raises(
        ArchiveValidationError
    ):
        archive.select_latest_run_rows(
            [
                {"id": 1},
                {"id": 1},
            ],
            REVIEW_ID,
        )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.select_latest_run_rows(
            [
                {"id": True},
            ],
            REVIEW_ID,
        )


def test_metadata_verdict_authoritative():
    assert archive.classify_verdict(
        {
            "summary":
                "CHANGES REQUIRED"
        },
        {
            "verdict":
                "PASS"
        },
    ) == (
        "PASS",
        "metadata",
    )


def test_summary_fallback_rules():
    assert archive.classify_verdict(
        {
            "summary":
                "PASS final"
        },
        {
            "verdict":
                "unknown"
        },
    ) == (
        "PASS",
        "summary",
    )

    assert archive.classify_verdict(
        {
            "summary":
                "CHANGES REQUIRED final"
        },
        {},
    ) == (
        "CHANGES REQUIRED",
        "summary",
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.classify_verdict(
            {
                "summary":
                    "PASS final"
            },
            {
                "verdict":
                    "YES"
            },
        )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.classify_verdict(
            {
                "summary":
                    "PASS and CHANGES REQUIRED"
            },
            {
                "verdict":
                    "unknown"
            },
        )


def test_parent_metadata_mismatch_fails(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        metadata_implementation_id=
            "t_33333333",
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_parent_list_must_be_exactly_one(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    con = sqlite3.connect(db)

    con.execute(
        """
        UPDATE task_events
        SET payload=?
        """,
        (
            json.dumps(
                {
                    "parents": [
                        IMPLEMENTATION_ID,
                        "t_33333333",
                    ]
                }
            ),
        ),
    )

    con.commit()
    con.close()

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_historical_missing_fingerprint_is_legacy(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        state=False,
    )

    path = archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    text = path.read_text()

    assert (
        "hermes.review-archive/v2"
        not in text
    )


def test_half_present_repository_state_fails(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        state=False,
        metadata_extra={
            "repository_state_sha256":
                "0" * 64
        },
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_malformed_repository_state_fails(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        state=False,
        metadata_extra={
            "repository_state":
                "bad",
            "repository_state_sha256":
                "0" * 64,
        },
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_bad_repository_aggregate_fails(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    metadata = insert_review(
        db,
        repo,
    )

    metadata[
        "repository_state"
    ]["head"] = "b" * 40

    con = sqlite3.connect(db)

    con.execute(
        """
        UPDATE task_runs
        SET metadata=?
        """,
        (
            json.dumps(
                metadata
            ),
        ),
    )

    con.commit()
    con.close()

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_top_level_repository_digest_mismatch(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    metadata = insert_review(
        db,
        repo,
    )

    metadata[
        "repository_state_sha256"
    ] = "0" * 64

    con = sqlite3.connect(db)

    con.execute(
        """
        UPDATE task_runs
        SET metadata=?
        """,
        (
            json.dumps(
                metadata
            ),
        ),
    )

    con.commit()
    con.close()

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )


def test_deterministic_filename_and_single_v2_section(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
        completed_at=1700000000,
    )

    path = archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    assert path == artifact_path(
        repo
    )

    assert (
        path.read_text().count(
            "## hermes.review-archive/v2"
        )
        == 1
    )


def test_idempotent_second_invocation_no_rewrite(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    first = archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    original = first.read_bytes()

    def forbidden_open(*args, **kwargs):
        raise AssertionError(
            "rewrite attempted"
        )

    monkeypatch.setattr(
        archive.os,
        "open",
        forbidden_open,
    )

    second = archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    assert second == first
    assert second.read_bytes() == original


def test_conflicting_artifact_never_overwritten(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    path = artifact_path(
        repo
    )

    path.parent.mkdir(
        parents=True
    )

    path.write_bytes(
        b"conflict"
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )

    assert path.read_bytes() == (
        b"conflict"
    )


def test_artifact_symlink_rejected(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    path = artifact_path(
        repo
    )

    path.parent.mkdir(
        parents=True
    )

    target = tmp_path / "outside"

    target.write_text(
        "outside"
    )

    path.symlink_to(
        target
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )

    assert target.read_text() == (
        "outside"
    )


def test_reviews_directory_symlink_escape_rejected(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    outside = tmp_path / "outside"
    outside.mkdir()

    (repo / ".ai").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(
        ArchiveValidationError
    ):
        archive.archive_review(
            str(repo),
            REVIEW_ID,
        )

    assert list(
        outside.iterdir()
    ) == []


def test_only_read_only_git_subprocesses(
    tmp_path,
    monkeypatch,
):
    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    db = make_db(
        tmp_path,
        monkeypatch,
    )

    insert_review(
        db,
        repo,
    )

    calls = []

    class Result:
        returncode = 0
        stdout = ""

    def fake_run(
        argv,
        **kwargs,
    ):
        calls.append(
            (
                argv,
                kwargs,
            )
        )
        return Result()

    monkeypatch.setattr(
        archive.subprocess,
        "run",
        fake_run,
    )

    archive.archive_review(
        str(repo),
        REVIEW_ID,
    )

    assert [
        item[0]
        for item in calls
    ] == [
        [
            "git",
            "status",
            "--short",
        ],
        [
            "git",
            "log",
            "--oneline",
            "-5",
        ],
    ]

    assert all(
        kwargs["shell"] is False
        and kwargs["timeout"]
        == archive.GIT_TIMEOUT_SECONDS
        for _, kwargs in calls
    )

    prohibited = {
        "add",
        "commit",
        "push",
        "merge",
        "reset",
        "restore",
        "checkout",
        "clean",
        "rebase",
        "update-index",
        "hash-object",
    }

    assert not any(
        any(
            token in prohibited
            for token in argv[1:]
        )
        for argv, _ in calls
    )


def test_cli_usage_and_task_id_validation(
    tmp_path,
    monkeypatch,
):
    assert archive.main([]) == (
        archive.EXIT_USAGE
    )

    repo = make_repo(
        tmp_path,
        monkeypatch,
    )

    make_db(
        tmp_path,
        monkeypatch,
    )

    assert archive.main(
        [
            str(repo),
            "../bad",
        ]
    ) == archive.EXIT_VALIDATION
