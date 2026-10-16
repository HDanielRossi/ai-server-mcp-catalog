"""S0.1b shadow routing: pure rule tests plus controller-hook isolation tests.

The hook is observe-only: with or without well-formed --shadow-* inputs, and
even when the hook itself fails, the `hermes kanban create` argv, stdout, and
exit code of the controller must be byte-identical. Malformed shadow argv
(missing value, option-like dash-leading value, abbreviated flag) is an
ordinary parser usage error (exit 3, no Kanban command), tested below.
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


@pytest.mark.parametrize("assignee, code", [
    (None, "ACTUAL_ASSIGNEE_MISSING"),
    ("Robert'); DROP", "ACTUAL_ASSIGNEE_INVALID"),
])
def test_mandatory_block_wins_over_missing_or_invalid_assignee(assignee, code):
    record = hpc.shadow_route(eligible(
        actual_assignee=assignee, mandatory_block=True, mandatory_block_reason="CROSS_REPOSITORY",
        declared_paths=["/abs"],
    ))
    assert record["decision"] == "CLAUDE_REQUIRED"
    assert record["reasons"][0] == {"type": "rule", "code": "MANDATORY_BLOCK", "value": "CROSS_REPOSITORY"}
    assert code in codes(record)
    assert "PATH_ABSOLUTE" in codes(record)
    assert record["actual_assignee"] is None


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
    (".git/config", "PATH_SENSITIVE"),
    (".github/workflows/deploy.yml", "PATH_SENSITIVE"),
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


def test_workdir_is_pseudonymous_not_irreversible():
    # Documented limitation: a known or guessed path is confirmable from the record.
    guess = "/opt/ai/projects/example-repo"
    record = hpc.shadow_route(eligible(workdir=guess))
    assert record["workdir"]["sha256_prefix"] == hashlib.sha256(guess.encode()).hexdigest()[:12]


# --- record size bounds ---

def test_per_index_reasons_are_capped_with_typed_truncation():
    record = hpc.shadow_route(eligible(
        declared_paths=["/abs%d" % i for i in range(50)],
        components=["Bad %d" % i for i in range(30)],
    ))
    path_reasons = [r for r in record["reasons"] if r["code"] == "PATH_ABSOLUTE"]
    component_reasons = [r for r in record["reasons"] if r["code"] == "COMPONENT_INVALID"]
    assert [r["index"] for r in path_reasons] == [0, 1, 2, 3]
    assert [r["index"] for r in component_reasons] == [0, 1, 2, 3]
    assert {"type": "truncated", "code": "PATH_REASONS_TRUNCATED", "omitted": 46} in record["reasons"]
    assert {"type": "truncated", "code": "COMPONENT_REASONS_TRUNCATED", "omitted": 26} in record["reasons"]
    assert record["decision"] == "UNKNOWN"


def test_no_truncation_reason_at_exact_cap():
    record = hpc.shadow_route(eligible(declared_paths=["/abs%d" % i for i in range(4)]))
    assert "PATH_REASONS_TRUNCATED" not in codes(record)


def worst_case_features():
    return {
        "declared_paths": ["/abs"] * 10_000,
        "components": ["X"] * 10_000,
        "mandatory_block": True,
        "mandatory_block_reason": "Z" * 10_000,
        "actual_assignee": "a" * 10_000,
        "workdir": "/opt/ai/projects/" + "é" * 255,
    }


def test_worst_case_line_is_bounded_and_deterministic():
    record = hpc.shadow_route(worst_case_features())
    line = hpc.shadow_route_line(record)
    assert line is not None and line.endswith("\n") and line.count("\n") == 1
    assert len(line.encode("utf-8")) <= hpc.SHADOW_MAX_LINE_BYTES < 4000
    parsed = json.loads(line)
    assert parsed["decision"] == "CLAUDE_REQUIRED"
    assert line == hpc.shadow_route_line(hpc.shadow_route(worst_case_features()))


def test_oversized_line_truncates_reasons_then_basename(monkeypatch):
    record = hpc.shadow_route(eligible(
        mandatory_block=True,
        declared_paths=["/abs"] * 10,
        components=["X"] * 10,
        workdir="/p/" + "b" * 255,
    ))
    full = hpc._shadow_dumps(record)
    monkeypatch.setattr(hpc, "SHADOW_MAX_LINE_BYTES", len(full) - 1)
    parsed = json.loads(hpc.shadow_route_line(record))
    assert parsed["reasons"][0]["code"] == "MANDATORY_BLOCK"
    assert parsed["reasons"][-1]["type"] == "truncated"
    assert parsed["reasons"][-1]["code"] == "RECORD_REASONS_TRUNCATED"
    assert len(parsed["reasons"]) - 1 + parsed["reasons"][-1]["omitted"] == len(record["reasons"])
    assert parsed["reasons"][:-1] == record["reasons"][:len(parsed["reasons"]) - 1]
    assert parsed["workdir"]["basename"] == "b" * 255

    # Only the rule plus the truncation marker left: the basename goes next.
    minimal = dict(record, reasons=record["reasons"][:1] + [
        {"type": "truncated", "code": "RECORD_REASONS_TRUNCATED", "omitted": len(record["reasons"]) - 1}])
    monkeypatch.setattr(hpc, "SHADOW_MAX_LINE_BYTES", len(hpc._shadow_dumps(minimal)) - 1)
    parsed = json.loads(hpc.shadow_route_line(record))
    assert parsed["workdir"]["basename"] is None
    assert parsed["workdir"]["sha256_prefix"] == record["workdir"]["sha256_prefix"]
    assert len(record["reasons"]) > 2  # input record is not mutated

    monkeypatch.setattr(hpc, "SHADOW_MAX_LINE_BYTES", 10)
    assert hpc.shadow_route_line(record) is None


def test_small_record_line_is_unchanged():
    record = hpc.shadow_route(eligible())
    assert hpc.shadow_route_line(record) == json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


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


def test_explicit_qwen_canary_uses_qwen_only_for_eligible_scope(workdir, monkeypatch, capsys):
    rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--system-one-qwen-canary=true", *SHADOW_FLAGS,
    ))
    assert rc == hpc.EXIT_OK
    create = calls[0]
    assert create[create.index("--assignee") + 1] == hpc.SYSTEM_ONE_QWEN_PROFILE
    body = create[create.index("--body") + 1]
    assert hpc.QWEN_CANARY_SCOPE_MARKER in body
    assert "- src/app.py" in body
    assert "- tests/test_app.py" in body
    record = json.loads(err)
    assert record["decision"] == "QWEN_CANDIDATE"
    assert record["actual_assignee"] == hpc.SYSTEM_ONE_QWEN_PROFILE
    assert json.loads(out)["idempotency_key"] == hpc.stable_key(
        workdir, "widgets", "implementation:qwen-canary",
    )


def test_explicit_false_qwen_canary_keeps_claude_for_eligible_scope(workdir, monkeypatch, capsys):
    rc, calls, out, _err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--system-one-qwen-canary=false", *SHADOW_FLAGS,
    ))
    assert rc == hpc.EXIT_OK
    create = calls[0]
    assert create[create.index("--assignee") + 1] == hpc.DEFAULT_IMPLEMENTATION_PROFILE
    assert json.loads(out)["idempotency_key"] == hpc.stable_key(workdir, "widgets", "implementation")


@pytest.mark.parametrize("extra", [
    ["--system-one-qwen-canary=true"],
    ["--system-one-qwen-canary=true", "--shadow-mandatory-block=true"],
    ["--system-one-qwen-canary=true", *SHADOW_FLAGS, "--shadow-component=deployment"],
    ["--system-one-qwen-canary=true", *SHADOW_FLAGS,
     *["--shadow-declared-path=src/extra%d.py" % n for n in range(7)]],
    ["--system-one-qwen-canary=true", *SHADOW_FLAGS, "--shadow-mandatory-block=yes"],
])
def test_qwen_canary_fails_closed_to_claude_without_eligible_scope(workdir, monkeypatch, capsys, extra):
    rc, calls, out, _err = run_controller(monkeypatch, capsys, impl_argv(workdir, *extra))
    assert rc == hpc.EXIT_OK
    create = calls[0]
    assert create[create.index("--assignee") + 1] == hpc.DEFAULT_IMPLEMENTATION_PROFILE
    assert json.loads(out)["idempotency_key"] == hpc.stable_key(workdir, "widgets", "implementation")


def test_qwen_canary_shadow_rule_exception_fails_closed_to_claude(workdir, monkeypatch, capsys):
    monkeypatch.setattr(hpc, "shadow_route", broken_rule)
    rc, calls, out, _err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--system-one-qwen-canary=true", *SHADOW_FLAGS,
    ))
    assert rc == hpc.EXIT_OK
    create = calls[0]
    assert create[create.index("--assignee") + 1] == hpc.DEFAULT_IMPLEMENTATION_PROFILE
    assert json.loads(out)["idempotency_key"] == hpc.stable_key(workdir, "widgets", "implementation")


@pytest.mark.parametrize("raw", ["TRUE", "yes", "1", ""])
def test_qwen_canary_requires_exact_boolean_literal(raw):
    with pytest.raises(hpc.CliUsageError):
        hpc.build_parser().parse_args([
            "create-implementation", "--workdir", "/w", "--feature", "f",
            "--system-one-qwen-canary=" + raw,
        ])


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


def test_hook_line_is_bounded_end_to_end(workdir, monkeypatch, capsys):
    extra = []
    for i in range(200):
        extra += ["--shadow-declared-path", "/abs/%d" % i, "--shadow-component", "Bad%d" % i]
    base = run_controller(monkeypatch, capsys, impl_argv(workdir))
    rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(workdir, *extra))
    assert (rc, calls, out) == base[:3]
    assert err.count("\n") == 1
    assert len(err.encode("utf-8")) <= hpc.SHADOW_MAX_LINE_BYTES
    assert json.loads(err)["decision"] == "UNKNOWN"


def test_hook_mandatory_block_with_dash_leading_equals_value(workdir, monkeypatch, capsys):
    base = run_controller(monkeypatch, capsys, impl_argv(workdir))
    rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(
        workdir, "--shadow-mandatory-block", "true", "--shadow-declared-path=-x",
    ))
    assert (rc, calls, out) == base[:3]
    record = json.loads(err)
    assert record["decision"] == "CLAUDE_REQUIRED"
    assert record["scope"]["path_count"] == 1


# --- parser contract for malformed shadow argv ---

@pytest.mark.parametrize("command", ["create-implementation", "create-correction"])
@pytest.mark.parametrize("shadow_argv", [
    # missing value
    ["--shadow-declared-path"],
    ["--shadow-component"],
    ["--shadow-mandatory-block"],
    ["--shadow-mandatory-block-reason"],
    ["--shadow-component", "--shadow-mandatory-block", "true"],
    # option-like dash-leading value in separate-argument form
    ["--shadow-declared-path", "-x"],
    ["--shadow-component", "--tests"],
    ["--shadow-mandatory-block-reason", "-R"],
    # abbreviated shadow flags
    ["--shadow-decl", "src/a.py"],
    ["--shadow-comp", "tests"],
    ["--shadow-comp=tests"],
    ["--shadow-mandatory-block-r", "X_Y"],
    ["--shadow", "x"],
])
def test_malformed_shadow_argv_is_parser_usage_error(command, shadow_argv):
    required = {
        "create-implementation": ["--workdir", "/w", "--feature", "f"],
        "create-correction": ["--workdir", "/w", "--feature", "f",
                              "--implementation_task_id", "t1", "--review_task_id", "t2"],
    }[command]
    with pytest.raises(hpc.CliUsageError):
        hpc.build_parser().parse_args([command, *required, *shadow_argv])


def test_malformed_shadow_argv_exits_3_without_kanban_call(workdir, monkeypatch, capsys):
    rc, calls, out, err = run_controller(monkeypatch, capsys, impl_argv(workdir, "--shadow-comp", "tests"))
    assert rc == hpc.EXIT_TRANSPORT == 3
    assert calls == []
    assert out == ""
    assert err.startswith("usage error: ")
    assert SECRET_BODY not in err


@pytest.mark.parametrize("shadow_argv, attr, value", [
    (["--shadow-declared-path=-x"], "shadow_declared_path", ["-x"]),
    (["--shadow-declared-path", "-1"], "shadow_declared_path", ["-1"]),
    (["--shadow-declared-path="], "shadow_declared_path", [""]),
    (["--shadow-component", "a b"], "shadow_component", ["a b"]),
    (["--shadow-mandatory-block", "yes"], "shadow_mandatory_block", "yes"),
    (["--shadow-mandatory-block-reason=--X"], "shadow_mandatory_block_reason", "--X"),
])
def test_well_formed_shadow_values_are_never_rejected(shadow_argv, attr, value):
    args = hpc.build_parser().parse_args(
        ["create-implementation", "--workdir", "/w", "--feature", "f", *shadow_argv])
    assert getattr(args, attr) == value


def test_existing_option_abbreviations_are_unchanged():
    args = hpc.build_parser().parse_args(["create-implementation", "--work", "/w", "--feat", "f", "--bo", "b"])
    assert (args.workdir, args.feature, args.body) == ("/w", "f", "b")
    args = hpc.build_parser().parse_args([
        "create-correction", "--workdir", "/w", "--feature", "f",
        "--implementation", "t1", "--review_t", "t2", "--correction", "c",
    ])
    assert (args.implementation_task_id, args.review_task_id, args.correction_instructions) == ("t1", "t2", "c")


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
