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
