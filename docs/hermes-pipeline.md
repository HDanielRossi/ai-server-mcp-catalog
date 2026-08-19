# Hermes Agent Pipeline

## Purpose

This document describes the validated Hermes multi-agent development pipeline used on the AI server.

The goal is to keep Discord/default as the orchestrator, delegate implementation to Claude Code through a controlled bridge, and delegate review to a read-only reviewer bridge.

## Validated architecture

```text
default / Discord
→ planner_bridge
→ pipeline_bridge
→ coder-claude
→ claude_bridge
→ reviewer
→ review_bridge
→ kanban_complete
```

## Agent roles

### default

The default profile is the orchestrator.

Responsibilities:

```text
- Receive the user request from Discord or CLI.
- Use planner_bridge for planning.
- Use pipeline_bridge to create implementation, review, and correction Kanban tasks.
- Own the workflow graph.
- Do not directly implement code.
- Do not create Kanban tasks assigned to planner-codex.
```

### planner_bridge

Planning-only bridge.

Responsibilities:

```text
- Inspect project context.
- Produce implementation plans.
- Avoid direct implementation.
- Avoid creating Kanban tasks directly.
- Return NEED_MORE_CONTEXT when the current project context is insufficient.
```

### pipeline_bridge

Kanban task creator.

Responsibilities:

```text
- Create implementation tasks for coder-claude.
- Create review tasks for reviewer.
- Create correction tasks when reviewer returns CHANGES REQUIRED.
- Preserve the workflow ownership under default.
```

### coder-claude

Implementation worker.

Responsibilities:

```text
- Implement only through claude_bridge.
- Modify only authorized files.
- Run the requested tests.
- Report git status, scoped diff, test output, and modified files before completing.
- Do not use direct Hermes/Qwen editing for implementation.
```

### claude_bridge

Controlled Claude Code execution bridge.

Path:

```text
/usr/local/bin/claude-bridge
/usr/local/lib/claude-bridge-mcp/server.py
```

Responsibilities:

```text
- Run Claude Code inside allowed project workspaces.
- Restrict workdir to /opt/ai/projects.
- Provide post-run verification.
- Return git status, scoped diff, and test output.
```

### reviewer

Read-only review worker.

Responsibilities:

```text
- Review implementation evidence.
- Use review_bridge as the primary evidence source.
- Do not use direct terminal, file, code execution, patch, edit, or write tools.
- Return PASS or CHANGES REQUIRED.
- Do not create downstream correction tasks.
```

### review_bridge

Read-only evidence bridge.

Path:

```text
/usr/local/lib/review-bridge-mcp/server.py
```

Main tool:

```text
mcp__review_bridge__collect
```

Responsibilities:

```text
- Collect git status.
- Collect scoped git diff.
- Run git diff --check.
- Read scoped file contents.
- Run allowed test command.
- Report git status after tests.
```

## Reviewer isolation

The reviewer profile is intentionally isolated from broad execution tools.

Disabled for reviewer:

```text
terminal
file
code_execution
web
browser
delegation
todo
skills
session_search
context_engine
```

Expected reviewer tools:

```text
clarify
kanban
memory
review_bridge
```

Mandatory rule:

```text
The reviewer must not complete a Kanban review task with PASS or CHANGES REQUIRED unless the same session contains a successful mcp__review_bridge__collect call.
```

## Validated workflow behavior

The pipeline has been validated with these development cases:

```text
power    → implementation + review PASS
square   → implementation + review CHANGES REQUIRED + correction + review PASS
cube     → implementation + review PASS
subtract → implementation + review PASS using isolated review_bridge
```

## Full pipeline validation: subtract helper

Validated on:

```text
/opt/ai/projects/agent-pipeline-test
```

Feature:

```text
add_subtract_function
```

Pipeline path validated:

```text
default/Discord
→ planner_bridge
→ pipeline_bridge
→ coder-claude
→ claude_bridge
→ reviewer
→ review_bridge
→ kanban_complete
```

Implementation task:

```text
t_24004e68
```

Review task:

```text
t_9f0cb466
```

Implementation result:

```text
app.py: added subtract(a, b)
tests/test_app.py: added exact subtract tests
pytest: 9 passed
```

Exact implementation:

```python
def subtract(a, b):
    return a - b
```

Exact tests:

```python
def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(3, 5) == -2
    assert subtract(-4, -6) == 2
    assert subtract(10.5, 0.5) == 10.0
```

Reviewer validation:

```text
reviewer used mcp__review_bridge__collect
reviewer did not use Tool — terminal
review_bridge collected:
- git status
- scoped git diff
- file contents
- pytest output
```

Review result:

```text
PASS
```

Known good test result:

```text
9 passed in 0.01s
```

Safety result:

```text
Reviewer is structurally isolated from direct terminal/file/code execution and validates through review_bridge evidence.
```

## Operational rule before using on production repositories

Before moving this pipeline to a production or critical repository, run one safe task in a non-critical repo and verify:

```text
- coder-claude uses mcp__claude_bridge__run
- reviewer uses mcp__review_bridge__collect
- reviewer does not use Tool — terminal
- tests pass
- git diff is scoped to authorized files
- only expected files are modified
```

## Current status

```text
The Hermes development pipeline is validated in the laboratory repo.
The reviewer isolation issue was found and corrected.
The reviewer now collects evidence through review_bridge before completing.
```
