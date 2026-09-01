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

## A5 production commit gate

Before the final review, verify the repository's tracked `.gitignore` contains the exact rooted rule `/.ai/reviews/` (see "A5.1 `/.ai/reviews/` Git-ignore precondition" below). Without it, a valid review archive artifact becomes a spurious repository-state change and `ready-to-commit` rejects an otherwise-correct pipeline run.

For A5, the earlier production flow is extended after the final reviewer PASS:

1. The final reviewer run records the authoritative `hermes.repository-state/v1` fingerprint.
2. Persist that final review as matching `hermes.review-archive/v2` evidence.
3. Run `ready-to-commit` with the exact implementation task ID and final review task ID.
4. Continue only when it returns `outcome="ready"`.
5. The human operator then gives separate explicit commit authorization.
6. Push requires a second, separate explicit human authorization.

`ready-to-commit` is read-only technical attestation. It does not stage files, commit, push, create tasks, mutate the repository, or constitute human approval. Commit authorization never implies push authorization.

Treat any of these as a hard stop:

- final review metadata lacks `hermes.repository-state/v1`;
- the `hermes.review-archive/v2` artifact does not exactly match final reviewer/Kanban state;
- `ready-to-commit` does not return `outcome="ready"`;
- repository state changes after review/archive and before commit;
- any worker or controller attempts to infer approval, stage, commit, or push automatically.

## A5.1 `/.ai/reviews/` Git-ignore precondition

Before the final review, confirm the target repository's tracked `.gitignore` contains the exact rooted rule:

    /.ai/reviews/

Only that exact directory is ignored. Do not broadly ignore `.ai/` (or `/.ai/`) — any other content under `.ai/` remains a normal, visible, untracked path and must still show up in `hermes.repository-state/v1` and still block READY_TO_COMMIT if it changes unexpectedly.

Why this matters: `review_archive_bridge` writes the final `hermes.review-archive/v2` artifact under `.ai/reviews/` after the reviewer has already completed its task. If that directory is not Git-ignored (or is ignored too broadly, hiding unrelated `.ai/` content), the archive write either becomes an untracked repository-state delta that `ready-to-commit` correctly rejects with `repository_state_mismatch_kanban`, or an unrelated change elsewhere under `.ai/` gets silently hidden from repository-state instead of blocking READY_TO_COMMIT.

The archive artifact itself remains control-plane evidence, not reviewed source-state, and its validity is still independently enforced by `hermes.review-archive/v2` — Git-ignoring `.ai/reviews/` never substitutes for that validation, and it is not human approval.

The human operator commits only after implementation completion, final review PASS, matching v2 archival, successful `ready-to-commit`, passing tests, reviewed Git diff, expected scope, and explicit commit authorization.

## A6 `pipeline_controller` MCP adapter

`templates/pipeline_controller_server.py` is a thin MCP façade over `scripts/hermes-pipeline-controller.py`: it exposes the controller's seven operations (`check_task`, `create_implementation`, `create_review`, `create_correction`, `wait_task`, `archive_review`, `ready_to_commit`) as typed MCP tools, one controller subprocess invocation per call, with no arbitrary command/argv tool and no commit/push/staging capability of any kind.

The controller remains the sole policy authority — workdir policy, Kanban validation, verdict classification, correction policy, repository-state/v1, archive validation, and READY_TO_COMMIT policy are all implemented exclusively in the controller, never in the adapter. `archive_review` and `ready_to_commit` remain separate, explicit operations; the adapter never chains one into the other, and READY_TO_COMMIT remains a technical, read-only attestation — it never infers or grants human commit/push approval.

A6 is repository-only: this task performs no live deployment, installation, or configuration change. Exposing the MCP adapter at a live path and wiring it into a profile's tool configuration is a separate, human-authorized rollout performed later by the operator, after review PASS. See "A6" in `docs/hermes-pipeline.md` for the full contract.

## A7 production `pipeline_controller` runtime boundary

A7 does not change the production repository's human commit/push policy. It adds a controlled future runtime exposure path for the seven-tool `pipeline-controller` MCP façade.

### Phase boundary

Treat these as separate gates:

1. **A7.1 repository-only hardening** — audit/tests/docs only; no live installation.
2. **A7.2 final repository review/release gate** — reviewer PASS, matching `hermes.review-archive/v2`, successful `ready-to-commit`, then separate human commit and push approvals.
3. **A7.3 operator runtime rollout** — a new, separately human-authorized host operation after the repository release gate.

Repository audit PASS or READY_TO_COMMIT never authorizes runtime installation.

### Required future live layout

The accepted A7.3 paths are:

```text
/usr/local/bin/hermes-pipeline-controller
/usr/local/lib/pipeline-controller-mcp/server.py
/usr/local/lib/pipeline-controller-mcp/.venv/
```

Do not install the adapter under `/usr/local/lib/pipeline-bridge-mcp/`.

The installed controller and adapter must have exact SHA-256 parity with the reviewed repository sources. The dedicated `.venv` must contain a non-empty `pyvenv.cfg` and an executable `bin/python3` or `bin/python`.

### Default-only registration

The Hermes registration key is `pipeline_controller`, as an immediate child of the top-level `mcp_servers` mapping:

```yaml
mcp_servers:
  pipeline_controller:
    ...
```

Only the default/global profile may register it.

It must remain absent from:

- `reviewer`
- `coder`
- `coder-claude`
- `planner-codex`
- `sysadmin`

`sysadmin` may execute an explicitly human-authorized rollout as an operator action, but it must not receive the pipeline orchestration MCP itself.

Unrelated existing `mcp_servers` entries must be preserved.

### Tool and approval boundary

The installed adapter exposes exactly:

```text
check_task
create_implementation
create_review
create_correction
wait_task
archive_review
ready_to_commit
```

No additional tool is allowed. No `commit*` or `push*` tool is allowed.

`ready_to_commit` remains read-only technical attestation. Successful READY_TO_COMMIT still requires explicit human commit approval, followed by a separate explicit human push approval before push.

### Operator rollout checklist

Before any A7.3 mutation:

- capture backups sufficient to restore every pre-existing target byte-for-byte;
- verify the reviewed repository state is the intended rollout source;
- keep `pipeline-controller` out of the `pipeline-bridge` ownership path;
- install the controller, dedicated adapter, and dedicated `.venv`;
- register `pipeline_controller` only in the default/global profile;
- run `scripts/audit-hermes-pipeline-hardening.sh --runtime` or `--all`;
- verify source/runtime SHA-256 parity;
- verify the exact seven-tool roster and no commit/push capability;
- perform bounded read-only MCP discovery/smoke probes without creating Kanban tasks solely for smoke testing;
- restart the Hermes gateway only if required and separately human-authorized;
- on failure, restore the captured pre-rollout state.

A7.3 runtime rollout is not part of ordinary repository onboarding and must never be inferred from a repository task's PASS result.
