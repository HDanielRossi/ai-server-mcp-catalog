# Hermes Production Repository Onboarding

## Purpose

This document defines the minimum process before allowing the Hermes development pipeline to work on a production or critical repository.

The goal is to preserve safety, reviewability, and rollback capability.

## Minimum requirements before first task

Before using Hermes on a real repository, verify:

- The repository is inside /opt/ai/projects.
- The working tree is clean.
- There is a recent commit before Hermes starts.
- Work happens on a dedicated branch.
- The test command is known and works before changes.
- The allowed files or directories are explicitly defined.
- The first task is small and reversible.
- default only creates implementation tasks and stops.
- coder-claude implements through claude_bridge.
- reviewer reviews through review_bridge.
- No automatic push is allowed.
- No reset, revert, merge, rebase, force-push, dependency install, or history rewrite is allowed unless explicitly authorized.

## Per-task production checklist

- confirmar branch dedicada
- confirmar working tree limpio antes de empezar
- confirmar contrato Hermes del repo
- confirmar archivos permitidos
- confirmar comando de test
- confirmar que default sólo crea implementation task y se detiene
- confirmar review obligatoria antes de commit
- confirmar que no hay push automático

## Recommended branch naming

Use a dedicated branch per Hermes task:

    hermes/<short-feature-name>

Example:

    git checkout -b hermes/add-health-check

## Repository contract

Every serious repository should have a Hermes contract file before running tasks.

Recommended path inside the target repo:

    .ai/hermes/repo-contract.md

Preflight v2 requires `.ai/hermes/repo-contract.md` before using Hermes in a repository. Preflight fails if the file is missing or does not contain every required section.

The contract must contain these required sections:

- Repository identity
- Allowed paths
- Forbidden paths
- Test command
- Review requirements
- Commit policy
- Push policy
- Rollback procedure

Use this template from the catalog:

    templates/hermes-repo-contract.md

## Safe first task pattern

The first task in a production-like repository should be intentionally small.

Good examples:

- Add one test for existing behavior.
- Add one small endpoint with a test.
- Add one validation helper with tests.
- Add one README clarification.
- Refactor one tiny function with tests.

Avoid as first task:

- Large refactors.
- Authentication changes.
- Database migrations.
- Docker, network, or security changes.
- Payment logic.
- Production deployment changes.
- Secrets or credential handling.
- Multi-file architecture changes.

## Pipeline flow for production repositories

Expected flow:

1. User requests task.
2. default calls planner_bridge.
3. default calls pipeline_bridge to create implementation task.
4. default stops and reports implementation task ID.
5. coder-claude implements through claude_bridge.
6. User verifies implementation task completed.
7. default or user creates review task through pipeline_bridge.
8. reviewer reviews through review_bridge.
9. If PASS, user commits.
10. If CHANGES REQUIRED, default creates correction task.
11. reviewer reviews correction.
12. User commits only after final PASS.

## Hard stop rules

Stop immediately if any of the following happens:

- default calls review_bridge directly.
- default creates review or correction before implementation is complete.
- reviewer uses direct terminal, file, or code execution.
- reviewer completes without mcp__review_bridge__collect.
- coder-claude edits files outside the authorized scope.
- Git status shows unexpected files.
- Tests fail.
- The repo was not clean before starting.
- The task requires secrets, deployment, or destructive changes.

## Commit policy

Hermes workers must not commit.

Only the human operator commits after:

- Implementation task completed.
- Review task completed with PASS.
- Tests pass locally.
- Git diff is reviewed.
- Only expected files changed.

Recommended commit format:

    <verb> <feature> with tests

Example:

    Add health check endpoint with tests

## Preflight command

Before starting a Hermes task on a real repo, run:

    /opt/ai/projects/ai-server-mcp-catalog/scripts/hermes-repo-preflight.sh /opt/ai/projects/<repo> ".venv/bin/python -m pytest -q"

The preflight checks:

- `.ai/hermes/repo-contract.md` exists.
- The contract contains every required section.
- Path is inside /opt/ai/projects.
- Directory is a Git repo.
- Working tree is clean.
- Current branch is shown.
- Test command passes.
- Hermes hardening audit passes.
