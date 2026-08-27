"""Tests for the pipeline_bridge_server template as a real MCP runtime adapter.

Loaded by absolute path so the template is exercised exactly as the operator
would install it. Uses fakes/mocking for subprocess/Kanban behavior: no real
Kanban tasks, no network, no GPU, no real Hermes subprocess.
"""

import asyncio
import importlib.util
import json
import os
import subprocess

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/pipeline_bridge_server.py"


def _load_module_with_subprocess_spy():
    """Load the template fresh, spying on subprocess.run for the duration of import."""
    calls = []
    original_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original_run(*args, **kwargs)

    subprocess.run = spy
    try:
        spec = importlib.util.spec_from_file_location("pipeline_bridge_server", TEMPLATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        subprocess.run = original_run
    return module, calls


pipeline_bridge_server, IMPORT_TIME_SUBPROCESS_CALLS = _load_module_with_subprocess_spy()

PipelineBridgeError = pipeline_bridge_server.PipelineBridgeError
stable_key = pipeline_bridge_server.stable_key
TaskBackend = pipeline_bridge_server.TaskBackend
create_implementation_task = pipeline_bridge_server.create_implementation_task
create_review_task = pipeline_bridge_server.create_review_task
create_correction_task = pipeline_bridge_server.create_correction_task
REVIEW_BODY_REQUIRED_SENTENCES = pipeline_bridge_server.REVIEW_BODY_REQUIRED_SENTENCES
REVIEWER_CONTRACT_SENTENCES = pipeline_bridge_server.REVIEWER_CONTRACT_SENTENCES
validate_workdir = pipeline_bridge_server.validate_workdir
HermesKanbanBackend = pipeline_bridge_server.HermesKanbanBackend
ALLOWED_ROOT = pipeline_bridge_server.ALLOWED_ROOT

REPO_ROOT = "/opt/ai/projects/ai-server-mcp-catalog"


class FakeBackend(TaskBackend):
    """In-memory stand-in for a real Kanban backend. No network, no external tasks."""

    def __init__(self, existing=None):
        self.existing = dict(existing) if existing else {}
        self.created = []
        self._counter = 0

    def existing_id_for_key(self, key):
        return self.existing.get(key)

    def create_task(self, key, title, body, metadata):
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.created.append({"key": key, "title": title, "body": body, "metadata": metadata, "id": task_id})
        self.existing[key] = task_id
        return task_id


def _run(coro):
    return asyncio.run(coro)


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert pipeline_bridge_server.__file__ == TEMPLATE_PATH


# --- MCP server surface --------------------------------------------------


def test_mcp_server_import_and_instantiation():
    from mcp.server import MCPServer

    assert isinstance(pipeline_bridge_server.mcp_server, MCPServer)
    assert pipeline_bridge_server.mcp_server.name == "pipeline-bridge"


def test_mcp_tools_registered_for_three_public_operations():
    tools = _run(pipeline_bridge_server.mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"create_implementation_task", "create_review_task", "create_correction_task"}


def test_mcp_server_has_executable_run_entrypoint():
    assert callable(pipeline_bridge_server.mcp_server.run)


def test_no_subprocess_execution_on_import():
    assert IMPORT_TIME_SUBPROCESS_CALLS == []


def test_main_guard_gates_run_and_is_not_reached_on_plain_import():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    assert 'if __name__ == "__main__":' in source
    guard_index = source.index('if __name__ == "__main__":')
    guarded_block = source[guard_index:]
    assert "mcp_server.run()" in guarded_block
    # The module-level name is "pipeline_bridge_server" (set via spec_from_file_location),
    # not "__main__", so loading it as a module never reaches the guarded run() call.
    assert pipeline_bridge_server.__name__ != "__main__"


# --- workdir validation ----------------------------------------------------


def test_validate_workdir_rejects_relative_path_before_resolving():
    with pytest.raises(PipelineBridgeError):
        validate_workdir("relative/path")


def test_validate_workdir_rejects_missing_directory():
    with pytest.raises(PipelineBridgeError):
        validate_workdir("/opt/ai/projects/this-does-not-exist-xyz")


def test_validate_workdir_rejects_non_directory():
    target = os.path.join(REPO_ROOT, "templates", "pipeline_bridge_server.py")
    assert os.path.isfile(target)
    with pytest.raises(PipelineBridgeError):
        validate_workdir(target)


def test_validate_workdir_rejects_path_outside_allowed_root():
    with pytest.raises(PipelineBridgeError):
        validate_workdir("/etc")


def test_validate_workdir_rejects_dotdot_escape():
    escape_path = os.path.join(REPO_ROOT, "..", "..")
    with pytest.raises(PipelineBridgeError):
        validate_workdir(escape_path)


def test_validate_workdir_accepts_valid_path_under_allowed_root():
    resolved = validate_workdir(REPO_ROOT)
    assert str(resolved) == os.path.realpath(REPO_ROOT)
    assert resolved.is_relative_to(ALLOWED_ROOT)


# --- subprocess safety (HermesKanbanBackend) -------------------------------


def test_hermes_backend_invokes_argv_list_with_shell_false(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs

        class Completed:
            returncode = 0
            stdout = json.dumps({"id": "t_fake123"})
            stderr = ""

        return Completed()

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    resolved = validate_workdir(REPO_ROOT)
    backend = HermesKanbanBackend(resolved)
    task_id = backend.create_task(
        "key:abc",
        "Implement feature-x",
        "body text",
        {"role": "implementation"},
    )

    assert task_id == "t_fake123"
    assert isinstance(captured["argv"], list)
    assert captured["argv"][0] == "hermes"
    assert captured["kwargs"]["shell"] is False
    assert "timeout" in captured["kwargs"]


def test_hermes_backend_existing_id_for_key_is_always_none_and_makes_no_call(monkeypatch):
    def fake_run(*args, **kwargs):
        raise AssertionError("existing_id_for_key must not invoke subprocess")

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    resolved = validate_workdir(REPO_ROOT)
    backend = HermesKanbanBackend(resolved)
    assert backend.existing_id_for_key("any-key") is None


def test_hermes_backend_reports_error_on_nonzero_exit(monkeypatch):
    def fake_run(argv, **kwargs):
        class Completed:
            returncode = 1
            stdout = ""
            stderr = "boom"

        return Completed()

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    resolved = validate_workdir(REPO_ROOT)
    backend = HermesKanbanBackend(resolved)
    with pytest.raises(PipelineBridgeError):
        backend.create_task("key:abc", "title", "body", {"role": "implementation"})


def test_hermes_backend_reports_error_on_timeout(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 60))

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    resolved = validate_workdir(REPO_ROOT)
    backend = HermesKanbanBackend(resolved)
    with pytest.raises(PipelineBridgeError):
        backend.create_task("key:abc", "title", "body", {"role": "implementation"})


def test_hermes_backend_never_creates_real_kanban_task(monkeypatch):
    """No fake here calls the real `hermes` binary; this asserts the seam is mockable."""

    def fake_run(argv, **kwargs):
        class Completed:
            returncode = 0
            stdout = json.dumps({"id": "t_never_real"})
            stderr = ""

        return Completed()

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)
    resolved = validate_workdir(REPO_ROOT)
    backend = HermesKanbanBackend(resolved)
    task_id = backend.create_task("key:x", "title", "body", {"role": "implementation"})
    assert task_id == "t_never_real"


# --- canonical Kanban argv --------------------------------------------------


def test_implementation_argv_assigns_coder_claude_and_max_retries():
    resolved = validate_workdir(REPO_ROOT)
    argv = pipeline_bridge_server._build_kanban_create_argv(
        "key:impl", "Implement x", "body", {"role": "implementation"}, resolved
    )
    assert argv[:3] == ["hermes", "kanban", "create"]
    assert "--assignee" in argv and argv[argv.index("--assignee") + 1] == "coder-claude"
    assert "--idempotency-key" in argv and argv[argv.index("--idempotency-key") + 1] == "key:impl"
    assert "--created-by" in argv and argv[argv.index("--created-by") + 1] == "pipeline_bridge"
    assert "--max-retries" in argv and argv[argv.index("--max-retries") + 1] == "3"
    assert "--workspace" in argv and argv[argv.index("--workspace") + 1] == f"dir:{resolved}"
    assert "--parent" not in argv
    assert "--json" in argv


def test_review_argv_assigns_reviewer_and_links_parent():
    resolved = validate_workdir(REPO_ROOT)
    argv = pipeline_bridge_server._build_kanban_create_argv(
        "key:review",
        "Review x",
        "body",
        {"role": "review", "implementation_task_id": "t_impl1"},
        resolved,
    )
    assert "--assignee" in argv and argv[argv.index("--assignee") + 1] == "reviewer"
    assert "--parent" in argv and argv[argv.index("--parent") + 1] == "t_impl1"
    assert "--max-retries" in argv and argv[argv.index("--max-retries") + 1] == "1"


def test_correction_argv_assigns_coder_claude_and_links_review_parent():
    resolved = validate_workdir(REPO_ROOT)
    argv = pipeline_bridge_server._build_kanban_create_argv(
        "key:correction",
        "Correct x",
        "body",
        {"role": "correction", "review_task_id": "t_review1"},
        resolved,
    )
    assert "--assignee" in argv and argv[argv.index("--assignee") + 1] == "coder-claude"
    assert "--parent" in argv and argv[argv.index("--parent") + 1] == "t_review1"
    assert "--max-retries" in argv and argv[argv.index("--max-retries") + 1] == "3"


# --- deterministic idempotency (unchanged pure-logic behavior) ------------


def test_implementation_key_matches_stable_key():
    backend = FakeBackend()
    create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key


def test_review_key_matches_stable_key_and_contains_implementation_id():
    backend = FakeBackend()
    impl_id = "task-impl-123"
    create_review_task(backend, "/opt/ai/projects/demo", "feature-x", impl_id, "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", f"review:{impl_id}")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key
    assert impl_id in expected_key


def test_correction_key_matches_stable_key():
    backend = FakeBackend()
    review_id = "task-review-456"
    create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", review_id, "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", f"correction:{review_id}")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key


def test_roles_are_distinct_for_identical_workdir_and_feature():
    impl_key = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    review_key = stable_key("/opt/ai/projects/demo", "feature-x", "review:t1")
    correction_key = stable_key("/opt/ai/projects/demo", "feature-x", "correction:t1")
    assert len({impl_key, review_key, correction_key}) == 3


def test_review_task_rejects_missing_implementation_id():
    backend = FakeBackend()
    with pytest.raises(PipelineBridgeError):
        create_review_task(backend, "/opt/ai/projects/demo", "feature-x", "", "must pass")
    with pytest.raises(PipelineBridgeError):
        create_review_task(backend, "/opt/ai/projects/demo", "feature-x", None, "must pass")


def test_correction_task_rejects_missing_review_id():
    backend = FakeBackend()
    with pytest.raises(PipelineBridgeError):
        create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", "", "must pass")
    with pytest.raises(PipelineBridgeError):
        create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", None, "must pass")


def test_implementation_task_idempotent_when_key_preseeded():
    key = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    backend = FakeBackend(existing={key: "existing-task-id"})

    result = create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")

    assert result == "existing-task-id"
    assert backend.created == []


def test_review_task_idempotent_when_key_preseeded():
    impl_id = "task-impl-1"
    key = stable_key("/opt/ai/projects/demo", "feature-x", f"review:{impl_id}")
    backend = FakeBackend(existing={key: "existing-review-id"})

    result = create_review_task(backend, "/opt/ai/projects/demo", "feature-x", impl_id, "must pass")

    assert result == "existing-review-id"
    assert backend.created == []


def test_correction_task_idempotent_when_key_preseeded():
    review_id = "task-review-1"
    key = stable_key("/opt/ai/projects/demo", "feature-x", f"correction:{review_id}")
    backend = FakeBackend(existing={key: "existing-correction-id"})

    result = create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", review_id, "must pass")

    assert result == "existing-correction-id"
    assert backend.created == []


def test_two_equivalent_requests_for_missing_key_produce_same_key():
    key_a = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    key_b = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    assert key_a == key_b

    backend1 = FakeBackend()
    backend2 = FakeBackend()
    create_implementation_task(backend1, "/opt/ai/projects/demo", "feature-x", "must pass")
    create_implementation_task(backend2, "/opt/ai/projects/demo", "feature-x", "must pass")

    assert backend1.created[0]["key"] == backend2.created[0]["key"]


# --- review body: existing required sentences + A3.5 reviewer contract ----


def test_review_body_contains_required_sentences_verbatim():
    backend = FakeBackend()
    create_review_task(backend, "/opt/ai/projects/demo", "feature-x", "impl-1", "must pass")

    body = backend.created[0]["body"]
    assert "If test_command is __skip__, do not invent or substitute another command" in body
    assert (
        "If tests are required by the acceptance criteria but no valid explicit test command "
        "is available, block the task instead of guessing"
        in body
    )
    for sentence in REVIEW_BODY_REQUIRED_SENTENCES:
        assert sentence in body


def test_review_body_contains_reviewer_hardening_contract():
    backend = FakeBackend()
    create_review_task(backend, "/opt/ai/projects/demo", "feature-x", "impl-1", "must pass")

    body = backend.created[0]["body"]
    for sentence in REVIEWER_CONTRACT_SENTENCES:
        assert sentence in body

    lowered = body.lower()
    assert "read-only" in lowered
    assert "mcp__review_bridge__collect" in body
    assert "terminal" in lowered and "execution" in lowered
    assert "memory" in lowered
    assert "fresh collect" in lowered or "fresh" in lowered
    assert "block" in lowered


def test_implementation_and_correction_bodies_do_not_require_reviewer_contract():
    backend = FakeBackend()
    create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")
    body = backend.created[0]["body"]
    for sentence in REVIEWER_CONTRACT_SENTENCES:
        assert sentence not in body


# --- backend isolation / metadata preservation (unchanged behavior) -------


def test_only_fake_backend_used_no_network():
    backend = FakeBackend()
    create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")

    assert len(backend.created) == 1
    for network_module in ("socket", "http.client", "urllib.request", "requests"):
        assert network_module not in getattr(pipeline_bridge_server, "__dict__", {})
    assert not hasattr(pipeline_bridge_server, "requests")
    assert not hasattr(pipeline_bridge_server, "socket")


def test_metadata_preserves_test_command_and_changed_paths_verbatim():
    backend = FakeBackend()
    changed_paths = ["src/a.py", "src/b.py"]
    create_implementation_task(
        backend,
        "/opt/ai/projects/demo",
        "feature-x",
        "must pass",
        changed_paths=changed_paths,
        test_command="__skip__",
    )

    created = backend.created[0]
    assert created["metadata"]["test_command"] == "__skip__"
    assert created["metadata"]["changed_paths"] == changed_paths
    assert "__skip__" in created["body"]
    assert repr(changed_paths) in created["body"]


def test_review_metadata_preserves_test_command_and_changed_paths_verbatim():
    backend = FakeBackend()
    changed_paths = ["src/a.py"]
    create_review_task(
        backend,
        "/opt/ai/projects/demo",
        "feature-x",
        "impl-1",
        "must pass",
        changed_paths=changed_paths,
        test_command="__skip__",
    )

    created = backend.created[0]
    assert created["metadata"]["test_command"] == "__skip__"
    assert created["metadata"]["changed_paths"] == changed_paths


def test_correction_body_includes_parent_review_task_id():
    backend = FakeBackend()
    review_id = "task-review-789"
    create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", review_id, "must pass")

    created = backend.created[0]
    assert review_id in created["body"]
    assert created["metadata"]["review_task_id"] == review_id


def test_stable_key_requires_non_empty_workdir_and_feature():
    with pytest.raises(PipelineBridgeError):
        stable_key("", "feature-x", "implementation")
    with pytest.raises(PipelineBridgeError):
        stable_key("/opt/ai/projects/demo", "", "implementation")


# --- MCP tool functions end-to-end (mocked subprocess) ---------------------


def test_mcp_tool_create_implementation_task_end_to_end(monkeypatch):
    def fake_run(argv, **kwargs):
        class Completed:
            returncode = 0
            stdout = json.dumps({"id": "t_impl_e2e"})
            stderr = ""

        return Completed()

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    tool_fn = pipeline_bridge_server._tool_create_implementation_task
    result = tool_fn(REPO_ROOT, "feature-e2e", "must pass")
    assert result == "t_impl_e2e"


def test_mcp_tool_rejects_workdir_outside_allowed_root(monkeypatch):
    def fake_run(argv, **kwargs):
        raise AssertionError("must not shell out when workdir validation fails")

    monkeypatch.setattr(pipeline_bridge_server.subprocess, "run", fake_run)

    tool_fn = pipeline_bridge_server._tool_create_implementation_task
    with pytest.raises(PipelineBridgeError):
        tool_fn("/etc", "feature-e2e", "must pass")
