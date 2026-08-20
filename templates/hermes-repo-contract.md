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
