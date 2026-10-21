# System One S0.7 proposal: five-path stability ceiling

Status: proposal only. It does not change the active S0.6 allowlist, installed
runtime, Qwen profile, controller, or worker authority.

## Objective

Treat the S0.6 five-path allowlist as a stability ceiling, not an automatic
step toward general documentation access. No sixth write path is proposed.
The active literal allowlist remains:

```text
app.py
tests/test_app.py
README.md
ARCHITECTURE.md
PROJECT.md
```

`DECISIONS.md`, `TODO.md`, every other document, configuration, CI,
dependencies, Git, profiles, and secrets remain denied. `DECISIONS.md` would
create apparent architecture-policy authority; `TODO.md` would create apparent
workflow or lifecycle authority. Neither is an eligible S0.7 addition.

## Recorded S0.6 evidence

The following canaries completed implementation, formal review,
`ready-to-commit`, merge, post-merge testing, and runtime audit:

1. `is_five`: implementation `t_d4a44449`, review `t_f41ef931`, PR #14,
   merge `6bec4d0`, 89 post-merge tests passed.
2. `is_six`: implementation `t_4f616d65`, review `t_f4aa5d71`, PR #15,
   merge `dd5ce8c`, 93 post-merge tests passed.
3. `is_seven`: implementation `t_47996c4d`, review `t_8f94f89d`, PR #16,
   merge `a82a451`, 97 post-merge tests passed.

Each canary declared exactly the five allowed paths with only the
`repository-source` and `tests` classifier components. The project-overview
document contained one factual helper-surface statement and no roadmap,
policy, operational, lifecycle, or authority claims.

## S0.7 boundary

S0.7 must preserve all of these invariants:

- No changes to the five-path bridge allowlist or to profile authority.
- No shell, Git, subprocess, arbitrary-path, patch, task-lifecycle, commit,
  or push authority for the Qwen worker.
- Exact controller-authored scope, formal review, archive, and
  `ready-to-commit` evidence for every future canary.
- Exact source/runtime SHA-256 parity and a passing runtime audit before each
  dispatch and after each merge.
- Platform handoff fields remain fail-closed: `changed_files` and
  `artifact_files`, when present, must each equal the reviewed diff exactly.

## Stability observation plan

Before any future authority proposal, collect a bounded set of three more
ordinary five-path canaries without changing the runtime contract. Record
their task IDs, review IDs, PRs, merge SHAs, post-merge tests, and runtime
audits. A scope escape, failed validation, metadata mismatch, unauthorized
mutation, review failure, or runtime parity failure stops the observation
period and requires a separately reviewed rollback or correction.

Any future request to add a new path is out of scope for S0.7. It requires a
new written proposal, explicit risk analysis, and separate human approvals for
source, runtime rollout, and first canary.
