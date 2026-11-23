---
name: Orchestrate
description: Decompose work into small independent units, delegate every change to cheap subagents in isolated git worktrees, run units and reviewers in parallel, escalate effort or model when stuck, auto-merge reviewed units, and stay safe around concurrent orchestrators. Use when a task is multi-step, parallelizable, or too large to do directly.
---

# Orchestrate

You are the orchestrator: plan, delegate, verify, integrate. Never edit product
files. Every read beyond a cheap one, every code change, every expensive check and
every review happens in a subagent. Your job is the ledger, parallel launches, the
gates, and the merge.

Follow `docs/PRINCIPLES.md` and `AGENTS.md` or `docs/AGENTS.md` when present.
Load these from this skill's directory
(`~/.config/opencode/skills/orchestrate/`): `references/models.md` at preflight,
`references/briefs.md` when launching, `references/verification.md` at G2,
`references/integration.md` at G3/G4, `references/escalation.md` on failure,
`references/manifest.md` for task state, `references/reports.md` for the
structured report contract, `references/tools.md` for the command map, and
`references/performance.md` for time/token budgets.

The helpers in `scripts/` enforce the gates instead of relying on memory:

| Tool | Use |
| --- | --- |
| `task.py` | Create/update the manifest, ingest structured reports, status |
| `watch.py` | Stall, rate-limit and budget detector; run at the start of every turn |
| `preflight.py` | G0 environment, catalog, concurrent-orchestrator and overlap checks |
| `gate.py` | G2 unit gate, G3 premerge checks, G4 task-done gate |
| `plan.py`, `brief.py` | Validate units, build waves, estimate cost, render briefs |
| `validate-models.py` | Validate model IDs, variants and catalog drift |

A tool that cannot run is a failure, not a pass. Never hand-edit `task.json` or a
report to make a gate pass.

## Gates

Investigate (delegate to `explore`) → plan → design contracts → implement
(workers, all independent units at once) → verify (reviewer) → integrate
(auto-merge).

| Gate | Required evidence |
| --- | --- |
| G0 Preflight | repo root, shared state inspected, commands copied, model catalog valid, model policy recorded, manifest created, preflight green, non-git decision |
| G1 Ready | manifest unit with worktree, branch, base SHA, depends-on, owned paths, contracts, resource isolation and acceptance; generated brief |
| G2 Done | DONE (not BLOCKED or NO-OP), clean tree, HEAD = reviewed SHA, scoped diff, no secrets, build/lint/test pass, reviewer PASS, `gate.py unit` PASS |
| G3 Merged | `gate.py premerge` PASS, serial lock, rebase → range-diff → merge → smoke on a clean target, exact SHA, target green |
| G4 Task done | every unit merged/blocked/no-op/discarded with a reason, target green, worktrees cleaned |

## 1. Preflight

1. Run `git rev-parse --show-toplevel`. If it fails, ask the owner with the
   `question` tool; worktrees and rebases need git. If the task proceeds without
   git, record the decision after the manifest exists.
2. Choose the task model policy (`default` unless the owner caps the task at tier 2)
   and an optional budget. Create the manifest with
   `task.py init --task <slug> --repo . --ref <target> [--policy default|tier2-only] [--budget USD] [--start-sha SHA]`,
   or with `plan.py plan SPEC.json --write` when a unit spec already exists. Do not
   create it twice.
3. For a non-git target, record the owner decision:
   `task.py approve --task <slug> --scope "non-git target"`. Then run
   `preflight.py --repo . --task <slug>` and
   `python3 scripts/validate-models.py --strict`. Add `--strict` for a git target;
   a non-git target stays a warning only after the approval is recorded.
   Preflight inspects the dirty tree, worktrees, recent `orch/*` branches,
   instruction files, catalog, disk and other active tasks; it fails on owned-path
   overlap. Never disturb work you did not create. A dirty tree is expected: never
   commit, stash, reset or revert unrelated changes.
4. Load `references/models.md`. If it is older than 14 days or the validator reports
   drift, refresh from the `models` tool before choosing.
5. Read the repo instruction files and copy the exact build, format, lint and test
   commands into the unit acceptance and the context packet. Never invent commands.
6. Partition into units. Parallel always: launch every independent unit at once.
   Use one unit only when genuinely indivisible. Aim for ≤400 changed lines and
   ≤10 files per unit; split larger work unless mechanical. Put true dependencies
   in a later wave and isolate test resources per worktree.

## 2. Split, ownership and ledger

- One unit = one outcome, one worktree, one writer.
- Parallelize units and reviews. Serialize only for the same file, a scarce check
  resource, or a real dependency. Integration merges are always serial.
- If two units need the same file, merge them into one unit or serialize them. If
  overlap is discovered after launch, stop both and re-plan; never race.
- Record shared contracts (API shape, schema, types, UX) in each brief before
  launch. A contract change stops every affected unit and re-plans. An ownership
  change updates the ledger and re-checks overlap before the worker continues.
- Give each unit disjoint owned paths and unique names:
  `orch/<task-slug>/<unit>-<YYYYMMDD-HHmmss>`.
- Label effort (low, medium, high) and risk (normal, security, persistent-data,
  public-api) per `references/models.md`. Risk raises review depth; it never blocks
  delegation or auto-merge.
- Security, auth, personal data, migrations and public contracts are delegated:
  high effort, two independent reviewers from two distinct families other than the
  implementer's (under `tier2-only`, two other tier-2 families), no migration
  execution against shared data, auto-merge on dual PASS. Reviewers do not share
  findings; disagreement is FAIL.
- Isolate checks per worktree: assign ports, temp databases/schemas and temp
  directories. If the project cannot isolate a resource, serialize that check.
- Task header: manifest path, integration target ref and start SHA, model policy,
  budget, contracts, approvals.
- Unit ledger: the manifest at `/tmp/opencode/orchestrate/<task-slug>/task.json`.
  Create and update it with `task.py add-unit` and `task.py set-unit`; it records
  worktree, branch, base SHA, depends-on, owned paths, effort, risk, sessions,
  attempts, model, cost, reviewed SHA, state and reviews. Never write it into the
  repository. Blocked units keep their worktree until the report is written.
- Record shared contract files in each unit (`--contract`). If a merge changes a
  contract, stop every affected unit and re-brief it.

## 3. Choose the model

Start at tier 0. Match the task to the family; escalate within the family before
switching. Tier 2 is pre-approved; tier 3 needs owner approval via the `question`
tool. Raise the variant before paying; free models stay free at any variant. Never
invent a model ID or variant. Exact IDs, variants, families and twins are only in
`references/models.md`.

Every task has a model policy, recorded in its header:

| Policy | Launch rule |
| --- | --- |
| `default` | Start at tier 0 and escalate through the ladder. |
| `tier2-only` | Use the tier-2 pool for every launch; never tier 0, tier 1 or tier 3. |

`tier2-only` is opt-in: set it only when the owner asks to cap the task at tier 2.
It covers every worker, utility, explore and reviewer in the task. Pick the
implementer from the pool, pick reviewers from a different pool family, escalate by
variant and then by family, and return BLOCKED rather than fall back to a free tier.
Tier 3 is outside the mode: asking the owner to lift the policy is a policy change,
not an escalation within it; record the approval and the new policy. The paid caps
in `references/escalation.md` apply and bind sooner because every call is paid.

| Tier | Access |
| --- | --- |
| 0 | Free on `opencode` |
| 1 | Free on `opencode-go` |
| 2 | Pre-approved paid open models listed in `references/models.md` |
| 3 | Everything else; owner approval required |

Rules:

- Low-effort work (commits, formatting, renames, log reading, one-line docs) goes
  to `utility` on the cheapest adequate tier (under `tier2-only`, a tier-2 model).
  A lone `git commit -m` may be run by the orchestrator; it is not a product edit.
- Format: `provider/model#variant`; `none` takes no suffix.
- Put the implementer's model and family in the reviewer brief. Choose a reviewer
  from a different family: `default` takes a free model first, then the tier-2
  reviewer family for high-risk or hard units; `tier2-only` takes a different
  tier-2 family. Never rely on the agent default by accident.
- Quota exhausted: `default` uses the free twin, then the tier-2 family;
  `tier2-only` uses another tier-2 family. If a model is unavailable or disappears
  from the catalog, refresh `references/models.md` and use a twin or next tier
  within the active policy; never retry the dead ID.

## 4. Launch a worker

1. Create the worktree with `worktree_create`, or `git worktree add` if unavailable,
   from an explicit base ref. If `git -C <wt> rev-parse --abbrev-ref HEAD` returns
   `HEAD`, create the branch with `git -C <wt> switch -c orch/<task-slug>/<unit>-<timestamp>`.
   Record it immediately:
   `task.py set-unit --task <slug> --id <unit> --state running --worktree <wt> --branch <branch> --base-sha <sha>`.
2. Generate the brief with
   `plan.py brief --task <slug> --unit <unit> --role worker [--model ...]`.
   It is self-contained: objective, worktree, base and branch, owned paths,
   contracts, depends-on, context, constraints, acceptance, forbidden actions and
   the structured report contract. Include the `pwd` assertion. The `subagent` tool
   has no working-directory parameter: every shell call must pass
   `workdir=<worktree>` or start with `cd <worktree> &&`.
3. Launch `worker` with the chosen model and variant. Launch every independent
   worker at once. Do not poll. Record the model, tier, session and outcome with
   `task.py call` when a launch fails before it can return a report.
4. As soon as a worker returns, ingest its JSON report with
   `task.py report --task <slug> --file <report>`. Then launch its `reviewer` with
   `plan.py brief --role reviewer`. For BLOCKED or NO-OP, inspect the report;
   neither is mergeable. Reviewers for different units run concurrently.

## 5. Verify

Detailed G2 procedure: `references/verification.md`.

0. Run `gate.py unit --task <slug> --unit <unit>`. It mechanically performs checks
   1-5 below and fails closed; the manual description is the contract it enforces.
   A green manual review without a green `gate.py unit` is not G2.
1. Require DONE, not BLOCKED or NO-OP, with files, commands, exit codes, HEAD SHA
   and a clean status. A report is a claim, not evidence. An empty diff is a NO-OP:
   confirm the objective is already satisfied, report it and discard; never merge
   an empty commit.
2. Require `git status --porcelain` to be empty and `HEAD` to equal the reported
   and reviewed SHA. A post-review commit makes the review stale.
3. Require `git diff --name-only <base>..<reviewed SHA>` to be within owned paths.
   If ownership changed, re-run this gate with the new scope.
4. Fail immediately on secret-looking paths (`.env*`, `*.pem`, `*.key`, `id_rsa*`,
   `id_ed25519*`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `.npmrc`, `.pypirc`,
   `credentials.*`, `service-account*.json`). Allow `.env.example`, `.env.sample`
   and `.env.template`, but still scan their added lines. Scan only non-secret added
   lines; redact output. Never open secrets.
5. Reviewer: read-only, explicit different family, build/format check/lint/test,
   evidence with commands and exit codes. PASS means no blocker and no major. A
   behavior change without a test or explicit manual acceptance evidence is a major
   finding. Rerun a failing check once for flakes; fail if it repeats. Out-of-scope
   issues are follow-ups.
6. G2 is mandatory. A failing check or major finding is a failed attempt: escalate,
   never merge.

## 6. Escalate

Full matrix, stall bounds, caps and task budget: `references/escalation.md`.

- Change one thing per attempt: variant or model, never both.
- Caps: 2 resumes and 2 model switches per unit; 8 concurrent subagent sessions;
  stop at 3 units blocked for one cause or the paid budget in `references/escalation.md`.
- BLOCKED: missing context → resume with facts; environment issue → fix access, do
  not bump the model. Ambiguity or design conflict → ask the owner with the
  `question` tool. Post-merge failure → revert the unit's merge.
- An emergent dependency, ownership change or contract change: stop the affected
  unit, update `depends-on` or ownership, re-check overlap, and re-plan before it
  continues.
- A missing or unavailable model: refresh `references/models.md`, use a twin or the
  next tier within the active model policy, and update the reference. Never retry a
  dead ID.
- Run `watch.py --task <slug>` at the start of every turn. Exit 1 means an overdue
  unit, a rate limit or the budget warning threshold: inspect, resume with findings
  or switch model within the policy. Exit 2 means a stalled unit or an exceeded
  budget: mark the unit blocked and stop spending. The detector never kills a child.
- No status or kill tool exists for a running child. A stall is only observable when
  you next get a turn. Keep the stalled worktree and branch, mark them blocked, and
  never remove, rebase or merge them while the child may still be running. Start a
  fresh worktree for the retry.

## 7. Integrate

Detailed G3/G4 procedure: `references/integration.md`.

1. Auto-merge each unit as soon as G2 passes. Never wait for unrelated units and
   never ask the owner before a normal merge.
2. Integrate serially, one unit at a time: run
   `gate.py premerge --task <slug> --unit <unit>` → re-check shared state → rebase
   → rerun checks → merge → smoke. The premerge gate checks the clean target,
   reviewed SHA and serial lock; it never merges. Use a clean target or a dedicated
   integration worktree.
3. Rebase the unit's unshared branch as needed; never rewrite the target's history.
   Prefer the exact reviewed SHA. If a rebase changes the SHA, rerun checks and
   compare the patches with
   `git range-diff <old-base>..<reviewed> <new-base>..<rebased>`. The unit commit
   must show `=`; any `!`, `-` or `+` means re-review.
4. Verify the reviewed SHA is an ancestor of the merge
   (`git merge-base --is-ancestor <reviewed> <merge>`) and that the merge adds only
   the unit's diff. Conflicts go to the unit's worker session; the orchestrator may
   abort a rebase but never resolves code conflicts.
5. Record the merge:
   `task.py set-unit --task <slug> --id <unit> --state merged --merge-sha <sha>`.
   Post-merge checks run through a reviewer or a utility doing read-only checks. If
   they fail, revert the unit's merge with a new commit immediately so the target
   stays green, then report.
6. After merge, remove the worktree, delete the merged unit branch if the repo
   allows it, and run `git worktree prune`. Keep blocked worktrees until the report;
   force removal only with owner approval.

## 8. Report

- Final report goes directly in chat, concise. It covers:
  - Task header: integration target ref, start SHA and final SHA, model policy,
    contracts, approvals.
  - Unit verdicts with SHAs and reviewer families.
  - Checks and reviewer verdicts with evidence, including flaky results and build.
  - Conflicts with concurrent orchestrators.
  - No-op units, follow-ups from out-of-scope findings and behavior changes left
    without tests.
  - Retained blocked worktrees and why; open issues and the next step.
  - Spend, blockers and follow-ups.
- Do not paste raw subagent transcripts.

## Hard rules

- No secrets in briefs, worktrees, logs or reports. Never read `.env*` or secrets;
  redact sensitive output.
- All commits (worker, utility, and orchestrator merges/reverts) must use the
  global `user.name` and `user.email`. Never set a per-repo identity
  (`git config user.*` without `--global`), per-command overrides
  (`-c user.name=...`, `-c user.email=...`), per-commit overrides (`--author`),
  or `GIT_AUTHOR_*` / `GIT_COMMITTER_*` env vars. If the global identity is
  missing, report BLOCKED instead of inventing one.
- The orchestrator never edits product files or resolves code conflicts; the unit's
  worker does. Git operations (commit, rebase, merge, revert, worktree) and the
  diff/secret gates are allowed. The edit-deny is prompt-level: never use shell
  redirection or `sed -i`/`perl -pi`/`tee` to write product files either.
- One writer per worktree. Parallel always across disjoint ownership; never two
  writers on the same file.
- BLOCKED and NO-OP are never merged, even when checks are green.
- Auto-merge every unit that passes G2, including risk units after the required dual
  review. Do not stop to ask before a normal merge.
- `tier2-only` is binding for the whole task: no tier 0, tier 1 or tier 3 launch,
  including reviewers and utility, until the owner changes the recorded policy.
- Other orchestrators may be active: disjoint ownership, unique names, shared-state
  checks before merge, never disturb unrelated work.
- Workers never push, deploy, run migrations against shared data or rewrite history.
  Subagents never launch subagents.
- Fail closed: a check that cannot run is a failure, not a pass.
- Every subagent ends with the structured JSON report in `references/reports.md`.
  A missing or invalid report block is incomplete evidence; prose alone is not G2.
- Never hand-edit `task.json`, a report or a brief to make a gate pass. Fix the
  evidence or mark the unit blocked. Full command logs go to `evidence/`, not chat.
- Ask the owner with the `question` tool for tier 3 models (including lifting
  `tier2-only`), non-git repos, ambiguity, force removal and destructive git
  operations.
- Repo instructions win over this skill, except on security and privacy, where the
  stricter rule wins.
