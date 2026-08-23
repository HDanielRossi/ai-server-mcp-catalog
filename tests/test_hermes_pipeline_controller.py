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
