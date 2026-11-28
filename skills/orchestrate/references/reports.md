# Structured reports and model responses

Every worker, reviewer, explore and utility session ends with a structured report.
The prose stays for humans; the JSON is the contract that the orchestrator,
manifest and gates parse. A report without the JSON block is incomplete evidence.

## Response envelope

The report is the **last thing** in the subagent response, in one fenced `json`
block. Do not split it across blocks and do not wrap it in another object.

```json
{
  "version": 1,
  "unit": "u1",
  "role": "worker",
  "status": "DONE",
  "sha": "def5678",
  "files": ["src/checkout/retry.ts"],
  "commands": [{"cmd": "npm test -- checkout", "exit": 0, "duration_s": 41.2}],
  "acceptance": [{"criterion": "retry test passes", "evidence": "1 passed", "pass": true}],
  "findings": [],
  "usage": {"tokens_in": 12000, "tokens_out": 2500},
  "model": "opencode-go/deepseek-v4.1-flash#max",
  "tier": 2,
  "family": "deepseek",
  "session": "ses_...",
  "notes": "short factual note",
  "next": "ready for review"
}
```

### Required fields

| Field | Worker | Reviewer | Explore / utility |
| --- | --- | --- | --- |
| `version` | 1 | 1 | 1 |
| `unit` | yes | yes | yes |
| `role` | yes | yes | yes |
| `status` | DONE, BLOCKED, NO-OP | PASS, FAIL, BLOCKED | DONE, BLOCKED, NO-OP |
| `sha` | HEAD SHA or null | reviewed SHA or null | null |
| `files` | yes | yes | yes |
| `commands` | at least 1, each with integer `exit` | at least 1 | optional |
| `acceptance` | at least 1, each with boolean `pass` | at least 1 | optional |
| `findings` | optional | required list | optional |
| `family` | optional | yes, reviewer's model family | optional |
| `usage` | optional | optional | optional |
| `model`, `tier` | recommended | recommended | recommended |

Findings use `blocker`, `major`, `minor` or `nit`; each needs `message` and should
carry `file` and `line` when possible. A PASS requires zero blockers and zero
majors. A DONE requires every command to exit 0 and every acceptance item to pass.

### Status semantics

- Worker `DONE`: the objective is complete in the worktree. The orchestrator still
  verifies the SHA, scope and secret scan.
- Worker `BLOCKED`: missing context, environment or an unavoidable scope/contract
  conflict. Never commit a partial guess.
- Worker `NO-OP`: the objective was already satisfied; no diff is expected.
- Reviewer `PASS`: independent review with green commands and no blocker/major.
- Reviewer `FAIL`: at least one blocker or major, or a failed check.
- Reviewer `BLOCKED`: independence or environment failure. Never count as PASS.

### Size budget

Keep the report compact. Full command logs belong in `evidence/`, not in chat.
Cap `minor` findings at 5 and `nit` findings at 5; blockers and majors are never
capped. Put long evidence in a file and reference its path.

## Orchestrator ingestion

```text
python3 scripts/task.py report --task SLUG --file report.json
python3 scripts/task.py report --task SLUG --json '<inline json>'
```

Ingestion validates the schema, copies the report into `reports/`, records one
call with model/tier/tokens/cost, and advances the unit:

- worker DONE -> `review_ready`; BLOCKED -> `blocked`; NO-OP -> `noop`
- explore DONE -> `review_ready` when it wrote files, otherwise `noop`; BLOCKED -> `blocked`
- reviewer PASS -> `reviewing` until the required independent families have passed;
  then `passed` and `reviewed_sha` is pinned
- reviewer FAIL -> `changes_requested`
- reviewer BLOCKED -> state unchanged, note appended

The latest report per role is authoritative. A report for an older SHA never
counts toward a PASS.

## Independence

The reviewer brief names the implementer's model and family. A reviewer report
whose `family` equals `implementer_family` is discarded; it cannot count toward
PASS. Risk units need two PASS verdicts from two distinct families other than the
implementer's. Reviewers do not share findings; disagreement is FAIL.

Under `tier2-only`, reviewer families come from the tier-2 pool. Under `default`,
prefer a free different family; a tier-2 reviewer only after the owner has explicitly
said to use tier 2.

## Secrets

Reports, briefs and evidence never contain secrets, tokens,
credentials, customer data or `.env` content. Redact command output before storing
it. A secret-looking path or added line fails the gate; report the path and pattern
name, never the value.
