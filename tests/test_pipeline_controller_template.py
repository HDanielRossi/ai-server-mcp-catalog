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

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "pipeline_controller_server.py",
)


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


# --- S0.1b: opt-in --shadow-* pass-through ------------------------------------


def _capture_argv(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompleted(0, "{}", "")

    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", fake_run)
    return captured


SHADOW_PARAMS = (
    "shadow_declared_paths",
    "shadow_components",
    "shadow_mandatory_block",
    "shadow_mandatory_block_reason",
)


@pytest.mark.parametrize("fn_name", ["_tool_create_implementation", "_tool_create_correction"])
def test_shadow_params_are_optional_and_default_to_none(fn_name):
    params = inspect.signature(getattr(pipeline_controller_server, fn_name)).parameters
    for name in SHADOW_PARAMS:
        assert params[name].default is None


def test_shadow_params_only_on_implementation_and_correction():
    for fn in (
        pipeline_controller_server._tool_check_task,
        pipeline_controller_server._tool_create_review,
        pipeline_controller_server._tool_wait_task,
        pipeline_controller_server._tool_archive_review,
        pipeline_controller_server._tool_ready_to_commit,
    ):
        assert not set(inspect.signature(fn).parameters) & set(SHADOW_PARAMS)


def test_shadow_params_exposed_as_typed_mcp_tool_arguments():
    tools = {tool.name: tool for tool in _run(pipeline_controller_server.mcp_server.list_tools())}
    for name in ("create_implementation", "create_correction"):
        properties = tools[name].input_schema["properties"]
        for param in SHADOW_PARAMS:
            assert param in properties
        assert set(SHADOW_PARAMS).isdisjoint(tools[name].input_schema.get("required", []))
    for name in ("check_task", "create_review", "wait_task", "archive_review", "ready_to_commit"):
        assert set(SHADOW_PARAMS).isdisjoint(tools[name].input_schema["properties"])


def test_create_implementation_without_shadow_inputs_has_no_shadow_flags(monkeypatch):
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_implementation(
        "/opt/ai/projects/demo", "feature-x", body="touch src/app.py and tests/test_app.py"
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-implementation",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--body", "touch src/app.py and tests/test_app.py",
    ]
    assert not any(arg.startswith("--shadow") for arg in captured["argv"])


def test_create_correction_without_shadow_inputs_has_no_shadow_flags(monkeypatch):
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_correction(
        "/opt/ai/projects/demo", "feature-x", "t_impl1", "t_review1",
        review_summary="CHANGES REQUIRED: mandatory block, fix docs/x.md",
        correction_instructions="edit tests/test_x.py",
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-correction",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
        "--review_task_id", "t_review1",
        "--review_summary", "CHANGES REQUIRED: mandatory block, fix docs/x.md",
        "--correction_instructions", "edit tests/test_x.py",
    ]
    assert not any(arg.startswith("--shadow") for arg in captured["argv"])


def test_create_implementation_forwards_explicit_shadow_inputs(monkeypatch):
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_implementation(
        "/opt/ai/projects/demo", "feature-x", body="do the thing",
        shadow_declared_paths=["src/app.py", "tests/test_app.py"],
        shadow_components=["repository-source", "tests"],
        shadow_mandatory_block=False,
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-implementation",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--body", "do the thing",
        "--shadow-declared-path=src/app.py",
        "--shadow-declared-path=tests/test_app.py",
        "--shadow-component=repository-source",
        "--shadow-component=tests",
        "--shadow-mandatory-block=false",
    ]
    assert captured["kwargs"]["shell"] is False


def test_create_correction_forwards_explicit_shadow_inputs(monkeypatch):
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_correction(
        "/opt/ai/projects/demo", "feature-x", "t_impl1", "t_review1",
        review_summary="CHANGES REQUIRED: fix x",
        shadow_declared_paths=["docs/guide.md"],
        shadow_components=["documentation"],
        shadow_mandatory_block=True,
        shadow_mandatory_block_reason="CROSS_REPOSITORY",
    )
    assert captured["argv"] == [
        CONTROLLER_PATH, "create-correction",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
        "--implementation_task_id", "t_impl1",
        "--review_task_id", "t_review1",
        "--review_summary", "CHANGES REQUIRED: fix x",
        "--shadow-declared-path=docs/guide.md",
        "--shadow-component=documentation",
        "--shadow-mandatory-block=true",
        "--shadow-mandatory-block-reason=CROSS_REPOSITORY",
    ]
    assert captured["kwargs"]["shell"] is False


@pytest.mark.parametrize("fn_name,positional", [
    ("_tool_create_implementation", ("/opt/ai/projects/demo", "feature-x")),
    ("_tool_create_correction", ("/opt/ai/projects/demo", "feature-x", "t_impl1", "t_review1")),
])
def test_partial_shadow_inputs_forward_only_supplied_fields(monkeypatch, fn_name, positional):
    captured = _capture_argv(monkeypatch)
    getattr(pipeline_controller_server, fn_name)(*positional, shadow_mandatory_block=False)
    shadow = [arg for arg in captured["argv"] if arg.startswith("--shadow")]
    assert shadow == ["--shadow-mandatory-block=false"]


def test_empty_shadow_lists_forward_nothing(monkeypatch):
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_implementation(
        "/opt/ai/projects/demo", "feature-x", shadow_declared_paths=[], shadow_components=[],
    )
    assert not any(arg.startswith("--shadow") for arg in captured["argv"])


def test_shadow_values_are_single_argv_elements_without_interpolation(monkeypatch):
    captured = _capture_argv(monkeypatch)
    hostile = ["-x", "a b; rm -rf / $(id) `id` > out", "--workdir=/etc", ""]
    pipeline_controller_server._tool_create_implementation(
        "/opt/ai/projects/demo", "feature-x",
        shadow_declared_paths=hostile,
        shadow_components=["--tests"],
        shadow_mandatory_block_reason="-R $HOME",
    )
    assert captured["argv"][-6:] == [
        "--shadow-declared-path=-x",
        "--shadow-declared-path=a b; rm -rf / $(id) `id` > out",
        "--shadow-declared-path=--workdir=/etc",
        "--shadow-declared-path=",
        "--shadow-component=--tests",
        "--shadow-mandatory-block-reason=-R $HOME",
    ]
    assert captured["argv"][:6] == [
        CONTROLLER_PATH, "create-implementation",
        "--workdir", "/opt/ai/projects/demo",
        "--feature", "feature-x",
    ]
    assert captured["kwargs"]["shell"] is False


@pytest.mark.parametrize("kwargs", [
    {"shadow_declared_paths": "src/app.py"},
    {"shadow_declared_paths": ["src/app.py", 3]},
    {"shadow_components": "tests"},
    {"shadow_components": [None]},
    {"shadow_mandatory_block": "false"},
    {"shadow_mandatory_block": 0},
    {"shadow_mandatory_block_reason": 5},
])
@pytest.mark.parametrize("fn_name,positional", [
    ("_tool_create_implementation", ("/opt/ai/projects/demo", "feature-x")),
    ("_tool_create_correction", ("/opt/ai/projects/demo", "feature-x", "t_impl1", "t_review1")),
])
def test_mistyped_shadow_inputs_rejected_before_subprocess(monkeypatch, fn_name, positional, kwargs):
    calls = []
    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(PipelineControllerAdapterError):
        getattr(pipeline_controller_server, fn_name)(*positional, **kwargs)
    assert calls == []


# --- S0.1b: strict MCP-level validation of shadow_* arguments ----------------
#
# These go through mcp_server.call_tool, i.e. the SDK's own Pydantic argument
# validation, which would otherwise coerce e.g. "false"/"no"/0 to False before
# the adapter's direct-call type checks ever see the value.

SHADOW_TOOL_REQUIRED_ARGS = {
    "create_implementation": {"workdir": "/opt/ai/projects/demo", "feature": "feature-x"},
    "create_correction": {
        "workdir": "/opt/ai/projects/demo",
        "feature": "feature-x",
        "implementation_task_id": "t_impl1",
        "review_task_id": "t_review1",
    },
}


def _spy_shadow_argv(monkeypatch):
    """Record the exact values the tool function hands to _shadow_argv."""
    seen = []
    original = pipeline_controller_server._shadow_argv

    def spy(*args):
        seen.append(args)
        return original(*args)

    monkeypatch.setattr(pipeline_controller_server, "_shadow_argv", spy)
    return seen


def _call_tool(name, arguments):
    return _run(pipeline_controller_server.mcp_server.call_tool(name, arguments))


@pytest.mark.parametrize("shadow_args", [
    {"shadow_mandatory_block": "false"},
    {"shadow_mandatory_block": "no"},
    {"shadow_mandatory_block": "true"},
    {"shadow_mandatory_block": 0},
    {"shadow_mandatory_block": 1},
    {"shadow_declared_paths": [3]},
    {"shadow_declared_paths": ["src/app.py", 3]},
    {"shadow_declared_paths": "src/app.py"},
    {"shadow_components": [1.5]},
    {"shadow_components": [True]},
    {"shadow_mandatory_block_reason": 5},
    {"shadow_mandatory_block_reason": False},
])
@pytest.mark.parametrize("tool_name", sorted(SHADOW_TOOL_REQUIRED_ARGS))
def test_call_tool_rejects_wrong_typed_shadow_json_without_subprocess(monkeypatch, tool_name, shadow_args):
    calls = []
    monkeypatch.setattr(pipeline_controller_server.subprocess, "run", lambda *a, **k: calls.append(a))
    adapter_calls = _spy_shadow_argv(monkeypatch)
    arguments = dict(SHADOW_TOOL_REQUIRED_ARGS[tool_name], **shadow_args)
    with pytest.raises(Exception) as excinfo:
        _call_tool(tool_name, arguments)
    assert next(iter(shadow_args)) in str(excinfo.value)
    # Rejected by the SDK's strict argument validation: the tool function
    # never ran, so neither the adapter nor the controller was reached.
    assert adapter_calls == []
    assert calls == []


@pytest.mark.parametrize("value,flag", [
    (True, "--shadow-mandatory-block=true"),
    (False, "--shadow-mandatory-block=false"),
])
@pytest.mark.parametrize("tool_name", sorted(SHADOW_TOOL_REQUIRED_ARGS))
def test_call_tool_passes_json_booleans_through_unchanged(monkeypatch, tool_name, value, flag):
    captured = _capture_argv(monkeypatch)
    adapter_calls = _spy_shadow_argv(monkeypatch)
    arguments = dict(SHADOW_TOOL_REQUIRED_ARGS[tool_name], shadow_mandatory_block=value)
    _call_tool(tool_name, arguments)
    assert len(adapter_calls) == 1
    assert adapter_calls[0][2] is value
    assert [arg for arg in captured["argv"] if arg.startswith("--shadow")] == [flag]


@pytest.mark.parametrize("tool_name", sorted(SHADOW_TOOL_REQUIRED_ARGS))
def test_call_tool_passes_strings_and_nulls_through_unchanged(monkeypatch, tool_name):
    captured = _capture_argv(monkeypatch)
    adapter_calls = _spy_shadow_argv(monkeypatch)
    arguments = dict(
        SHADOW_TOOL_REQUIRED_ARGS[tool_name],
        shadow_declared_paths=["src/app.py", "-x"],
        shadow_components=["tests"],
        shadow_mandatory_block=None,
        shadow_mandatory_block_reason="CROSS_REPOSITORY",
    )
    _call_tool(tool_name, arguments)
    assert adapter_calls == [(["src/app.py", "-x"], ["tests"], None, "CROSS_REPOSITORY")]
    assert [arg for arg in captured["argv"] if arg.startswith("--shadow")] == [
        "--shadow-declared-path=src/app.py",
        "--shadow-declared-path=-x",
        "--shadow-component=tests",
        "--shadow-mandatory-block-reason=CROSS_REPOSITORY",
    ]


@pytest.mark.parametrize("tool_name", sorted(SHADOW_TOOL_REQUIRED_ARGS))
def test_call_tool_sdk_pre_parses_json_text_for_structured_values(monkeypatch, tool_name):
    # The SDK json.loads() string arguments for non-`str` params and keeps the
    # result only when it is not a JSON scalar (string/number/boolean). So JSON
    # text for an array or null is normalized before strict validation, while
    # "false"/"no" stay raw strings and are rejected (see the test above).
    captured = _capture_argv(monkeypatch)
    adapter_calls = _spy_shadow_argv(monkeypatch)
    arguments = dict(
        SHADOW_TOOL_REQUIRED_ARGS[tool_name],
        shadow_declared_paths='["src/app.py", "-x"]',
        shadow_mandatory_block="null",
    )
    _call_tool(tool_name, arguments)
    assert adapter_calls == [(["src/app.py", "-x"], None, None, None)]
    assert [arg for arg in captured["argv"] if arg.startswith("--shadow")] == [
        "--shadow-declared-path=src/app.py",
        "--shadow-declared-path=-x",
    ]


def _schema_variants(prop):
    return prop.get("anyOf", [prop])


@pytest.mark.parametrize("tool_name", sorted(SHADOW_TOOL_REQUIRED_ARGS))
def test_strict_shadow_params_keep_optional_json_schema_types(tool_name):
    tools = {tool.name: tool for tool in _run(pipeline_controller_server.mcp_server.list_tools())}
    schema = tools[tool_name].input_schema
    properties = schema["properties"]
    assert set(SHADOW_PARAMS).isdisjoint(schema.get("required", []))

    expected = {
        "shadow_declared_paths": "array",
        "shadow_components": "array",
        "shadow_mandatory_block": "boolean",
        "shadow_mandatory_block_reason": "string",
    }
    for param, json_type in expected.items():
        variants = _schema_variants(properties[param])
        assert {v.get("type") for v in variants} == {json_type, "null"}, (param, properties[param])
        assert properties[param].get("default") is None
        if json_type == "array":
            (array_variant,) = [v for v in variants if v.get("type") == "array"]
            assert array_variant["items"] == {"type": "string"}
    # Strictness is a validation-time constraint only; it must not leak
    # non-standard keywords into the published schema.
    assert "strict" not in json.dumps({p: properties[p] for p in SHADOW_PARAMS})


def _load_controller_module():
    path = os.path.join(os.path.dirname(TEMPLATE_PATH), "..", "scripts", "hermes-pipeline-controller.py")
    spec = importlib.util.spec_from_file_location("hpc_adapter_shadow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_shadow_argv_parses_as_typed_controller_inputs(monkeypatch):
    """Cross-check against the real controller parser (no Kanban, no I/O):
    adapter-built argv, including dash-leading values and a literal false,
    parses into exactly the caller-supplied typed shadow inputs."""
    hpc = _load_controller_module()
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_correction(
        "/w", "f", "t1", "t2",
        shadow_declared_paths=["-x", "src/app.py"],
        shadow_components=["tests"],
        shadow_mandatory_block=False,
        shadow_mandatory_block_reason="--X",
    )
    args = hpc.build_parser().parse_args(captured["argv"][1:])
    assert args.shadow_declared_path == ["-x", "src/app.py"]
    assert args.shadow_component == ["tests"]
    assert args.shadow_mandatory_block == "false"
    assert hpc._shadow_mandatory_block_value(args.shadow_mandatory_block) is False
    assert args.shadow_mandatory_block_reason == "--X"


def test_omitted_shadow_inputs_stay_missing_and_route_unknown(monkeypatch):
    hpc = _load_controller_module()
    captured = _capture_argv(monkeypatch)
    pipeline_controller_server._tool_create_implementation(
        "/w", "f", body="edit src/app.py and tests/test_app.py (tests, repository-source)",
        shadow_mandatory_block=False,
    )
    args = hpc.build_parser().parse_args(captured["argv"][1:])
    assert args.shadow_declared_path is None
    assert args.shadow_component is None
    record = hpc.shadow_route({
        "declared_paths": args.shadow_declared_path,
        "components": args.shadow_component,
        "mandatory_block": hpc._shadow_mandatory_block_value(args.shadow_mandatory_block),
        "mandatory_block_reason": args.shadow_mandatory_block_reason,
        "actual_assignee": "coder-claude",
        "workdir": "/w",
    })
    assert record["decision"] == "UNKNOWN"
    assert record["scope"] == {"path_count": None, "component_count": None}


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
