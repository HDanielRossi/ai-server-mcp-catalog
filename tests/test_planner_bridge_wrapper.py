"""Regression tests for the repo-only planner-bridge wrapper.

These tests never invoke the real Hermes planner/model. Most substitute a
harmless capture executable through PLANNER_CODEX_PYTHON so argv and stdin
transport can be inspected without making a model call. Explicit context
files live beneath the repository's already-ignored tmp/ directory and are
always cleaned up.
"""

import ast
import hashlib
import re
import os
import shutil
import stat
import subprocess
import uuid

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WRAPPER_PATH = os.path.join(REPO_ROOT, "scripts", "planner-bridge")
TMP_ROOT = os.path.join(REPO_ROOT, "tmp", "planner-bridge-wrapper-tests")

FAKE_PLANNER_CODEX = """#!/usr/bin/env bash
printf '%s\\0' "$@" > "$CAPTURE_FILE"
cat > "${CAPTURE_FILE}.stdin"
echo "FAKE_PLANNER_CODEX_CALLED"
exit 0
"""


@pytest.fixture
def context_dir():
    os.makedirs(TMP_ROOT, exist_ok=True)
    d = os.path.join(TMP_ROOT, uuid.uuid4().hex)
    os.makedirs(d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp_root():
    yield
    if os.path.isdir(TMP_ROOT) and not os.listdir(TMP_ROOT):
        os.rmdir(TMP_ROOT)
    parent = os.path.dirname(TMP_ROOT)
    if os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)


@pytest.fixture
def fake_bin(tmp_path):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script_path = bin_dir / "planner-codex"
    script_path.write_text(FAKE_PLANNER_CODEX, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    capture_file = tmp_path / "capture.bin"
    return str(bin_dir), str(capture_file)


def _run_wrapper(prompt, context_files=None, fake_bin=None, workdir=REPO_ROOT, timeout=30):
    bin_dir, capture_file = fake_bin
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = capture_file
    # The production wrapper defaults to Hermes' venv Python. Tests substitute
    # this harmless capture executable so the real Hermes/planner is never run.
    env["PLANNER_CODEX_PYTHON"] = os.path.join(bin_dir, "planner-codex")

    argv = [WRAPPER_PATH, workdir, prompt]
    for cf in context_files or []:
        argv += ["--context-file", cf]

    result = subprocess.run(
        argv,
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result, capture_file


def _captured_argv(capture_file):
    if not os.path.exists(capture_file):
        return None
    with open(capture_file, "rb") as f:
        data = f.read()
    parts = data.decode("utf-8").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _captured_prompt(capture_file):
    path = capture_file + ".stdin"
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def _write_file(path, content_bytes):
    with open(path, "wb") as f:
        f.write(content_bytes)


def _filler_content(total_bytes, begin_sentinel, end_sentinel):
    assert total_bytes > len(begin_sentinel) + len(end_sentinel)
    middle_len = total_bytes - len(begin_sentinel) - len(end_sentinel)
    middle = (b"x" * middle_len)
    return begin_sentinel + middle + end_sentinel


def test_embedded_python_runner_is_syntactically_valid_and_applies_profile_first():
    with open(WRAPPER_PATH, "r", encoding="utf-8") as f:
        source = f.read()

    start_marker = "RUNNER_CODE='"
    end_marker = "\n'\n\nprintf '%s' \"$PROMPT\""

    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    runner_code = source[start:end]

    # The fake transport tests deliberately do not execute Hermes, so parse
    # the embedded Python itself as a regression guard.
    ast.parse(runner_code)

    profile_pos = runner_code.index(
        'sys.argv = ["hermes", "-p", "planner-codex"]'
    )
    import_pos = runner_code.index(
        "from hermes_cli.main import _run_and_exit_oneshot"
    )
    stdin_pos = runner_code.index("prompt = sys.stdin.read()")

    assert profile_pos < import_pos < stdin_pos


# test_legacy_two_argument_invocation_remains_supported
def test_legacy_two_argument_invocation_remains_supported(fake_bin):
    result, capture_file = _run_wrapper("plan the legacy thing", fake_bin=fake_bin)

    assert result.returncode == 0, result.stdout + result.stderr
    argv = _captured_argv(capture_file)
    assert argv is not None, "fake planner-codex was not invoked"

    prompt = _captured_prompt(capture_file)
    assert "User planning request:" in prompt
    assert "plan the legacy thing" in prompt
    # The automatic repository snapshot may legitimately contain the literal
    # marker names in documentation/tests. Detect an actual injected runtime
    # explicit-context block instead of searching for the marker alone.
    explicit_runtime_block = re.compile(
        r"===== EXPLICIT_CONTEXT_BEGIN =====\\n"
        r"path=[^\\n]+\\n"
        r"bytes=\\d+\\n"
        r"sha256=[0-9a-f]{64}\\n"
    )
    assert explicit_runtime_block.search(prompt) is None
    assert argv is not None
    assert argv[-1] == REPO_ROOT
    assert "-z" not in argv
    assert "--oneshot" not in argv


# test_explicit_untracked_context_is_delivered_complete
#
# Regression for the Linux MAX_ARG_STRLEN failure discovered during bootstrap:
# use the real repository as workdir so its normal automatic snapshot is added
# to a >100 KiB explicit context file. The assembled prompt must be delivered
# complete over stdin and must never become one giant argv element.
def test_explicit_untracked_context_is_delivered_complete(fake_bin, context_dir):
    begin_sentinel = b"===BEGIN_UNIQUE_SENTINEL_9f3a1c==="
    end_sentinel = b"===END_UNIQUE_SENTINEL_9f3a1c==="
    content = _filler_content(110_000, begin_sentinel, end_sentinel)

    file_path = os.path.join(context_dir, "big-untracked.txt")
    _write_file(file_path, content)
    rel_path = os.path.relpath(file_path, REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan with explicit context",
        context_files=[rel_path],
        fake_bin=fake_bin,
        workdir=REPO_ROOT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    argv = _captured_argv(capture_file)
    assert argv is not None
    prompt = _captured_prompt(capture_file)

    assert "===== EXPLICIT_CONTEXT_BEGIN =====" in prompt
    assert f"path={rel_path}" in prompt
    assert f"bytes={len(content)}" in prompt
    assert f"sha256={hashlib.sha256(content).hexdigest()}" in prompt
    assert content.decode("utf-8") in prompt
    assert "===== EXPLICIT_CONTEXT_END =====" in prompt


# test_multiple_context_files_preserve_order
def test_multiple_context_files_preserve_order(fake_bin, context_dir):
    markers = ["MARKER_AAA_111", "MARKER_BBB_222", "MARKER_CCC_333"]
    rel_paths = []
    for i, marker in enumerate(markers):
        file_path = os.path.join(context_dir, f"file_{i}.txt")
        _write_file(file_path, marker.encode("utf-8"))
        rel_paths.append(os.path.relpath(file_path, REPO_ROOT))

    result, capture_file = _run_wrapper(
        "plan with ordered context", context_files=rel_paths, fake_bin=fake_bin
    )

    assert result.returncode == 0, result.stdout + result.stderr
    argv = _captured_argv(capture_file)
    prompt = _captured_prompt(capture_file)

    positions = [prompt.index(marker) for marker in markers]
    assert positions == sorted(positions)


# test_context_file_cannot_escape_workdir
def test_context_file_cannot_escape_workdir(fake_bin):
    result, capture_file = _run_wrapper(
        "plan escape", context_files=["../../../../etc/passwd"], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file), "planner-codex must not run when validation fails"
    assert "escapes workdir" in (result.stdout + result.stderr)


# test_absolute_context_file_is_rejected
def test_absolute_context_file_is_rejected(fake_bin):
    result, capture_file = _run_wrapper(
        "plan absolute", context_files=["/etc/passwd"], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "must be relative" in (result.stdout + result.stderr)


# test_symlink_escape_is_rejected
def test_symlink_escape_is_rejected(fake_bin, context_dir, tmp_path):
    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("outside content", encoding="utf-8")

    link_path = os.path.join(context_dir, "escape-link.txt")
    os.symlink(str(outside_target), link_path)
    rel_path = os.path.relpath(link_path, REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan symlink escape", context_files=[rel_path], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "escapes workdir" in (result.stdout + result.stderr)


def test_symlink_within_workdir_is_permitted(fake_bin, context_dir):
    real_file = os.path.join(context_dir, "real.txt")
    _write_file(real_file, b"real content inside workdir")

    link_path = os.path.join(context_dir, "inside-link.txt")
    os.symlink(real_file, link_path)
    rel_path = os.path.relpath(link_path, REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan symlink inside", context_files=[rel_path], fake_bin=fake_bin
    )

    assert result.returncode == 0, result.stdout + result.stderr
    argv = _captured_argv(capture_file)
    assert argv is not None
    prompt = _captured_prompt(capture_file)
    assert prompt is not None
    assert "real content inside workdir" in prompt


# test_missing_context_file_is_rejected
def test_missing_context_file_is_rejected(fake_bin, context_dir):
    rel_path = os.path.relpath(os.path.join(context_dir, "does-not-exist.txt"), REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan missing", context_files=[rel_path], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "does not exist" in (result.stdout + result.stderr)


# test_directory_context_is_rejected
def test_directory_context_is_rejected(fake_bin, context_dir):
    subdir = os.path.join(context_dir, "a-directory")
    os.makedirs(subdir)
    rel_path = os.path.relpath(subdir, REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan directory", context_files=[rel_path], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "directory" in (result.stdout + result.stderr)


# test_context_file_over_256k_is_rejected
def test_context_file_over_256k_is_rejected(fake_bin, context_dir):
    oversized = os.path.join(context_dir, "oversized.txt")
    _write_file(oversized, b"y" * (262144 + 1))
    rel_path = os.path.relpath(oversized, REPO_ROOT)

    result, capture_file = _run_wrapper(
        "plan oversized", context_files=[rel_path], fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "exceeds" in (result.stdout + result.stderr)


# test_more_than_12_context_files_is_rejected
def test_more_than_12_context_files_is_rejected(fake_bin, context_dir):
    rel_paths = []
    for i in range(13):
        file_path = os.path.join(context_dir, f"small_{i}.txt")
        _write_file(file_path, f"content {i}".encode("utf-8"))
        rel_paths.append(os.path.relpath(file_path, REPO_ROOT))

    result, capture_file = _run_wrapper(
        "plan too many", context_files=rel_paths, fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "Too many" in (result.stdout + result.stderr)


# test_context_total_over_512k_is_rejected
def test_context_total_over_512k_is_rejected(fake_bin, context_dir):
    rel_paths = []
    for i in range(3):
        file_path = os.path.join(context_dir, f"chunk_{i}.txt")
        _write_file(file_path, b"c" * 200_000)
        rel_paths.append(os.path.relpath(file_path, REPO_ROOT))

    result, capture_file = _run_wrapper(
        "plan total over limit", context_files=rel_paths, fake_bin=fake_bin
    )

    assert result.returncode != 0
    assert not os.path.exists(capture_file)
    assert "exceed total" in (result.stdout + result.stderr)
