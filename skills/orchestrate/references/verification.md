# Verification details (G2)

The orchestrator owns only cheap gates; the reviewer owns the expensive checks.
This file is the detailed procedure behind `SKILL.md` §5.

The mechanical implementation is `gate.py unit --task <slug> --unit <unit>`. It
performs sections 1-4 and 6, fails closed, and prints the checks it ran. Run it;
the rest of this file is the contract it enforces. A green manual review without a
green `gate.py unit` is not G2.

## 1. Worker completion

A unit advances only when the worker's first line is `DONE`. `BLOCKED` and `NO-OP`
are valid reports but never mergeable. The report must contain:

- files changed
- every command with its exit code
- acceptance evidence
- the HEAD SHA
- the `git status --porcelain` result
- the structured JSON report from `references/reports.md`

Ingest it with `task.py report --task <slug> --file <report>`. A missing or invalid
JSON block is incomplete evidence; prose alone does not advance the unit.

An empty diff is a `NO-OP`: confirm the objective is already satisfied, record it
in the report and discard the worktree. Never merge an empty commit.

## 2. Clean tree and SHA pinning

Before launching the reviewer:

```text
git -C <wt> status --porcelain
git -C <wt> rev-parse HEAD
```

- `status --porcelain` must be empty. Intended files must be committed; build
  artifacts must be ignored by `.gitignore`, not merged.
- The printed HEAD SHA must equal the SHA in the worker report.
- Put the SHA in the ledger as the reviewed SHA.

Before merging, verify again that `git -C <wt> rev-parse HEAD` still equals the
reviewed SHA. A commit after the review makes the review stale: re-review or
discard. Never merge a SHA the reviewer did not see.

## 3. Diff scope

```text
git -C <wt> diff --name-only <base>..<reviewed SHA>
```

Every path must be a subset of the unit's owned paths. Any outside path fails the
unit. If ownership was expanded, update the ledger first and re-check every other
unit for overlap before re-running this gate.

## 4. Secret scan without reading secrets

Fail immediately if the diff touches secret-looking paths:

```text
.env*  *.pem  *.key  *.p12  *.pfx  *.jks  *.keystore
id_rsa*  id_ed25519*  *.npmrc  *.pypirc
credentials.*  service-account*.json
```

Allow templates such as `.env.example`, `.env.sample` and `.env.template`, but
still scan their added lines for token patterns. Do not open secret files. For
every other added line, scan for private keys, API keys, bearer tokens and
passwords. Redact sensitive command output before putting it in a report or brief.

## 5. Check resource isolation

Checks for different units must not share mutable state. Before launch, assign:

- a distinct port range per worktree
- a temp database or schema per worktree
- a temp cache and artifact directory per worktree
- a temp HOME where the tool writes dotfiles

If the project cannot isolate a resource, serialize that check instead of running
it in parallel. The reviewer uses ephemeral state and never touches shared data.

## 6. Reviewer contract

Launch the reviewer with the brief in `references/briefs.md`, the implementer's
model and family, and an explicitly chosen model:

- `default`: a free model from a family different from the implementer's, first
- `default`: the tier-2 reviewer family in `references/models.md` for high-risk or
  hard units, only after the owner has explicitly said to use tier 2
- `tier2-only`: a different tier-2 family; never a free model or tier 3
- never the implementer's family, and never the agent default by accident

If the only available reviewer is the implementer's family, report BLOCKED rather
than run a non-independent review. A reviewer BLOCKED is an attempt failure:
relaunch with another family or fix the environment; never count it as PASS.

The reviewer must:

- confirm `git rev-parse HEAD` equals the reviewed SHA
- inspect the diff and changed files; do not re-read the whole repository unless
  the diff requires it
- run the build, format in check mode, lint/analyze and tests; for normal-risk units
  target the changed tests and their nearest dependants, for risk units run the
  full suite
- treat a behavior change without a new test or explicit manual acceptance
  evidence as a major finding
- leave tracked files unchanged and clean the artifacts it creates
- report the reviewed SHA, commands, exit codes and severity-ordered findings
- cap minors at 5 and nits at 5; blockers and majors are unlimited
- return PASS only with all commands green and no blocker or major finding
- end with the structured JSON reviewer report; `gate.py unit` parses it

## 7. Flakes

Rerun a failing check once.

- Green on rerun: record it as flaky and continue.
- Fails twice: real failure, fail the unit.
- Never convert a known failure into a pass because one rerun was green.

## 8. Out-of-scope findings

Pre-existing problems outside the reviewed diff are follow-ups. They do not fail
the unit. Record them in the report and do not let them expand the unit's scope.
Behavior changes with no test are not follow-ups; they are major findings inside
the unit.

## G2 checklist

- [ ] `gate.py unit` PASS (mechanical checks below)
- [ ] worker reported DONE, with structured JSON evidence, HEAD SHA and clean status
- [ ] no empty diff; NO-OP units are discarded, not merged
- [ ] `git status --porcelain` empty
- [ ] HEAD equals reported SHA and reviewed SHA
- [ ] diff paths within owned paths; ownership changes re-checked
- [ ] no secret paths; templates scanned without opening secrets
- [ ] check resources isolated or serialized
- [ ] reviewer model family differs from the implementer's and respects the task model policy
- [ ] build, format check, lint/analyze and tests pass
- [ ] behavior changes covered by tests or explicit manual evidence
- [ ] reviewer PASS, no blocker or major, evidence includes commands and exit codes
- [ ] flakes recorded, not silently passed
- [ ] out-of-scope findings moved to follow-ups
