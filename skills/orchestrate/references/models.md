# Models, tiers and variants

Verified against the live catalog on 2026-11-26. Model IDs and variants drift.
Refresh if this file is older than 14 days, before a large task, or whenever a
launch fails:

- `models({ provider: "opencode", all: true })`
- `models({ provider: "opencode-go", all: true })`

Refresh checklist:

1. Compare every ID and variant below with the live result.
2. Update the tier tables, free-twin map and the verification date.
3. Check that `SKILL.md` contains no model IDs or variants; this file is the only
   source of truth for them.
4. Never launch a guessed model ID or an invented variant.
5. Run `python3 ~/.config/opencode/skills/orchestrate/scripts/validate-models.py`.
   It validates model IDs and providers against `opencode models` and checks that
   `SKILL.md` contains no model IDs. Variants still need the manual comparison from
   step 1.

If a model is missing at launch, refresh this file and use a twin or the next tier.
Never retry a dead ID.

## Tier 0 — free on `opencode`

| Model | Family | Variants |
| --- | --- | --- |
| `beam-free` | beam | low, medium, high, xhigh, max |
| `tidepool-alpha-free` | tidepool | minimal, low, medium, high, xhigh, max |
| `deepseek-v4.1-flash-free` | deepseek | low, high, max |
| `mimo-v2.6-flash-free` | mimo | none |
| `flint-40b-free` | flint | low, medium, high, xhigh, max |
| `nemotron-3.5-super-vl-free` | nemotron | none, minimal, low, medium, high, xhigh |
| `nemotron-3.5-lightning-free` | nemotron | none |
| `hy4-flash-free` | hy | none, high |
| `iceman-free` | iceman | none |
| `big-pickle` | big-pickle | none |
| `muse-ember-1.0` | muse | minimal, low, medium, high, xhigh, max |
| `kimi-k3.1-pro` | kimi | low, high, max |
| `seaglass-alpha-free` | seaglass | none |

## Tier 1 — free on `opencode-go`

| Model | Family | Variants |
| --- | --- | --- |
| `beam-free` | beam | low, medium, high, xhigh, max |
| `hy4-flash-free` | hy | none, high |
| `iceman-contributor-free` | iceman | none |
| `hungerg-free` | hungerg | none |

## Tier 2 — paid open models (owner approval required)

| Model | Family | Variants | USD per 1M in/out |
| --- | --- | --- | --- |
| `deepseek-v4.1-flash` | deepseek | low, high, max | 0.15 / 0.60 |
| `muse-spark-1.3-contributor` | muse | minimal, low, medium, high, xhigh | 0.10 / 0.20 |
| `glm-5.3-flash` | glm | low, high, max | 0.15 / 0.50 |
| `hy3` | hy | none, low, high | 0.14 / 0.58 |
| `mimo-v2.6-flash` | mimo | none | 0.14 / 0.28 |
| `minimax-m3.1-flash` | minimax | low, medium, high, xhigh, max | 0.15 / 0.60 |
| `qwen3.8-flash` | qwen | none, low, medium, xhigh | 0.15 / 0.47 |

Tier 2 is the cost-capped open-model pool. It is NOT pre-approved: every tier 2
launch needs the owner to explicitly say to use it, recorded with
`task.py approve --task <slug> --scope "tier 2 <reason>"` before the first paid
launch. The orchestrator never prompts for tier 2 under `default`. A model belongs
here only when it is open-weight, priced at or below
**$0.20 per 1M input and $0.60 per 1M output** tokens in every context tier, and is
the newest version of its family in the live catalog. The table lists the current
members; add an equivalent model by editing this file, verifying the live catalog,
removing any duplicate tier 3 row and running `scripts/validate-models.py`. Never
add a model by inference at launch.

Tier 2 is owner-approved only; use it for hard units after free tiers stall, when a
free twin does not exist, or when no free reviewer from a different family is
available - and only after the approval is recorded.

Reviewer selection: put the implementer's model and family in the reviewer brief,
then prefer a free model from a different family. A tier-2 reviewer (e.g.
`muse-spark-1.3-contributor#high` for high-risk units or hard diffs) only after the
owner has explicitly said to use tier 2. If the only available reviewer is the
implementer's family, report BLOCKED instead of running a non-independent review.
The reviewer agent default is a fallback, not the policy.

## Tier 2 only policy

`tier2-only` is a task-wide model policy, opt-in and recorded in the task header.
The owner enables it; never infer it. In this mode:

- Every launch uses a model from the tier 2 table: worker, utility, explore and
  reviewer. Never launch a tier 0 or tier 1 free model and never launch tier 3.
- Implementer: choose by task family as usual, restricted to the pool.
- Reviewer: choose a different tier-2 family. The pool has enough families for the
  two-reviewer rule on risk units, so no exception is needed.
- Escalation: raise the variant, then switch to another tier-2 family. A quota
  error or a missing model never falls back to a free tier; if no tier-2 family is
  usable, report BLOCKED.
- Tier 3 is outside the mode. To use it, the owner must explicitly lift
  `tier2-only`; record the decision and the new policy in the task header.
- Paid caps in `escalation.md` still apply. Every call is paid, so they bind much
  sooner; report spend before stopping.

## Tier 3 — owner approval required

Tier 3, like tier 2, is forbidden by default. Under `default` the orchestrator
may only use it after the owner explicitly says to (tier 3 may be requested via
the `question` tool + `task.py approve --task <slug> --scope "tier 3 <reason>"`).
Everything not listed above. Common examples (superseded versions of a pool model
also stay here):

| Model | Family | Variants | USD per 1M in/out |
| --- | --- | --- | --- |
| `hy4` | hy | none, high | 0.834 / 2.501 |
| `hy4-preview` | hy | none, high | 0.834 / 2.501 |
| `longcat-2.5` | longcat | none | 0.30 / 1.20 |

The `deepseek` provider also exposes `deepseek-flash` (none, low, high, max;
0.15/0.60) and `deepseek-v4-pro` (none, low, high, max; 0.66/1.98). Treat both
as tier 3.

## Variant semantics

- `none` means the model takes no suffix, e.g. `opencode-go/iceman-contributor-free`.
- A model with no variants in this table has no effort dial. Escalate by moving
  to a twin (below) or the next tier. Never invent a variant.
- Raising a variant on a free model stays free; it only increases token use.
  On paid models it increases cost.
- Format: `provider/model#variant`, e.g. `opencode/beam-free#low`,
  `opencode-go/deepseek-v4.1-flash#max`.

## Free twins (quota fallback)

| Tier 0 (`opencode`) | Tier 1 (`opencode-go`) |
| --- | --- |
| `beam-free` | `beam-free` |
| `hy4-flash-free` | `hy4-flash-free` |
| `iceman-free` | `iceman-contributor-free` |
| `deepseek-v4.1-flash-free` | none (paid `deepseek-v4.1-flash` is tier 2) |
| other free models | none; move to the nearest family at tier 1 or 2 |

## Task-to-family guide

| Task | First choice | Review with |
| --- | --- | --- |
| Code edits, features, fixes | `deepseek-v4.1-flash-free`, `flint-40b-free`, `beam-free` | different family: beam vs deepseek vs glm vs muse |
| Architecture, design, hard debugging | `beam-free#max`, `deepseek-v4.1-flash-free#max`, then tier 2 only with owner approval | `muse-spark-1.3-contributor#high` only with owner approval |
| Exploration, large reads, summarization | `nemotron-3.5-super-vl-free`, `beam-free#low`, `hy4-flash-free` | n/a (read-only) |
| Low-effort: commits, renames, logs, one-line docs | `big-pickle`, `mimo-v2.6-flash-free`, `beam-free#low` | none |
| Multimodal or screenshot inspection | `nemotron-3.5-super-vl-free` | n/a |
| Writing and docs | `beam-free#high`, `nemotron-3.5-super-vl-free#high` | different family |

## Effort and risk labels

| Label | Model policy | Review policy |
| --- | --- | --- |
| effort low | tier 0 low/no variant, or `utility` | none, unless risk applies |
| effort medium | tier 0 high | one reviewer |
| effort high | tier 0 max/xhigh, tier 1 max, then tier 2 only with owner approval | one reviewer, tier 2 only with owner approval |
| risk normal | any | one reviewer |
| risk security / persistent-data / public-api | high effort | two independent reviewers from two distinct families other than the implementer's; no migration execution against shared data |

Under `default`, tier 2 and tier 3 rows in this table are usable only after the
owner has explicitly said to use that tier; otherwise treat the unit as BLOCKED and
ask. Under `tier2-only`, read every tier reference in this table as the tier-2
pool: effort low uses a low or no variant, medium uses medium/high, high uses
high/xhigh/max; reviewer policy is unchanged and uses other tier-2 families.

Risk raises review depth; it does not block delegation or auto-merge. The two
reviewers do not share findings, and a disagreement counts as FAIL. A unit is
merged after all required reviewers return PASS and checks pass.
