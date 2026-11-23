# Task manifest and ledger

The manifest is the durable state of one orchestration task. It replaces prose in
`/tmp/opencode/orchestrate/<slug>.md` and survives an orchestrator restart.
Humans can read it; scripts parse it. The schema lives in `scripts/tasklib.py`.

## Location

`/tmp/opencode/orchestrate/<slug>/`

```text
task.json            manifest (single source of truth)
events.jsonl         append-only audit log
reports/             structured worker/reviewer/explore/utility reports
briefs/              generated briefs, when the orchestrator writes them
evidence/            command logs and captured evidence
```

`ORCHESTRATE_ROOT` overrides the root for tests and isolated runs. Never write the
manifest into the product repository.

## Schema v1

```json
{
  "version": 1,
  "task": "checkout-retry",
  "created_at": "2026-11-17T17:00:00Z",
  "updated_at": "2026-11-17T17:20:00Z",
  "target": {"repo": "/work/app", "ref": "main", "start_sha": "abc1234"},
  "policy": {"name": "default", "max_tier": 3, "allow_free": true},
  "budget": {"limit_usd": 5.0, "spent_usd": 0.42},
  "approvals": [{"at": "...", "scope": "tier 3 reviewer", "note": "owner approved"}],
  "contracts": [{"path": "api/openapi.yaml", "sha256": "..."}],
  "units": {
    "u1": {
      "id": "u1",
      "objective": "add retry to checkout",
      "worktree": "/work/app-wt/u1",
      "branch": "orch/checkout-retry/u1-20261117-1700",
      "base_sha": "abc1234",
      "depends_on": [],
      "owned_paths": ["src/checkout/retry.ts", "test/checkout/retry.test.ts"],
      "contracts": ["api/openapi.yaml"],
      "resource_isolation": "PORT=4101, DB=checkout_u1",
      "context": "context.md",
      "acceptance": ["npm test -- checkout", "npm run lint"],
      "effort": "high",
      "risk": "normal",
      "state": "merged",
      "attempts": 1,
      "model": "opencode-go/deepseek-v4.1-flash#max",
      "implementer_family": "deepseek",
      "session": "ses_...",
      "worker_sha": "def5678",
      "reviewed_sha": "def5678",
      "merge_sha": "0123abc",
      "reviewer_verdicts": [
        {"verdict": "PASS", "family": "beam", "sha": "def5678", "model": "opencode/beam-free#max"}
      ],
      "reports": ["reports/u1-worker-20261117T171000Z-01.json"],
      "started_at": "2026-11-17T17:00:00Z",
      "ended_at": "2026-11-17T17:20:00Z",
      "note": "",
      "follow_ups": []
    }
  },
  "calls": [
    {
      "at": "2026-11-17T17:10:00Z",
      "unit": "u1",
      "role": "worker",
      "model": "opencode-go/deepseek-v4.1-flash#max",
      "tier": 2,
      "session": "ses_...",
      "outcome": "DONE",
      "tokens_in": 12000,
      "tokens_out": 2500,
      "cost_usd": 0.0033,
      "error": null
    }
  ]
}
```

## Fields

| Field | Meaning |
| --- | --- |
| `target` | Product repo, integration ref and the start SHA. Worktrees must branch from it. |
| `policy` | `default` or `tier2-only`; `max_tier` and `allow_free` are derived and validated. |
| `budget` | Optional USD ceiling. `spent_usd` is computed from recorded calls and model prices. |
| `approvals` | Owner decisions with scope: tier 3, policy lift, force removal, non-git work. |
| `contracts` | Shared files and their hashes at task start; a change stops affected units. |
| `units` | One outcome, one worktree, one writer. `owned_paths` must not overlap. |
| `calls` | Every model invocation or resume, free or paid, with outcome and tokens. |
| `reviewer_verdicts` | One entry per review. PASS counts only for the current worker SHA, a family different from the implementer's, and a distinct family. |
| `reports` | Relative paths of structured reports; the latest per role is authoritative. |

## Unit states

```text
planned -> ready -> running -> review_ready -> reviewing -> passed -> merged
   |         |         |            |              |
   |         |         |            +-> changes_requested -> running
   |         |         +-> blocked
   |         +-> blocked
   +-> discarded / noop
```

- `blocked` and `noop` are valid outcomes but never mergeable.
- `passed` means G2 evidence is present but the merge has not happened.
- `merged` requires `reviewed_sha` and `merge_sha`.
- G4 is green only when every unit is `merged`, `blocked`, `noop` or `discarded`.

## Commands

Run from the skill directory:

```text
python3 scripts/task.py init --task SLUG --repo PATH --ref REF \
  [--policy default|tier2-only] [--budget USD] [--start-sha SHA]
python3 scripts/task.py add-unit --task SLUG --id U1 --objective TEXT \
  --owned PATH[,PATH] [--depends-on U0] [--effort low|medium|high|xhigh|max] \
  [--risk normal|security|persistent-data|public-api] [--model MODEL#VARIANT] \
  [--implementer-family FAMILY] [--acceptance CMD] [--contract PATH]
python3 scripts/task.py set-unit --task SLUG --id U1 [--state STATE] \
  [--worker-sha SHA] [--reviewed-sha SHA] [--merge-sha SHA] [--note TEXT] \
  [--follow-up TEXT]
python3 scripts/task.py target --task SLUG [--ref REF] [--start-sha SHA]
python3 scripts/task.py call --task SLUG --role ROLE --tier N --outcome OUTCOME \
  [--unit U1] [--model MODEL#VARIANT] [--session SES] \
  [--tokens-in N] [--tokens-out N] [--error TEXT]
python3 scripts/task.py report --task SLUG --file REPORT.json
python3 scripts/task.py status --task SLUG [--json]
python3 scripts/task.py check --task SLUG
python3 scripts/task.py approve --task SLUG --scope TEXT [--note TEXT]
python3 scripts/task.py path --task SLUG
```

`report` validates the report, stores a copy, records one call, and advances the
unit state. The orchestrator may also record calls explicitly when a launch fails
before a report exists.

## Invariants

- One manifest per task; writes are atomic (`task.json` is replaced, never
  partially written). Only the orchestrator writes it.
- A unit's `owned_paths` are disjoint from every other unit's. Overlap is an error,
  not a warning.
- Dependencies form a DAG.
- A passed or merged unit has a worker SHA and the required number of independent
  PASS verdicts.
- A `tier2-only` task records tier 2 calls only.
- Spend is append-only; do not edit `spent_usd` by hand.
- Never store secrets, tokens or customer data in any field. Use paths and hashes.

## Status and watch

`task.py status` prints units, elapsed time, review progress and spend.
`scripts/watch.py` adds stall and rate-limit exit codes and next-action
suggestions. Run it at the start of every orchestrator turn.
