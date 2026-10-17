# System One S0.5 proposal: bounded architecture-document path

Status: proposal only. This document does not authorize a runtime rollout,
does not change the Qwen write bridge, and does not widen any worker authority.

## Objective

If S0.4 supplies sufficient consecutive evidence, consider one minimal
documentation-path expansion from the active three-path allowlist to an exact
four-path allowlist:

```text
app.py
tests/test_app.py
README.md
ARCHITECTURE.md
```

`ARCHITECTURE.md` is the only proposed addition. It is an existing pilot file
that describes the public API, so it can be checked directly against a narrow
helper implementation. This is not a glob, a directory, or general Markdown
access. `PROJECT.md`, `DECISIONS.md`, `TODO.md`, all other documentation,
configuration, CI, dependency, profile, Git, and secret paths remain denied.

## Preconditions

An operator may consider S0.5 only after all of the following are recorded:

1. Three consecutive S0.4 canaries have passed implementation, formal review,
   `ready-to-commit`, merge, and verified post-merge testing.
2. Each canary has source/runtime parity evidence collected before dispatch and
   has no scope escape, validation retry, unauthorized mutation, review
   failure, or runtime parity failure.
3. The implementation and review task IDs, pull request, merge SHA, and
   verified post-merge worktree output are retained for all three canaries.
4. A human explicitly approves the S0.5 source change, runtime rollout, and
   first S0.5 canary separately. A prior S0.4 approval is not an S0.5 approval.

Any violation resets the S0.4 evidence sequence and leaves the active
three-path allowlist unchanged.

## Proposed authority boundary

The classifier remains limited to `repository-source` and `tests`; the
architecture document does not become a third classifier component. The Qwen
worker retains only the existing read and one-occurrence `replace_exact`
operations plus its fixed serial validation bridge. It receives no shell, Git,
subprocess, arbitrary file, patch, task-lifecycle, commit, or push authority.

`ready-to-commit` must continue to compare every reviewed changed path with
the controller-authored declared scope. The formal reviewer must additionally
verify that the architecture entry describes exactly the helper implemented in
`app.py` and documented in `README.md`.

## First-canary shape

The first S0.5 task should be one pure integer helper, for example
`is_nonpositive(value)`, with all four exact paths declared. It must:

- reject `bool` and non-integer inputs with `TypeError`;
- define the zero/positive/negative result boundary in direct tests;
- add one concise README usage section; and
- update only the public-function statement in `ARCHITECTURE.md`.

The task must use the existing single serial validation call. The formal review
must collect fresh targeted and full pytest evidence and reject any document
claim that differs from the implementation.

## Reviewed activation change

After the preconditions and human approval, the implementation change must be
limited to:

1. Add the literal `ARCHITECTURE.md` entry to the write bridge allowlist.
2. Extend the bridge tests with an exact read/replace success case for that
   file and negative cases for every other pilot document path.
3. Change the repository/runtime audit from the exact S0.4 three-path contract
   to the exact S0.5 four-path contract.
4. Update the operational documentation with the S0.5 gates and first-canary
   review requirements.

The controller, MCP roster, Qwen model pin, toolset, validation sequence, and
commit/push boundaries are out of scope for S0.5.

## Rollback

On any failed gate, revert the reviewed S0.5 allowlist change, reinstall the
previous write-bridge runtime file, and require a passing runtime audit before
any subsequent Qwen dispatch. Preserve the failed task and review evidence;
do not use `git reset --hard` or silently retry the canary.
