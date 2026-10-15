"""S0.1b shadow routing: pure rule tests plus controller-hook isolation tests.

The hook is observe-only: with or without --shadow-* inputs, and even when
the hook itself fails, the `hermes kanban create` argv, stdout, and exit
code of the controller must be byte-identical.
"""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys

import pytest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "hermes-pipeline-controller.py"
)
spec = importlib.util.spec_from_file_location("hpc_shadow", MODULE_PATH)
hpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hpc)

SECRET_BODY = "SECRET-BODY-do-not-leak sk-test-123"


def eligible(**overrides):
    features = {
        "declared_paths": ["src/app.py", "tests/test_app.py"],
        "components": ["repository-source", "tests"],
        "mandatory_block": False,
        "actual_assignee": "coder-claude",
        "workdir": "/opt/ai/projects/example-repo",
    }
    features.update(overrides)
    return features


def codes(record):
    return [r["code"] for r in record["reasons"]]


# --- pure rule ---

def test_eligible_scope_is_qwen_candidate():
    record = hpc.shadow_route(eligible())
    assert record["schema"] == "system-one.shadow-route/v1"
    assert record["decision"] == "QWEN_CANDIDATE"
    assert record["actual_assignee"] == "coder-claude"
    assert record["reasons"][0] == {"type": "rule", "code": "NARROW_ELIGIBLE_SCOPE"}
    assert record["scope"] == {"path_count": 2, "component_count": 2}
    assert record["features"] == {
        "declared_paths": True, "components": True, "mandatory_block": True,
        "mandatory_block_reason": False, "actual_assignee": True,
    }


def test_eligible_at_exact_limits_with_documentation():
    paths = ["docs/p%d.md" % i for i in range(8)]
    record = hpc.shadow_route(eligible(declared_paths=paths, components=["documentation"]))
    assert record["decision"] == "QWEN_CANDIDATE"


def test_rule_is_deterministic():
    assert hpc.shadow_route(eligible()) == hpc.shadow_route(eligible())


def test_mandatory_block_wins_over_eligible_scope():
    record = hpc.shadow_route(eligible(mandatory_block=True, mandatory_block_reason="SECURITY_SENSITIVE"))
    assert record["decision"] == "CLAUDE_REQUIRED"
    assert record["reasons"][0] == {"type": "rule", "code": "MANDATORY_BLOCK", "value": "SECURITY_SENSITIVE"}


def test_mandatory_block_does_not_need_scope():
    record = hpc.shadow_route({"mandatory_block": True, "actual_assignee": "coder-claude"})
    assert record["decision"] == "CLAUDE_REQUIRED"


def test_mandatory_block_reason_without_block_is_unknown():
    record = hpc.shadow_route(eligible(mandatory_block_reason="SECURITY_SENSITIVE"))
    assert record["decision"] == "UNKNOWN"
    assert "MANDATORY_BLOCK_REASON_WITHOUT_BLOCK" in codes(record)


@pytest.mark.parametrize("missing, code", [
    ("declared_paths", "DECLARED_PATHS_MISSING"),
    ("components", "COMPONENTS_MISSING"),
    ("mandatory_block", "MANDATORY_BLOCK_MISSING"),
    ("actual_assignee", "ACTUAL_ASSIGNEE_MISSING"),
])
def test_missing_required_feature_is_unknown(missing, code):
    features = eligible()
    del features[missing]
    record = hpc.shadow_route(features)
    assert record["decision"] == "UNKNOWN"
    assert {"type": "missing_feature", "code": code} in record["reasons"]
    assert record["features"][missing] is False


def test_empty_feature_lists_are_missing():
    record = hpc.shadow_route(eligible(declared_paths=[], components=[]))
    assert record["decision"] == "UNKNOWN"
    assert {"DECLARED_PATHS_MISSING", "COMPONENTS_MISSING"} <= set(codes(record))


@pytest.mark.parametrize("value", ["false", 0, 1, "true"])
def test_non_boolean_mandatory_block_is_unknown(value):
    record = hpc.shadow_route(eligible(mandatory_block=value))
    assert record["decision"] == "UNKNOWN"
    assert "MANDATORY_BLOCK_INVALID" in codes(record)


@pytest.mark.parametrize("bad_path, code", [
    ("/etc/passwd", "PATH_ABSOLUTE"),
    ("~/x", "PATH_ABSOLUTE"),
    ("C:/x", "PATH_ABSOLUTE"),
    ("src/../../etc", "PATH_PARENT_TRAVERSAL"),
    ("..", "PATH_PARENT_TRAVERSAL"),
    ("src\\app.py", "PATH_NONCANONICAL"),
    ("src/a\nb", "PATH_NONCANONICAL"),
    ("", "PATH_EMPTY"),
    ("./", "PATH_REPOSITORY_ROOT"),
    (7, "PATH_NOT_STRING"),
])
def test_noncanonical_scope_is_unknown(bad_path, code):
    record = hpc.shadow_route(eligible(declared_paths=["src/app.py", bad_path]))
    assert record["decision"] == "UNKNOWN"
    assert {"type": "invalid_feature", "code": code, "index": 1} in record["reasons"]
    assert record["scope"]["path_count"] is None


def test_paths_are_canonicalized_and_deduplicated():
    record = hpc.shadow_route(eligible(declared_paths=["src/app.py", "./src//app.py", "src/app.py/"]))
    assert record["decision"] == "QWEN_CANDIDATE"
    assert record["scope"]["path_count"] == 1


def test_too_many_paths_is_unknown():
    record = hpc.shadow_route(eligible(declared_paths=["src/f%d.py" % i for i in range(9)]))
    assert record["decision"] == "UNKNOWN"
    assert "PATH_COUNT_EXCEEDED" in codes(record)


def test_too_many_or_ineligible_components_is_unknown():
    record = hpc.shadow_route(eligible(components=["repository-source", "tests", "documentation"]))
    assert record["decision"] == "UNKNOWN"
    assert "COMPONENT_COUNT_EXCEEDED" in codes(record)
    record = hpc.shadow_route(eligible(components=["infrastructure"]))
    assert record["decision"] == "UNKNOWN"
    assert "COMPONENT_NOT_ELIGIBLE" in codes(record)
    record = hpc.shadow_route(eligible(components=["Bad Component"]))
    assert record["decision"] == "UNKNOWN"
    assert "COMPONENT_INVALID" in codes(record)


def test_record_redacts_body_paths_and_full_workdir():
    record = hpc.shadow_route(eligible(declared_paths=["src/secret_module_name.py"]))
    serialized = json.dumps(record)
    assert record["body"] == "REDACTED"
    assert "secret_module_name" not in serialized
    assert "/opt/ai/projects" not in serialized
    assert record["workdir"] == {
        "basename": "example-repo",
        "sha256_prefix": hashlib.sha256(b"/opt/ai/projects/example-repo").hexdigest()[:12],
    }


def test_invalid_assignee_is_not_echoed():
    record = hpc.shadow_route(eligible(actual_assignee="Robert'); DROP"))
    assert record["decision"] == "UNKNOWN"
    assert record["actual_assignee"] is None
    assert "ACTUAL_ASSIGNEE_INVALID" in codes(record)


# --- controller hook ---

SHADOW_FLAGS = [
    "--shadow-declared-path", "src/app.py",
    "--shadow-declared-path", "tests/test_app.py",
    "--shadow-component", "repository-source",
    "--shadow-component", "tests",
    "--shadow-mandatory-block", "false",
]


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    repo = root / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setattr(hpc, "ALLOWED_ROOT", root.resolve())
    return str(repo.resolve())


def run_controller(monkeypatch, capsys, argv, review_show=None):
    calls = []

    def fake_run(args):
        calls.append(list(args))
        if args[1:3] == ["kanban", "show"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(review_show), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"id": "t_created"}), stderr="")

    monkeypatch.setattr(hpc, "run_hermes_command", fake_run)
    rc = hpc.main(argv)
    captured = capsys.readouterr()
    return rc, calls, captured.out, captured.err


def impl_argv(workdir, *extra):
    return ["create-implementation", "--workdir", workdir, "--feature", "widgets",
            "--body", SECRET_BODY, *extra]


def test_hook_emits_one_redacted_line_without_changing_behavior(workdir, monkeypatch, capsys):
    base_rc, base_calls, base_out, base_err = run_controller(monkeypatch, capsys, impl_argv(workdir))
    rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(workdir, *SHADOW_FLAGS))

    assert base_err == ""
    assert (rc, calls, out) == (base_rc, base_calls, base_out)
    lines = err.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["decision"] == "QWEN_CANDIDATE"
    assert record["actual_assignee"] == "coder-claude"
    assert record["workdir"]["basename"] == "repo"
    assert SECRET_BODY not in err
    assert "widgets" not in err
    assert workdir not in err


def test_hook_reports_mandatory_block_from_cli(workdir, monkeypatch, capsys):
    _rc, _calls, _out, err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--shadow-mandatory-block", "true",
        "--shadow-mandatory-block-reason", "CROSS_REPOSITORY",
    ))
    record = json.loads(err)
    assert record["decision"] == "CLAUDE_REQUIRED"


def test_hook_partial_input_is_unknown(workdir, monkeypatch, capsys):
    _rc, _calls, _out, err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--shadow-declared-path", "/abs/path",
    ))
    record = json.loads(err)
    assert record["decision"] == "UNKNOWN"
    assert record["features"]["mandatory_block"] is False


def broken_rule(_features):
    raise RuntimeError("shadow rule exploded")


class BrokenStderr(io.StringIO):
    def write(self, _data):
        raise BrokenPipeError("stderr closed")


@pytest.mark.parametrize("failure", ["rule", "stderr"])
def test_hook_failure_is_swallowed_and_changes_nothing(workdir, monkeypatch, capsys, failure):
    base = run_controller(monkeypatch, capsys, impl_argv(workdir))
    with monkeypatch.context() as patch:
        if failure == "rule":
            patch.setattr(hpc, "shadow_route", broken_rule)
        else:
            patch.setattr(sys, "stderr", BrokenStderr())
        rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(workdir, *SHADOW_FLAGS))
    assert (rc, calls, out) == base[:3]
    assert err == ""


def test_hook_receives_immutable_argv(workdir, monkeypatch, capsys):
    seen = []
    real_hook = hpc._shadow_route_hook

    def spy(args, argv, resolved):
        seen.append(type(argv))
        real_hook(args, argv, resolved)

    monkeypatch.setattr(hpc, "_shadow_route_hook", spy)
    run_controller(monkeypatch, capsys, impl_argv(workdir, *SHADOW_FLAGS))
    assert seen == [tuple]


def test_correction_hook_preserves_behavior(workdir, monkeypatch, capsys):
    review_show = {
        "task": {"id": "t_review1", "status": "done"},
        "latest_summary": None, "parents": [], "children": [], "comments": [],
        "events": [], "runs": [{"id": 1, "status": "done", "outcome": "completed",
                                "summary": "CHANGES REQUIRED",
                                "metadata": {"verdict": "CHANGES REQUIRED"}}],
    }
    argv = ["create-correction", "--workdir", workdir, "--feature", "widgets",
            "--implementation_task_id", "t_impl1", "--review_task_id", "t_review1"]
    base = run_controller(monkeypatch, capsys, argv, review_show)
    assert base[0] == hpc.EXIT_OK, base
    rc, calls, out, err = run_controller(monkeypatch, capsys, argv + SHADOW_FLAGS, review_show)
    assert (rc, calls, out) == base[:3]
    assert json.loads(err)["decision"] == "QWEN_CANDIDATE"
