"""Repo-only template for deterministic, idempotent pipeline task creation.

This module is a template: it is copied and installed by the operator into
its running location. It is pure logic with no side effects and imports
nothing that is network-dependent or live-bridge-dependent. Stdlib only
(hashlib, abc); no subprocess, no sockets, no HTTP clients, no real
Kanban/bridge import.

Task creation is idempotent: the same (workdir, feature, role) always maps
to the same key, and TaskBackend.existing_id_for_key is always consulted
before TaskBackend.create_task is called.
"""

import hashlib
from abc import ABC, abstractmethod


REVIEW_BODY_REQUIRED_SENTENCES = (
    "If test_command is __skip__, do not invent or substitute another command",
    "If tests are required by the acceptance criteria but no valid explicit test command is available, "
    "block the task instead of guessing",
)


class PipelineBridgeError(Exception):
    """Raised when pipeline task creation inputs fail validation."""


def stable_key(workdir, feature, role):
    """Return a deterministic idempotency key for the (workdir, feature, role) tuple.

    Pure function of its three arguments: identical inputs always produce
    the identical key.
    """
    if not workdir:
        raise PipelineBridgeError("workdir must be non-empty")
    if not feature:
        raise PipelineBridgeError("feature must be non-empty")

    payload = repr((workdir, feature, role))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"key:{digest}:{workdir}|{feature}|{role}"


class TaskBackend(ABC):
    """Injection seam for task creation. The template never contacts a real Kanban."""

    @abstractmethod
    def existing_id_for_key(self, key):
        """Return the existing task id for key, or None if no task exists yet."""
        raise NotImplementedError

    @abstractmethod
    def create_task(self, key, title, body, metadata):
        """Create a task and return its id."""
        raise NotImplementedError


def _common_metadata(role, workdir, feature, acceptance_criteria, changed_paths, test_command):
    return {
        "workdir": workdir,
        "feature": feature,
        "role": role,
        "acceptance_criteria": acceptance_criteria,
        "changed_paths": changed_paths,
        "test_command": test_command,
    }


def _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command):
    return [
        f"workdir: {workdir}",
        f"feature: {feature}",
        f"changed_paths: {changed_paths!r}",
        f"test_command: {test_command!r}",
        "acceptance_criteria:",
        str(acceptance_criteria),
    ]


def create_implementation_task(
    backend, workdir, feature, acceptance_criteria, changed_paths=None, test_command=None
):
    """Idempotently create (or return the existing id for) the implementation task."""
    key = stable_key(workdir, feature, "implementation")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Implementation: {feature}"
    body_lines = [f"Implementation task for feature {feature!r} in {workdir}."]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body = "\n".join(body_lines)
    metadata = _common_metadata("implementation", workdir, feature, acceptance_criteria, changed_paths, test_command)
    return backend.create_task(key, title, body, metadata)


def create_review_task(
    backend,
    workdir,
    feature,
    implementation_task_id,
    acceptance_criteria,
    changed_paths=None,
    test_command=None,
):
    """Idempotently create (or return the existing id for) the review task.

    Rejects if implementation_task_id is missing or empty: a review task
    must always be anchored to a specific implementation task.
    """
    if not implementation_task_id:
        raise PipelineBridgeError("implementation_task_id must be non-empty")

    key = stable_key(workdir, feature, f"review:{implementation_task_id}")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Review: {feature}"
    body_lines = [
        f"Review for implementation task {implementation_task_id}.",
        f"implementation_task_id: {implementation_task_id}",
    ]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body_lines.append("")
    body_lines.extend(REVIEW_BODY_REQUIRED_SENTENCES)
    body = "\n".join(body_lines)

    metadata = _common_metadata("review", workdir, feature, acceptance_criteria, changed_paths, test_command)
    metadata["implementation_task_id"] = implementation_task_id
    return backend.create_task(key, title, body, metadata)


def create_correction_task(
    backend,
    workdir,
    feature,
    review_task_id,
    acceptance_criteria,
    changed_paths=None,
    test_command=None,
):
    """Idempotently create (or return the existing id for) the correction task.

    Rejects if review_task_id is missing or empty: a correction task must
    always be anchored to the review task that requested it.
    """
    if not review_task_id:
        raise PipelineBridgeError("review_task_id must be non-empty")

    key = stable_key(workdir, feature, f"correction:{review_task_id}")
    existing_id = backend.existing_id_for_key(key)
    if existing_id is not None:
        return existing_id

    title = f"Correction: {feature}"
    body_lines = [
        f"Correction for review task {review_task_id}.",
        f"review_task_id: {review_task_id}",
    ]
    body_lines += _common_body_lines(workdir, feature, acceptance_criteria, changed_paths, test_command)
    body = "\n".join(body_lines)

    metadata = _common_metadata("correction", workdir, feature, acceptance_criteria, changed_paths, test_command)
    metadata["review_task_id"] = review_task_id
    return backend.create_task(key, title, body, metadata)
