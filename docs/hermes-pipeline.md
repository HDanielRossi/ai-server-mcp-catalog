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

For reviews of the audit script itself, `review_bridge` allows `./scripts/audit-hermes-pipeline-hardening.sh` as an authorized `test_command`.

## Review archive bridge

The reviewer remains read-only and does not write files. After a review has already completed, `review_archive_bridge` can persist the resulting review artifact with:

```text
mcp__review_archive_bridge__persist_review_artifact
```

The bridge writes only inside `.ai/reviews/`. It does not execute the review, modify code, commit, or push.

For example, persisting review task `t_1bf0c4e1` generated:

```text
.ai/reviews/20260819_220453-t_1bf0c4e1.md
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
review_bridge
```

Mandatory rule:

```text
The reviewer must not complete a Kanban review task with PASS or CHANGES REQUIRED unless the same session contains a successful mcp__review_bridge__collect call.
```

## Reviewer memory isolation

The reviewer profile must not expose or use Memory during review tasks.

Expected reviewer tools:

```text
clarify
kanban
review_bridge
```

Isolation requirements:

- Memory must not appear in `reviewer tools --summary`.
- The reviewer must use only `mcp__review_bridge__collect` for review evidence.
- The reviewer must not use `mcp__review_bridge__read_resource` or `mcp__review_bridge__list_resources`.
- The reviewer must not save, search, or persist memory during reviews.

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

## qwen3.8:27b realistic pipeline validation: Flask ready endpoint

Validated on:

- Repository: `/opt/ai/projects/hermes-realistic-smoke`
- Local model: `qwen3.8:27b`
- Implementation task: `t_13a1c209`
- Feature: `GET /ready`

Implementation result:

- `coder-claude` implemented `GET /ready` in `app.py`.
- The endpoint returns JSON `{"ready": true}`.
- `tests/test_app.py` includes `test_ready()`.
- The test verifies `response.status_code == 200`.
- The test verifies `response.get_json() == {"ready": True}`.
- Pytest reported `4 passed`.

Expected diff shape:

- `app.py` modified.
- `tests/test_app.py` modified.
- No dependency files changed.
- No documentation/configuration files changed.

Pipeline validation:

- `default/Discord` created only the implementation task.
- `default/Discord` did not execute implementation directly.
- `default/Discord` did not create review/correction in the same response.
- `coder-claude` completed the implementation task.
- `reviewer` completed formal review through `review_bridge`.
- Final review verdict: `PASS`.

Safety conclusion:

The Hermes pipeline remains valid after switching the local model to `qwen3.8:27b`.

The new local model respects the orchestrator/tool boundary, supports the planner/pipeline bridge flow, and works with the isolated reviewer workflow on a realistic non-critical Flask repository.

## A3.5 — Agent resource & bridge hardening (repository-only artifacts)

- SCOPE: The A3.5 implementation artifacts (templates and tests) are repository-only; installation of them remains operator-only. This task changes NO live bridge, NO installed reviewer SOUL, NO systemd service, NO Docker, NO host configuration. Live installation/deployment happens later ONLY by the human operator, after review PASS. The audit script itself has TWO layers: Section A checks repository/template invariants and Section B checks installed-runtime invariants via read-only probes.

### Artifacts

- `templates/review_bridge_server.py` — bounded, read-only review evidence collection logic.
- `templates/reviewer-SOUL.md` — verbatim-installable reviewer policy template.
- `templates/pipeline_bridge_server.py` — deterministic, idempotent pipeline task-creation logic.
- `templates/claude_bridge_server.py` — hardened, budget-bounded Claude CLI bridge.
- `tests/test_review_bridge_template.py` — tests for the review evidence template.
- `tests/test_pipeline_bridge_template.py` — tests for the pipeline task-creation template.
- `tests/test_claude_bridge_template.py` — tests for the Claude CLI bridge template.
- `scripts/audit-hermes-pipeline-hardening.sh` — two-layer invariant audit for all of the above: Section A repository/template invariants and Section B installed-runtime invariants (read-only probes of the installed runtime).

### Invariant audit (`scripts/audit-hermes-pipeline-hardening.sh`)

Two aggregated layers, reported as a single PASS/FAIL:

- Section A — repository/template invariants: file-existence plus required/forbidden content checks for the four templates, the three test suites, and the docs. Purely repository-local; no host state is read (REPO-ONLY: this Section A layer probes no installed runtime).
- Section B — installed-runtime invariants: read-only probes of the installed runtime (`/usr/local/lib/pipeline-bridge-mcp/server.py`, `/usr/local/lib/review-bridge-mcp/server.py`, `~/.hermes/SOUL.md`, `~/.hermes/config.yaml`, `~/.hermes/profiles/reviewer/config.yaml`, `~/.hermes/profiles/reviewer/SOUL.md`, and a read-only `reviewer tools --summary` probe). These preserve the previously validated hardening guarantees: installed pipeline_bridge review idempotency (`review:{implementation_task_id}`); global/default config does NOT expose `review_bridge` while the reviewer profile DOES; the default SOUL contains the "Async Kanban Boundary"; the reviewer SOUL contains the mandatory-evidence / `mcp__review_bridge__collect` requirement; the reviewer does NOT expose Memory; global/default DOES expose `review_archive_bridge` while the reviewer does NOT; the installed review_bridge `ALLOWED_TEST_COMMANDS` contains `./scripts/audit-hermes-pipeline-hardening.sh`; and repository status/cleanliness reporting for the pipeline repositories.
- All Section B probes are read-only: they modify no runtime files and the audit installs, restarts, or repairs nothing.
- Section B reports a clearly marked SKIPPED only when the Hermes runtime itself is not installed/present on the host; an individual failing invariant is always FAILED and is never downgraded to SKIPPED.
- On the iamex production AI server the runtime is installed, so both layers execute and the audit must finish PASS.

### Review evidence bridge (`templates/review_bridge_server.py`)

Defaults:

- `test_command` defaults to `"__skip__"`.
- `changed_paths` defaults to an empty string (normalizes to an empty list).
- `include_diff` defaults to `False`.
- `include_repo_evidence` defaults to `True`.

Bounds and behavior:

- `MAX_CONTENT_WINDOW_LINES = 200` bounds any requested content window to at most 200 lines.
- Each content-window request covers exactly one file — one file per content window.
- When no content window is requested, `content_window` is reported as `"not-requested"` and the file content field is reported as `"SKIPPED"`.
- `include_diff=true` requires at least one changed path, or evidence collection is rejected.
- The allowed test commands are exactly these three, verbatim:
  - `__skip__`
  - `/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q`
  - `./scripts/audit-hermes-pipeline-hardening.sh`
- `__skip__` is an EXPLICIT no-test request — no substitution for any other command is ever made.

### Reviewer policy (`templates/reviewer-SOUL.md`)

- Review is read-only: the reviewer never edits code and never writes files.
- A successful `review_bridge` collect must complete before any verdict is issued — no verdict from assumption, memory, or an unfulfilled collect.
- Content windows are bounded to at most 200 lines, and each collect covers exactly one file per window.
- No parallel collects.
- About 8 successful collects maximum per review.
- If a collect fails, the reviewer may make one deterministic correction and retry; if that also fails, it must stop and block, including the exact error text received.
- The reviewer must block when the changed files for the workflow item cannot be identified — never guess paths.
- The two verbatim test-command discipline rules:

  "If test_command is __skip__, do not invent or substitute another command"

  "If tests are required by the acceptance criteria but no valid explicit test command is available, block the task instead of guessing"

### Pipeline bridge (`templates/pipeline_bridge_server.py`)

Idempotency keys are produced by `stable_key(workdir, feature, role)` using exactly these three role formulas:

- implementation → `stable_key(workdir, feature, "implementation")`
- review → `stable_key(workdir, feature, "review:{implementation_task_id}")`
- correction → `stable_key(workdir, feature, "correction:{review_task_id}")`

Embedding the parent task ID (`implementation_task_id` for review, `review_task_id` for correction) directly into the key means two independent runs targeting different parent tasks can never collide on the same key, while repeated calls for the same parent task always resolve to the same key — this is what makes task creation idempotent instead of merely deduplicated by chance. Missing or empty parent IDs (`implementation_task_id` for review tasks, `review_task_id` for correction tasks) are rejected outright.

### Claude bridge (`templates/claude_bridge_server.py`)

- Public API: `run(workdir, task_id, prompt, ...)`.
- Call budget threshold: `CALL_BUDGET_THRESHOLD = 4`. Calls 1-2 are tagged `normal`. Call 3 succeeds but is tagged `exceptional`/`budget-warning`. Call 4 and any later call are rejected with `BudgetExhaustedError` and NO subprocess invocation occurs.
- Budget accounting is per-`task_id`.
- A cross-process lock (`fcntl.flock`) on a stable lock file (`ledger.lock`) guards every ledger read/write.
- Ledger writes are atomic: temp-file write + `os.fsync` + `os.replace`.
- Fail-closed: a malformed, wrong-schema, or corrupt ledger raises `LedgerCorruptionError` and is never reset or repaired.
- A reserved call is consumed (recorded against the budget) even when the subprocess invocation, JSON parsing, or telemetry validation step subsequently fails.
- Telemetry is persisted per accepted call with every one of these fields: `duration_ms`, `duration_api_ms`, `num_turns`, `total_cost_usd`, `session_id`, `subtype`, `is_error`, `iterations`, `modelUsage`, `usage.input_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, `usage.output_tokens`, `usage.iterations`.

### Paths

- `workdir` must be an existing strict-descendant directory of `/opt/ai/projects` — the root itself is rejected.
- `changed_paths` entries must be relative, must not contain `..`, and must resolve inside `workdir` (a symlink-escape attempt is rejected).

### Subprocess safety

- The Claude CLI argv is passed as a list, never a string, with `shell=False` (never `shell=True`).
- `cwd` is the validated, realpath-resolved `workdir`.
- Required Claude flags: `--print`, `--output-format`, `json`, `--no-session-persistence`.
- `--max-budget-usd` is appended only when the caller passes an explicit non-`None` value.
- there is no token cap and no default hard dollar cap — telemetry is persist-only and never itself enforces a budget.

### No repo assumptions

The templates make no `app.py` or `tests/test_app.py` (or any other specific framework/layout) assumption — they operate purely on caller-supplied paths and identifiers.

### Verification

Both commands below must pass:

```
/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q
bash scripts/audit-hermes-pipeline-hardening.sh
```

Expected exit behavior:

- `/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q` exits 0 with all tests passing.
- `bash scripts/audit-hermes-pipeline-hardening.sh` prints `PASS` and exits 0, or prints a `FAIL` listing and exits 1.

### Operator

Live installation of any of the above templates is excluded from this task and may occur later only through the human operator, after review PASS.

## A3.5.0 — Planner explicit-context bootstrap (repository-only artifacts)

- SCOPE: bootstrap exception, repository-only. `templates/planner_bridge_server.py` and `scripts/planner-bridge` are versioned copies of the installed planner-bridge MCP server and wrapper, extended with an explicit-context feature. No runtime deployment occurs as part of this repository implementation — the live `/usr/local/lib/planner-bridge-mcp/server.py` and `/usr/local/bin/planner-bridge` are unchanged and continue serving traffic exactly as before. Installing this bootstrap live is a separate, human-authorized rollout that happens later, after review PASS.
- WHY: the production planner bridge cannot currently obtain explicit repository context needed to plan its own repair. Its live wrapper builds a fixed, truncated automatic snapshot (bounded `find`/`sed` excerpts of a short allowlist of files) with no way for a caller to hand it additional, complete, caller-chosen file content.

### Artifacts

- `templates/planner_bridge_server.py` — versioned MCP server; a real, directly installable `mcp.server.MCPServer` adapter (not a pure-helper template).
- `scripts/planner-bridge` — versioned, hardened wrapper; the filesystem/security enforcement boundary for explicit context files.
- `tests/test_planner_bridge_template.py` — installability/regression tests for the MCP server template.
- `tests/test_planner_bridge_wrapper.py` — regression tests for the wrapper, substituting a harmless fake Python transport executable through `PLANNER_CODEX_PYTHON` (never the real Hermes planner/model).

### API: `run(workdir, prompt, context_files=None)`

- Backward compatible: existing callers supplying only `workdir` and `prompt` are unaffected — `context_files` is additive and optional.
- The automatic bounded snapshot (git status, git log, top-level tree, a short allowlist of doc/script files, each bounded by `head -200` / `sed -n '1,700p'` or `1,260p'`) is unchanged and still always runs.
- `context_files` accepts `None` or a list of relative path strings, at most `MAX_CONTEXT_FILES = 12` entries; validated before any subprocess is started. The wrapper is invoked with argv (never `shell=True`): `/usr/local/bin/planner-bridge <workdir> <prompt> --context-file <path> --context-file <path> ...`.
- Explicit files may be tracked or untracked in git — the wrapper checks the filesystem, not git status.

### Explicit context security contract (enforced by `scripts/planner-bridge`, before the Hermes planner one-shot runs)

- Each `--context-file` must be relative (absolute paths are rejected), must resolve (via `realpath -e`) to a regular file, and that resolved path must remain inside the canonicalized workdir — this single check rejects both `..` traversal and any symlink whose target escapes the workdir. A symlink is permitted only when its resolved target stays inside the workdir.
- Limits: `MAX_CONTEXT_FILES = 12`, `MAX_CONTEXT_FILE_BYTES = 262144` (256 KiB per file), `MAX_CONTEXT_TOTAL_BYTES = 524288` (512 KiB combined). All limits are enforced before the Hermes planner one-shot is invoked; a failed validation exits non-zero and the planner never runs. No file is ever silently truncated — a file over the limit is rejected outright.
- The automatically selected snapshot keeps its existing bounded `sed`/`head` behavior unchanged; this feature does not make the automatic snapshot unbounded.

### Explicit context snapshot format

Each accepted file is embedded completely, in the order supplied on the command line, with unambiguous boundaries:

```
===== EXPLICIT_CONTEXT_BEGIN =====
path=<requested relative path>
bytes=<exact byte count>
sha256=<sha256>
<complete file contents>
===== EXPLICIT_CONTEXT_END =====
```

### Temporary context files

Callers that need to stage an explicit context file that isn't already part of the project tree can use the repository's own already-`.gitignore`d `tmp/` directory as a relative-path scratch location (e.g. `--context-file tmp/notes.md`) — it resolves inside the workdir like any other relative path and requires no additional allowlisting.

### Prompt transport and `MAX_ARG_STRLEN`

The original wrapper delivered the complete assembled prompt as one `-z "$PROMPT"` argv element. During A3.5.0 bootstrap verification, a real-repository probe with a 118982-byte explicit context bundle plus the normal automatic snapshot reproduced Linux `MAX_ARG_STRLEN`: `execve()` failed with `Argument list too long` before planner execution. A small-workdir test had previously hidden this integration failure.

A3.5.0 therefore does not transport the assembled prompt through argv. `scripts/planner-bridge` keeps all filesystem validation and snapshot construction in the wrapper, then pipes the complete prompt over stdin to a small Python runner selected by `PLANNER_CODEX_PYTHON` (defaulting to the Hermes Agent venv Python). Only small control values such as the verified workdir and runner source remain in argv.

The runner changes to the verified workdir, installs `sys.argv = ["hermes", "-p", "planner-codex"]` before importing `hermes_cli.main`, and therefore reuses Hermes' normal early `_apply_profile_override()` behavior: the planner profile's `HERMES_HOME` is selected before profile-sensitive configuration, dotenv, MCP, and other imports occur. It then reads the complete prompt with `sys.stdin.read()` and invokes Hermes' normal `_run_and_exit_oneshot()` path with `clarify,context_engine,memory`.

This transport removes the single-argv-size bottleneck without weakening the explicit-context limits: `MAX_CONTEXT_FILES = 12`, `MAX_CONTEXT_FILE_BYTES = 262144`, and `MAX_CONTEXT_TOTAL_BYTES = 524288` remain the accepted-input contract. The wrapper regression suite includes a >100 KiB explicit-context case using the normal repository snapshot so the previously observed `MAX_ARG_STRLEN` failure cannot be hidden by an artificially small workdir.

### Verification

Both commands below must pass, in addition to the existing verification commands above:

```
/home/hdgr/.hermes/hermes-agent/venv/bin/python3 -m pytest -q tests/test_planner_bridge_template.py tests/test_planner_bridge_wrapper.py
bash scripts/audit-hermes-pipeline-hardening.sh
```
