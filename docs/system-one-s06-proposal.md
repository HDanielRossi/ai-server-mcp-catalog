# System One S0.6 proposal: evidence-gated project-overview path

Status: source activation approved after the S0.5 evidence gate. Runtime
rollout and the first S0.6 canary still require their own separate explicit
operator approvals. This document does not itself change the installed runtime,
Qwen profile, or worker authority.

## Objective

Consider one narrowly bounded fifth documentation path only after S0.5 has
demonstrated repeatable success. The proposed exact allowlist would be:

```text
app.py
tests/test_app.py
README.md
ARCHITECTURE.md
PROJECT.md
```

`PROJECT.md` is the sole proposed addition. It contains the pilot's static
purpose statement, so a canary could add one concise, factual statement about
the newly documented helper surface. This is not a glob, a directory, or a
general-documentation permission. `DECISIONS.md`, `TODO.md`, all other
documentation, configuration, CI, dependency, profile, Git, and secret paths
remain denied.

## Why not the other pilot documents

- `DECISIONS.md` records engineering policy; changing it could imply authority
  to make architectural decisions.
- `TODO.md` represents workflow state and could be mistaken for task-lifecycle
  authority.
- `PROJECT.md` describes a stable repository fact and remains subject to an
  exact one-occurrence replacement and formal document-consistency review.

## Preconditions

An operator may consider S0.6 only after all of the following are recorded:

1. Three consecutive S0.5 canaries have passed implementation, formal review,
   `ready-to-commit`, merge, and verified post-merge testing. The first S0.5
   canary alone is not sufficient evidence.
2. Each canary has source/runtime SHA-256 parity evidence before dispatch and
   has no scope escape, validation retry, unauthorized mutation, review
   failure, documentation inconsistency, or runtime parity failure.
3. The implementation and review task IDs, pull request, merge SHA, and
   verified post-merge output are retained for all three canaries.
4. A human explicitly approves the S0.6 source change, runtime rollout, and
   first S0.6 canary separately. Prior S0.5 approvals do not authorize S0.6.

Any violation resets the S0.5 evidence sequence and leaves the active
four-path allowlist unchanged.

## Proposed authority boundary

The classifier remains limited to `repository-source` and `tests`; none of
the three documentation paths becomes a classifier component. The Qwen worker
retains only `read_file`, one-occurrence `replace_exact`, and the existing
fixed serial validation bridge. It receives no shell, Git, subprocess,
arbitrary-path, patch, task-lifecycle, commit, or push authority.

The controller must continue to compare every reviewed changed path with the
controller-authored declared scope. The formal reviewer must verify that the
README, architecture, and project-overview statements all describe exactly
the helper implemented in `app.py`, and must reject operational, roadmap, or
policy claims in `PROJECT.md`.

## First-canary shape

The first S0.6 task should be one pure integer helper with all five exact paths
declared. It must add direct tests, one concise README usage section, one
public-function statement in `ARCHITECTURE.md`, and one factual project-
overview sentence in `PROJECT.md`. It must use the existing single serial
validation call; targeted and full pytest evidence remain mandatory.

## Reviewed activation change

After the preconditions and separate human approvals, the activation change
must be limited to:

1. Add the literal `PROJECT.md` entry to the write-bridge allowlist.
2. Add exact read/replace tests for `PROJECT.md` and negative tests for
   `DECISIONS.md`, `TODO.md`, and every other pilot document path.
3. Change the repository/runtime audit from the exact S0.5 four-path contract
   to the exact S0.6 five-path contract.
4. Update operational documentation with the S0.6 gates and project-document
   review requirement.

The controller, MCP roster, Qwen model pin, toolset, validation sequence, and
commit/push boundaries remain out of scope.

## Rollback

On any failed gate, revert the reviewed S0.6 allowlist change, reinstall the
previous write-bridge runtime file, and require a passing runtime audit before
any later Qwen dispatch. Preserve failed task and review evidence; do not use
`git reset --hard` or silently retry a canary.
