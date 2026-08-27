"""Tests for the claude_bridge_server template as a real MCP runtime adapter.

Loaded by absolute path so the template is exercised exactly as the operator
would install it. Uses fakes/mocking only: no real Claude call, no network,
no Kanban, and no runtime mutation of repo state (ledger writes are always
redirected to a temp state_dir).
"""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import threading

from types import SimpleNamespace

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/claude_bridge_server.py"


def _load_module_with_subprocess_spy():
    """Load the template fresh, spying on subprocess.run for the duration of import."""
    calls = []
    original_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original_run(*args, **kwargs)

    subprocess.run = spy
    try:
        spec = importlib.util.spec_from_file_location("claude_bridge_server", TEMPLATE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        subprocess.run = original_run
    return module, calls


claude_bridge_server, IMPORT_TIME_SUBPROCESS_CALLS = _load_module_with_subprocess_spy()

BridgeError = claude_bridge_server.BridgeError
BudgetExhaustedError = claude_bridge_server.BudgetExhaustedError
LedgerCorruptionError = claude_bridge_server.LedgerCorruptionError

# A real, existing directory under PROJECTS_ROOT: this repo itself.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKDIR = REPO_ROOT
TMP_ROOT = os.path.join(REPO_ROOT, "tests", ".tmp")


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert claude_bridge_server.__file__ == TEMPLATE_PATH


@pytest.fixture
def state_dir():
    os.makedirs(TMP_ROOT, exist_ok=True)
    d = tempfile.mkdtemp(dir=TMP_ROOT, prefix="claude-bridge-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_root():
    yield
    if os.path.isdir(TMP_ROOT) and not os.listdir(TMP_ROOT):
        os.rmdir(TMP_ROOT)


def make_ok_telemetry(**overrides):
    telemetry = {
        "duration_ms": 1234,
        "duration_api_ms": 1000,
        "num_turns": 3,
        "total_cost_usd": 0.05,
        "session_id": "sess-abc",
        "subtype": "success",
        "is_error": False,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 1,
            "output_tokens": 20,
        },
    }
    telemetry.update(overrides)
    return telemetry


class FakeSubprocess:
    """Stand-in for subprocess.run injected via _subprocess_run. No real process spawned."""

    def __init__(self, telemetry_fn=None, stdout_fn=None, raise_exc=None):
        self.invocations = []
        self._lock = threading.Lock()
        self.telemetry_fn = telemetry_fn
        self.stdout_fn = stdout_fn
        self.raise_exc = raise_exc

    def __call__(self, argv, **kwargs):
        with self._lock:
            self.invocations.append({"argv": list(argv), "kwargs": kwargs})
            n = len(self.invocations)
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.stdout_fn is not None:
            stdout = self.stdout_fn(n)
        else:
            telemetry = self.telemetry_fn(n) if self.telemetry_fn else make_ok_telemetry()
            stdout = json.dumps(telemetry)
        return SimpleNamespace(stdout=stdout, returncode=0)

    @property
    def call_count(self):
        return len(self.invocations)


# 1. budget-1-2
def test_budget_calls_1_and_2_are_normal(state_dir):
    fake = FakeSubprocess()
    task_id = "task-normal"
    r1 = claude_bridge_server.run(WORKDIR, "prompt one", task_id, state_dir=state_dir, _subprocess_run=fake)
    r2 = claude_bridge_server.run(WORKDIR, "prompt two", task_id, state_dir=state_dir, _subprocess_run=fake)

    assert r1["call_number"] == 1
    assert r2["call_number"] == 2
    assert r1["tags"] == ["normal"]
    assert r2["tags"] == ["normal"]
    assert r1["outcome"] == "ok"
    assert r2["outcome"] == "ok"


# 2. budget-3
def test_budget_call_3_is_exceptional_budget_warning(state_dir):
    fake = FakeSubprocess()
    task_id = "task-three"
    claude_bridge_server.run(WORKDIR, "prompt one", task_id, state_dir=state_dir, _subprocess_run=fake)
    claude_bridge_server.run(WORKDIR, "prompt two", task_id, state_dir=state_dir, _subprocess_run=fake)
    r3 = claude_bridge_server.run(WORKDIR, "prompt three", task_id, state_dir=state_dir, _subprocess_run=fake)

    assert r3["call_number"] == 3
    assert sorted(r3["tags"]) == sorted(["exceptional", "budget-warning"])
    assert r3["outcome"] == "ok"


# 3. budget-4-plus
def test_budget_call_4_and_5_raise_without_reaching_subprocess(state_dir):
    fake = FakeSubprocess()
    task_id = "task-four"
    for i in range(3):
        claude_bridge_server.run(WORKDIR, f"prompt {i}", task_id, state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "prompt four", task_id, state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "prompt five", task_id, state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


# 4. budget-isolation
def test_budget_isolated_per_task_id(state_dir):
    fake = FakeSubprocess()
    r_a = claude_bridge_server.run(WORKDIR, "p", "task-a", state_dir=state_dir, _subprocess_run=fake)
    r_b = claude_bridge_server.run(WORKDIR, "p", "task-b", state_dir=state_dir, _subprocess_run=fake)

    assert r_a["call_number"] == 1
    assert r_b["call_number"] == 1
    assert r_a["tags"] == ["normal"]
    assert r_b["tags"] == ["normal"]


# 5. concurrency
def test_concurrency_same_call_number_not_double_admitted(state_dir):
    task_id = "task-concurrent"
    warmup_fake = FakeSubprocess()
    claude_bridge_server.run(WORKDIR, "warmup 1", task_id, state_dir=state_dir, _subprocess_run=warmup_fake)
    claude_bridge_server.run(WORKDIR, "warmup 2", task_id, state_dir=state_dir, _subprocess_run=warmup_fake)
    # The next reservation for this task_id is call_number 3.

    release_event = threading.Event()
    started_event = threading.Event()

    class BlockingFakeSubprocess(FakeSubprocess):
        def __call__(self, argv, **kwargs):
            with self._lock:
                self.invocations.append({"argv": list(argv), "kwargs": kwargs})
            started_event.set()
            release_event.wait(timeout=5)
            return SimpleNamespace(stdout=json.dumps(make_ok_telemetry()), returncode=0)

    fake = BlockingFakeSubprocess()
    barrier = threading.Barrier(2)
    outcomes = {}
    errors = {}

    def worker(name):
        barrier.wait(timeout=5)
        try:
            outcomes[name] = claude_bridge_server.run(
                WORKDIR, f"prompt-{name}", task_id, state_dir=state_dir, _subprocess_run=fake
            )
        except Exception as e:
            errors[name] = e

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start()
    t2.start()

    # Wait until the call-3 reservation has been made and its subprocess is
    # blocking. The lock is released before the subprocess runs, so the
    # other thread's reservation attempt (call 4) should resolve promptly
    # without waiting on the blocked subprocess.
    assert started_event.wait(timeout=5)

    deadline_iterations = 200
    while len(errors) < 1 and deadline_iterations > 0:
        deadline_iterations -= 1
        threading.Event().wait(0.01)

    assert len(errors) == 1
    (only_error,) = errors.values()
    assert isinstance(only_error, BudgetExhaustedError)

    release_event.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(outcomes) == 1
    (only_outcome,) = outcomes.values()
    assert only_outcome["call_number"] == 3
    assert only_outcome["outcome"] == "ok"
    assert fake.call_count == 1

    ledger_path = os.path.join(state_dir, claude_bridge_server.LEDGER_FILENAME)
    with open(ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    calls = data["tasks"][task_id]["calls"]
    call_numbers = [c["accepted_at_call"] for c in calls]
    assert call_numbers == sorted(set(call_numbers)), "call numbers must be unique and ordered"
    assert len(calls) == 3


# 6. ledger-atomicity
def test_ledger_atomicity_valid_json_after_success(state_dir):
    fake = FakeSubprocess()
    task_id = "task-atomic"
    claude_bridge_server.run(WORKDIR, "p", task_id, state_dir=state_dir, _subprocess_run=fake)

    ledger_path = os.path.join(state_dir, claude_bridge_server.LEDGER_FILENAME)
    with open(ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["version"] == claude_bridge_server.LEDGER_VERSION
    assert task_id in data["tasks"]
    calls = data["tasks"][task_id]["calls"]
    assert len(calls) == 1
    assert calls[0]["outcome"] == "ok"
    assert calls[0]["accepted_at_call"] == 1


# 7. ledger-corruption
LEDGER_CORRUPTION_CASES = [
    ("malformed-json", "{not json"),
    ("wrong-top-level-type", json.dumps([1, 2, 3])),
    ("missing-tasks-key", json.dumps({"version": claude_bridge_server.LEDGER_VERSION})),
    (
        "task-entry-not-dict",
        json.dumps({"version": claude_bridge_server.LEDGER_VERSION, "tasks": {"task-corrupt": "nope"}}),
    ),
]


@pytest.mark.parametrize("name,content", LEDGER_CORRUPTION_CASES)
def test_ledger_corruption_fails_closed_and_leaves_file_untouched(state_dir, name, content):
    ledger_path = os.path.join(state_dir, claude_bridge_server.LEDGER_FILENAME)
    with open(ledger_path, "w", encoding="utf-8") as f:
        f.write(content)

    fake = FakeSubprocess()
    with pytest.raises((LedgerCorruptionError, BridgeError)):
        claude_bridge_server.run(WORKDIR, "p", "task-corrupt", state_dir=state_dir, _subprocess_run=fake)

    with open(ledger_path, "r", encoding="utf-8") as f:
        after = f.read()
    assert after == content
    assert fake.call_count == 0


# 8. failed-call-consumes
def test_failed_call_still_consumes_call_number(state_dir):
    task_id = "task-failed"

    fake1 = FakeSubprocess(raise_exc=RuntimeError("boom"))
    r1 = claude_bridge_server.run(WORKDIR, "p1", task_id, state_dir=state_dir, _subprocess_run=fake1)
    assert r1["outcome"] == "failed"
    assert r1["call_number"] == 1

    fake2 = FakeSubprocess(stdout_fn=lambda n: "not json")
    r2 = claude_bridge_server.run(WORKDIR, "p2", task_id, state_dir=state_dir, _subprocess_run=fake2)
    assert r2["outcome"] == "failed"
    assert r2["call_number"] == 2

    fake3 = FakeSubprocess(stdout_fn=lambda n: json.dumps({"duration_ms": 1}))
    r3 = claude_bridge_server.run(WORKDIR, "p3", task_id, state_dir=state_dir, _subprocess_run=fake3)
    assert r3["outcome"] == "failed"
    assert r3["call_number"] == 3

    fake4 = FakeSubprocess()
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "p4", task_id, state_dir=state_dir, _subprocess_run=fake4)
    assert fake4.call_count == 0


# 9. telemetry
def test_telemetry_all_required_fields_persisted(state_dir):
    task_id = "task-telemetry"
    telemetry = make_ok_telemetry(session_id="sess-xyz")
    telemetry["iterations"] = 4
    telemetry["modelUsage"] = {"model": {"input_tokens": 5}}
    fake = FakeSubprocess(stdout_fn=lambda n: json.dumps(telemetry))

    r = claude_bridge_server.run(WORKDIR, "p", task_id, state_dir=state_dir, _subprocess_run=fake)
    entry = r["ledger_entry"]

    assert entry["duration_ms"] == telemetry["duration_ms"]
    assert entry["duration_api_ms"] == telemetry["duration_api_ms"]
    assert entry["num_turns"] == telemetry["num_turns"]
    assert entry["total_cost_usd"] == telemetry["total_cost_usd"]
    assert entry["session_id"] == "sess-xyz"
    assert entry["subtype"] == telemetry["subtype"]
    assert entry["is_error"] == telemetry["is_error"]
    assert entry["usage"]["input_tokens"] == telemetry["usage"]["input_tokens"]
    assert entry["usage"]["cache_creation_input_tokens"] == telemetry["usage"]["cache_creation_input_tokens"]
    assert entry["usage"]["cache_read_input_tokens"] == telemetry["usage"]["cache_read_input_tokens"]
    assert entry["usage"]["output_tokens"] == telemetry["usage"]["output_tokens"]
    assert entry["iterations"] == 4
    assert entry["modelUsage"] == {"model": {"input_tokens": 5}}


def test_telemetry_absent_optional_fields_are_none_not_fabricated(state_dir):
    task_id = "task-telemetry-none"
    telemetry = make_ok_telemetry()
    fake = FakeSubprocess(stdout_fn=lambda n: json.dumps(telemetry))

    r = claude_bridge_server.run(WORKDIR, "p", task_id, state_dir=state_dir, _subprocess_run=fake)
    entry = r["ledger_entry"]

    assert entry["iterations"] is None
    assert entry["modelUsage"] is None


# 10. workdir
def test_workdir_projects_root_itself_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            claude_bridge_server.PROJECTS_ROOT, "p", "task-w1", state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_workdir_outside_projects_root_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run("/tmp/elsewhere", "p", "task-w2", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


def test_workdir_nonexistent_under_root_rejected(state_dir):
    fake = FakeSubprocess()
    nonexistent = os.path.join(claude_bridge_server.PROJECTS_ROOT, "definitely-does-not-exist-xyz-123")
    with pytest.raises(BridgeError):
        claude_bridge_server.run(nonexistent, "p", "task-w3", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


def test_workdir_real_existing_repo_dir_accepted(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(WORKDIR, "p", "task-w4", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert fake.call_count == 1


# 11. changed-paths
def test_changed_paths_absolute_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "p", "task-cp1", changed_paths=["/etc/passwd"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_dotdot_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "p", "task-cp2", changed_paths=["a/../b"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_escapes_workdir_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "p", "task-cp3", changed_paths=["../sibling"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_safe_relative_accepted(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(
        WORKDIR,
        "p",
        "task-cp4",
        changed_paths=["templates/reviewer-SOUL.md"],
        state_dir=state_dir,
        _subprocess_run=fake,
    )
    assert r["outcome"] == "ok"
    assert fake.call_count == 1


# 12. argv
def test_argv_structure_and_prompt_safety(state_dir):
    fake = FakeSubprocess()
    prompt = "$(pwd); rm -rf x"
    r = claude_bridge_server.run(WORKDIR, prompt, "task-argv", state_dir=state_dir, _subprocess_run=fake)

    assert fake.call_count == 1
    invocation = fake.invocations[0]
    argv = invocation["argv"]
    kwargs = invocation["kwargs"]

    assert isinstance(argv, list)
    assert kwargs.get("shell", False) is False
    assert os.path.realpath(kwargs.get("cwd")) == os.path.realpath(WORKDIR)
    assert argv.count("--print") == 1
    assert argv.count("--output-format") == 1
    assert argv.count("json") == 1
    assert argv.count("--no-session-persistence") == 1
    assert argv[-1] == prompt
    assert argv == r["argv"]


# 13. optional-budget
def test_optional_budget_flag_absent_when_none(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(
        WORKDIR, "p", "task-budget-none", max_budget_usd=None, state_dir=state_dir, _subprocess_run=fake
    )
    argv = r["argv"]
    assert "--max-budget-usd" not in argv
    assert "--max-tokens" not in argv
    assert "--token-cap" not in argv


def test_optional_budget_flag_present_when_set(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(
        WORKDIR, "p", "task-budget-set", max_budget_usd=1.5, state_dir=state_dir, _subprocess_run=fake
    )
    argv = r["argv"]
    assert argv.count("--max-budget-usd") == 1
    idx = argv.index("--max-budget-usd")
    assert argv[idx + 1] == str(1.5)
    assert "--max-tokens" not in argv
    assert "--token-cap" not in argv


# 14. generic-repo
def test_template_source_has_no_hardcoded_repo_file_refs():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert "app.py" not in source
    assert "tests/test_app.py" not in source


# 15. isolation
def test_no_network_imports_in_template_source():
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert "import socket" not in source
    assert "import urllib" not in source
    assert "from urllib" not in source
    assert "import http.client" not in source
    assert "from http" not in source


# --- MCP server surface --------------------------------------------------


def test_mcp_server_import_and_instantiation():
    from mcp.server import MCPServer

    assert isinstance(claude_bridge_server.mcp_server, MCPServer)
    assert claude_bridge_server.mcp_server.name == "claude-bridge"


def test_run_is_registered_as_the_only_mcp_tool():
    tools = asyncio.run(claude_bridge_server.mcp_server.list_tools())
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"run"}


def test_mcp_server_has_executable_run_entrypoint():
    assert callable(claude_bridge_server.mcp_server.run)


def test_run_server_function_exists_and_is_callable():
    assert callable(claude_bridge_server.run_server)


def test_no_subprocess_execution_on_import():
    assert IMPORT_TIME_SUBPROCESS_CALLS == []


def test_main_guard_gates_run_server_and_is_not_reached_on_plain_import():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        source = f.read()
    assert 'if __name__ == "__main__":' in source
    guard_index = source.index('if __name__ == "__main__":')
    guarded_block = source[guard_index:]
    assert "run_server()" in guarded_block
    # The module-level name is "claude_bridge_server" (set via spec_from_file_location),
    # not "__main__", so loading it as a module never reaches the guarded run_server() call.
    assert claude_bridge_server.__name__ != "__main__"


def test_run_server_invokes_mcp_server_run(monkeypatch):
    called = {}

    def fake_run():
        called["invoked"] = True

    monkeypatch.setattr(claude_bridge_server.mcp_server, "run", fake_run)
    claude_bridge_server.run_server()
    assert called.get("invoked") is True


# --- MCP tool end-to-end (mocked subprocess, redirected ledger state_dir) --


def test_mcp_tool_run_end_to_end(monkeypatch, state_dir):
    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout=json.dumps(make_ok_telemetry()), returncode=0)

    monkeypatch.setattr(claude_bridge_server.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_bridge_server, "_default_state_dir", lambda: state_dir)

    result = claude_bridge_server._tool_run(WORKDIR, "prompt via tool", "task-tool-e2e")
    assert result["outcome"] == "ok"
    assert result["call_number"] == 1


def test_mcp_tool_run_rejects_workdir_outside_projects_root(monkeypatch, state_dir):
    def fake_run(argv, **kwargs):
        raise AssertionError("must not shell out when workdir validation fails")

    monkeypatch.setattr(claude_bridge_server.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_bridge_server, "_default_state_dir", lambda: state_dir)

    with pytest.raises(BridgeError):
        claude_bridge_server._tool_run("/tmp/elsewhere", "p", "task-tool-reject")


# --- workdir: non-absolute / non-directory / traversal ----------------------


def test_workdir_non_absolute_rejected_before_resolving(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            "relative/workdir", "p", "task-w5", state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_workdir_non_directory_rejected(state_dir):
    fake = FakeSubprocess()
    target = os.path.join(WORKDIR, "templates", "claude_bridge_server.py")
    assert os.path.isfile(target)
    with pytest.raises(BridgeError):
        claude_bridge_server.run(target, "p", "task-w7", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


def test_workdir_traversal_escape_rejected(state_dir):
    fake = FakeSubprocess()
    escape_path = os.path.join(claude_bridge_server.PROJECTS_ROOT, "..", "..")
    with pytest.raises(BridgeError):
        claude_bridge_server.run(escape_path, "p", "task-w6", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


# --- canonical positional contract: run(workdir, prompt) -------------------


def test_canonical_two_positional_call_succeeds(monkeypatch, state_dir):
    """The canonical public contract is run(workdir, prompt): task_id is optional and
    keyword-capable, not a required third positional. This pins that exact two-arg call.
    """
    fake = FakeSubprocess()
    monkeypatch.setattr(claude_bridge_server, "_default_state_dir", lambda: state_dir)

    r = claude_bridge_server.run(WORKDIR, "a positional prompt", _subprocess_run=fake)

    assert r["outcome"] == "ok"
    assert r["argv"][-1] == "a positional prompt"
    assert fake.call_count == 1


def test_second_positional_is_prompt_not_task_id(state_dir):
    """Pin first-two-positional semantics: (workdir, prompt). The second positional
    string is forwarded into argv as prompt content, and is never treated as a ledger
    key - an omitted task_id resolves to a deterministic anonymous ledger bucket keyed
    by a hash of (workdir, prompt), never by the raw prompt text.
    """
    fake = FakeSubprocess()
    r = claude_bridge_server.run(WORKDIR, "not-a-task-id", state_dir=state_dir, _subprocess_run=fake)

    assert r["argv"][-1] == "not-a-task-id"

    ledger_path = os.path.join(state_dir, claude_bridge_server.LEDGER_FILENAME)
    with open(ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    (only_key,) = data["tasks"].keys()
    assert only_key.startswith(claude_bridge_server.ANONYMOUS_KEY_PREFIX)
    assert "not-a-task-id" not in only_key
    assert "not-a-task-id" not in data["tasks"]


def test_task_id_optional_does_not_break_two_or_three_arg_callers(state_dir):
    fake = FakeSubprocess()
    r_two = claude_bridge_server.run(WORKDIR, "p1", state_dir=state_dir, _subprocess_run=fake)
    r_three = claude_bridge_server.run(WORKDIR, "p2", task_id="t", state_dir=state_dir, _subprocess_run=fake)

    assert r_two["outcome"] == "ok"
    assert r_three["outcome"] == "ok"
    assert fake.call_count == 2


def test_anonymous_ledger_bucket_respects_budget_threshold(state_dir):
    """Calls with task_id omitted, sharing the same (workdir, prompt) identity, share
    one deterministic ledger key, so the budget threshold still bounds them exactly
    like an explicit task_id would.
    """
    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "the same legacy prompt", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "the same legacy prompt", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


ANON_WORKDIR_2 = os.path.join(WORKDIR, "tests")


def test_anonymous_bucket_different_prompts_same_workdir_do_not_share(state_dir):
    """Different prompts against the same workdir must not share a legacy bucket:
    each (workdir, prompt) identity gets its own independent budget.
    """
    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "prompt A", state_dir=state_dir, _subprocess_run=fake)
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "prompt A", state_dir=state_dir, _subprocess_run=fake)

    r = claude_bridge_server.run(WORKDIR, "prompt B", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert r["call_number"] == 1


def test_anonymous_bucket_same_prompt_different_workdirs_do_not_share(state_dir):
    """The same prompt text against two different workdirs must not share a legacy
    bucket: workdir is part of the anonymous identity.
    """
    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "shared prompt text", state_dir=state_dir, _subprocess_run=fake)
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "shared prompt text", state_dir=state_dir, _subprocess_run=fake)

    r = claude_bridge_server.run(ANON_WORKDIR_2, "shared prompt text", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert r["call_number"] == 1


def test_anonymous_bucket_exhausting_one_identity_does_not_reject_unrelated_identity(state_dir):
    """Exhausting one anonymous identity's budget must not affect an unrelated
    identity's budget (different workdir AND different prompt).
    """
    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "identity one", state_dir=state_dir, _subprocess_run=fake)
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "identity one", state_dir=state_dir, _subprocess_run=fake)

    r = claude_bridge_server.run(ANON_WORKDIR_2, "identity two", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert r["call_number"] == 1


def test_anonymous_bucket_rejection_happens_before_subprocess(state_dir):
    """The 4th call to an exhausted anonymous identity must be rejected before the
    subprocess is ever invoked.
    """
    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "reject before subprocess", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "reject before subprocess", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


def test_anonymous_bucket_expires_after_ttl_and_becomes_usable_again(monkeypatch, state_dir):
    """After the anonymous budget TTL window elapses, an exhausted identity resets
    and becomes usable again. Uses an injected fake clock only - no real sleeping.
    """
    fake_time = {"t": 1_000.0}
    monkeypatch.setattr(claude_bridge_server, "_now", lambda: fake_time["t"])

    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "ttl prompt", state_dir=state_dir, _subprocess_run=fake)
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "ttl prompt", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    # Advance the injected clock past the TTL window; no real time passes.
    fake_time["t"] += claude_bridge_server.ANONYMOUS_BUDGET_TTL_SECONDS

    r = claude_bridge_server.run(WORKDIR, "ttl prompt", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert r["call_number"] == 1
    assert fake.call_count == 4


def test_anonymous_bucket_not_yet_expired_stays_exhausted(monkeypatch, state_dir):
    """Just under the TTL window, the exhausted identity must still be rejected."""
    fake_time = {"t": 1_000.0}
    monkeypatch.setattr(claude_bridge_server, "_now", lambda: fake_time["t"])

    fake = FakeSubprocess()
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "ttl boundary prompt", state_dir=state_dir, _subprocess_run=fake)

    fake_time["t"] += claude_bridge_server.ANONYMOUS_BUDGET_TTL_SECONDS - 1

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "ttl boundary prompt", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


def test_anonymous_ledger_key_deterministic_and_excludes_raw_prompt_text(state_dir):
    """The effective anonymous id must be deterministic for the same (workdir, prompt)
    identity, and the raw prompt text must never appear in the ledger key or file.
    """
    prompt = "a very secret legacy prompt, do not leak me"

    fake1 = FakeSubprocess()
    r1 = claude_bridge_server.run(WORKDIR, prompt, state_dir=state_dir, _subprocess_run=fake1)

    other_state_dir = tempfile.mkdtemp(dir=TMP_ROOT, prefix="claude-bridge-det-")
    try:
        fake2 = FakeSubprocess()
        r2 = claude_bridge_server.run(WORKDIR, prompt, state_dir=other_state_dir, _subprocess_run=fake2)
        assert r1["task_id"] == r2["task_id"]
    finally:
        shutil.rmtree(other_state_dir, ignore_errors=True)

    assert r1["task_id"].startswith(claude_bridge_server.ANONYMOUS_KEY_PREFIX)
    assert prompt not in r1["task_id"]

    ledger_path = os.path.join(state_dir, claude_bridge_server.LEDGER_FILENAME)
    with open(ledger_path, "r", encoding="utf-8") as f:
        raw_ledger_text = f.read()
    assert prompt not in raw_ledger_text


def test_explicit_task_id_budget_not_subject_to_ttl(monkeypatch, state_dir):
    """Explicit task_id budgets are NOT bounded by the anonymous TTL window: once
    exhausted, advancing the injected clock must not reset them.
    """
    fake_time = {"t": 1_000.0}
    monkeypatch.setattr(claude_bridge_server, "_now", lambda: fake_time["t"])

    fake = FakeSubprocess()
    task_id = "task-no-ttl"
    for _ in range(3):
        claude_bridge_server.run(WORKDIR, "p", task_id, state_dir=state_dir, _subprocess_run=fake)

    fake_time["t"] += claude_bridge_server.ANONYMOUS_BUDGET_TTL_SECONDS * 10

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, "p", task_id, state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


def test_mcp_tool_schema_requires_only_workdir_and_prompt():
    tools = asyncio.run(claude_bridge_server.mcp_server.list_tools())
    (run_tool,) = [t for t in tools if t.name == "run"]
    assert run_tool.input_schema["required"] == ["workdir", "prompt"]
    assert set(run_tool.input_schema["properties"]) >= {"workdir", "prompt"}
    if "task_id" in run_tool.input_schema["properties"]:
        assert "task_id" not in run_tool.input_schema["required"]


def test_mcp_tool_run_accepts_two_field_workdir_prompt_payload(monkeypatch, state_dir):
    def fake_run(argv, **kwargs):
        return SimpleNamespace(stdout=json.dumps(make_ok_telemetry()), returncode=0)

    monkeypatch.setattr(claude_bridge_server.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_bridge_server, "_default_state_dir", lambda: state_dir)

    result = asyncio.run(
        claude_bridge_server.mcp_server.call_tool("run", {"workdir": WORKDIR, "prompt": "prompt via mcp two-field"})
    )
    assert result is not None


# --- subprocess transport: timeout / OS failure / bounded timeout ----------


def test_subprocess_timeout_returns_documented_failed_contract(state_dir):
    fake = FakeSubprocess(raise_exc=subprocess.TimeoutExpired(cmd=["claude"], timeout=300))
    r = claude_bridge_server.run(WORKDIR, "p", "task-timeout", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "failed"
    assert r["ledger_entry"]["error"] == "subprocess-failed"
    assert fake.call_count == 1


def test_subprocess_os_transport_failure_returns_documented_failed_contract(state_dir):
    fake = FakeSubprocess(raise_exc=OSError("claude executable not found"))
    r = claude_bridge_server.run(WORKDIR, "p", "task-oserror", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "failed"
    assert r["ledger_entry"]["error"] == "subprocess-failed"
    assert fake.call_count == 1


def test_bounded_timeout_is_passed_to_subprocess(state_dir):
    fake = FakeSubprocess()
    claude_bridge_server.run(WORKDIR, "p", "task-timeout-kwarg", state_dir=state_dir, _subprocess_run=fake)
    kwargs = fake.invocations[0]["kwargs"]
    assert "timeout" in kwargs
    assert isinstance(kwargs["timeout"], (int, float))
    assert kwargs["timeout"] > 0


def test_no_hidden_retry_subprocess_called_exactly_once_per_run(state_dir):
    fake = FakeSubprocess()
    claude_bridge_server.run(WORKDIR, "p", "task-single-call", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 1


def test_no_hidden_retry_subprocess_called_exactly_once_per_run_on_failure(state_dir):
    fake = FakeSubprocess(raise_exc=RuntimeError("boom"))
    r = claude_bridge_server.run(WORKDIR, "p", "task-single-call-fail", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "failed"
    assert fake.call_count == 1


def test_non_zero_exit_with_unparseable_output_yields_failed_contract(state_dir):
    """The bridge does not branch on returncode directly: a failure response is classified
    via its stdout's JSON/telemetry validity, the same way regardless of exit code. This
    pins that existing, documented behavior (the error string carries the useful info).
    """
    fake = FakeSubprocess(stdout_fn=lambda n: "error: something went wrong")
    r = claude_bridge_server.run(WORKDIR, "p", "task-nonzero-exit", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "failed"
    assert r["ledger_entry"]["error"] == "json-decode-failed"
    assert fake.call_count == 1


def test_non_zero_exit_with_valid_error_telemetry_is_still_ok_outcome(state_dir):
    """When Claude CLI exits non-zero but still emits parseable telemetry JSON with
    is_error=True (the real CLI's --output-format json contract), the bridge trusts the
    telemetry's is_error field rather than inspecting the process returncode directly.
    This pins that existing, documented behavior.
    """
    telemetry = make_ok_telemetry(is_error=True, subtype="error_during_execution")
    fake = FakeSubprocess(stdout_fn=lambda n: json.dumps(telemetry))
    r = claude_bridge_server.run(
        WORKDIR, "p", "task-nonzero-exit-telemetry", state_dir=state_dir, _subprocess_run=fake
    )
    assert r["outcome"] == "ok"
    assert r["ledger_entry"]["is_error"] is True


# --- budget invariants (already-preserved behavior, pinned explicitly) -----


def test_budget_invariants_pinned():
    assert claude_bridge_server.CALL_BUDGET_THRESHOLD == 4
    assert claude_bridge_server.CALL_TAGS == {
        1: ["normal"],
        2: ["normal"],
        3: ["exceptional", "budget-warning"],
    }
