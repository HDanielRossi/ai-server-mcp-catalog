# Implementation routing contract: formal-review reviewer identity (D029)

This document is the normative contract for the effective identity of the
formal-review reviewer as enforced by `scripts/hermes-pipeline-controller.py`.
It is a repository-side validation contract only: it does not install
profiles, change the installed Hermes runtime, or call a model/provider API.
Kanban mutation still occurs only through the controller's authorized
lifecycle subcommands, and commit/push remain human-only.

D029 is a clean successor of D028 on the current lifecycle. It adapts only the
D028 reviewer execution-identity guarantee; the preserved D026/D028 chain is
not merged or cherry-picked, and S0.5-S0.7 routing scope and the A7 controller
locks are unchanged in meaning.

## Two separate identity contracts

Human-facing labels and executable identifiers are distinct contracts and are
never substituted for each other:

| Contract   | Profile    | Model         | Provider       |
|------------|------------|---------------|----------------|
| Label      | `reviewer` | `GPT-5.6 Sol` | `OpenAI Codex` |
| Executable | `reviewer` | `gpt-5.6-sol` | `openai-codex` |

The labels are for human reporting only. Only the executable slugs cross the
Hermes boundary: `create_review` passes `--assignee reviewer --model
gpt-5.6-sol --provider openai-codex` to `hermes kanban create`. Each triple is
pinned independently by a SHA-256 digest of its newline-joined values, so
drifting any single constant fails closed (`reviewer_execution_pin_mismatch`
or `reviewer_label_pin_mismatch`) instead of silently moving the accepted
identity.

## Evidence

Reviewer identity is never accepted from reviewer-authored completion
metadata. The reviewer SOUL does not require identity fields; if
`reviewer_profile`, `reviewer_model` or `reviewer_provider` are present they
must equal the label contract, otherwise the review is rejected as
contradictory (`reviewer_self_report_contradiction`). Their presence is never
sufficient on its own.

The executable identity is proven only from Hermes-owned records of the review
task:

1. **Current configuration.** `task.assignee`, `task.model_override` and
   `task.provider_override` equal the executable triple.
2. **Immutable creation pin.** The task history has exactly one `created`
   event, it is the first event, it carries no run id, and its payload records
   the same `assignee`, `model_override` and `provider_override`.
3. **No later mutation.** The history contains no `model_override_set` event
   and no event kind outside the controller's recognized vocabulary. An
   override changed away and back therefore still fails closed, because the
   mutation event cannot be erased.
4. **Run binding.** The accepted review run (the run with the maximum integer
   id) has profile `reviewer` and exactly one run-scoped `claimed` event whose
   `run_id` and `payload.run_id` both equal the accepted run id. A claim for an
   earlier run never attests a later one, and any claim for a later run id
   makes the accepted run stale.

Installed Hermes exposes no model or provider field on task runs. The current
task configuration, the immutable `created` event, the absence of override
mutations and the run-bound `claimed` event are together the strongest
run-bound execution evidence available without changing the installed
runtime; no run-level attestation is fabricated.

## Enforcement points

- `create-review` reads the created (or idempotently replayed) task back with
  `hermes kanban show` and reports success only if ownership (task id,
  single implementation parent, workspace) and evidence items 1-3 hold.
  Otherwise it blocks with `reviewer-execution post-condition failed`.
- `check`, `create-correction` and `archive-review` require evidence items 1-4
  for every task carrying the `HERMES_FORMAL_REVIEW_V1` marker
  (`FORMAL_REVIEW_REVIEWER_UNATTESTED`). Unmarked historical reviewer tasks
  keep their existing compatibility.
- `ready-to-commit` requires evidence items 1-4 for the review it attests,
  marked or not, before verdict, archive and repository-state gates run.

## Fail-closed cases

Missing evidence (no events, no `created` event, no claim for the accepted
run), stale evidence (a claim only for another run, or a later claimed run),
and contradictory evidence (current configuration or creation payload
differing from the pin, display labels used as executable values, an override
mutation, an unrecognized event, a malformed claim, or a contradicting
self-report) all reject. Review tasks created before D029 carry no
executable overrides and therefore cannot become READY_TO_COMMIT; because
`create-review` replays them by idempotency key, they are reported as blocked
rather than silently upgraded.
