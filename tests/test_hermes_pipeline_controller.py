import importlib.util
import json
import os
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path

import pytest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "hermes-pipeline-controller.py"
)
spec = importlib.util.spec_from_file_location("hpc", MODULE_PATH)
hpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hpc)

TASK_ID = "t_pipeline_controller_a1"


def make_task(**overrides):
    t = {
        "id": TASK_ID,
        "title": "pipeline-controller-a1",
        "body": "Implement pipeline-controller-a1",
        "assignee": "coder-claude",
        "status": "done",
        "priority": 0,
        "tenant": None,
        "workspace_kind": "dir",
        "workspace_path": "/opt/ai/projects/ai-server-mcp-catalog",
        "branch_name": "hermes/pipeline-controller-v1",
        "project_id": None,
        "created_by": "pipeline_bridge",
        "created_at": 1,
        "started_at": 2,
        "completed_at": 3,
        "result": "PASS",
        "skills": None,
        "max_retries": 0,
        "model_override": None,
        "provider_override": None,
        "session_id": "s1",
        "workflow_template_id": None,
        "current_step_key": None,
    }
    t.update(overrides)
    return t


def make_run(**overrides):
    r = {
        "id": 1,
        "profile": "coder-claude",
        "step_key": None,
        "status": "done",
        "outcome": "completed",
        "started_at": 2,
        "ended_at": 3,
        "summary": "ok",
        "error": None,
        "metadata": None,
        "worker_pid": 42,
    }
    r.update(overrides)
    return r


def make_show(**overrides):
    s = {
        "task": make_task(),
        "latest_summary": "ok",
        "parents": [],
        "children": [],
        "comments": [],
        "events": [{"kind": "created"}],
        "runs": [make_run()],
    }
    s.update(overrides)
    return s


def stub_hermes(show_obj=None, runs_obj=None, show_exit=0, runs_exit=0, show_stdout=None, runs_stdout=None):
    def run(args, **kwargs):
        if args[1:3] == ["kanban", "show"]:
            out = show_stdout if show_stdout is not None else json.dumps(show_obj)
            return subprocess.CompletedProcess(args, show_exit, stdout=out, stderr="")
        if args[1:3] == ["kanban", "runs"]:
            out = runs_stdout if runs_stdout is not None else json.dumps(runs_obj)
            return subprocess.CompletedProcess(args, runs_exit, stdout=out, stderr="")
        raise AssertionError("unexpected subprocess call: %r" % (args,))

    return mock.patch.object(hpc.subprocess, "run", side_effect=run)


def parse_stdout_line(c):
    captured = c.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == ["task_id", "show_exit_code", "runs_exit_code", "valid", "errors"]
    return captured, obj


def test_check_done_pass_is_valid(capsys):
    with stub_hermes(make_show(), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_OK
    assert m.call_count == 2
    captured, obj = parse_stdout_line(capsys)
    assert obj["task_id"] == TASK_ID
    assert obj["show_exit_code"] == 0
    assert obj["runs_exit_code"] == 0
    assert obj["valid"] is True
    assert obj["errors"] == []


def test_check_archived_with_blocked_runs_is_valid(capsys):
    runs = [make_run(status="blocked", outcome="blocked")]
    with stub_hermes(make_show(task=make_task(status="archived"), runs=runs), runs) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is True
    assert obj["errors"] == []


def test_check_changes_required_summary_is_structurally_valid(capsys):
    with stub_hermes(make_show(task=make_task(result=None), runs=[make_run(summary="CHANGES REQUIRED")]), [make_run(summary="CHANGES REQUIRED")]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is True
    assert obj["errors"] == []


def test_check_nonexistent_task_is_transport_failure(capsys):
    with stub_hermes(show_exit=1, show_stdout="no such task: " + TASK_ID, runs_exit=1, runs_stdout="no such task") as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "no such task" in captured.err


def test_check_rejects_extra_task_key(capsys):
    t = make_task()
    t["extra_key"] = "surprise"
    with stub_hermes(make_show(task=t), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("unknown keys" in e and "extra_key" in e for e in obj["errors"])


def test_check_rejects_missing_task_key(capsys):
    t = make_task()
    del t["session_id"]
    with stub_hermes(make_show(task=t), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("missing required keys" in e and "session_id" in e for e in obj["errors"])


def test_check_rejects_unknown_event_kind(capsys):
    with stub_hermes(make_show(events=[{"kind": "reviewed"}]), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("kind" in e and "reviewed" in e for e in obj["errors"])


def test_check_rejects_wrong_run_key_set(capsys):
    with stub_hermes(make_show(), [dict(make_run(), extra_field=1)]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("runs[0]" in e and "unknown keys" in e and "extra_field" in e for e in obj["errors"])


def test_check_rejects_unknown_task_status(capsys):
    with stub_hermes(make_show(task=make_task(status="running")), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("task.status" in e and "running" in e for e in obj["errors"])


def test_check_rejects_unknown_run_status(capsys):
    with stub_hermes(make_show(runs=[make_run(status="failed")]), [make_run(status="failed")]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("runs[0].status" in e for e in obj["errors"])


def test_check_rejects_mismatched_run_payloads(capsys):
    with stub_hermes(make_show(), [dict(make_run(), summary="different")]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("not equal" in e for e in obj["errors"])


def test_check_rejects_wrong_task_id(capsys):
    with stub_hermes(make_show(task=make_task(id="t_other")), [make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("task.id" in e and "t_other" in e for e in obj["errors"])


def test_check_rejects_non_json_show_stdout(capsys):
    with stub_hermes(show_stdout="not json", runs_obj=[make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "not valid JSON" in captured.err


def test_check_rejects_non_json_runs_stdout(capsys):
    with stub_hermes(show_obj=make_show(), runs_stdout="not json") as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_check_rejects_unknown_show_top_level_shape(capsys):
    s = make_show()
    s["extra_top"] = 1
    with stub_hermes(show_obj=s, runs_obj=[make_run()]) as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_check_rejects_nonzero_runs_exit(capsys):
    with stub_hermes(show_obj=make_show(), runs_exit=1, runs_stdout="boom") as m:
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "exited 1" in captured.err


def test_check_handles_missing_hermes_executable(capsys):
    def raise_fn(*args, **kwargs):
        raise FileNotFoundError("hermes: command not found")
    with mock.patch.object(hpc.subprocess, "run", side_effect=raise_fn):
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "launch hermes" in captured.err


@pytest.mark.parametrize("argv", [
    [],
    ["frobnicate"],
    ["check"],
    ["check", "t1", "extra"],
])
def test_bad_cli_usage_returns_exit_3(capsys, argv):
    rc = hpc.main(argv)
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage error" in captured.err


# --- A2: create-implementation / create-review / create-correction ---

VALID_WD = "/opt/ai/projects/ai-server-mcp-catalog"
IMPL_ID = "t_impl1"
REVIEW_ID = "t_review1"


def stub_create_aware(show_obj=None, create_obj=None, show_exit=0, create_exit=0,
                       show_stdout=None, create_stdout=None):
    calls = []

    def run(args, **kwargs):
        calls.append(list(args))
        if args[1:3] == ["kanban", "show"]:
            out = show_stdout if show_stdout is not None else json.dumps(show_obj)
            return subprocess.CompletedProcess(args, show_exit, stdout=out, stderr="")
        if args[1:3] == ["kanban", "create"]:
            out = create_stdout if create_stdout is not None else json.dumps(create_obj)
            return subprocess.CompletedProcess(args, create_exit, stdout=out, stderr="")
        raise AssertionError("unexpected subprocess call: %r" % (args,))

    patcher = mock.patch.object(hpc.subprocess, "run", side_effect=run)
    return patcher, calls


def parse_single_json_line(capsys, expected_keys):
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == expected_keys
    return captured, obj


def show_calls(calls):
    return [c for c in calls if c[1:3] == ["kanban", "show"]]


def create_calls(calls):
    return [c for c in calls if c[1:3] == ["kanban", "create"]]


def test_stable_key_determinism():
    k1 = hpc.stable_key(VALID_WD, "my-feature", "implementation")
    k2 = hpc.stable_key(VALID_WD, "my-feature", "implementation")
    assert k1 == k2
    assert k1.startswith("pipeline:implementation:")
    digest = k1.split(":")[2]
    assert len(digest) == 16
    int(digest, 16)  # must be hex

    k3 = hpc.stable_key(VALID_WD, "my-feature", "review")
    assert k3 != k1
    assert k3.startswith("pipeline:review:")


def test_create_implementation_success(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_created"})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["phase"] == "implementation"
    assert obj["task_id"] == "t_created"
    assert obj["parents"] == []
    assert obj["workdir"] == VALID_WD
    assert obj["feature"] == "widgets"
    assert obj["idempotency_key"] == hpc.stable_key(VALID_WD, "widgets", "implementation")

    assert len(show_calls(calls)) == 0
    assert len(create_calls(calls)) == 1
    argv = create_calls(calls)[0]
    assert argv == [
        "hermes", "kanban", "create",
        "Implement widgets in ai-server-mcp-catalog",
        "--body", argv[5],
        "--assignee", "coder-claude",
        "--workspace", "dir:" + VALID_WD,
        "--idempotency-key", obj["idempotency_key"],
        "--created-by", "pipeline_bridge",
        "--max-retries", "3",
        "--json",
    ]


def test_formal_review_check_rejects_narrative_pass_without_metadata(capsys):
    task = make_task(
        id="t_formalreview1", title="formal review", assignee="reviewer",
        body=hpc.FORMAL_REVIEW_MARKER + "\nReview this change",
        workspace_path=VALID_WD,
    )
    run = make_run(
        id=2, profile="reviewer", status="done", outcome="completed",
        summary="PASS", metadata=None,
    )
    show = make_show(task=task, parents=[IMPL_ID], runs=[run])
    with stub_hermes(show, [run]):
        rc = hpc.main(["check", "t_formalreview1"])
    assert rc == hpc.EXIT_VALIDATION
    _, obj = parse_stdout_line(capsys)
    assert obj["valid"] is False
    assert any("FORMAL_REVIEW_METADATA_INVALID" in error for error in obj["errors"])


def test_unmarked_reviewer_task_keeps_legacy_check_compatibility(capsys):
    task = make_task(assignee="reviewer", body="Historical reviewer task")
    run = make_run(profile="reviewer", summary="PASS")
    with stub_hermes(make_show(task=task, runs=[run]), [run]):
        rc = hpc.main(["check", TASK_ID])
    assert rc == hpc.EXIT_OK
    _, obj = parse_stdout_line(capsys)
    assert obj["valid"] is True


def _formal_state(workdir):
    state = {
        "schema": hpc.REPOSITORY_STATE_SCHEMA,
        "workdir": os.path.realpath(workdir),
        "head": "a" * 40,
        "changed_paths": [],
        "staged_patch_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "unstaged_patch_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "untracked": [],
    }
    state["aggregate_sha256"] = hpc._sha256_canonical_excluding(state, "aggregate_sha256")
    return state


@pytest.mark.parametrize("verdict", ["PASS", "CHANGES REQUIRED"])
def test_formal_review_metadata_valid_verdicts_are_accepted(verdict):
    state = _formal_state(VALID_WD)
    task = make_task(
        assignee="reviewer", body=hpc.FORMAL_REVIEW_MARKER,
        workspace_path=VALID_WD,
    )
    metadata = {
        "implementation_task_id": IMPL_ID,
        "verdict": verdict,
        "mutation_performed": False,
        "repository_state": state,
        "repository_state_sha256": state["aggregate_sha256"],
    }
    assert hpc._formal_review_metadata_errors(
        task, [IMPL_ID], [make_run(id=2, profile="reviewer", metadata=metadata)]
    ) == []


@pytest.mark.parametrize("field", [
    "implementation_task_id", "mutation_performed", "repository_state",
    "repository_state_sha256", "verdict",
])
def test_formal_review_metadata_missing_field_is_rejected(field):
    state = _formal_state(VALID_WD)
    metadata = {
        "implementation_task_id": IMPL_ID,
        "verdict": "PASS",
        "mutation_performed": False,
        "repository_state": state,
        "repository_state_sha256": state["aggregate_sha256"],
    }
    metadata.pop(field)
    task = make_task(assignee="reviewer", body=hpc.FORMAL_REVIEW_MARKER, workspace_path=VALID_WD)
    errors = hpc._formal_review_metadata_errors(
        task, [IMPL_ID], [make_run(id=2, profile="reviewer", metadata=metadata)]
    )
    assert errors
    assert "FORMAL_REVIEW_METADATA_INVALID" in " ".join(errors)


def test_create_implementation_workdir_outside_allowed_root(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_created"})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", "/tmp/foo", "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert obj["blocked"] is True
    assert obj["task_id"] is None
    assert obj["reason"].startswith("workdir")
    assert len(create_calls(calls)) == 0


def test_create_implementation_relative_workdir(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_created"})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", "relative/path", "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert obj["reason"].startswith("workdir")
    assert len(create_calls(calls)) == 0


def test_create_implementation_nonexistent_workdir(capsys):
    missing = "/opt/ai/projects/definitely_not_here_xyz"
    patcher, calls = stub_create_aware(create_obj={"id": "t_created"})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", missing, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "not exist" in obj["reason"] or "directory" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_implementation_transport_nonzero_exit(capsys):
    patcher, calls = stub_create_aware(create_exit=1, create_stdout="boom")
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert len(create_calls(calls)) == 1


def test_create_implementation_missing_id(capsys):
    patcher, calls = stub_create_aware(create_obj={"foo": 1})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "no task id" in captured.err


@pytest.mark.parametrize("malformed", [
    [],
    [{"id": "t_one"}, {"id": "t_two"}],
    {"id": ""},
    {"id": 123},
])
def test_create_implementation_malformed_create_results(capsys, malformed):
    patcher, calls = stub_create_aware(create_obj=malformed)
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""


def test_create_result_accepts_singleton_list(capsys):
    patcher, calls = stub_create_aware(create_obj=[{"id": "t_created"}])
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["task_id"] == "t_created"


def test_idempotency_key_returned_id_preserved(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_original"})
    with patcher:
        rc = hpc.main([
            "create-implementation", "--workdir", VALID_WD, "--feature", "widgets",
        ])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["task_id"] == "t_original"


def test_create_review_success(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_review_created"})
    with patcher:
        rc = hpc.main([
            "create-review", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID,
        ])
    assert rc == hpc.EXIT_OK
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["phase"] == "review"
    assert obj["parents"] == [IMPL_ID]
    assert "review:" + IMPL_ID in obj["idempotency_key"]

    argv = create_calls(calls)[0]
    assert argv == [
        "hermes", "kanban", "create",
        "Review widgets in ai-server-mcp-catalog",
        "--body", argv[5],
        "--assignee", "reviewer",
        "--parent", IMPL_ID,
        "--workspace", "dir:" + VALID_WD,
        "--idempotency-key", obj["idempotency_key"],
        "--created-by", "pipeline_bridge",
        "--max-retries", "1",
        "--json",
    ]
    assert argv[5].startswith(hpc.FORMAL_REVIEW_MARKER + "\n")


def test_create_review_empty_impl_id(capsys):
    patcher, calls = stub_create_aware(create_obj={"id": "t_review_created"})
    with patcher:
        rc = hpc.main([
            "create-review", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", "",
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "implementation_task_id" in obj["reason"]
    assert len(create_calls(calls)) == 0


@pytest.mark.parametrize("bad_id", ["   ", "!!!", "ab", "t1"])
def test_create_review_malformed_impl_ids(capsys, bad_id):
    patcher, calls = stub_create_aware(create_obj={"id": "t_review_created"})
    with patcher:
        rc = hpc.main([
            "create-review", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", bad_id,
        ])
    assert rc == hpc.EXIT_VALIDATION
    assert len(create_calls(calls)) == 0
    assert len(show_calls(calls)) == 0


def _done_show(review_id=REVIEW_ID, runs=None, status="done"):
    if runs is None:
        runs = [make_run(id=1, summary="CHANGES REQUIRED")]
    return make_show(task=make_task(id=review_id, status=status), runs=runs)


def test_create_correction_changes_required_allowed(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="CHANGES REQUIRED")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_OK
    assert len(show_calls(calls)) == 1
    assert len(create_calls(calls)) == 1
    assert calls.index(show_calls(calls)[0]) < calls.index(create_calls(calls)[0])
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["parents"] == [IMPL_ID, REVIEW_ID]


def test_create_correction_uses_max_run_id(capsys):
    runs = [
        make_run(id=5, summary="PASS"),
        make_run(id=9, summary="CHANGES REQUIRED"),
        make_run(id=2, summary="PASS"),
    ]
    show_obj = _done_show(runs=runs)
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_OK
    assert len(create_calls(calls)) == 1


def test_create_correction_pass_blocks(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="PASS")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "PASS" in obj["reason"]
    assert len(show_calls(calls)) == 1
    assert len(create_calls(calls)) == 0


def test_create_correction_ambiguous_blocks(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="PASS and CHANGES REQUIRED")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "ambiguous" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_correction_unknown_verdict_blocks(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="looks fine to me")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "ambiguous" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_correction_review_not_done_blocks(capsys):
    show_obj = _done_show(status="running", runs=[make_run(id=1, summary="CHANGES REQUIRED")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "not done" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_correction_twoParents_in_argv(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="CHANGES REQUIRED")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_OK
    argv = create_calls(calls)[0]
    parent_indices = [i for i, v in enumerate(argv) if v == "--parent"]
    assert len(parent_indices) == 2
    assert argv[parent_indices[0] + 1] == IMPL_ID
    assert argv[parent_indices[1] + 1] == REVIEW_ID
    captured, obj = parse_single_json_line(
        capsys, ["phase", "task_id", "idempotency_key", "workdir", "feature", "parents"]
    )
    assert obj["parents"] == [IMPL_ID, REVIEW_ID]


def test_create_correction_does_notTrust_cli_reviewSummary(capsys):
    show_obj = _done_show(runs=[make_run(id=1, summary="PASS")])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
            "--review_summary", "CHANGES REQUIRED",
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "PASS" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_correction_emptyRuns_blocks(capsys):
    show_obj = _done_show(runs=[])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "ambiguous" in obj["reason"]
    assert len(create_calls(calls)) == 0


def test_create_correction_transport_show_failure(capsys):
    patcher, calls = stub_create_aware(show_exit=1, show_stdout="boom", create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert len(create_calls(calls)) == 0


def test_bad_cli_usage_create_missing_feature(capsys):
    rc = hpc.main(["create-implementation", "--workdir", VALID_WD])
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage error" in captured.err


# === A3 wait tests (pipeline-controller-a3-polling) ===

def wait_round_result_for(status="running"):
    d = {
        "task": make_task(status=status),
        "latest_summary": None,
        "parents": [],
        "children": [],
        "comments": [],
        "events": [],
        "runs": [],
    }
    return d


def stub_wait_read(status, show_exit=0, raise_exc=None):
    def run(argv, **kwargs):
        expected = ["hermes", "kanban", "show", TASK_ID, "--json"]
        if list(argv) != expected:
            raise AssertionError("unexpected argv: %r" % (argv,))
        if kwargs.get("shell") is not False:
            raise AssertionError("wait read must use shell=False, got kwargs=%r" % (kwargs,))
        if raise_exc is not None:
            raise raise_exc
        if show_exit != 0:
            return subprocess.CompletedProcess(argv, show_exit, stdout="boom", stderr="")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for(status)), stderr="")
    return mock.patch.object(hpc.subprocess, "run", side_effect=run)


def read_first_json_line(cap):
    cap2 = cap.readouterr()
    lines = [l for l in cap2.out.splitlines() if l.strip()]
    assert len(lines) == 1
    return cap2, json.loads(lines[0])


def fake_monotonic(seq):
    calls = {"n": 0}
    def _m():
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[idx]
    return _m


def test_wait_terminal_done(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    with mock.patch.object(hpc, "SLEEP") as sleep, \
         stub_wait_read("done") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
        sleep.assert_not_called()
    assert rc == hpc.EXIT_OK
    cap2, obj = read_first_json_line(capsys)
    assert list(obj.keys()) == ["outcome", "task_id", "status"]
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "done"}
    assert m.call_count == 1
    assert m.call_args_list[0][0][0] == ["hermes", "kanban", "show", TASK_ID, "--json"]
    assert m.call_args_list[0][1].get("shell") is False


def test_wait_terminal_archived(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    with mock.patch.object(hpc, "SLEEP") as sleep, \
         stub_wait_read("archived") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
        sleep.assert_not_called()
    assert rc == hpc.EXIT_OK
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "archived"}


def test_wait_terminal_blocked(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    with mock.patch.object(hpc, "SLEEP") as sleep, \
         stub_wait_read("blocked") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
        sleep.assert_not_called()
    assert rc == hpc.EXIT_OK
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "blocked"}


def test_wait_non_terminal_then_terminal_one_bounded_sleep(capsys, monkeypatch):
    seq = iter([0.0, 0.0, 2.0, 2.0, 2.0, 2.0])
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: next(seq))
    statuses = iter(["running", "done"])
    def run(argv, **kwargs):
        expected = ["hermes", "kanban", "show", TASK_ID, "--json"]
        assert list(argv) == expected
        assert kwargs.get("shell") is False
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for(next(statuses))), stderr="")
    with mock.patch.object(hpc, "SLEEP") as sleep, \
         mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "3", "--interval", "0.5"])
    assert rc == hpc.EXIT_OK
    assert sleep.call_count == 1
    assert sleep.call_args_list[0][0][0] <= 0.5001
    assert m.call_count == 2
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "done"}


def test_wait_timeout_before_second_poll_reports_status(capsys, monkeypatch):
    seq = [0.0, 0.0, 1.0, 1.0]
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    # First read is gate-protected (deadline=1.0, clock=0.0, budget 1.0 > 0):
    # non-terminal "running". Then remaining (1.0) <= interval (1.0), so the
    # post-round gate reports the timeout before any second poll starts.
    with stub_wait_read("running") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
    assert rc == hpc.EXIT_TIMEOUT
    cap2, obj = read_first_json_line(capsys)
    assert list(obj.keys()) == ["outcome", "task_id", "last_status", "timeout_seconds"]
    assert obj == {
        "task_id": TASK_ID, "outcome": "timeout",
        "last_status": "running", "timeout_seconds": 1.0,
    }


def test_wait_first_read_gate_rejects_when_deadline_expired(capsys, monkeypatch):
    # The first read is NOT exempt from the deadline gate: with the budget
    # already exhausted before any read is attempted, the gate fires ->
    # exit 4, last_status None, zero subprocess runs, zero sleeps.
    seq = [0.0, 1.5, 1.5, 1.5]
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    with mock.patch.object(hpc, "SLEEP") as sle, \
         mock.patch.object(hpc.subprocess, "run") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
    assert rc == hpc.EXIT_TIMEOUT
    assert m.call_count == 0
    sle.assert_not_called()
    cap2, obj = read_first_json_line(capsys)
    assert list(obj.keys()) == ["outcome", "task_id", "last_status", "timeout_seconds"]
    assert obj == {
        "task_id": TASK_ID, "outcome": "timeout",
        "last_status": None, "timeout_seconds": 1.0,
    }


def test_wait_timeout_without_valid_state(capsys, monkeypatch):
    # Transport failure, then clock advances past the deadline before retry.
    seq = [0.0, 2.0]
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    with stub_wait_read(status="running", raise_exc=FileNotFoundError("hermes")) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_TIMEOUT
    cap2, obj = read_first_json_line(capsys)
    assert list(obj.keys()) == ["outcome", "task_id", "last_status", "timeout_seconds"]
    assert obj == {"task_id": TASK_ID, "outcome": "timeout",
                   "last_status": None, "timeout_seconds": 1.0}


def test_wait_interval_clipping_uses_deadline_budget(capsys, monkeypatch):
    # interval=5 (longer than timeout=1). SLEEP must be bounded to <= the
    # remaining deadline budget, not the full interval.
    seq = [0.5, 1.0, 1.0]
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    with mock.patch.object(hpc, "SLEEP") as sleep, stub_wait_read("ready"):
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--interval", "5"])
    assert rc == hpc.EXIT_TIMEOUT
    assert sleep.call_count == 1
    assert sleep.call_args_list[0][0][0] <= 0.5001


def test_wait_retry_then_success_no_retry_sleep(capsys, monkeypatch):
    # First attempt: FileNotFoundError. Retry (no sleep). Succeeds with "done".
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        expected = ["hermes", "kanban", "show", TASK_ID, "--json"]
        assert list(argv) == expected
        assert kwargs.get("shell") is False
        if run.calls[0] == 0:
            raise FileNotFoundError("hermes")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for("done")), stderr="")
    run.calls = run.calls if hasattr(run, "calls") else [0]
    # rebind: use a fresh callable with its own counter.
    calls = [0]
    def run2(argv, **kwargs):
        expected = ["hermes", "kanban", "show", TASK_ID, "--json"]
        assert list(argv) == expected
        assert kwargs.get("shell") is False
        idx = calls[0]
        calls[0] += 1
        if idx == 0:
            raise FileNotFoundError("hermes")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for("done")), stderr="")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run2) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--max-retries", "1"])
    assert rc == hpc.EXIT_OK
    assert m.call_count == 2
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "done"}


def test_wait_generic_oserror_exhaustion_exact_attempts(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    with mock.patch.object(hpc.subprocess, "run",
                           side_effect=OSError("no net")) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "1"])
    assert rc == hpc.EXIT_TRANSPORT
    assert m.call_count == 2
    cap2 = capsys.readouterr()
    assert cap2.err.startswith("transport error: ")
    assert "no net" in cap2.err
    lines = [l for l in cap2.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == ["outcome", "task_id", "attempts", "error"]
    assert obj == {
        "task_id": TASK_ID, "outcome": "transport_error",
        "attempts": 2, "error": obj["error"],
    }
    assert "no net" in obj["error"]


def test_wait_nonzero_exit_transport_exhaust(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_TRANSPORT
    assert m.call_count == 3
    cap2 = capsys.readouterr()
    assert cap2.err.startswith("transport error: ")
    lines = [l for l in cap2.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == ["outcome", "task_id", "attempts", "error"]
    assert obj["outcome"] == "transport_error"
    assert obj["attempts"] == 3


def test_wait_non_json_stdout_is_transport(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="not-json", stderr="")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "1"])
    assert rc == hpc.EXIT_TRANSPORT
    assert m.call_count == 2
    cap2 = capsys.readouterr()
    assert cap2.err.startswith("transport error: ")
    lines = [l for l in cap2.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["outcome"] == "transport_error"
    assert "not valid JSON" in obj["error"]


def test_wait_wrong_top_level_shape_exits_2(capsys, monkeypatch):
    # Missing top-level keys (task, latest_summary, parents, children, comments, events, runs)
    bad = {"task": make_task(status="done")}  # missing 6 keys
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(bad), stderr="")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_VALIDATION
    assert m.call_count == 1
    cap2 = capsys.readouterr()
    assert cap2.err == ""
    lines = [l for l in cap2.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == ["outcome", "task_id", "error"]
    assert obj["outcome"] == "structural_error"
    assert "top-level" in obj["error"]


def test_wait_wrong_task_id_exits_2(capsys, monkeypatch):
    right = wait_round_result_for("done")
    right["task"] = dict(make_task(), id="t_other")
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(right), stderr="")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_VALIDATION
    assert m.call_count == 1
    cap2 = capsys.readouterr()
    assert cap2.err == ""
    obj = json.loads([l for l in cap2.out.splitlines() if l.strip()][0])
    assert list(obj.keys()) == ["outcome", "task_id", "error"]
    assert "t_other" in obj["error"]
    assert obj["outcome"] == "structural_error"


def test_wait_bad_status_type_exits_2(capsys, monkeypatch):
    right = wait_round_result_for("done")
    right["task"] = dict(make_task(), status=[2, 3])
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(right), stderr="")
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_VALIDATION
    assert m.call_count == 1
    cap2 = capsys.readouterr()
    assert cap2.err == ""
    obj = json.loads([l for l in cap2.out.splitlines() if l.strip()][0])
    assert list(obj.keys()) == ["outcome", "task_id", "error"]
    assert obj["outcome"] == "structural_error"


def test_wait_retry_counter_resets_between_rounds(capsys, monkeypatch):
    # Round 1: 2 transport failures then success (non-terminal "running").
    # Round 2: 3 transport failures (max_retries=2, so max attempts=3).
    # Expected: 5 read calls total, exit 3 (transport), attempts=3.
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    def run(argv, **kwargs):
        expected = ["hermes", "kanban", "show", TASK_ID, "--json"]
        assert list(argv) == expected
        assert kwargs.get("shell") is False
        idx = run.calls[0]
        run.calls[0] += 1
        # Round 1: calls 0, 1, 2. Round 2: calls 3, 4, 5.
        if idx == 0 or idx == 1 or idx >= 3:
            raise OSError("no net %d" % idx)
        # idx == 2: success non-terminal
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for("running")), stderr="")
    run.calls = [0]
    with mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(
            ["wait", TASK_ID, "--timeout", "1",
             "--interval", "0.1", "--max-retries", "2"])
    assert rc == hpc.EXIT_TRANSPORT
    assert m.call_count == 6
    cap2 = capsys.readouterr()
    assert cap2.err.startswith("transport error: ")
    obj = json.loads([l for l in cap2.out.splitlines() if l.strip()][0])
    assert list(obj.keys()) == ["outcome", "task_id", "attempts", "error"]
    assert obj["outcome"] == "transport_error"
    assert obj["attempts"] == 3
    assert "no net 5" in obj["error"]


def test_wait_subprocess_timeout_deadline_expiry_returns_4(capsys, monkeypatch):
    # Deadline already past when the subprocess read is attempted.
    seq = [0.0, 1.5, 1.5, 1.5]
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    def bad_run(argv, **kwargs):
        # Assert the passed timeout budget is bounded by remaining (<= timeout).
        passed = kwargs.get("timeout")
        assert passed is not None and passed <= 1.0001, \
            "expected subprocess timeout bounded by remaining budget, got %r" % (passed,)
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=passed)
    with mock.patch.object(hpc.subprocess, "run", side_effect=bad_run) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
    assert rc == hpc.EXIT_TIMEOUT
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "timeout",
                   "last_status": None, "timeout_seconds": 1.0}


def test_wait_subprocess_timeout_with_time_remaining_retries(capsys, monkeypatch):
    # A TimeoutExpired that occurs while budget remains is NOT an immediate
    # timeout: it consumes one retry attempt and the loop retries. Here the
    # first read times out and the second read succeeds.
    seq = [0.0] * 8
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    call = {"n": 0}
    def run(argv, **kwargs):
        assert list(argv) == ["hermes", "kanban", "show", TASK_ID, "--json"]
        assert kwargs.get("shell") is False
        n = call["n"]
        call["n"] += 1
        if n == 0:
            raise subprocess.TimeoutExpired(
                cmd=list(argv), timeout=kwargs.get("timeout"))
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps(wait_round_result_for("done")), stderr="")
    with mock.patch.object(hpc, "SLEEP") as sle, \
         mock.patch.object(hpc.subprocess, "run", side_effect=run) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--max-retries", "1"])
    assert rc == hpc.EXIT_OK
    assert m.call_count == 2
    sle.assert_not_called()
    cap2, obj = read_first_json_line(capsys)
    assert obj == {"task_id": TASK_ID, "outcome": "terminal", "status": "done"}


def test_wait_subprocess_timeout_exhausts_retries_deadline_remaining(capsys, monkeypatch):
    # Retries exhausted (max_retries + 1 attempts) while the deadline has NOT
    # expired -> transport error, exit 3, attempts == max_retries + 1.
    seq = [0.0] * 8
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    with mock.patch.object(hpc, "SLEEP") as sle, \
         mock.patch.object(
             hpc.subprocess, "run",
             side_effect=subprocess.TimeoutExpired(cmd=["hermes"], timeout=1.0)) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--max-retries", "1"])
    assert rc == hpc.EXIT_TRANSPORT
    assert m.call_count == 2
    sle.assert_not_called()
    cap2 = capsys.readouterr()
    assert cap2.err.startswith("transport error: ")
    assert "subprocess timeout" in cap2.err
    obj = json.loads([l for l in cap2.out.splitlines() if l.strip()][0])
    assert list(obj.keys()) == ["outcome", "task_id", "attempts", "error"]
    assert obj == {"task_id": TASK_ID, "outcome": "transport_error",
                   "attempts": 2, "error": "subprocess timeout"}


def test_wait_subprocess_timeout_deadline_expired_mid_read(capsys, monkeypatch):
    # The read starts with budget remaining but the deadline expires while
    # the subprocess call is in flight -> timeout, exit 4. max_retries=2 keeps
    # attempts (1) below the retry cap (3), proving the deadline branch (not
    # retry exhaustion) produced the exit code.
    seq = [0.0, 0.0, 1.5, 1.5]
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    def bad_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=list(argv), timeout=kwargs.get("timeout"))
    with mock.patch.object(hpc, "SLEEP") as sle, \
         mock.patch.object(hpc.subprocess, "run", side_effect=bad_run) as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1", "--max-retries", "2"])
    assert rc == hpc.EXIT_TIMEOUT
    assert m.call_count == 1
    sle.assert_not_called()
    cap2, obj = read_first_json_line(capsys)
    assert list(obj.keys()) == ["outcome", "task_id", "last_status", "timeout_seconds"]
    assert obj == {
        "task_id": TASK_ID, "outcome": "timeout",
        "last_status": None, "timeout_seconds": 1.0,
    }


def test_wait_cli_usage_errors_exit_3(capsys, monkeypatch):
    bad_argvs = [
        ["wait", TASK_ID],                                  # missing --timeout
        ["wait", "   ", "--timeout", "1"],                  # empty task_id (after strip)
        ["wait", TASK_ID, "--timeout", "0"],
        ["wait", TASK_ID, "--timeout", "-1"],
        ["wait", TASK_ID, "--timeout", "nan"],
        ["wait", TASK_ID, "--timeout", "inf"],
        ["wait", TASK_ID, "--timeout", "1", "--interval", "0"],
        ["wait", TASK_ID, "--timeout", "1", "--interval", "-0.1"],
        ["wait", TASK_ID, "--timeout", "1", "--interval", "nan"],
        ["wait", TASK_ID, "--timeout", "1", "--max-retries", "-1"],
        ["wait", TASK_ID, "--timeout", "1", "--max-retries", "0.5"],
        ["wait", TASK_ID, "--timeout", "1", "--max-retries", "x"],
    ]
    for argv in bad_argvs:
        capsys.readouterr()
        with mock.patch.object(hpc.subprocess, "run") as m, \
             mock.patch.object(hpc, "MONOTONIC") as mono, \
             mock.patch.object(hpc, "SLEEP") as sle:
            rc = hpc.main(argv)
            m.assert_not_called()
            sle.assert_not_called()
        assert rc == hpc.EXIT_TRANSPORT, argv
        cap2 = capsys.readouterr()
        assert cap2.out == "", argv
        assert cap2.err.startswith("usage error: "), argv


def test_wait_defaults_resolve_to_interval_1_and_retries_2():
    p = hpc.build_parser()
    args = p.parse_args(["wait", TASK_ID, "--timeout", "4.2"])
    assert float(args.timeout) == pytest.approx(4.2)
    assert float(args.interval) == pytest.approx(1.0)
    assert int(args.max_retries) == 2
    # And the CLI-level validation accepts the defaults (no usage error raised).
    with mock.patch.object(hpc.subprocess, "run") as m:
        with mock.patch.object(hpc, "MONOTONIC") as mono, mock.patch.object(hpc, "SLEEP") as sle:
            rc = hpc.wait_for_task(TASK_ID, 4.2, 1.0, 2) if False else 0
    # (the real behavior is exercised by the other wait tests)


# === A4: archive-review tests (pipeline-controller-a4-review-archive) ===

ARCHIVE_HELPER = hpc.ARCHIVE_HELPER_PATH


def make_archive_task(**overrides):
    base = make_task(id=REVIEW_ID, assignee="reviewer")
    base.update(overrides)
    return base


def make_archive_run(**overrides):
    base = make_run(id=1, profile="reviewer", status="done", outcome="completed",
                     summary="PASS", metadata=None)
    base.update(overrides)
    return base


def make_archive_show(**overrides):
    s = make_show(task=make_archive_task(), runs=[make_archive_run()])
    s.update(overrides)
    return s


def stub_archive_aware(show_obj=None, show_exit=0, show_stdout=None,
                        helper_returncode=0, helper_stdout="", helper_stderr="",
                        helper_raise=None):
    calls = []
    kwargs_list = []

    def run(args, **kwargs):
        args = list(args)
        calls.append(args)
        kwargs_list.append(kwargs)
        if args[:3] == ["hermes", "kanban", "show"]:
            out = show_stdout if show_stdout is not None else json.dumps(show_obj)
            return subprocess.CompletedProcess(args, show_exit, stdout=out, stderr="")
        if args[:1] == [ARCHIVE_HELPER]:
            if helper_raise is not None:
                raise helper_raise
            return subprocess.CompletedProcess(
                args, helper_returncode, stdout=helper_stdout, stderr=helper_stderr)
        raise AssertionError("unexpected subprocess call: %r" % (args,))

    patcher = mock.patch.object(hpc.subprocess, "run", side_effect=run)
    return patcher, calls, kwargs_list


def archive_show_calls(calls):
    return [c for c in calls if c[:3] == ["hermes", "kanban", "show"]]


def archive_helper_calls(calls):
    return [c for c in calls if c[:1] == [ARCHIVE_HELPER]]


def parse_archive_blocked(capsys):
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == ["phase", "blocked", "reason", "task_id"]
    assert obj["phase"] == "archive-review"
    assert obj["blocked"] is True
    assert obj["task_id"] is None
    return captured, obj


def parse_archive_success(capsys):
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == [
        "phase", "outcome", "review_task_id", "workdir", "verdict", "completed_at",
    ]
    assert obj["phase"] == "archive-review"
    assert obj["outcome"] == "archive-succeeded"
    return captured, obj


def run_archive_review(workdir=VALID_WD, review_task_id=REVIEW_ID):
    return hpc.main(["archive-review", "--workdir", workdir, "--review_task_id", review_task_id])


# --- A: CLI / input validation ---

def test_archive_review_valid_args_accepted(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    assert len(archive_show_calls(calls)) == 1
    assert len(archive_helper_calls(calls)) == 1


@pytest.mark.parametrize("argv", [
    ["archive-review"],
    ["archive-review", "--workdir", VALID_WD],
    ["archive-review", "--review_task_id", REVIEW_ID],
    ["archive-review", "--workdir", VALID_WD, "--review_task_id", REVIEW_ID, "extra"],
])
def test_archive_review_cli_usage_errors_exit_3(capsys, argv):
    with mock.patch.object(hpc.subprocess, "run") as m:
        rc = hpc.main(argv)
    assert rc == hpc.EXIT_TRANSPORT
    m.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage error" in captured.err


@pytest.mark.parametrize("bad_id", ["", "   ", "!!!", "ab", "t1"])
def test_archive_review_malformed_review_task_id_rejected(capsys, bad_id):
    with mock.patch.object(hpc.subprocess, "run") as m:
        rc = run_archive_review(review_task_id=bad_id)
    assert rc == hpc.EXIT_VALIDATION
    m.assert_not_called()
    captured, obj = parse_archive_blocked(capsys)
    assert "review_task_id" in obj["reason"]


@pytest.mark.parametrize("bad_wd", [
    "/tmp/foo",
    "relative/path",
    "/opt/ai/projects/definitely_not_here_xyz",
])
def test_archive_review_bad_workdir_rejected(capsys, bad_wd):
    with mock.patch.object(hpc.subprocess, "run") as m:
        rc = run_archive_review(workdir=bad_wd)
    assert rc == hpc.EXIT_VALIDATION
    m.assert_not_called()
    captured, obj = parse_archive_blocked(capsys)
    assert obj["reason"].startswith("workdir")


# --- B: authoritative show ---

def test_archive_review_show_argv_exact(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    shows = archive_show_calls(calls)
    assert len(shows) == 1
    assert shows[0] == ["hermes", "kanban", "show", REVIEW_ID, "--json"]


def test_archive_review_show_before_helper_ordering(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    show_idx = calls.index(["hermes", "kanban", "show", REVIEW_ID, "--json"])
    helper_idx = calls.index([ARCHIVE_HELPER, VALID_WD, REVIEW_ID])
    assert show_idx < helper_idx


def test_archive_review_show_nonzero_exit_transport(capsys):
    patcher, calls, _ = stub_archive_aware(show_exit=1, show_stdout="boom")
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    assert len(archive_helper_calls(calls)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_archive_review_show_non_json_transport(capsys):
    patcher, calls, _ = stub_archive_aware(show_stdout="not json")
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    assert len(archive_helper_calls(calls)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_archive_review_show_payload_not_dict_transport(capsys):
    patcher, calls, _ = stub_archive_aware(show_obj=[1, 2, 3])
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    assert len(archive_helper_calls(calls)) == 0


def test_archive_review_wrong_task_id_blocked(capsys):
    show_obj = make_archive_show(task=make_archive_task(id="t_other"))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "t_other" in obj["reason"]


# --- C: workspace ---

def test_archive_review_workspace_kind_not_dir_blocked(capsys):
    show_obj = make_archive_show(task=make_archive_task(workspace_kind="git"))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "workspace_kind" in obj["reason"]


@pytest.mark.parametrize("bad_path", [None, 123, "", "relative/path"])
def test_archive_review_malformed_workspace_path_blocked(capsys, bad_path):
    show_obj = make_archive_show(task=make_archive_task(workspace_path=bad_path))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "workspace_path" in obj["reason"]


def test_archive_review_missing_workspace_path_key_blocked(capsys):
    task = make_archive_task()
    del task["workspace_path"]
    show_obj = make_archive_show(task=task)
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "workspace_path" in obj["reason"]


def test_archive_review_workspace_path_mismatch_blocked(capsys):
    show_obj = make_archive_show(
        task=make_archive_task(workspace_path="/opt/ai/projects/other-repo")
    )
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "does not match" in obj["reason"]


# --- D: task state ---

@pytest.mark.parametrize("bad_assignee", ["coder-claude", "", None, "Reviewer"])
def test_archive_review_wrong_assignee_blocked(capsys, bad_assignee):
    show_obj = make_archive_show(task=make_archive_task(assignee=bad_assignee))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "assignee" in obj["reason"]


@pytest.mark.parametrize("bad_status", ["running", "blocked", "ready", "archived"])
def test_archive_review_wrong_status_blocked(capsys, bad_status):
    show_obj = make_archive_show(task=make_archive_task(status=bad_status))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "status" in obj["reason"]


# --- E: completed_at ---

def test_archive_review_completed_at_missing_key_blocked(capsys):
    task = make_archive_task()
    del task["completed_at"]
    show_obj = make_archive_show(task=task)
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "completed_at" in obj["reason"]


@pytest.mark.parametrize("bad_val", [None, 0, -1, "3", 3.0, True, False])
def test_archive_review_completed_at_invalid_blocked(capsys, bad_val):
    show_obj = make_archive_show(task=make_archive_task(completed_at=bad_val))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "completed_at" in obj["reason"]


def test_archive_review_completed_at_positive_int_accepted(capsys):
    show_obj = make_archive_show(task=make_archive_task(completed_at=99))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj["completed_at"] == 99


# --- F: latest run selection ---

def test_archive_review_latest_run_selected_by_max_id_not_position(capsys):
    runs = [
        make_archive_run(id=9, summary="PASS"),
        make_archive_run(id=2, summary="CHANGES REQUIRED"),
        make_archive_run(id=5, summary="CHANGES REQUIRED"),
    ]
    show_obj = make_archive_show(runs=runs)
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "PASS"


def test_archive_review_empty_runs_blocked(capsys):
    show_obj = make_archive_show(runs=[])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "runs" in obj["reason"]


def test_archive_review_runs_not_list_blocked(capsys):
    show_obj = make_archive_show(runs={"not": "a list"})
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0


@pytest.mark.parametrize("bad_run", [
    {"profile": "reviewer", "status": "done", "outcome": "completed", "summary": "PASS"},
    dict(make_archive_run(), id="1"),
    dict(make_archive_run(), id=None),
    dict(make_archive_run(), id=True),
])
def test_archive_review_malformed_run_id_blocked(capsys, bad_run):
    show_obj = make_archive_show(runs=[bad_run])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0


def test_archive_review_duplicate_run_ids_blocked(capsys):
    runs = [make_archive_run(id=1, summary="PASS"), make_archive_run(id=1, summary="CHANGES REQUIRED")]
    show_obj = make_archive_show(runs=runs)
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "duplicate" in obj["reason"]


def test_archive_review_latest_run_invalid_not_fallback(capsys):
    runs = [
        make_archive_run(id=1, outcome="completed", summary="PASS"),
        make_archive_run(id=2, outcome="in_progress", summary="PASS"),
    ]
    show_obj = make_archive_show(runs=runs)
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "outcome" in obj["reason"]


# --- G: latest run state ---

def test_archive_review_latest_run_wrong_profile_blocked(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(profile="coder-claude")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "profile" in obj["reason"]


def test_archive_review_latest_run_wrong_status_blocked(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(status="blocked")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "run status" in obj["reason"]


def test_archive_review_latest_run_wrong_outcome_blocked(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(outcome="blocked")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "outcome" in obj["reason"]


# --- H: verdict logic ---

def test_archive_review_verdict_metadata_pass_summary_both_markers_success(capsys):
    run = make_archive_run(metadata={"verdict": "PASS"}, summary="PASS but CHANGES REQUIRED noted")
    show_obj = make_archive_show(runs=[run])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    assert len(archive_helper_calls(calls)) == 1
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "PASS"


def test_archive_review_verdict_metadata_changes_required_summary_both_markers_success(capsys):
    run = make_archive_run(metadata={"verdict": "CHANGES REQUIRED"}, summary="PASS but CHANGES REQUIRED noted")
    show_obj = make_archive_show(runs=[run])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    assert len(archive_helper_calls(calls)) == 1
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "CHANGES REQUIRED"


@pytest.mark.parametrize("bad_verdict", ["other", 123, ["PASS"], {}])
def test_archive_review_verdict_metadata_invalid_blocked(capsys, bad_verdict):
    run = make_archive_run(metadata={"verdict": bad_verdict}, summary="PASS")
    show_obj = make_archive_show(runs=[run])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "verdict" in obj["reason"]


@pytest.mark.parametrize("metadata", [None, {}, {"verdict": None}, {"verdict": "unknown"}])
def test_archive_review_verdict_metadata_fallback_to_summary(capsys, metadata):
    run = make_archive_run(metadata=metadata, summary="PASS")
    show_obj = make_archive_show(runs=[run])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "PASS"


def test_archive_review_verdict_summary_pass_only_success(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(metadata=None, summary="PASS")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "PASS"


def test_archive_review_verdict_summary_changes_required_only_success(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(metadata=None, summary="CHANGES REQUIRED")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj["verdict"] == "CHANGES REQUIRED"


def test_archive_review_verdict_summary_both_markers_blocked(capsys):
    show_obj = make_archive_show(
        runs=[make_archive_run(metadata=None, summary="PASS and CHANGES REQUIRED")]
    )
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "verdict" in obj["reason"]


def test_archive_review_verdict_summary_neither_marker_blocked(capsys):
    show_obj = make_archive_show(runs=[make_archive_run(metadata=None, summary="looks fine")])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0
    captured, obj = parse_archive_blocked(capsys)
    assert "verdict" in obj["reason"]


@pytest.mark.parametrize("bad_summary", [None, 123])
def test_archive_review_verdict_summary_not_str_blocked(capsys, bad_summary):
    show_obj = make_archive_show(runs=[make_archive_run(metadata=None, summary=bad_summary)])
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_VALIDATION
    assert len(archive_helper_calls(calls)) == 0


# --- I: helper contract ---

def test_archive_review_helper_argv_exact(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    helpers = archive_helper_calls(calls)
    assert len(helpers) == 1
    assert helpers[0] == [ARCHIVE_HELPER, VALID_WD, REVIEW_ID]


def test_archive_review_helper_kwargs_shell_false_timeout_120(capsys):
    show_obj = make_archive_show()
    patcher, calls, kwargs_list = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    helper_idx = [i for i, c in enumerate(calls) if c[:1] == [ARCHIVE_HELPER]][0]
    kwargs = kwargs_list[helper_idx]
    assert kwargs.get("shell") is False
    assert kwargs.get("timeout") == hpc.ARCHIVE_HELPER_TIMEOUT == 120


def test_archive_review_success_output_shape(capsys):
    show_obj = make_archive_show(task=make_archive_task(completed_at=42))
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert obj == {
        "phase": "archive-review",
        "outcome": "archive-succeeded",
        "review_task_id": REVIEW_ID,
        "workdir": VALID_WD,
        "verdict": "PASS",
        "completed_at": 42,
    }


def test_archive_review_idempotent_two_calls_both_succeed(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc1 = run_archive_review()
        capsys.readouterr()
        rc2 = run_archive_review()
    assert rc1 == hpc.EXIT_OK
    assert rc2 == hpc.EXIT_OK
    assert len(archive_helper_calls(calls)) == 2


def test_archive_review_helper_nonzero_exit_transport(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj, helper_returncode=1, helper_stderr="boom")
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_archive_review_helper_oserror_transport(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj, helper_raise=OSError("no exec"))
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err


def test_archive_review_helper_timeout_transport(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(
        show_obj=show_obj,
        helper_raise=subprocess.TimeoutExpired(cmd=[ARCHIVE_HELPER], timeout=120))
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_TRANSPORT
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "transport error" in captured.err
    assert "timed out" in captured.err


def test_archive_review_helper_stdout_junk_not_leaked(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj, helper_stdout="some junk output\nmore junk")
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    captured, obj = parse_archive_success(capsys)
    assert "junk" not in captured.out


def test_archive_review_never_calls_kanban_create(capsys):
    show_obj = make_archive_show()
    patcher, calls, _ = stub_archive_aware(show_obj=show_obj)
    with patcher:
        rc = run_archive_review()
    assert rc == hpc.EXIT_OK
    assert not any("create" in c for c in calls)


# --- J: create-correction regression (shared classify_verdict helper) ---

def test_create_correction_metadata_changes_required_allowed(capsys):
    show_obj = _done_show(runs=[
        make_run(id=1, metadata={"verdict": "CHANGES REQUIRED"}, summary="unrelated narrative")
    ])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_OK
    assert len(create_calls(calls)) == 1


def test_create_correction_metadata_pass_overrides_summary_marker_blocks(capsys):
    show_obj = _done_show(runs=[
        make_run(id=1, metadata={"verdict": "PASS"}, summary="CHANGES REQUIRED mentioned only in narrative")
    ])
    patcher, calls = stub_create_aware(show_obj=show_obj, create_obj={"id": "t_corr"})
    with patcher:
        rc = hpc.main([
            "create-correction", "--workdir", VALID_WD, "--feature", "widgets",
            "--implementation_task_id", IMPL_ID, "--review_task_id", REVIEW_ID,
        ])
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_single_json_line(capsys, ["phase", "blocked", "reason", "task_id"])
    assert "PASS" in obj["reason"]
    assert len(create_calls(calls)) == 0


# --- K: wait regression (no archive-review helper calls, no .ai/reviews writes) ---

def test_wait_never_calls_archive_helper(capsys, monkeypatch):
    monkeypatch.setattr(hpc, "MONOTONIC", lambda: 0.0)
    with mock.patch.object(hpc, "SLEEP"), stub_wait_read("done") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
    assert rc == hpc.EXIT_OK
    for call in m.call_args_list:
        argv = list(call.args[0])
        assert argv[:1] != [ARCHIVE_HELPER]
        assert not any("review-archive-bridge" in str(a) for a in argv)


def test_wait_timeout_never_calls_archive_helper(capsys, monkeypatch):
    seq = [0.0, 0.0, 1.0, 1.0]
    monkeypatch.setattr(hpc, "SLEEP", lambda s: None)
    monkeypatch.setattr(hpc, "MONOTONIC", fake_monotonic(seq))
    with stub_wait_read("running") as m:
        rc = hpc.main(["wait", TASK_ID, "--timeout", "1"])
    assert rc == hpc.EXIT_TIMEOUT
    for call in m.call_args_list:
        argv = list(call.args[0])
        assert argv[:1] != [ARCHIVE_HELPER]
        assert not any("review-archive-bridge" in str(a) for a in argv)


# === A5 B2b: ready-to-commit tests (pipeline-a5-ready-to-commit) ===
#
# These tests use real, hermetic temporary git repositories (with
# hpc.ALLOWED_ROOT monkeypatched to a tmp_path root) so the extensive
# git-based repository-state / conflict / submodule / diff-check logic in
# ready_to_commit is exercised against real git, never reimplemented here.
# Only the two `hermes kanban` JSON reads are stubbed; every other
# subprocess call (git) is passed through to the real, unpatched
# subprocess.run captured below before any test applies a mock.

REAL_SUBPROCESS_RUN = subprocess.run

RTC_IMPL_ID = "t_implready1"
RTC_REVIEW_ID = "t_reviewready1"


def _git(repo, *args):
    result = REAL_SUBPROCESS_RUN(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError("git %r failed (rc=%s): %s" % (list(args), result.returncode, result.stderr))
    return result


def make_rtc_repo(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    repo = root / "repo"
    repo.mkdir()
    monkeypatch.setattr(hpc, "ALLOWED_ROOT", root.resolve())
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "rtc-test@example.com")
    _git(repo, "config", "user.name", "RTC Test")
    _git(repo, "config", "commit.gpgsign", "false")
    # /.ai/reviews/ is git-ignored so the review-archive artifact written
    # under .ai/reviews/ never itself shows up as an untracked
    # repository-state delta relative to the state captured at review
    # time. Only that exact control-plane directory is ignored — the
    # rest of .ai/ is NOT broadly ignored, so any other unrelated
    # untracked path (including elsewhere under .ai/) still shows up in
    # repository-state/v1 like any other untracked path.
    (repo / ".gitignore").write_text("/.ai/reviews/\n")
    (repo / "README.md").write_text("hello world\n")
    _git(repo, "add", ".gitignore", "README.md")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo.resolve()


def capture_state(repo):
    canonical = os.path.realpath(str(repo))
    return hpc._capture_repository_state_once(repo, canonical)


def make_impl_task_for(workdir, **overrides):
    t = make_task(
        id=RTC_IMPL_ID, assignee="coder-claude", status="done",
        workspace_kind="dir", workspace_path=str(workdir), completed_at=1000,
    )
    t.update(overrides)
    return t


def make_impl_run_for(**overrides):
    r = make_run(id=1, profile="coder-claude", status="done", outcome="completed", summary="ok", metadata=None)
    r.update(overrides)
    return r


def make_impl_show_for(workdir, runs=None, task_overrides=None):
    task = make_impl_task_for(workdir, **(task_overrides or {}))
    if runs is None:
        runs = [make_impl_run_for()]
    return make_show(task=task, parents=[], runs=runs)


def make_review_task_for(workdir, **overrides):
    t = make_task(
        id=RTC_REVIEW_ID, assignee="reviewer", status="done",
        workspace_kind="dir", workspace_path=str(workdir), completed_at=2000,
    )
    t.update(overrides)
    return t


def make_review_metadata_for(state, **overrides):
    md = {
        "implementation_task_id": RTC_IMPL_ID,
        "verdict": "PASS",
        "mutation_performed": False,
        "repository_state": state,
        "repository_state_sha256": state["aggregate_sha256"] if isinstance(state, dict) else None,
    }
    md.update(overrides)
    return md


def make_review_run_for(state, run_id=10, metadata=None, **overrides):
    if metadata is None:
        metadata = make_review_metadata_for(state)
    r = make_run(id=run_id, profile="reviewer", status="done", outcome="completed", summary="PASS", metadata=metadata)
    r.update(overrides)
    return r


def make_review_show_for(workdir, state, runs=None, task_overrides=None, parents=None, run_id=10, metadata=None):
    task = make_review_task_for(workdir, **(task_overrides or {}))
    if runs is None:
        runs = [make_review_run_for(state, run_id=run_id, metadata=metadata)]
    if parents is None:
        parents = [RTC_IMPL_ID]
    return make_show(task=task, parents=parents, runs=runs)


def make_envelope_for(workdir, state, review_run_id=10, review_completed_at=2000,
                       verdict="PASS", verdict_source="metadata", **overrides):
    canonical = os.path.realpath(str(workdir))
    envelope = {
        "schema": hpc.REVIEW_ARCHIVE_SCHEMA_V2,
        "workdir": canonical,
        "implementation_task_id": RTC_IMPL_ID,
        "review_task_id": RTC_REVIEW_ID,
        "review_run_id": review_run_id,
        "review_completed_at": review_completed_at,
        "verdict": verdict,
        "verdict_source": verdict_source,
        "repository_state": state,
    }
    envelope.update(overrides)
    envelope["archive_envelope_sha256"] = hpc._sha256_canonical_excluding(envelope, "archive_envelope_sha256")
    return envelope


RTC_ARCHIVE_FILENAME = "20240101_010203-%s.md" % RTC_REVIEW_ID


def write_archive_artifact(workdir, envelope, filename=None):
    reviews_dir = Path(workdir) / ".ai" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = RTC_ARCHIVE_FILENAME
    content = "# archive\n\n```json\n" + json.dumps(envelope, sort_keys=True) + "\n```\n"
    path = reviews_dir / filename
    path.write_text(content)
    return path


def setup_rtc_ready(tmp_path, monkeypatch):
    repo = make_rtc_repo(tmp_path, monkeypatch)
    state = capture_state(repo)
    impl_show = make_impl_show_for(repo)
    review_show = make_review_show_for(repo, state)
    envelope = make_envelope_for(repo, state)
    write_archive_artifact(repo, envelope)
    return repo, state, impl_show, review_show, envelope


def make_rtc_stub(impl_show, review_show):
    calls = []

    def run(args, **kwargs):
        argv = list(args)
        calls.append(argv)
        if len(argv) >= 4 and argv[:3] == ["hermes", "kanban", "show"]:
            tid = argv[3]
            if tid == RTC_IMPL_ID:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(impl_show), stderr="")
            if tid == RTC_REVIEW_ID:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(review_show), stderr="")
            raise AssertionError("unexpected show task id: %r" % (tid,))
        if len(argv) >= 4 and argv[:3] == ["hermes", "kanban", "runs"]:
            tid = argv[3]
            if tid == RTC_IMPL_ID:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(impl_show["runs"]), stderr="")
            if tid == RTC_REVIEW_ID:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(review_show["runs"]), stderr="")
            raise AssertionError("unexpected runs task id: %r" % (tid,))
        if argv[:1] == [hpc.ARCHIVE_HELPER_PATH]:
            raise AssertionError("ready-to-commit must never invoke review-archive-bridge")
        if argv[:3] == ["hermes", "kanban", "create"]:
            raise AssertionError("ready-to-commit must never create Kanban tasks")
        return REAL_SUBPROCESS_RUN(args, **kwargs)

    return run, calls


def run_rtc(workdir, impl_id=RTC_IMPL_ID, review_id=RTC_REVIEW_ID):
    return hpc.main([
        "ready-to-commit", "--workdir", str(workdir),
        "--implementation_task_id", impl_id, "--review_task_id", review_id,
    ])


def parse_rtc_success(capsys):
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == [
        "phase", "outcome", "workdir", "implementation_task_id", "review_task_id",
        "review_run_id", "verdict", "verdict_source", "review_archived",
        "repository_state_sha256", "human_approval_required", "commit_performed", "push_performed",
    ]
    assert obj["phase"] == "ready-to-commit"
    assert obj["outcome"] == "ready"
    return captured, obj


def parse_rtc_reject(capsys):
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert list(obj.keys()) == [
        "phase", "outcome", "workdir", "implementation_task_id", "review_task_id",
        "reason_code", "reason", "human_approval_required", "commit_performed", "push_performed",
    ]
    assert obj["phase"] == "ready-to-commit"
    assert obj["outcome"] == "not-ready"
    assert obj["human_approval_required"] is True
    assert obj["commit_performed"] is False
    assert obj["push_performed"] is False
    return captured, obj


PROHIBITED_GIT_SUBCOMMANDS = {
    "add", "commit", "push", "merge", "reset", "restore", "checkout",
    "clean", "rebase", "update-index", "hash-object", "stash", "cherry-pick",
    "revert", "am", "apply", "rm", "mv", "tag", "init",
}


def snapshot_worktree(repo):
    repo = Path(repo)
    snapshot = {}
    for path in sorted(repo.rglob("*")):
        rel = path.relative_to(repo)
        if ".git" in rel.parts:
            continue
        key = str(rel)
        if path.is_symlink():
            snapshot[key] = ("symlink", os.readlink(path))
        elif path.is_file():
            snapshot[key] = ("file", path.read_bytes())
        elif path.is_dir():
            snapshot[key] = ("dir", None)
    return snapshot


# --- 1/2/3: happy path, idempotency, exact success keys ---

def test_rtc_happy_path_exit_0_and_success_keys(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    assert rc != hpc.EXIT_TIMEOUT
    captured, obj = parse_rtc_success(capsys)
    assert obj["workdir"] == str(repo)
    assert obj["implementation_task_id"] == RTC_IMPL_ID
    assert obj["review_task_id"] == RTC_REVIEW_ID
    assert obj["review_run_id"] == 10
    assert obj["verdict"] == "PASS"
    assert obj["verdict_source"] == "metadata"
    assert obj["review_archived"] is True
    assert obj["repository_state_sha256"] == state["aggregate_sha256"]
    assert obj["human_approval_required"] is True
    assert obj["commit_performed"] is False
    assert obj["push_performed"] is False


def test_rtc_idempotent_repeated_calls(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc1 = run_rtc(repo)
        _, obj1 = parse_rtc_success(capsys)
        rc2 = run_rtc(repo)
        _, obj2 = parse_rtc_success(capsys)
    assert rc1 == hpc.EXIT_OK
    assert rc2 == hpc.EXIT_OK
    assert obj1 == obj2


def test_rtc_not_ready_exact_keys(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"]["verdict"] = "CHANGES REQUIRED"
    review_show["runs"][0]["summary"] = "CHANGES REQUIRED"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    assert rc != hpc.EXIT_TIMEOUT
    captured, obj = parse_rtc_reject(capsys)
    assert obj["workdir"] == str(repo)
    assert obj["implementation_task_id"] == RTC_IMPL_ID
    assert obj["review_task_id"] == RTC_REVIEW_ID
    assert obj["reason_code"] == "verdict_not_pass"


# --- 5: required arguments / usage exit 3 ---

@pytest.mark.parametrize("argv", [
    ["ready-to-commit"],
    ["ready-to-commit", "--workdir", VALID_WD],
    ["ready-to-commit", "--implementation_task_id", RTC_IMPL_ID],
    ["ready-to-commit", "--review_task_id", RTC_REVIEW_ID],
    ["ready-to-commit", "--workdir", VALID_WD, "--implementation_task_id", RTC_IMPL_ID],
    ["ready-to-commit", "--workdir", VALID_WD, "--review_task_id", RTC_REVIEW_ID],
    ["ready-to-commit", "--implementation_task_id", RTC_IMPL_ID, "--review_task_id", RTC_REVIEW_ID],
    ["ready-to-commit", "--workdir", VALID_WD, "--implementation_task_id", RTC_IMPL_ID,
     "--review_task_id", RTC_REVIEW_ID, "extra"],
])
def test_rtc_cli_usage_errors_exit_3(capsys, argv):
    with mock.patch.object(hpc.subprocess, "run") as m:
        rc = hpc.main(argv)
    assert rc == hpc.EXIT_TRANSPORT
    m.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage error" in captured.err


# --- 6: implementation identity/workspace/assignee/status/completed_at ---

@pytest.mark.parametrize("mutate,expected_code", [
    (lambda t: t.__setitem__("id", "t_other9"), "implementation_task_id_mismatch"),
    (lambda t: t.__setitem__("workspace_kind", "git"), "implementation_workspace_kind_mismatch"),
    (lambda t: t.__setitem__("workspace_path", "/opt/ai/projects/somewhere-else"), "implementation_workspace_mismatch"),
    (lambda t: t.__setitem__("assignee", "reviewer"), "implementation_assignee_mismatch"),
    (lambda t: t.__setitem__("status", "running"), "implementation_status_mismatch"),
    (lambda t: t.__setitem__("completed_at", 0), "implementation_completed_at_invalid"),
])
def test_rtc_implementation_field_rejections(tmp_path, monkeypatch, capsys, mutate, expected_code):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    mutate(impl_show["task"])
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == expected_code


# --- 7: review identity/workspace/assignee/status/completed_at ---

@pytest.mark.parametrize("mutate,expected_code", [
    (lambda t: t.__setitem__("id", "t_other9"), "review_task_id_mismatch"),
    (lambda t: t.__setitem__("workspace_kind", "git"), "review_workspace_kind_mismatch"),
    (lambda t: t.__setitem__("workspace_path", "/opt/ai/projects/somewhere-else"), "review_workspace_mismatch"),
    (lambda t: t.__setitem__("assignee", "coder-claude"), "review_assignee_mismatch"),
    (lambda t: t.__setitem__("status", "running"), "review_status_mismatch"),
    (lambda t: t.__setitem__("completed_at", 0), "review_completed_at_invalid"),
])
def test_rtc_review_field_rejections(tmp_path, monkeypatch, capsys, mutate, expected_code):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    mutate(review_show["task"])
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == expected_code


# --- 8: show.runs vs standalone runs mismatch ---

def test_rtc_implementation_show_runs_mismatch(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    base_run, _ = make_rtc_stub(impl_show, review_show)

    def tampered(args, **kwargs):
        argv = list(args)
        if len(argv) >= 4 and argv[:3] == ["hermes", "kanban", "runs"] and argv[3] == RTC_IMPL_ID:
            different = [dict(impl_show["runs"][0], summary="different")]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(different), stderr="")
        return base_run(args, **kwargs)

    with mock.patch.object(hpc.subprocess, "run", side_effect=tampered):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "implementation_runs_mismatch"


def test_rtc_review_show_runs_mismatch(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    base_run, _ = make_rtc_stub(impl_show, review_show)

    def tampered(args, **kwargs):
        argv = list(args)
        if len(argv) >= 4 and argv[:3] == ["hermes", "kanban", "runs"] and argv[3] == RTC_REVIEW_ID:
            different = [dict(review_show["runs"][0], summary="different")]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(different), stderr="")
        return base_run(args, **kwargs)

    with mock.patch.object(hpc.subprocess, "run", side_effect=tampered):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_runs_mismatch"


# --- 9: latest run selected by maximum unique integer run id ---

def test_rtc_review_latest_run_selected_by_max_id(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    decoy = make_review_run_for(
        state, run_id=2, metadata=make_review_metadata_for(state, verdict="CHANGES REQUIRED"),
        summary="CHANGES REQUIRED",
    )
    good = review_show["runs"][0]
    review_show["runs"] = [decoy, good]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    captured, obj = parse_rtc_success(capsys)
    assert obj["review_run_id"] == 10


def test_rtc_implementation_latest_run_selected_by_max_id(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    decoy = make_impl_run_for(id=1, profile="reviewer")
    good = make_impl_run_for(id=5)
    impl_show["runs"] = [decoy, good]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK


# --- 10: duplicate/malformed run ids reject ---

def test_rtc_review_duplicate_run_ids_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"] = [make_review_run_for(state, run_id=10), make_review_run_for(state, run_id=10)]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_run_selection_invalid"
    assert "duplicate" in obj["reason"]


@pytest.mark.parametrize("bad_id", [None, "10", True])
def test_rtc_review_malformed_run_id_rejects(tmp_path, monkeypatch, capsys, bad_id):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["id"] = bad_id
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_run_selection_invalid"


# --- 11/12: stale latest implementation/review run rejects ---

def test_rtc_stale_latest_implementation_run_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    good = impl_show["runs"][0]
    stale = make_impl_run_for(id=99, status="running", outcome="in_progress")
    impl_show["runs"] = [good, stale]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] in (
        "implementation_run_outcome_mismatch", "implementation_run_status_mismatch",
        "implementation_run_profile_mismatch",
    )


def test_rtc_stale_latest_review_run_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    good = review_show["runs"][0]
    stale = make_review_run_for(state, run_id=99, status="blocked")
    review_show["runs"] = [good, stale]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_run_status_mismatch"


# --- 13/14/15/16: verdict classification ---

def test_rtc_metadata_verdict_pass_authority(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["summary"] = "narrative mentions CHANGES REQUIRED but is not authoritative"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    captured, obj = parse_rtc_success(capsys)
    assert obj["verdict"] == "PASS"
    assert obj["verdict_source"] == "metadata"


def test_rtc_verdict_changes_required_blocks(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"]["verdict"] = "CHANGES REQUIRED"
    review_show["runs"][0]["summary"] = "CHANGES REQUIRED"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "verdict_not_pass"
    assert "CHANGES REQUIRED" in obj["reason"]


def test_rtc_ambiguous_verdict_blocks(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"] = make_review_metadata_for(state, verdict=None)
    review_show["runs"][0]["summary"] = "no clear verdict here"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "verdict_blocked"


def test_rtc_invalid_metadata_verdict_blocks_even_with_pass_summary(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"] = make_review_metadata_for(state, verdict="MAYBE")
    review_show["runs"][0]["summary"] = "PASS"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "verdict_blocked"
    assert "invalid metadata verdict" in obj["reason"]


# --- 17: parent mismatch ---

def test_rtc_review_parent_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["parents"] = ["t_wrongparent1"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_parents_mismatch"


# --- 18: mutation_performed must be exactly False ---

@pytest.mark.parametrize("bad_val", [True, "false", None, 0, 1])
def test_rtc_mutation_performed_must_be_exact_false(tmp_path, monkeypatch, capsys, bad_val):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"]["mutation_performed"] = bad_val
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_mutation_performed_invalid"


def test_rtc_mutation_performed_missing_key_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    del review_show["runs"][0]["metadata"]["mutation_performed"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_mutation_performed_invalid"


# --- 19: missing/malformed/tampered repository_state ---

def test_rtc_repository_state_missing_key_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    del review_show["runs"][0]["metadata"]["repository_state"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_shape"


def test_rtc_repository_state_not_dict_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"]["repository_state"] = "not-a-dict"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_shape"


def test_rtc_repository_state_wrong_schema_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad_state = dict(state, schema="wrong/v9")
    review_show["runs"][0]["metadata"]["repository_state"] = bad_state
    review_show["runs"][0]["metadata"]["repository_state_sha256"] = state["aggregate_sha256"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_schema_mismatch"


def test_rtc_repository_state_workdir_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad_state = dict(state, workdir="/opt/ai/projects/somewhere-else")
    bad_state["aggregate_sha256"] = hpc._sha256_canonical_excluding(bad_state, "aggregate_sha256")
    review_show["runs"][0]["metadata"]["repository_state"] = bad_state
    review_show["runs"][0]["metadata"]["repository_state_sha256"] = bad_state["aggregate_sha256"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_workdir_mismatch"


def test_rtc_repository_state_aggregate_malformed_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad_state = dict(state, aggregate_sha256="not-hex")
    review_show["runs"][0]["metadata"]["repository_state"] = bad_state
    review_show["runs"][0]["metadata"]["repository_state_sha256"] = "not-hex"
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_sha256_malformed"


def test_rtc_repository_state_tampered_aggregate_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad_state = dict(state, head="f" * 40)
    review_show["runs"][0]["metadata"]["repository_state"] = bad_state
    review_show["runs"][0]["metadata"]["repository_state_sha256"] = bad_state["aggregate_sha256"]
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_sha256_invalid"


# --- 20: repository_state_sha256 duplicate mismatch ---

def test_rtc_repository_state_sha256_duplicate_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    review_show["runs"][0]["metadata"]["repository_state_sha256"] = "0" * 64
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_metadata_repository_state_sha256_mismatch"


# --- 21: missing archive ---

def test_rtc_archive_missing_directory_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    shutil.rmtree(Path(repo) / ".ai")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_missing"


def test_rtc_archive_empty_reviews_dir_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    (Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME).unlink()
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_ambiguous"


# --- 22: multiple archives ---

def test_rtc_multiple_archives_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    write_archive_artifact(repo, envelope, filename="20240102_020304-%s.md" % RTC_REVIEW_ID)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_ambiguous"


# --- 23: nonregular archive (FIFO) ---

def test_rtc_archive_fifo_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.unlink()
    os.mkfifo(artifact)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_not_regular"


# --- 24: symlink archive ---

def test_rtc_archive_symlink_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    reviews_dir = Path(repo) / ".ai" / "reviews"
    artifact = reviews_dir / RTC_ARCHIVE_FILENAME
    real_content = artifact.read_bytes()
    artifact.unlink()
    target = tmp_path / "outside_archive.md"
    target.write_bytes(real_content)
    artifact.symlink_to(target)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_not_regular"


# --- 25: non-v2/historical archive ---

def test_rtc_archive_historical_no_v2_block_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# legacy archive\n\nno machine-readable section here\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_envelope_ambiguous"


# --- 26: malformed v2 JSON ---

def test_rtc_archive_malformed_json_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# archive\n\n```json\n{not valid json,,,\n```\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_envelope_ambiguous"


# --- 27: tampered archive_envelope_sha256 ---

def test_rtc_archive_tampered_envelope_hash_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    tampered = dict(envelope, archive_envelope_sha256="0" * 64)
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# archive\n\n```json\n" + json.dumps(tampered, sort_keys=True) + "\n```\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_envelope_sha256_mismatch"


# --- 28: archive/Kanban identity mismatch ---

def test_rtc_archive_review_task_id_field_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad = dict(envelope)
    bad["review_task_id"] = "t_wrongreview1"
    bad["archive_envelope_sha256"] = hpc._sha256_canonical_excluding(bad, "archive_envelope_sha256")
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# archive\n\n```json\n" + json.dumps(bad, sort_keys=True) + "\n```\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_envelope_mismatch"


def test_rtc_archive_implementation_task_id_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    bad = dict(envelope)
    bad["implementation_task_id"] = "t_wrongimpl1"
    bad["archive_envelope_sha256"] = hpc._sha256_canonical_excluding(bad, "archive_envelope_sha256")
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# archive\n\n```json\n" + json.dumps(bad, sort_keys=True) + "\n```\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_envelope_mismatch"
    assert "implementation_task_id" in obj["reason"]


# --- 29: archive/review fingerprint mismatch ---

def test_rtc_archive_repository_state_fingerprint_mismatch_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    other_state = dict(state, head="e" * 40)
    other_state["aggregate_sha256"] = hpc._sha256_canonical_excluding(other_state, "aggregate_sha256")
    bad = make_envelope_for(repo, other_state)
    artifact = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    artifact.write_text("# archive\n\n```json\n" + json.dumps(bad, sort_keys=True) + "\n```\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "review_archive_repository_state_mismatch"


# --- 30: git diff --check failure ---

def test_rtc_git_diff_check_failure_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    (Path(repo) / "README.md").write_text("hello world\nbad line with trailing space   \n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "git_diff_check_failed"


# --- 31: post-review HEAD change ---

def test_rtc_post_review_head_change_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    (Path(repo) / "NOTES.md").write_text("notes\n")
    _git(repo, "add", "NOTES.md")
    _git(repo, "commit", "-q", "-m", "advance head after review")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"


# --- 32: post-review changed-path/content change ---

def test_rtc_post_review_content_change_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    (Path(repo) / "README.md").write_text("hello world\nan extra unstaged line\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"


# --- 33: staging partition change ---

def test_rtc_staging_partition_change_rejects(tmp_path, monkeypatch, capsys):
    repo = make_rtc_repo(tmp_path, monkeypatch)
    (Path(repo) / "README.md").write_text("hello world\nmodified before review\n")
    state = capture_state(repo)
    impl_show = make_impl_show_for(repo)
    review_show = make_review_show_for(repo, state)
    envelope = make_envelope_for(repo, state)
    write_archive_artifact(repo, envelope)
    _git(repo, "add", "README.md")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"


# --- 34: untracked content/target change ---

def test_rtc_untracked_content_change_rejects(tmp_path, monkeypatch, capsys):
    repo = make_rtc_repo(tmp_path, monkeypatch)
    (Path(repo) / "scratch.txt").write_text("original content\n")
    state = capture_state(repo)
    impl_show = make_impl_show_for(repo)
    review_show = make_review_show_for(repo, state)
    envelope = make_envelope_for(repo, state)
    write_archive_artifact(repo, envelope)
    (Path(repo) / "scratch.txt").write_text("changed content\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"


# --- 35: unstable double repository capture ---

def test_rtc_unstable_double_capture_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    real_capture_once = hpc._capture_repository_state_once
    call_count = {"n": 0}

    def flaky_capture(resolved_workdir, canonical_workdir):
        call_count["n"] += 1
        result = real_capture_once(resolved_workdir, canonical_workdir)
        if call_count["n"] % 2 == 0:
            result = dict(result, head="0" * 40)
        return result

    with mock.patch.object(hpc.subprocess, "run", side_effect=run), \
         mock.patch.object(hpc, "_capture_repository_state_once", side_effect=flaky_capture):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_invalid"
    assert "unstable" in obj["reason"]


# --- 36: conflicts reject ---

def test_rtc_merge_conflict_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    _git(repo, "checkout", "-q", "-b", "feature")
    (Path(repo) / "README.md").write_text("feature branch change\n")
    _git(repo, "commit", "-q", "-am", "feature change")
    _git(repo, "checkout", "-q", "-")
    (Path(repo) / "README.md").write_text("main branch change\n")
    _git(repo, "commit", "-q", "-am", "main change")
    merge_result = REAL_SUBPROCESS_RUN(
        ["git", "merge", "feature", "--no-edit"], cwd=str(repo), capture_output=True, text=True,
    )
    assert merge_result.returncode != 0
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    # `git diff --check` itself flags conflict markers, so the unresolved
    # merge can be caught either by that gate or by the later conflict
    # detection inside repository-state capture; both are fail-closed.
    assert obj["reason_code"] in ("git_diff_check_failed", "repository_state_invalid")


# --- 37: changed submodule reject ---

def test_rtc_changed_submodule_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)

    sub_src = tmp_path / "sub_source"
    sub_src.mkdir()
    _git(sub_src, "init", "-q")
    _git(sub_src, "config", "user.email", "sub@example.com")
    _git(sub_src, "config", "user.name", "Sub")
    (sub_src / "f.txt").write_text("v1\n")
    _git(sub_src, "add", "f.txt")
    _git(sub_src, "commit", "-q", "-m", "sub v1")

    add_result = REAL_SUBPROCESS_RUN(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_src), "sub"],
        cwd=str(repo), capture_output=True, text=True,
    )
    assert add_result.returncode == 0, add_result.stderr
    _git(repo, "commit", "-q", "-m", "add submodule")

    sub_repo = Path(repo) / "sub"
    _git(sub_repo, "config", "user.email", "sub@example.com")
    _git(sub_repo, "config", "user.name", "Sub")
    (sub_repo / "f.txt").write_text("v2\n")
    _git(sub_repo, "commit", "-q", "-am", "sub v2")

    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_invalid"
    assert "submodule" in obj["reason"]


# --- 38: unsupported untracked special filesystem entry reject ---

def test_rtc_untracked_special_entry_rejects(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    os.mkfifo(Path(repo) / "weird_fifo")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_invalid"
    assert "special filesystem entry" in obj["reason"]


# --- 39/40: never archives, never creates Kanban tasks ---

def test_rtc_never_invokes_archive_helper(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    assert not any(c[:1] == [hpc.ARCHIVE_HELPER_PATH] for c in calls)


def test_rtc_never_creates_kanban_tasks(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    assert not any(c[:3] == ["hermes", "kanban", "create"] for c in calls)


# --- 41: no prohibited Git mutation commands ---

def test_rtc_no_prohibited_git_mutation_commands(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    git_calls = [c for c in calls if c and c[0] == "git"]
    assert git_calls
    for argv in git_calls:
        assert not any(token in PROHIBITED_GIT_SUBCOMMANDS for token in argv[1:]), argv


# --- 42: no filesystem mutation attributable to ready-to-commit ---

def test_rtc_no_filesystem_mutation(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    before = snapshot_worktree(repo)
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    after = snapshot_worktree(repo)
    assert after == before


# --- 43: ready-to-commit never returns exit 4 ---

def test_rtc_never_returns_exit_timeout(tmp_path, monkeypatch, capsys):
    rc1 = hpc.main([
        "ready-to-commit", "--workdir", "/tmp/not-allowed",
        "--implementation_task_id", RTC_IMPL_ID, "--review_task_id", RTC_REVIEW_ID,
    ])
    assert rc1 != hpc.EXIT_TIMEOUT
    capsys.readouterr()

    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    rc2 = hpc.main([
        "ready-to-commit", "--workdir", str(repo),
        "--implementation_task_id", "!!", "--review_task_id", RTC_REVIEW_ID,
    ])
    assert rc2 != hpc.EXIT_TIMEOUT
    capsys.readouterr()

    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc3 = run_rtc(repo)
    assert rc3 != hpc.EXIT_TIMEOUT


# === A5.1: /.ai/reviews/ narrow ignore-scope integration regression tests ===
#
# make_rtc_repo() now git-ignores only the exact rooted path /.ai/reviews/
# (not the broad .ai/) so a real review-archive artifact under .ai/reviews/
# never becomes an untracked repository-state delta, while any other
# unrelated untracked path — including elsewhere under .ai/ — still shows
# up in repository-state/v1 and still blocks READY_TO_COMMIT.

# --- a: state captured before archive creation is identical to state
# captured after a valid archive is written beneath the ignored
# .ai/reviews/ directory ---

def test_a51_repository_state_identical_before_and_after_ignored_archive_write(tmp_path, monkeypatch):
    repo = make_rtc_repo(tmp_path, monkeypatch)
    state_before = capture_state(repo)
    envelope = make_envelope_for(repo, state_before)
    write_archive_artifact(repo, envelope)
    state_after = capture_state(repo)
    assert state_after == state_before


# --- b: READY_TO_COMMIT happy path succeeds with the archive physically
# present on disk under the ignored .ai/reviews/ directory ---

def test_a51_ready_to_commit_succeeds_with_archive_physically_under_ignored_reviews_dir(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    archive_path = Path(repo) / ".ai" / "reviews" / RTC_ARCHIVE_FILENAME
    assert archive_path.is_file()
    status = _git(repo, "status", "--porcelain").stdout
    assert ".ai" not in status
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_OK
    captured, obj = parse_rtc_success(capsys)
    assert obj["outcome"] == "ready"
    assert obj["review_archived"] is True
    assert obj["repository_state_sha256"] == state["aggregate_sha256"]


# --- c: an unrelated untracked path outside .ai/reviews/ still changes
# repository state and blocks READY_TO_COMMIT ---

def test_a51_unrelated_untracked_path_outside_ai_blocks_ready_to_commit(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    (Path(repo) / "unexpected_top_level.txt").write_text("surprise\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"


# --- d: an unrelated untracked path elsewhere under .ai/ (outside the
# narrowly ignored .ai/reviews/) is NOT hidden and still blocks
# READY_TO_COMMIT ---

def test_a51_unrelated_untracked_path_under_ai_outside_reviews_blocks_ready_to_commit(tmp_path, monkeypatch, capsys):
    repo, state, impl_show, review_show, envelope = setup_rtc_ready(tmp_path, monkeypatch)
    unexpected = Path(repo) / ".ai" / "unexpected.txt"
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("not a review archive\n")
    run, calls = make_rtc_stub(impl_show, review_show)
    with mock.patch.object(hpc.subprocess, "run", side_effect=run):
        rc = run_rtc(repo)
    assert rc == hpc.EXIT_VALIDATION
    captured, obj = parse_rtc_reject(capsys)
    assert obj["reason_code"] == "repository_state_mismatch_kanban"
