# Hermes Repository Contract

## Repository identity

/opt/ai/projects/ai-server-mcp-catalog

### Project purpose

This repository documents and maintains the local MCP/Hermes pipeline catalog for the AI server.

It contains documentation, onboarding material, installation scripts, and validation scripts for Hermes, MCP tools, ComfyUI MCP, GitHub MCP, filesystem MCP, Docker MCP, planner_bridge, pipeline_bridge, claude_bridge, and review_bridge.

### Risk level

medium

## Allowed paths

Hermes may modify only these areas unless explicitly authorized by the human operator:

- docs/
- templates/
- scripts/audit-hermes-pipeline-hardening.sh
- scripts/hermes-repo-preflight.sh
- scripts/hermes-pipeline-controller.py
- tests/ for tests of the catalog's internal tooling, such as tests/test_hermes_pipeline_controller.py
- README.md if present

### First safe task

Hermes may perform local, testable development of scripts/hermes-pipeline-controller.py and its tests under tests/, or a documentation task such as improving wording in docs/production-repo-onboarding.md or adding a small checklist section to docs/hermes-pipeline.md. pipeline_controller is repository-local, testable tooling at this stage and is not an active runtime component: it must not be installed, deployed, or wired as an active runtime.

The first task must not modify live Hermes configs, MCP servers, systemd services, Docker Compose files, or production repositories.

## Forbidden paths

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

Specifically, and without weakening anything above: no installation or modification under /usr/local/; no changes under ~/.hermes/ or to live Hermes configuration; no systemd changes; no Docker production configuration changes; no automatic commit, push, merge, or deploy.

Project-specific forbidden areas:

- Any file that changes active runtime behavior unless explicitly authorized.
- Any script that installs, removes, restarts, or reconfigures services unless explicitly authorized.
- Any destructive Git operation.

## Test command

./scripts/audit-hermes-pipeline-hardening.sh

Build command: none

Lint command: none

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

## Push policy

No push without explicit human approval.

## Rollback procedure

Preferred non-destructive commands:

git status --short
git diff
git restore <files>

Do not use destructive reset unless explicitly approved.
