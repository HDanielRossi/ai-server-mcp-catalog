"""Tests for the repo-only claude_bridge_server template, loaded by absolute path."""

import importlib.util
import json
import os
import shutil
import tempfile
import threading

from types import SimpleNamespace

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/claude_bridge_server.py"

spec = importlib.util.spec_from_file_location("claude_bridge_server", TEMPLATE_PATH)
claude_bridge_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_bridge_server)

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
    r1 = claude_bridge_server.run(WORKDIR, task_id, "prompt one", state_dir=state_dir, _subprocess_run=fake)
    r2 = claude_bridge_server.run(WORKDIR, task_id, "prompt two", state_dir=state_dir, _subprocess_run=fake)

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
    claude_bridge_server.run(WORKDIR, task_id, "prompt one", state_dir=state_dir, _subprocess_run=fake)
    claude_bridge_server.run(WORKDIR, task_id, "prompt two", state_dir=state_dir, _subprocess_run=fake)
    r3 = claude_bridge_server.run(WORKDIR, task_id, "prompt three", state_dir=state_dir, _subprocess_run=fake)

    assert r3["call_number"] == 3
    assert sorted(r3["tags"]) == sorted(["exceptional", "budget-warning"])
    assert r3["outcome"] == "ok"


# 3. budget-4-plus
def test_budget_call_4_and_5_raise_without_reaching_subprocess(state_dir):
    fake = FakeSubprocess()
    task_id = "task-four"
    for i in range(3):
        claude_bridge_server.run(WORKDIR, task_id, f"prompt {i}", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, task_id, "prompt four", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3

    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, task_id, "prompt five", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 3


# 4. budget-isolation
def test_budget_isolated_per_task_id(state_dir):
    fake = FakeSubprocess()
    r_a = claude_bridge_server.run(WORKDIR, "task-a", "p", state_dir=state_dir, _subprocess_run=fake)
    r_b = claude_bridge_server.run(WORKDIR, "task-b", "p", state_dir=state_dir, _subprocess_run=fake)

    assert r_a["call_number"] == 1
    assert r_b["call_number"] == 1
    assert r_a["tags"] == ["normal"]
    assert r_b["tags"] == ["normal"]


# 5. concurrency
def test_concurrency_same_call_number_not_double_admitted(state_dir):
    task_id = "task-concurrent"
    warmup_fake = FakeSubprocess()
    claude_bridge_server.run(WORKDIR, task_id, "warmup 1", state_dir=state_dir, _subprocess_run=warmup_fake)
    claude_bridge_server.run(WORKDIR, task_id, "warmup 2", state_dir=state_dir, _subprocess_run=warmup_fake)
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
                WORKDIR, task_id, f"prompt-{name}", state_dir=state_dir, _subprocess_run=fake
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
    claude_bridge_server.run(WORKDIR, task_id, "p", state_dir=state_dir, _subprocess_run=fake)

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
        claude_bridge_server.run(WORKDIR, "task-corrupt", "p", state_dir=state_dir, _subprocess_run=fake)

    with open(ledger_path, "r", encoding="utf-8") as f:
        after = f.read()
    assert after == content
    assert fake.call_count == 0


# 8. failed-call-consumes
def test_failed_call_still_consumes_call_number(state_dir):
    task_id = "task-failed"

    fake1 = FakeSubprocess(raise_exc=RuntimeError("boom"))
    r1 = claude_bridge_server.run(WORKDIR, task_id, "p1", state_dir=state_dir, _subprocess_run=fake1)
    assert r1["outcome"] == "failed"
    assert r1["call_number"] == 1

    fake2 = FakeSubprocess(stdout_fn=lambda n: "not json")
    r2 = claude_bridge_server.run(WORKDIR, task_id, "p2", state_dir=state_dir, _subprocess_run=fake2)
    assert r2["outcome"] == "failed"
    assert r2["call_number"] == 2

    fake3 = FakeSubprocess(stdout_fn=lambda n: json.dumps({"duration_ms": 1}))
    r3 = claude_bridge_server.run(WORKDIR, task_id, "p3", state_dir=state_dir, _subprocess_run=fake3)
    assert r3["outcome"] == "failed"
    assert r3["call_number"] == 3

    fake4 = FakeSubprocess()
    with pytest.raises(BudgetExhaustedError):
        claude_bridge_server.run(WORKDIR, task_id, "p4", state_dir=state_dir, _subprocess_run=fake4)
    assert fake4.call_count == 0


# 9. telemetry
def test_telemetry_all_required_fields_persisted(state_dir):
    task_id = "task-telemetry"
    telemetry = make_ok_telemetry(session_id="sess-xyz")
    telemetry["iterations"] = 4
    telemetry["modelUsage"] = {"model": {"input_tokens": 5}}
    fake = FakeSubprocess(stdout_fn=lambda n: json.dumps(telemetry))

    r = claude_bridge_server.run(WORKDIR, task_id, "p", state_dir=state_dir, _subprocess_run=fake)
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

    r = claude_bridge_server.run(WORKDIR, task_id, "p", state_dir=state_dir, _subprocess_run=fake)
    entry = r["ledger_entry"]

    assert entry["iterations"] is None
    assert entry["modelUsage"] is None


# 10. workdir
def test_workdir_projects_root_itself_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            claude_bridge_server.PROJECTS_ROOT, "task-w1", "p", state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_workdir_outside_projects_root_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run("/tmp/elsewhere", "task-w2", "p", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


def test_workdir_nonexistent_under_root_rejected(state_dir):
    fake = FakeSubprocess()
    nonexistent = os.path.join(claude_bridge_server.PROJECTS_ROOT, "definitely-does-not-exist-xyz-123")
    with pytest.raises(BridgeError):
        claude_bridge_server.run(nonexistent, "task-w3", "p", state_dir=state_dir, _subprocess_run=fake)
    assert fake.call_count == 0


def test_workdir_real_existing_repo_dir_accepted(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(WORKDIR, "task-w4", "p", state_dir=state_dir, _subprocess_run=fake)
    assert r["outcome"] == "ok"
    assert fake.call_count == 1


# 11. changed-paths
def test_changed_paths_absolute_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "task-cp1", "p", changed_paths=["/etc/passwd"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_dotdot_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "task-cp2", "p", changed_paths=["a/../b"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_escapes_workdir_rejected(state_dir):
    fake = FakeSubprocess()
    with pytest.raises(BridgeError):
        claude_bridge_server.run(
            WORKDIR, "task-cp3", "p", changed_paths=["../sibling"], state_dir=state_dir, _subprocess_run=fake
        )
    assert fake.call_count == 0


def test_changed_paths_safe_relative_accepted(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(
        WORKDIR,
        "task-cp4",
        "p",
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
    r = claude_bridge_server.run(WORKDIR, "task-argv", prompt, state_dir=state_dir, _subprocess_run=fake)

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
        WORKDIR, "task-budget-none", "p", max_budget_usd=None, state_dir=state_dir, _subprocess_run=fake
    )
    argv = r["argv"]
    assert "--max-budget-usd" not in argv
    assert "--max-tokens" not in argv
    assert "--token-cap" not in argv


def test_optional_budget_flag_present_when_set(state_dir):
    fake = FakeSubprocess()
    r = claude_bridge_server.run(
        WORKDIR, "task-budget-set", "p", max_budget_usd=1.5, state_dir=state_dir, _subprocess_run=fake
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
