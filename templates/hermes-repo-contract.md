# Hermes Repository Contract

## Repository identity

Absolute path inside /opt/ai/projects:

    <absolute path>

## Project purpose

Describe what this project does.

## Risk level

Choose one:

    low
    medium
    high
    production-critical

## Allowed paths

Hermes may modify only:

- <file or directory>
- <file or directory>

## Forbidden paths

Hermes must not modify:

- secrets
- credentials
- deployment configuration
- production data
- database migrations
- CI/CD configuration
- Docker, network, or security configuration
- repository history
- files outside the explicit task scope

Project-specific forbidden areas:

- <file or directory>
- <file or directory>

## Test command

    <test command>

Example:

    .venv/bin/python -m pytest -q

## Build command

    <build command or none>

## Lint command

    <lint command or none>

## First safe task

Describe the first tiny, reversible task Hermes may perform.

## Review requirements

Reviewer must verify:

- mcp__review_bridge__collect was used.
- Tests pass.
- Git diff is scoped to authorized files.
- Only expected files changed.
- Behavior matches acceptance criteria.
- No forbidden files changed.

## Commit policy

Hermes workers must not commit.

Human operator commits only after review PASS.

## Push policy

No push without explicit human approval.

## Rollback procedure

Preferred non-destructive commands:

    git status --short
    git diff
    git restore <files>

Do not use destructive reset unless explicitly approved.

## A5 READY_TO_COMMIT requirements

Before commit, the final review must satisfy all of the following:

- The repository's tracked `.gitignore` contains the exact rooted rule `/.ai/reviews/` before the final review (see "A5.1 `/.ai/reviews/` Git-ignore precondition" below). This is a required precondition, not optional hygiene.
- The final authoritative reviewer collect occurs after all required evidence and tests.
- The completed review records the exact `hermes.repository-state/v1` envelope and `aggregate_sha256`.
- Review remains read-only and records `mutation_performed=false`.
- The final PASS review is persisted as matching `hermes.review-archive/v2` evidence.
- `ready-to-commit` returns `outcome="ready"` for the exact implementation task ID and final review task ID.

`ready-to-commit` is read-only technical attestation. It must not stage files, commit, push, create tasks, mutate repository files, or infer human approval.

### A5.1 `/.ai/reviews/` Git-ignore precondition

Before the final review, this repository's tracked `.gitignore` must contain the exact rooted rule:

    /.ai/reviews/

Only that exact directory is ignored — do not broadly ignore `.ai/` or `/.ai/`. Any other untracked content under `.ai/` remains a normal, visible path that still shows up in `hermes.repository-state/v1` and still blocks READY_TO_COMMIT if it changes.

Review archive artifacts written under `.ai/reviews/` (by `review_archive_bridge`, after the reviewer has already completed) are control-plane evidence, not reviewed source-state paths. Their validity is still independently enforced by `hermes.review-archive/v2` regardless of Git-ignore status. An archive is not human approval, and Git-ignoring `.ai/reviews/` is not a substitute for that validation. READY_TO_COMMIT remains strictly read-only, and commit and push approvals remain separate, explicit human actions after READY_TO_COMMIT succeeds.

A successful READY_TO_COMMIT result must preserve:

- `human_approval_required=true`
- `commit_performed=false`
- `push_performed=false`

The human operator gives explicit commit authorization only after READY_TO_COMMIT succeeds.

Push requires a separate explicit human authorization. Commit authorization never implies push authorization.

## A6 `pipeline_controller` MCP adapter

When the pipeline is driven through the `pipeline-controller` MCP server (`templates/pipeline_controller_server.py`), the same READY_TO_COMMIT and archival rules above apply unchanged: the MCP server is a thin façade over `scripts/hermes-pipeline-controller.py` and never reimplements or second-guesses controller policy.

- The MCP server exposes exactly seven tools: `check_task`, `create_implementation`, `create_review`, `create_correction`, `wait_task`, `archive_review`, `ready_to_commit`. There is no arbitrary command/argv/executable/shell tool, and no `git add`/`git commit`/`git push` or other staging tool.
- `archive_review` and `ready_to_commit` remain separate, explicit operations — the adapter never chains one into the other.
- `ready_to_commit` remains strictly read-only technical attestation through the MCP adapter exactly as through the CLI: it never stages, commits, pushes, or infers human approval.
- Runtime exposure of the MCP adapter (installing and wiring it into a live profile) is a separate, human-authorized step outside of this repository contract.

## A7 `pipeline_controller` runtime exposure boundary

Use of the `pipeline-controller` MCP does not authorize live deployment, commit, or push.

The A7 lifecycle is deliberately separated:

1. **A7.1 — repository-only hardening and verification.**
2. **A7.2 — final read-only review, matching `hermes.review-archive/v2`, successful `ready-to-commit`, then explicit human commit and push approvals.**
3. **A7.3 — separate human-authorized runtime rollout.**

Completion of A7.1 or A7.2 never implicitly authorizes A7.3.

### Future runtime ownership

If this environment later exposes `pipeline-controller`, the accepted runtime ownership is:

```text
/usr/local/bin/hermes-pipeline-controller
/usr/local/lib/pipeline-controller-mcp/server.py
/usr/local/lib/pipeline-controller-mcp/.venv/
```

The adapter must not be installed in `/usr/local/lib/pipeline-bridge-mcp/`.

The installed adapter and controller must preserve exact SHA-256 parity with their reviewed repository sources.

The dedicated `.venv` must contain:

- a non-empty `.venv/pyvenv.cfg`;
- an executable `.venv/bin/python3` or `.venv/bin/python`.

### Exact MCP surface

The MCP server identity is `pipeline-controller`. The Hermes registration key is `pipeline_controller`.

The only authorized MCP tools are:

- `check_task`
- `create_implementation`
- `create_review`
- `create_correction`
- `wait_task`
- `archive_review`
- `ready_to_commit`

No additional tool is authorized. No `commit*`, `push*`, staging, arbitrary argv, executable, command, or shell capability is authorized.

### Default-only privilege rule

`pipeline_controller` may be registered only as an immediate child of the default/global top-level `mcp_servers` mapping:

```yaml
mcp_servers:
  pipeline_controller:
    ...
```

It must not be registered in these Hermes profiles:

- `reviewer`
- `coder`
- `coder-claude`
- `planner-codex`
- `sysadmin`

A top-level occurrence outside `mcp_servers` or a nested occurrence below another MCP entry does not satisfy the registration contract.

Other legitimate MCP registrations remain permitted and must not be removed solely to satisfy this rule.

`sysadmin` may perform a separately human-authorized host rollout as an operator action, but it must not receive the pipeline orchestration MCP itself.

### Human authorization remains mandatory

A successful `ready_to_commit` result is technical attestation only and must preserve the equivalent of:

- `human_approval_required=true`
- `commit_performed=false`
- `push_performed=false`

Commit requires explicit human authorization after READY_TO_COMMIT succeeds.

Push requires a second, separate explicit human authorization. Commit authorization never implies push authorization.

Runtime installation is also a distinct human-authorized operation. Repository review or commit approval never silently authorizes A7.3 deployment.

Before any A7.3 live mutation, the operator must preserve enough pre-rollout state to restore replaced runtime/configuration targets byte-for-byte if validation fails.

## A8 `pipeline_controller` sole default lifecycle interface

The default profile's Kanban lifecycle ownership is exercised exclusively through `pipeline_controller`. The default profile must not register or use `pipeline_bridge` directly, and must not register or use `review_archive_bridge` directly.

- `pipeline_controller` exposes exactly seven tools: `check_task`, `create_implementation`, `create_review`, `create_correction`, `wait_task`, `archive_review`, `ready_to_commit`. No MCP tool defined in this repository (`planner_bridge`, `pipeline_bridge`, `review_archive_bridge`, `pipeline_controller`, `review_bridge`, `claude_bridge`) is named `commit*`, `push*`, or `staging*`.
- `planner_bridge` remains planning-only: it never creates, checks, waits on, reviews, corrects, archives, or attests readiness for any Kanban task.
- `reviewer` remains read-only and uses only `review_bridge`. `coder-claude` implements only through `claude_bridge`. Neither role gains direct Kanban lifecycle authority.
- `pipeline_controller` itself remains forbidden in the `reviewer`, `coder`, `coder-claude`, `planner-codex`, and `sysadmin` profiles.
- `ready_to_commit` remains strictly read-only technical attestation (A5). Commit approval requires one explicit human authorization; push approval requires a second, separate explicit human authorization. Commit authorization never implies push authorization.

### Phases

```text
A8.1 = repository implementation (documentation, contract, audit, and templates/default-SOUL.md).
A8.2 = read-only review + archive + READY_TO_COMMIT + human commit/push.
A8.3 = runtime policy/config rollout (separate human authorization).
```

Completion of A8.1 never implicitly authorizes A8.2 or A8.3. The repository audit (`scripts/audit-hermes-pipeline-hardening.sh`, bare invocation / `--repo-only`) verifies this contract hermetically against `templates/default-SOUL.md`, this contract file, and repository MCP source — never against live runtime state. The audit does not fail merely because a live default profile still lists `pipeline_bridge`/`review_archive_bridge`; deregistering them live is a separate, later, human-authorized A8.3 operation. `templates/default-SOUL.md` is the versioned, installable policy template for this target state; it is not the live executable SOUL and installing it live is a separate operator action.
