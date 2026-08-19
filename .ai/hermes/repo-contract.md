# Hermes Repository Contract

## Repository

/opt/ai/projects/ai-server-mcp-catalog

## Project purpose

This repository documents and maintains the local MCP/Hermes pipeline catalog for the AI server.

It contains documentation, onboarding material, installation scripts, and validation scripts for Hermes, MCP tools, ComfyUI MCP, GitHub MCP, filesystem MCP, Docker MCP, planner_bridge, pipeline_bridge, claude_bridge, and review_bridge.

## Risk level

medium

## Allowed work areas

Hermes may modify only these areas unless explicitly authorized by the human operator:

- docs/
- templates/
- scripts/audit-hermes-pipeline-hardening.sh
- scripts/hermes-repo-preflight.sh
- README.md if present

## Forbidden work areas

Hermes must not modify:

- secrets
- credentials
- .env files
- SSH keys
- GitHub tokens
- live Hermes profile configs
- ~/.hermes/
- /usr/local/lib/
- /usr/local/bin/
- systemd unit files
- Docker Compose production files
- repository history
- files outside the explicit task scope

Project-specific forbidden areas:

- Any file that changes active runtime behavior unless explicitly authorized.
- Any script that installs, removes, restarts, or reconfigures services unless explicitly authorized.
- Any destructive Git operation.

## Test command

./scripts/audit-hermes-pipeline-hardening.sh

## Build command

none

## Lint command

none

## First safe task

Hermes may perform a documentation-only task, such as improving wording in docs/production-repo-onboarding.md or adding a small checklist section to docs/hermes-pipeline.md.

The first task must not modify runtime scripts, Hermes configs, MCP servers, systemd services, Docker Compose files, or production repositories.

## Review requirements

Reviewer must verify:

- mcp__review_bridge__collect was used.
- Test command passes.
- Git diff is scoped to authorized files.
- Only expected files changed.
- Behavior matches acceptance criteria.
- No forbidden files changed.
- No runtime behavior changed unless explicitly authorized.

## Commit policy

Hermes workers must not commit.

Human operator commits only after review PASS.

No push without explicit human approval.

## Rollback plan

Preferred non-destructive commands:

git status --short
git diff
git restore <files>

Do not use destructive reset unless explicitly approved.
