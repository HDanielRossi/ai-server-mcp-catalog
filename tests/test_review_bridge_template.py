"""Tests for the repo-only review_bridge_server template, loaded by absolute path."""

import importlib.util
import os

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/review_bridge_server.py"

spec = importlib.util.spec_from_file_location("review_bridge_server", TEMPLATE_PATH)
review_bridge_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review_bridge_server)

ReviewBridgeError = review_bridge_server.ReviewBridgeError
collect_evidence = review_bridge_server.collect_evidence
normalize_changed_paths = review_bridge_server.normalize_changed_paths
validate_test_command = review_bridge_server.validate_test_command
validate_content_window = review_bridge_server.validate_content_window


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert review_bridge_server.__file__ == TEMPLATE_PATH


def test_defaults():
    assert review_bridge_server.DEFAULT_TEST_COMMAND == "__skip__"
    assert review_bridge_server.DEFAULT_CHANGED_PATHS == ""
    assert review_bridge_server.DEFAULT_INCLUDE_DIFF is False
    assert review_bridge_server.DEFAULT_INCLUDE_REPO_EVIDENCE is True
    assert review_bridge_server.MAX_CONTENT_WINDOW_LINES == 200

    result = collect_evidence()
    assert result["test_command"] == "__skip__"
    assert result["changed_paths"] == []
    assert result["include_diff"] is False
    assert result["include_repo_evidence"] is True


def test_allowlist_accepts_all_three_commands():
    for command in review_bridge_server.ALLOWED_TEST_COMMANDS:
        assert validate_test_command(command) == command


def test_allowlist_rejects_unknown_command():
    with pytest.raises(ReviewBridgeError):
        validate_test_command("rm -rf /")


def test_no_window_defaults():
    result = collect_evidence(changed_paths="a.py", content_window=None)
    assert result["content_window"] == "not-requested"
    assert result["file_content"] == "SKIPPED"


def test_window_valid_full_range_accepted():
    result = collect_evidence(
        changed_paths="a.py",
        content_window={"path": "a.py", "start_line": 1, "end_line": 200},
    )
    assert result["content_window"] == "requested:a.py:1-200"
    assert result["file_content"] == "window:a.py:1-200"


def test_window_invalid_zero_paths():
    with pytest.raises(ReviewBridgeError):
        validate_content_window([], {"path": "a.py", "start_line": 1, "end_line": 10})


def test_window_invalid_two_paths_in_one_window():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(
            ["a.py", "b.py"],
            {"path": ["a.py", "b.py"], "start_line": 1, "end_line": 10},
        )


def test_window_invalid_start_after_end():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "a.py", "start_line": 10, "end_line": 5})


def test_window_invalid_oversized():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "a.py", "start_line": 1, "end_line": 201})


def test_window_invalid_path_not_in_changed_paths():
    with pytest.raises(ReviewBridgeError):
        validate_content_window(["a.py"], {"path": "b.py", "start_line": 1, "end_line": 10})


def test_diff_invalid_without_changed_paths():
    with pytest.raises(ReviewBridgeError):
        collect_evidence(changed_paths="", include_diff=True)


def test_diff_false_without_paths_is_fine():
    result = collect_evidence(changed_paths="", include_diff=False)
    assert result["status"] == "ok"
    assert result["diff"] == "not-requested"


def test_paths_absolute_rejected():
    with pytest.raises(ReviewBridgeError):
        normalize_changed_paths("/etc/passwd")


def test_paths_dotdot_rejected():
    with pytest.raises(ReviewBridgeError):
        normalize_changed_paths("a/../b")


def test_paths_empty_string_normalizes_to_no_paths():
    assert normalize_changed_paths("") == []


def test_paths_comma_string_splits_and_strips():
    assert normalize_changed_paths("a.py, b.py") == ["a.py", "b.py"]


def test_skip_command_never_substituted():
    result = collect_evidence(test_command="__skip__")
    assert result["test_command"] == "__skip__"
