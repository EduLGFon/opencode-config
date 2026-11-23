# Escalation, stalls and caps

An attempt ends when a worker returns, a reviewer returns FAIL, two risk
reviewers disagree, a check fails twice, or the unit is declared stalled.
Escalate one dimension at a time.

## Model policies

`default` follows the free-first ladder. `tier2-only` is the owner-enabled
task-wide policy: every worker, utility, explore and reviewer runs on the tier-2
pool, and escalation never leaves it. The matrix below is written for `default`;
apply these substitutions under `tier2-only`:

| `default` step | `tier2-only` step |
| --- | --- |
| free twin | another tier-2 family |
| next tier | raise the variant, then another tier-2 family |
| tier 2 | BLOCKED; the owner decides whether to lift the policy |

Never leave the active policy to recover from a quota error, a missing model or a
failed attempt.

## Detector

Run `watch.py --task <slug>` at the start of every turn. It reads the manifest and
reports:

| Exit | Meaning | Action |
| --- | --- | --- |
| 0 | healthy | continue |
| 1 | overdue unit, rate limit, or budget >= warning threshold | inspect; resume with findings or switch model within the policy |
| 2 | stalled unit or budget exceeded | mark the unit blocked, stop spending, report |

The detector is read-only. It never kills a child and never changes a state; the
orchestrator makes the call. Rate-limit counts come from recorded call errors, so
record failed launches with `task.py call --error`.

## Matrix

| Symptom | Action |
| --- | --- |
| timeout, network error, 429, quota error | retry once, same model and variant; then free twin; then tier 2 same family. `tier2-only`: retry once, then another tier-2 family; never a free tier. |
| model unavailable or not found | refresh `references/models.md` from the `models` tool, use the free twin or next tier, and update the reference. Never retry the dead ID. Under `tier2-only`, switch to another tier-2 family. |
| worker returns BLOCKED | read the reason. Bad brief or missing context: resume the same session with the missing facts. Environment or access problem: fix the environment or reassign, do not bump the model. A BLOCKED unit is never merged. |
| worker returns NO-OP | confirm the objective is already satisfied, report it and discard the worktree. Never merge an empty commit. |
| shallow output, missed files, ignored constraints | resume same session, same model, one variant up |
| reviewer FAIL with blocker/major | resume same session with the findings and failing commands, one variant up. Do not send the whole diff back. |
| reviewer BLOCKED | independence or environment failure: relaunch with another family or fix access. Never count it as PASS. |
| risk reviewers disagree | treat the unit as FAIL. Reconcile only after both independent verdicts; do not merge on one PASS. |
| flaky check | rerun once. Green rerun: record flaky and continue. Fails twice: treat as a real failure. Never auto-pass a known failure. |
| emergent dependency | stop the unit, set `depends-on`, and run it in the next wave after the other unit merges. Do not let it edit the other unit's files. |
| ownership change needed | stop the unit, update owned paths in the ledger, re-check every other unit for overlap, then resume. If overlap is unavoidable, merge the units or re-plan. |
| shared contract change | stop every affected unit and re-plan. Do not let workers invent incompatible contracts. |
| still failing after the effort bump | next tier, same family, same session; under `tier2-only`, another tier-2 family, same session |
| no completion notice within the effort bound | treat as stalled: keep the worktree and branch, mark the unit blocked, and relaunch in a fresh worktree with a tighter brief. Never poll in a loop. |
| ambiguous requirement or design conflict | stop the unit and ask the owner with the `question` tool. Do not merge on green checks alone. |
| security, privacy, schema or public-contract risk | keep the unit delegated at high effort; require two independent reviewers from distinct families; auto-merge only on dual PASS and green checks. |
| post-merge check fails | revert the unit's merge with a new commit immediately to keep the target green, then report the unit. |
| 2 model switches or 2 resumes on one unit | stop and report blocked with the full trail. |
| 3 units blocked for one cause, or a task budget cap | stop the task and report the budget trail. |

## Stalls

`watch.py` computes these bounds from the manifest's `started_at` timestamps and
the unit's effort. Use them as a guide, not a contract:

| Effort | Bound before treating the unit as stalled |
| --- | --- |
| low | 15 minutes |
| medium | 30 minutes |
| high / xhigh | 60 minutes |
| max | 90 minutes |

There is no status or kill tool for a running child. A stall is only observable
when the orchestrator next gets a turn, so a hung session may keep running after
you abandon it.

- Keep the stalled worktree and its branch. Do not remove, rebase or merge them
  while the child may still be running.
- Mark the unit and branch blocked; never reuse them.
- Start a fresh worktree and branch for the retry with a smaller scope.
- Remove the old worktree only after the task ends or the session is known finished.
- A stalled session can still be resumed later if its work is salvageable.

Keep units small so a stall is cheap.

## Caps

A paid call is one subagent invocation or resume on a tier 2 or tier 3 model.
Free calls do not count. Paid implementation attempts and paid review calls have
separate budgets. Under `tier2-only` every call is paid, including explore and
utility; count those as paid auxiliary calls.

| Cap | Limit |
| --- | --- |
| resumes per unit | 2 |
| model switches per unit | 2 |
| reviewer rounds per unit | 3, including re-reviews after a rebase; a parallel risk round counts as one |
| concurrent subagent sessions | 8; queue further units in waves |
| paid implementation attempts per unit | 3 |
| paid implementation attempts per task | 6 |
| paid review calls per task | 12 |
| paid auxiliary calls per task (explore, utility) | 6, only under `tier2-only` |
| units blocked for one cause | 3 |

Whichever cap trips first stops the unit or task. Report it as blocked with the
model, variant, session ID and failing evidence for every attempt.

## Escalation rules

- Change one thing per attempt: variant or model, never both.
- Prefer a free twin before paying. Prefer a higher variant before a new family.
  Under `tier2-only`, skip the free twin and switch to the cheaper tier-2 family
  after the variant step.
- Never invent a variant. Models with no variants escalate by twin or tier.
- On a resume, send only the findings, the diff summary and the new acceptance
  criteria. Keep the same session so context survives.
- Keep reviewers independent: never let the implementer review its own unit, never
  pass the worker's conclusions into the reviewer brief, and never share one risk
  reviewer's findings with the other.
- A review is only evidence when it names the reviewed SHA, the commands and the
  exit codes.
- Record owner approvals with their scope in the task header.
- Keep blocked worktrees until the report is written; they are the failure evidence.
  Remove them afterward, with force only after owner approval.

## Cost ledger

Record with `task.py call`; `task.py report` records the report call automatically.
Record for every call:

- task model policy (`default` or `tier2-only`)
- unit, attempt number, agent type
- provider/model#variant and tier
- free, paid implementation attempt, paid review call or paid auxiliary call, with
  the per-1M in/out price from `models.md`
- outcome: DONE, BLOCKED, NO-OP, PASS, FAIL, flaky, stalled

Summarize the ledger in the final report, including paid implementation attempts,
paid review calls and paid auxiliary calls separately. If a unit or the task
reaches a cap, report the spend before stopping.
