# Tool quick reference

All commands run from the skill directory. All tools are standard-library Python
and write task state under `/tmp/opencode/orchestrate/<slug>/`, never into the
product repository. `ORCHESTRATE_ROOT` overrides the root for tests.

## End-to-end flow

```text
preflight.py          G0: environment, model catalog, other orchestrators
task.py init          create the manifest (or use plan.py plan --write)
plan.py plan          validate units, build waves, estimate cost
plan.py brief         generate a self-contained worker/reviewer brief
task.py report        ingest the structured report, record the call, advance state
watch.py              every turn: stalls, rate limits, budget
gate.py unit          G2: SHA, scope, secrets, reviewer PASS, evidence
gate.py premerge      G3 read-only: clean target, serial lock
git merge             the orchestrator performs the merge (never the tools)
gate.py owner         G4: every unit terminal, spend and approvals complete
```

## `task.py` — manifest, reports

```text
task.py init --task SLUG --repo PATH --ref REF [--policy default|tier2-only] [--budget USD]
task.py add-unit --task SLUG --id U --objective TEXT --owned PATH[,PATH] ...
task.py set-unit --task SLUG --id U [--state STATE] [--worker-sha SHA] ...
task.py target --task SLUG [--ref REF] [--start-sha SHA]
task.py call --task SLUG --role ROLE --tier 0-3 --outcome OUTCOME ...
task.py report --task SLUG --file REPORT.json
task.py status --task SLUG [--json]
task.py check --task SLUG
task.py approve --task SLUG --scope TEXT [--note TEXT]
task.py path --task SLUG
```

- `report` validates the structured JSON, stores it, records a call, and advances
  the unit. See `references/reports.md`.
- `status` shows units, elapsed time, review progress and spend.
- `check` validates the manifest before G2/G4.
- Manifest schema and states: `references/manifest.md`.

## `watch.py` — T1 stall and rate-limit detector

```text
watch.py --task SLUG [--json] [--at ISO8601] [--rate-window MIN] [--budget-warn 0.8]
```

Read-only. Run it at the start of every orchestrator turn.

| Exit | Meaning | Action |
| --- | --- | --- |
| 0 | healthy | continue |
| 1 | overdue unit, rate limit, budget >= warn | inspect, switch model or resume with findings |
| 2 | stalled unit or budget exceeded | mark blocked, stop spending, report |

Suggestions respect the task policy: `default` uses the free twin and then asks
the owner before any paid tier (never auto-suggests tier 2/3); `tier2-only` stays
in the tier-2 pool and never falls back to free.

## `preflight.py` — G0

```text
preflight.py [--repo PATH] [--task SLUG] [--json] [--strict]
```

Checks git root, dirty tree (allowed), worktrees, existing `orch/*` branches,
instruction files, the model validator, disk space, Python version, and other
active manifests. It fails on owned-path overlap with another task and warns about
other active orchestrators. A non-git repo is a warning unless `--strict`, which
turns it into a failure and requires the owner decision.

## `gate.py` — G2 / G3 read-only / G4

```text
gate.py unit --task SLUG --unit UID [--json]
gate.py premerge --task SLUG --unit UID [--json]
gate.py owner --task SLUG [--json]
```

`unit` checks: manifest validity, unit state, worker DONE and reviewer PASS,
SHA pinning (every revision is format-validated before a git call, so option-like
values are rejected), clean worktree, diff scope, empty diff, secret paths and
added-line tokens (redacted), independent reviewer families, reviewer severity,
command exit codes and acceptance evidence, tier2-only model compliance, and
`default` paid-tier approval compliance (tier 2/3 need a recorded owner
approval). `premerge` checks
a clean target host and takes a non-blocking serial lock; it never merges.
`owner` checks G4 completeness.

The merge itself is always an explicit orchestrator git operation, followed by the
smoke test. A failed post-merge check reverts the merge with a new commit.

## `plan.py` and `brief.py` — T4

```text
plan.py plan SPEC.json [--json] [--write] [--force] [--max-concurrency 8]
plan.py brief --task SLUG --unit UID --role worker|reviewer|explore|utility \
  [--model MODEL#VARIANT] [--implementer-family FAMILY] [--out PATH]
```

`plan` validates the unit graph, rejects cycles, unknown dependencies and overlapping
owned paths, packs topological waves under the concurrency cap, and estimates cost
from optional token estimates. `--write` creates the manifest and units; the default
is a dry run. `brief` renders a self-contained brief with the `pwd` assertion,
owned paths, constraints, acceptance and the structured report contract.

## `validate-models.py` — catalog check

```text
python3 scripts/validate-models.py [--strict]
```

Validates every model row against the live catalog, keeps model IDs out of
`SKILL.md`, and warns about stale dates and unlisted catalog models. Run it in
preflight and before any task that changes models.

## Exit codes and failure policy

- `0` success; `1` gate/attention failure; `2` usage error, stalled unit or
  budget exceeded.
- A check that cannot run is a failure, not a pass.
- Non-git target: preflight warns, and fails under `--strict`. Proceed only after
  the owner decision is recorded in `task.py approve`; use a content hash as the
  reviewed/merge identity and treat the git-specific `gate.py unit` checks as not
  applicable, with manual scope and secret checks instead.
- If the harness does not expose token usage, record 0 and state in the owner
  report that spend is understated. Never invent token counts.
- Never edit the manifest by hand to make a gate pass. Fix the evidence or mark the
  unit blocked.
- Never store secrets, tokens or customer data in a manifest, report or brief.
