"""Tests for the repo-only pipeline_bridge_server template, loaded by absolute path."""

import importlib.util
import os

import pytest

TEMPLATE_PATH = "/opt/ai/projects/ai-server-mcp-catalog/templates/pipeline_bridge_server.py"

spec = importlib.util.spec_from_file_location("pipeline_bridge_server", TEMPLATE_PATH)
pipeline_bridge_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline_bridge_server)

PipelineBridgeError = pipeline_bridge_server.PipelineBridgeError
stable_key = pipeline_bridge_server.stable_key
TaskBackend = pipeline_bridge_server.TaskBackend
create_implementation_task = pipeline_bridge_server.create_implementation_task
create_review_task = pipeline_bridge_server.create_review_task
create_correction_task = pipeline_bridge_server.create_correction_task
REVIEW_BODY_REQUIRED_SENTENCES = pipeline_bridge_server.REVIEW_BODY_REQUIRED_SENTENCES


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


def test_module_loaded_from_repo_path():
    assert os.path.isabs(TEMPLATE_PATH)
    assert pipeline_bridge_server.__file__ == TEMPLATE_PATH


# 1. implementation-key
def test_implementation_key_matches_stable_key():
    backend = FakeBackend()
    create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", "implementation")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key


# 2. review-key
def test_review_key_matches_stable_key_and_contains_implementation_id():
    backend = FakeBackend()
    impl_id = "task-impl-123"
    create_review_task(backend, "/opt/ai/projects/demo", "feature-x", impl_id, "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", f"review:{impl_id}")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key
    assert impl_id in expected_key


# 3. correction-key
def test_correction_key_matches_stable_key():
    backend = FakeBackend()
    review_id = "task-review-456"
    create_correction_task(backend, "/opt/ai/projects/demo", "feature-x", review_id, "must pass")

    expected_key = stable_key("/opt/ai/projects/demo", "feature-x", f"correction:{review_id}")
    assert len(backend.created) == 1
    assert backend.created[0]["key"] == expected_key


# 4. parent-validation
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


# 5. idempotency
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


# 6. review-body
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


# 7. backend-isolation
def test_only_fake_backend_used_no_network():
    import sys

    backend = FakeBackend()
    create_implementation_task(backend, "/opt/ai/projects/demo", "feature-x", "must pass")

    assert len(backend.created) == 1
    for network_module in ("socket", "http.client", "urllib.request", "requests"):
        assert network_module not in getattr(pipeline_bridge_server, "__dict__", {})
    assert not hasattr(pipeline_bridge_server, "requests")
    assert not hasattr(pipeline_bridge_server, "socket")


# 8. metadata-preservation
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
