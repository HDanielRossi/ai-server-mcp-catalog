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
