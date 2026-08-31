"""Tests for the pipeline_controller_server template: a thin MCP adapter over
scripts/hermes-pipeline-controller.py.

Loaded by absolute path so the template is exercised exactly as the
operator would install it. All subprocess execution is mocked: no real
controller binary, no real Kanban, no real git, no network. The controller
itself remains untouched and untested here -- see
tests/test_hermes_pipeline_controller.py for controller-policy coverage.
"""

import asyncio
import importlib.util
import inspect
import json
import math
import os
import subprocess

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/pipeline_controller_server.py"


def _load_module_with_subprocess_spy():
    """Load the template fresh, spying on subprocess.run for the duration of import."""
    calls = []
    original_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original_run(*args, **kwargs)

    subprocess.run = spy
    try:
        spec = importlib.util.spec_from_file_location("pipeline_controller_server", TEMPLATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        subprocess.run = original_run
    return module, calls


pipeline_controller_server, IMPORT_TIME_SUBPROCESS_CALLS = _load_module_with_subprocess_spy()

PipelineControllerAdapterError = pipeline_controller_server.PipelineControllerAdapterError
CONTROLLER_PATH = pipeline_controller_server.CONTROLLER_PATH
RESULT_SCHEMA = pipeline_controller_server.RESULT_SCHEMA
MAX_WAIT_TIMEOUT_SECONDS = pipeline_controller_server.MAX_WAIT_TIMEOUT_SECONDS
WAIT_TRANSPORT_GRACE_SECONDS = pipeline_controller_server.WAIT_TRANSPORT_GRACE_SECONDS
MAX_CAPTURED_STDOUT_CHARS = pipeline_controller_server.MAX_CAPTURED_STDOUT_CHARS
MAX_CAPTURED_STDERR_CHARS = pipeline_controller_server.MAX_CAPTURED_STDERR_CHARS

EXPECTED_TOOL_NAMES = {
    "check_task",
    "create_implementation",
    "create_review",
    "create_correction",
    "wait_task",
    "archive_review",
    "ready_to_commit",
}


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(coro):
    return asyncio.run(coro)


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert pipeline_controller_server.__file__ == TEMPLATE_PATH


def test_no_subprocess_execution_on_import():
    assert IMPORT_TIME_SUBPROCESS_CALLS == []


def test_no_import_time_controller_probe_in_source():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    assert "os.path.exists" not in source
    assert "os.stat" not in source
    assert ".is_file()" not in source


# --- 1/2/3: MCP server surface ---------------------------------------------


def test_mcp_server_import_and_instantiation():
    from mcp.server import MCPServer

    assert isinstance(pipeline_controller_server.mcp_server, MCPServer)
    assert pipeline_controller_server.mcp_server.name == "pipeline-controller"


def test_exactly_seven_tools_registered():
    tools = _run(pipeline_controller_server.mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == EXPECTED_TOOL_NAMES


def test_mcp_server_has_executable_run_entrypoint():
    assert callable(pipeline_controller_server.mcp_server.run)


def test_main_guard_gates_run_and_is_not_reached_on_plain_import():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    assert 'if __name__ == "__main__":' in source
    guard_index = source.index('if __name__ == "__main__":')
    guarded_block = source[guard_index:]
    assert "mcp_server.run()" in guarded_block
    assert pipeline_controller_server.__name__ != "__main__"


# --- 4/5: exact controller argv construction --------------------------------


def test_check_task_argv(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, json.dumps({"task_id": "t_1"}), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_check_task("t_1")
    assert captured["argv"] == [CONTROLLER_PATH, "check", "t_1"]


def test_create_implementation_argv_without_body(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_implementation("/opt/ai/projects/demo", "feature-x")
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-implementation",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
    ]


def test_create_implementation_argv_with_body(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_implementation(
        "/opt/ai/projects/demo", "feature-x", body="do the thing"
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-implementation",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--body", "do the thing",
    ]


def test_create_review_argv_without_instructions(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_review("/opt/ai/projects/demo", "feature-x", "t_impl1")
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-review",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
    ]


def test_create_review_argv_with_instructions(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_review(
        "/opt/ai/projects/demo", "feature-x", "t_impl1", review_instructions="look closely"
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-review",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
        "--review_instructions", "look closely",
    ]


def test_create_correction_argv_without_optional_fields(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_correction(
        "/opt/ai/projects/demo", "feature-x", "t_impl1", "t_review1"
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-correction",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
        "--review_task_id", "t_review1",
    ]


def test_create_correction_argv_with_optional_fields(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_create_correction(
        "/opt/ai/projects/demo",
        "feature-x",
        "t_impl1",
        "t_review1",
        review_summary="CHANGES REQUIRED: fix x",
        correction_instructions="fix x specifically",
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-correction",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
        "--review_task_id", "t_review1",
        "--review_summary", "CHANGES REQUIRED: fix x",
        "--correction_instructions", "fix x specifically",
    ]


def test_wait_task_argv_defaults(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompleted(0, json.dumps({"outcome": "terminal"}), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_wait_task("t_1", 30.0)
    assert captured["argv"] == [
        CONTROLLER_PATH, "wait", "t_1",
        "--timeout", "30.0",
        "--interval", "1.0",
        "--max-retries", "2",
    ]
    assert captured["kwargs"]["timeout"] == pytest.approx(30.0 + WAIT_TRANSPORT_GRACE_SECONDS)


def test_wait_task_argv_custom_interval_and_retries(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_wait_task("t_1", 10.0, interval=2.5, max_retries=5)
    assert captured["argv"] == [
        CONTROLLER_PATH, "wait", "t_1",
        "--timeout", "10.0",
        "--interval", "2.5",
        "--max-retries", "5",
    ]


def test_archive_review_argv(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_archive_review("/opt/ai/projects/demo", "t_review1")
    assert captured["argv"] == [
        CONTROLLER_PATH, "archive-review",
        "--workdir", "/opt/ai/projects/demo",
        "--review_task_id", "t_review1",
    ]


def test_ready_to_commit_argv(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_ready_to_commit("/opt/ai/projects/demo", "t_impl1", "t_review1")
    assert captured["argv"] == [
        CONTROLLER_PATH, "ready-to-commit",
        "--workdir", "/opt/ai/projects/demo",
        "--implementation_task_id", "t_impl1",
        "--review_task_id", "t_review1",
    ]


# --- 6/7: shell=False + bounded subprocess ----------------------------------


def test_all_subprocess_calls_use_shell_false_and_are_bounded(monkeypatch):
    kwargs_seen = []

    def fake_run(argv, **kwargs):
        kwargs_seen.append(kwargs)
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    pipeline_controller_server._tool_check_task("t_1")
    pipeline_controller_server._tool_create_implementation("/opt/ai/projects/demo", "f")
    pipeline_controller_server._tool_create_review("/opt/ai/projects/demo", "f", "t_impl1")
    pipeline_controller_server._tool_create_correction("/opt/ai/projects/demo", "f", "t_impl1", "t_review1")
    pipeline_controller_server._tool_wait_task("t_1", 5.0)
    pipeline_controller_server._tool_archive_review("/opt/ai/projects/demo", "t_review1")
    pipeline_controller_server._tool_ready_to_commit("/opt/ai/projects/demo", "t_impl1", "t_review1")

    assert len(kwargs_seen) == 7
    for kwargs in kwargs_seen:
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert isinstance(kwargs["timeout"], (int, float))
        assert kwargs["timeout"] > 0


# --- 8: normal success passthrough ------------------------------------------


def test_successful_controller_json_returned_without_semantic_rewriting(monkeypatch):
    controller_payload = {
        "phase": "implementation",
        "task_id": "t_new1",
        "idempotency_key": "pipeline:implementation:abc",
        "workdir": "/opt/ai/projects/demo",
        "feature": "feature-x",
        "parents": [],
    }

    def fake_run(argv, **kwargs):
        return FakeCompleted(0, json.dumps(controller_payload), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_create_implementation("/opt/ai/projects/demo", "feature-x")
    assert result == {
        "schema": RESULT_SCHEMA,
        "command": "create-implementation",
        "exit_code": 0,
        "payload": controller_payload,
        "stderr": "",
    }


# --- 9/10/11: exit codes preserved exactly ----------------------------------


def test_exit_2_validation_reason_code_unchanged(monkeypatch):
    controller_payload = {
        "phase": "ready-to-commit",
        "outcome": "not-ready",
        "reason_code": "verdict_not_pass",
        "reason": "review verdict is 'CHANGES REQUIRED', expected PASS",
    }

    def fake_run(argv, **kwargs):
        return FakeCompleted(2, json.dumps(controller_payload), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_ready_to_commit("/opt/ai/projects/demo", "t_impl1", "t_review1")
    assert result["exit_code"] == 2
    assert result["payload"]["reason_code"] == "verdict_not_pass"
    assert result["payload"] == controller_payload


def test_exit_3_transport_error_preserved(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(3, "", "transport error: boom")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_check_task("t_1")
    assert result["exit_code"] == 3
    assert result["payload"] is None
    assert "boom" in result["stderr"]


def test_exit_4_wait_timeout_preserved(monkeypatch):
    controller_payload = {
        "outcome": "timeout",
        "task_id": "t_1",
        "last_status": "running",
        "timeout_seconds": 30.0,
    }

    def fake_run(argv, **kwargs):
        return FakeCompleted(4, json.dumps(controller_payload), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_wait_task("t_1", 30.0)
    assert result["exit_code"] == 4
    assert result["payload"] == controller_payload


# --- 12: malformed/multiple/non-object stdout fails closed ------------------


def test_malformed_json_stdout_fails_closed(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(0, "{not json", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_check_task("t_1")


def test_multiple_json_values_stdout_fails_closed(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(0, '{"a": 1}{"b": 2}', "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_check_task("t_1")


def test_non_object_json_stdout_fails_closed(monkeypatch):
    def fake_run(argv, **kwargs):
        return FakeCompleted(0, "[1, 2, 3]", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_check_task("t_1")


def test_oversized_stdout_fails_closed(monkeypatch):
    huge_stdout = "{" + "a" * (MAX_CAPTURED_STDOUT_CHARS + 10)

    def fake_run(argv, **kwargs):
        return FakeCompleted(0, huge_stdout, "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_check_task("t_1")


def test_stderr_is_bounded(monkeypatch):
    huge_stderr = "x" * (MAX_CAPTURED_STDERR_CHARS + 500)

    def fake_run(argv, **kwargs):
        return FakeCompleted(3, "", huge_stderr)

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_check_task("t_1")
    assert len(result["stderr"]) <= MAX_CAPTURED_STDERR_CHARS + len("...[truncated]")
    assert result["stderr"] != huge_stderr


# --- 13/14: launch/timeout failures are deterministic adapter errors -------


def test_launch_oserror_fails_deterministically(monkeypatch):
    def fake_run(argv, **kwargs):
        raise OSError("no such file or directory")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_check_task("t_1")


def test_subprocess_timeout_fails_deterministically(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_wait_task("t_1", 5.0)


# --- 15: wait outer timeout includes grace, doesn't preempt controller ------


def test_wait_subprocess_timeout_is_requested_timeout_plus_grace(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return FakeCompleted(0, json.dumps({"outcome": "terminal", "task_id": "t_1", "status": "done"}), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    pipeline_controller_server._tool_wait_task("t_1", 120.0)
    assert captured["timeout"] == pytest.approx(120.0 + WAIT_TRANSPORT_GRACE_SECONDS)
    assert captured["timeout"] > 120.0


def test_wait_task_argv_timeout_flag_is_exact_requested_value_not_grace_adjusted(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    pipeline_controller_server._tool_wait_task("t_1", 45.0)
    timeout_index = captured["argv"].index("--timeout")
    assert captured["argv"][timeout_index + 1] == "45.0"


# --- 16: invalid NaN/inf/nonpositive timeout/interval reject before subprocess


@pytest.mark.parametrize("bad_timeout", [math.nan, math.inf, -math.inf, 0, -5.0])
def test_wait_task_rejects_invalid_timeout_before_subprocess(monkeypatch, bad_timeout):
    def fake_run(argv, **kwargs):
        raise AssertionError("subprocess must not run for an invalid timeout")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_wait_task("t_1", bad_timeout)


def test_wait_task_rejects_timeout_above_adapter_maximum(monkeypatch):
    def fake_run(argv, **kwargs):
        raise AssertionError("subprocess must not run when timeout exceeds the adapter maximum")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_wait_task("t_1", MAX_WAIT_TIMEOUT_SECONDS + 1)


@pytest.mark.parametrize("bad_interval", [math.nan, math.inf, 0, -1.0])
def test_wait_task_rejects_invalid_interval_before_subprocess(monkeypatch, bad_interval):
    def fake_run(argv, **kwargs):
        raise AssertionError("subprocess must not run for an invalid interval")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_wait_task("t_1", 30.0, interval=bad_interval)


# --- 17: negative/non-int max_retries rejects -------------------------------


@pytest.mark.parametrize("bad_retries", [-1, 1.5, "2", True])
def test_wait_task_rejects_invalid_max_retries_before_subprocess(monkeypatch, bad_retries):
    def fake_run(argv, **kwargs):
        raise AssertionError("subprocess must not run for invalid max_retries")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    with pytest.raises(PipelineControllerAdapterError):
        pipeline_controller_server._tool_wait_task("t_1", 30.0, max_retries=bad_retries)


# --- 18: no arbitrary command/argv tool exists ------------------------------


def test_no_extra_tools_beyond_the_intended_seven():
    tools = _run(pipeline_controller_server.mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == EXPECTED_TOOL_NAMES


def test_no_tool_accepts_arbitrary_argv_or_command_parameter():
    tool_funcs = [
        pipeline_controller_server._tool_check_task,
        pipeline_controller_server._tool_create_implementation,
        pipeline_controller_server._tool_create_review,
        pipeline_controller_server._tool_create_correction,
        pipeline_controller_server._tool_wait_task,
        pipeline_controller_server._tool_archive_review,
        pipeline_controller_server._tool_ready_to_commit,
    ]
    forbidden_param_names = {"argv", "command", "cmd", "shell_command", "executable", "args"}
    for fn in tool_funcs:
        params = set(inspect.signature(fn).parameters.keys())
        assert not (params & forbidden_param_names), (
            f"{fn.__name__} exposes a forbidden argv/command parameter: {params & forbidden_param_names}"
        )


# --- 19: no commit/push/staging functionality exists ------------------------


def test_no_commit_push_or_staging_functionality_in_source():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    for forbidden in (
        "git add", "git commit", "git push", "git reset", "git restore", "git checkout",
        '"git"', "'git'",
    ):
        assert forbidden not in source, f"forbidden token present in template: {forbidden!r}"
    # Exactly one subprocess execution point exists: the controller invocation.
    assert source.count("subprocess.run(") == 1


# --- 20/21: no cross-tool chaining ------------------------------------------


def test_ready_to_commit_only_invokes_ready_to_commit_and_never_archive_review(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeCompleted(0, json.dumps({"phase": "ready-to-commit", "outcome": "ready"}), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    pipeline_controller_server._tool_ready_to_commit("/opt/ai/projects/demo", "t_impl1", "t_review1")

    assert len(calls) == 1
    assert calls[0][1] == "ready-to-commit"
    assert "archive-review" not in calls[0]


def test_archive_review_only_invokes_archive_review_and_never_ready_to_commit(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeCompleted(0, json.dumps({"phase": "archive-review", "outcome": "archive-succeeded"}), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    pipeline_controller_server._tool_archive_review("/opt/ai/projects/demo", "t_review1")

    assert len(calls) == 1
    assert calls[0][1] == "archive-review"
    assert "ready-to-commit" not in calls[0]


def test_ready_to_commit_source_never_references_archive_review():
    source = inspect.getsource(pipeline_controller_server._tool_ready_to_commit)
    assert "archive-review" not in source
    assert "_tool_archive_review" not in source


def test_archive_review_source_never_references_ready_to_commit():
    source = inspect.getsource(pipeline_controller_server._tool_archive_review)
    assert "ready-to-commit" not in source
    assert "_tool_ready_to_commit" not in source


# --- READY_TO_COMMIT passthrough never infers human approval ---------------


def test_ready_to_commit_passthrough_does_not_add_approval_semantics(monkeypatch):
    controller_payload = {
        "phase": "ready-to-commit",
        "outcome": "ready",
        "human_approval_required": True,
        "commit_performed": False,
        "push_performed": False,
    }

    def fake_run(argv, **kwargs):
        return FakeCompleted(0, json.dumps(controller_payload), "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)

    result = pipeline_controller_server._tool_ready_to_commit("/opt/ai/projects/demo", "t_impl1", "t_review1")
    assert result["payload"] == controller_payload
    assert result["payload"]["human_approval_required"] is True
    assert "approved" not in result
    assert "human_approval_granted" not in result
