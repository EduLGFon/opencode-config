# Performance: where orchestration time and tokens go

Estimates below are reasoned planning guidance, not measured telemetry. Grounding
cites the skill text that creates the cost.

## 1. Where time and tokens go

| Cost driver | Why it costs | Source |
| --- | --- | --- |
| Fresh context per unit | Every subagent re-reads files and re-discovers conventions | `briefs.md` "Every subagent starts with a fresh context" |
| Repeated contracts/context in briefs | Worker and reviewer briefs paste the same contracts, snippets and commands | `briefs.md` Worker/Reviewer brief templates |
| Reviewer full-repo reads | Reviewer "inspects the full diff" and runs all checks even for trivial units | `briefs.md` Reviewer brief; `verification.md` §6 |
| Verbose prose reports | Reports list files, commands, exit codes, acceptance, SHA, status, follow-ups | `briefs.md` Report; `SKILL.md` §8 |
| Eager variant bumps | Effort labels jump to max/xhigh before exhausting cheaper variants | `models.md` effort table; `escalation.md` matrix |
| Full review for low-risk units | Normal-risk units still get build/format/lint/test + one reviewer | `verification.md` §6; `models.md` risk table |
| Serial waves and integration | One unit at a time through rebase, checks, merge, smoke | `SKILL.md` §1, §7; `integration.md` §1 |
| Re-review after rebase | range-diff `!`/`-`/`+` forces a fresh review round | `integration.md` §3 |
| Stall detection next turn only | No kill/status tool; hung children run until orchestrator's next turn | `SKILL.md` §6; `escalation.md` Stalls |
| Repeated catalog loads | `references/models.md` refresh + `validate-models.py` per task | `models.md` refresh rules; `SKILL.md` §1.5 |
| Exploration per unit | One Explore brief per unit re-reads the same repo | `briefs.md` Explore brief; `SKILL.md` §1 |
| Large output reports in chat | Full logs pasted instead of summarized | `briefs.md` Report; `SKILL.md` §8 |
| Manual preflight failures | Repo root, dirty tree, commands, model catalog done by hand | `SKILL.md` §1 |

## 2. Ranked recommendations

| # | Recommendation | Change | Expected effect (estimate) | Effort | Risk |
| --- | --- | --- | --- | --- | --- |
| 1 | One explore pass writes a compact context packet; briefs point to it by path | `briefs.md` templates (Context field) + new `references/context.md` convention; `SKILL.md` §1.4 | Cuts repeated file reads across units | M | Low |
| 2 | Diff-only review for normal-risk units; full review only for risk classes | `verification.md` §6; `briefs.md` Reviewer brief | Fewer tokens per review | S | Medium (misclassification) |
| 3 | One reviewer for normal risk; two only for security/persistent-data/public-api | Already in `tasklib.RISK_REVIEWS`; enforce in `SKILL.md` §2 and `verification.md` | Halves review calls on normal units | S | Low |
| 4 | Free models for explore/utility/mechanical; paid only after recorded failure | `models.md` task-to-family; `SKILL.md` §3 | Keeps paid calls near zero early | S | Low |
| 5 | Avoid max/xhigh by default; bump variant before switching family | `models.md` effort labels; `escalation.md` matrix | Lower per-call tokens | S | Low |
| 6 | Merge tiny related units; keep units under ~400 changed lines | `SKILL.md` §1.6, §2 | Fewer sessions and merges | S | Medium (scope creep) |
| 7 | Serialize only shared files/resources/contracts | `SKILL.md` §2; `integration.md` | More parallelism, fewer waves | S | Low |
| 8 | Single explore pass for the whole task, not one per unit | `briefs.md` Explore brief; `SKILL.md` §1 | Removes N duplicate explorations | S | Low |
| 9 | Output budgets: report length caps; findings minors/nits <= 5 each | `briefs.md` Report handling; `verification.md` §6 | Smaller reports, less chat tokens | S | Low |
| 10 | Command evidence as summary; full logs as files in evidence dir | `briefs.md` Report; `verification.md` §4 | Less inline log text | S | Low |
| 11 | Skip re-review when range-diff shows `=` only | `integration.md` §3 | Avoids redundant review rounds | S | Medium |
| 12 | Stall watch via manifest timestamps + `task.py status` | `escalation.md` Stalls; `tasklib.status` | Earlier stall detection | M | Low |

## 3. Context packet design

One explore pass per task writes `/tmp/opencode/orchestrate/<task-slug>/context.md`
(also record its path in the manifest `context` field per unit). Every worker and
reviewer brief references it by path instead of pasting facts.

Belongs in the packet: exact build/format/lint/test commands; repo layout and
entry points; architecture and module boundaries; coding conventions and patterns;
contract files (API shape, schema, types, UX); owned-path map and overlap notes;
known hazards (flaky tests, slow checks, shared resources); resource-isolation
assignment template.

Does not belong: conversation history; secrets or `.env` contents; full file dumps;
per-unit diffs; large command logs; speculative recommendations.

Briefs should say `Context: read <path> sections 2-4; do not re-explore`.

## 4. Review-efficiency design

Reviewers receive the diff, changed-file list and targeted commands only; no full
repo re-read. Normal-risk units get a diff-only review (diff + owned paths + the
changed tests). Full review (build/format/lint/test + acceptance walkthrough) is
reserved for `security`, `persistent-data` and `public-api` risk classes. Keep one
reviewer for normal risk and two independent reviewers from distinct families for
the three risk classes; reviewers never share findings. Do not weaken the security
rule: risk units stay high effort with dual review and auto-merge only on dual
PASS. Use `tasklib.required_reviews` / `approved_review_families` to gate PASS.

## 5. Model and effort routing

- Exploration, large reads, summarization: free models (`longcat`, `nemotron`,
  `inkling`) at no variant.
- Utility/mechanical edits (commits, renames, formatting): `big-pickle`,
  `mimo-v2.6-flash-free`, `beam-free#low`.
- Implementation: free flash family first; escalate variant, then family, then tier.
- Paid (tier 2) only after a recorded failure or when no free different-family
  reviewer exists.
- Avoid `max`/`xhigh` by default; raise the variant one step before switching
  families; prefer twin over new tier.
- Under `tier2-only`, every call is paid (explore and utility included); caps in
  `escalation.md` bind sooner, so report spend before stopping.

## 6. Batching and wave guidance

- Merge tiny related units before launch; one outcome per unit, but combine
  mechanical edits.
- Keep units under ~400 changed lines and ~10 files (`SKILL.md` §1.6).
- Serialize only on shared files, scarce check resources, or real dependencies.
- Run all independent units and reviews in one wave; queue overflow beyond 8
  concurrent sessions.
- One explore pass for the task; do not spawn an explorer per unit.

## 7. Output budgets

- Worker report: files, commands+exit codes, acceptance, HEAD SHA, status, open
  issues only.
- Reviewer findings: blockers and majors unlimited; minors <= 5; nits <= 5;
  out-of-scope items as follow-ups.
- Command evidence: summary (command, exit code, pass/fail, flake note); full logs
  written to `/tmp/opencode/orchestrate/<slug>/evidence/` and referenced by path.
- Final task report: ledger table + verdicts + conflicts + follow-ups; no pasted logs.

## 8. Task budget profile (planning estimates)

| Profile | Units | Max concurrent sessions | Worker+reviewer calls | Paid-call ceiling | Wall-clock target |
| --- | --- | --- | --- | --- | --- |
| Small | 1-3 | 4 | 2-6 | 0-2 | 1-2 h (estimate) |
| Medium | 4-8 | 8 | 8-16 | 3-6 | 3-6 h (estimate) |
| Large | 9-15 | 8 (queued waves) | 18-30 | 6-12 | 1-2 days (estimate) |

All figures are planning estimates, not measured facts.

## 9. Quick wins to adopt now

- [ ] `task.py init` + `add-unit` to replace the hand-written ledger (`tasklib` manifest).
- [ ] `task.py report` to ingest structured worker/reviewer reports instead of prose.
- [ ] `task.py check` as the gate runner before G2/G4.
- [ ] Add a `context` field (path to context packet) to unit templates and briefs.
- [ ] `scripts/watch.py` (stall/rate-limit exit codes) plus `task.py status` each turn.
- [ ] Cap findings in reviewer brief template (minors/nits <= 5).
- [ ] Evidence directory for full logs; summaries in reports.
- [ ] Single explore pass producing the context packet.

## 10. Do not do

- Do not skip reviews for risk units to save tokens.
- Do not share one risk reviewer's findings with the other.
- Do not merge without SHA pinning and range-diff `=`.
- Do not run parallel writers on the same file.
- Do not lower the tier for a security review to save money.
- Do not auto-pass a known failure because a rerun was green.
- Do not merge BLOCKED or NO-OP units.