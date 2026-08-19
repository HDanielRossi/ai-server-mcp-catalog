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

Important review idempotency rule:

```text
Review task idempotency must include implementation_task_id.

Correct:
key = stable_key(str(path), feature, f"review:{implementation_task_id}")
```

This prevents a review task created after a correction from accidentally reusing an older review task for the same feature.

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

## Default async boundary

Default owns orchestration but must not execute worker responsibilities directly.

Rules:

```text
- After creating an implementation task through pipeline_bridge, default must not call claude_bridge or review_bridge directly.
- After creating a review task through pipeline_bridge, default must not call review_bridge directly.
- Default may create a correction task only after a reviewer Kanban task has actually completed and its latest summary or result explicitly contains CHANGES REQUIRED.
- Default must not create a correction task from its own direct evidence collection, parent summaries, assumptions, or incomplete worker state.
- If implementation or review is still ready, todo, running, or pending, default must stop and report the real task IDs instead of continuing the workflow in the same Discord response.
```

## Validated workflow behavior

The pipeline has been validated with these development cases:

```text
power    → implementation + review PASS
square   → implementation + review CHANGES REQUIRED + correction + review PASS
cube     → implementation + review PASS
subtract → implementation + review PASS using isolated review_bridge
modulo   → implementation + premature review + correction + final review PASS
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

## Review task body hardening

pipeline_bridge review tasks must include explicit review_bridge evidence requirements.

Required review task body language:

```text
- Use mcp__review_bridge__collect before giving any verdict.
- Do not complete from parent summaries, recent work history, task text, memory, or assumptions alone.
- Use this workdir for review_bridge: <workdir>
- Use changed_paths from the implementation parent metadata when available.
- If changed_paths are not available, use the files explicitly authorized by the implementation task or planner output.
- If the changed files cannot be identified, block the task instead of guessing.
- Run verification through review_bridge using:
  .venv/bin/python -m pytest -q
- Verify tests and scoped diff from review_bridge evidence.
- If review_bridge is unavailable or fails, block the task and include the exact error.
```

Smoke validation:

```text
review_bridge_body_smoke_001
```

Smoke result:

```text
reviewer used mcp__review_bridge__collect
pytest: 9 passed
reviewer completed with PASS
reviewer did not use Tool — terminal
```

## Full pipeline validation after pipeline_bridge review hardening: modulo helper

Validated on:

```text
/opt/ai/projects/agent-pipeline-test
```

Feature:

```text
add_modulo_function
```

Initial implementation task:

```text
t_0c41d5b0
```

Initial review task:

```text
t_4d6a828e
```

Correction task:

```text
t_934dcfd9
```

Final review task after retry:

```text
t_0c8d7115
```

Final implementation result:

```text
modulo(a, b) added to app.py
test_modulo() added to tests/test_app.py
pytest: 10 passed
review verdict: PASS
```

Exact implementation:

```python
def modulo(a, b):
    return a % b
```

Exact tests:

```python
def test_modulo():
    assert modulo(10, 3) == 1
    assert modulo(14, 7) == 0
    assert modulo(-10, 3) == 2
    assert modulo(10.5, 4) == 2.5
```

Reviewer evidence path:

```text
reviewer
→ mcp__review_bridge__collect
→ kanban_complete
```

Final reviewer evidence:

```text
git status:
 M app.py
 M tests/test_app.py

scoped diff:
 app.py
 tests/test_app.py

pytest:
 10 passed in 0.01s
```

Safety validation:

```text
reviewer used mcp__review_bridge__collect
reviewer did not use Tool — terminal
review task body included mandatory review_bridge evidence requirements
reviewer completed with PASS
```

Issues discovered during this test:

```text
1. Discord/default initially advanced the workflow too far in one response.
2. A first review was created before the implementation had actually completed.
3. pipeline_bridge review idempotency needed to include implementation_task_id to avoid review task reuse after correction.
4. One reviewer run crashed with pid not alive, but retry completed successfully.
```

Current conclusion:

```text
The pipeline can complete implementation, correction, and final review with isolated reviewer evidence after retry.
```

## Known failure modes and fixes

### 1. Fake tool-call text in Discord

Symptom:

```text
Discord prints tool names such as mcpplanner_bridgerun or mcppipeline_bridgecreate_implementation_task as plain text.
```

Expected behavior:

```text
The exported session must show real Tool entries:
Tool — mcp__planner_bridge__run
Tool — mcp__pipeline_bridge__create_implementation_task
```

Fix:

```text
Add Real Tool Use Enforcement to default SOUL.
Restart hermes-gateway.service.
Run /reset in Discord.
Verify exported session contains real Tool entries.
```

### 2. Default advances the pipeline too far

Symptom:

```text
default creates implementation, review, correction, and another review in the same Discord response without waiting for workers.
```

Risk:

```text
reviewer may review before coder-claude has completed implementation.
default may create correction from stale or incomplete state.
```

Fix:

```text
Add Async Kanban Boundary to default SOUL.
Default must stop after creating worker tasks and report real task IDs.
Default must only create correction after a completed reviewer task explicitly reports CHANGES REQUIRED.
```

### 3. Review idempotency collision

Symptom:

```text
A review created after a correction reuses an older review task for the same feature.
```

Cause:

```text
create_review_task used a stable key based only on workdir + feature + review.
```

Fix:

```python
key = stable_key(str(path), feature, f"review:{implementation_task_id}")
```

### 4. Reviewer direct terminal use

Symptom:

```text
Exported reviewer session shows:
Tool — terminal
```

Fix:

```text
Disable terminal, file, code_execution, web, browser, delegation, todo, skills, session_search, and context_engine for reviewer.
Connect review_bridge explicitly to reviewer profile.
Add mandatory evidence rule to reviewer SOUL.
```

### 5. Reviewer completes without evidence

Symptom:

```text
Reviewer completes with PASS but the exported session has no mcp__review_bridge__collect call.
```

Fix:

```text
Add Mandatory Evidence Before Verdict rule to reviewer SOUL.
Reviewer must block if review_bridge is unavailable.
Reviewer must not call kanban_complete unless the same session contains mcp__review_bridge__collect.
```

### 6. Reviewer crash

Symptom:

```text
Kanban task status: blocked
Run outcome: crashed
Error: pid <pid> not alive
```

Observed example:

```text
t_2b2e7799 blocked
run 53 crashed
pid 60147 not alive
```

Fix:

```text
Check journalctl and dmesg for OOM or process kill.
If no system-level error appears, create a clean retry review task with a new feature name and the same implementation_task_id.
Do not commit until retry review completes with PASS.
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
- default does not execute worker responsibilities directly
- review task idempotency includes implementation_task_id
```

## Current status

```text
The Hermes development pipeline is validated in the laboratory repo.
The reviewer isolation issue was found and corrected.
The reviewer now collects evidence through review_bridge before completing.
pipeline_bridge review task bodies now explicitly require review_bridge evidence.
pipeline_bridge review idempotency was corrected to include implementation_task_id.
The modulo validation confirmed implementation, correction, and final review with isolated reviewer evidence.
```

## Discord/default async boundary validation: noop async smoke

Validated on:

- Repository: `/opt/ai/projects/agent-pipeline-test`
- Feature: `add-noop-async-smoke`
- Discord/default session: `20260819_093821_c78a5172`
- Implementation task: `t_768b2a09`

Purpose:

- Verify that default/Discord creates only the implementation task.
- Verify that default/Discord does not advance to review, correction, or final review in the same response.

Expected default/Discord behavior:

1. Call `mcp__planner_bridge__run`.
2. Call `mcp__pipeline_bridge__create_implementation_task`.
3. Return the real implementation task ID.
4. Stop.

Observed evidence:

- `Tool — mcp__planner_bridge__run`
- `Tool — mcp__pipeline_bridge__create_implementation_task`
- `t_768b2a09`

Negative evidence:

- No `Tool — mcp__review_bridge__collect`
- No `create_review_task`
- No `create_correction_task`
- No `kanban_complete`

Implementation result:

- `coder-claude` completed `t_768b2a09`.
- `noop_async_smoke` was added to `app.py`.
- A pytest test using `asyncio.run(noop_async_smoke())` was added.
- Pytest reported `11 passed`.

Safety conclusion:

The default async boundary is validated.

Discord/default can be used as the main pipeline entry point without immediately advancing into review or correction in the same response.

Operational rule confirmed:

- After creating an implementation task through `pipeline_bridge`, default must stop and report the task ID.
- Review must be created later, only after implementation has completed.
- Correction must be created later, only after a completed reviewer task explicitly reports `CHANGES REQUIRED`.
