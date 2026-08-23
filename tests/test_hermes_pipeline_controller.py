import importlib.util
import json
import os
import subprocess
import unittest.mock as mock

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
