# Briefs and report contracts

Every subagent starts with a fresh context. A brief must be self-contained:
the agent sees only this packet, not the orchestrator's conversation.

`plan.py brief` renders these templates from the manifest and appends the
structured report contract. Every subagent ends with the JSON report defined in
`references/reports.md`; a prose-only response is incomplete evidence. The
orchestrator ingests it with `task.py report`.

## Working directory

The `subagent` tool has no working-directory parameter. A child session does not
reliably start in its worktree. Every brief must open with this assertion:

```text
Worktree: <absolute path>
First: run `pwd`. If it is not the worktree, every shell command must either
pass workdir=<absolute path> or start with `cd <absolute path> &&`.
```

Use absolute paths everywhere. Never rely on the process default directory.

## Worker brief

```text
Objective: <one outcome, one sentence>

Worktree: <absolute path>
Base: <base ref and SHA>
Branch: <branch name>
Depends on: <merged unit, or "none">
Contracts: <API shape, schema, types or UX this unit must preserve>
Resource isolation: <assigned ports, temp DB/schema, temp dirs>
Model policy: <default or tier2-only>

First: run `pwd`. If it is not the worktree, every shell command must either
pass workdir=<absolute path> or start with `cd <absolute path> &&`.

Owned paths: <exact files or directories; nothing else>
Context packet: <path to the task context file; read it instead of re-exploring>
Context: <only the task-specific facts not already in the context packet>
Constraints:
- Read <instruction files> and follow them.
- <style, compatibility, dependency limits>
- No new packages unless stated.
- Commit all intended files in the worktree with a clear message; do not push.
- All commits must use the global `user.name` and `user.email`. Do not set a
  per-repo identity, `-c user.*` overrides, `--author`, or `GIT_AUTHOR_*` /
  `GIT_COMMITTER_*` env vars. If the global identity is missing, report BLOCKED
  instead of inventing one.
- Do not merge the integration target into your branch. The orchestrator rebases.
- If you need a file outside your owned paths, or a contract must change, stop and
  report BLOCKED instead of expanding scope.
- Before reporting DONE, `git status --porcelain` must be empty and you must
  report the HEAD SHA.
- If the objective is already satisfied with no changes, report NO-OP.
- If BLOCKED, do not commit a partial guess.

Acceptance:
- <build command, if the repo defines one> must exit 0.
- <exact format/lint/test commands> must exit 0.
- <observable behavior, test name, or assertion>

Forbidden:
- editing outside the worktree or outside owned paths
- merging the integration target into the unit branch
- git push, deploy, migrations against shared data, history rewrite
- reading .env or secret files
- launching subagents
- changing files owned by another unit
- sharing ports, databases or caches with another worktree

Report:
Line 1: DONE, BLOCKED or NO-OP
Then: files changed, commands run with exit codes, acceptance evidence,
HEAD SHA, `git status --porcelain` result, open issues, and anything the
reviewer must know.
Last: the structured JSON report from `references/reports.md`.
```

Rules for the orchestrator:

- One outcome per worker. If the brief needs "and", split it.
- Never paste secrets, tokens, customer data or full `.env` content.
- Keep context factual; do not include the conversation history.
- If the unit is high risk, say so and state the two-reviewer requirement in the
  acceptance section.
- State the task model policy in every brief. Under `tier2-only`, the reviewer brief
  names only tier-2 models and offers no free-tier fallback.
- Generate the brief with `plan.py brief`; do not hand-copy the template. Ingest the
  returned JSON with `task.py report`.
- Give every unit the same context packet path when one exists. It replaces repeated
  exploration and repeated context in each brief.

## Reviewer brief

```text
Review this worktree read-only. Do not edit files.

Worktree: <absolute path>
Base SHA: <sha>
Reviewed SHA: <sha>
Implementer model: <provider/model#variant>
Implementer family: <family>
Model policy: <default or tier2-only>
Owned paths: <paths>
Contracts: <same contract as the worker brief>
Resource isolation: <assigned ports, temp DB/schema, temp dirs>
Context packet: <same path as the worker brief>
Acceptance:
- <build, format, lint and test commands, same as the worker brief>
- <observable behavior>

First: run `pwd`. If it is not the worktree, prefix every command with
`cd <absolute path> &&` or pass workdir=<absolute path>.

Do:
- Confirm `git rev-parse HEAD` equals the reviewed SHA before starting.
- Confirm your model family differs from the implementer's. If it does not, return
  BLOCKED; do not run a non-independent review.
- Inspect the diff <base>..<reviewed SHA> and the changed files. Do not re-read the
  whole repository unless the diff requires it. Never trust the implementer's report.
- Run the project's build, format (check mode), lint/analyze and test commands
  yourself. For normal-risk units, target the changed tests and their nearest
  dependants; for risk units run the full suite.
- Treat a behavior change without a new test or explicit manual acceptance
  evidence as a major finding.
- Do not modify tracked files. Clean up untracked artifacts you create; never
  delete pre-existing untracked files that you did not create.
- Use only the assigned ports/temp DBs/dirs; never touch shared data.
- Rerun a failing check once to distinguish a flake from a real failure. Record a
  green rerun as flaky; fail a check that fails twice.
- Verify acceptance criteria one by one.
- Classify findings: blocker (wrong result, security, data loss, acceptance
  failure), major (bug, regression, missing requirement), minor (quality, no
  behavior change), nit (style).
- Report all blockers and majors. Cap minors at 5 and nits at 5; put the rest in
  follow-ups.
- Treat pre-existing issues outside the diff as follow-ups, not failures.

Report:
Line 1: PASS or FAIL. Use BLOCKED only when you cannot review independently
(for example, your family matches the implementer's) or the environment is
unavailable.
Then: the reviewed SHA, every command and its exit code, findings by severity
with file:line, acceptance evidence, flaky reruns, and out-of-scope follow-ups.
Last: the structured JSON report from `references/reports.md`.
PASS requires all commands to pass and no blocker or major finding.
```

For risk units, launch two reviewers from two distinct families other than the
implementer's, with the same brief. They review independently and do not share
findings. Both must return PASS; any disagreement is FAIL. Ingest each report with
`task.py report`; `gate.py unit` counts only independent PASS verdicts for the
current SHA.

## Explore brief

```text
Read-only investigation. Do not edit files.

Root: <absolute path>
Model policy: <default or tier2-only>
Questions:
1. <question>
2. <question>
Scope: <paths to inspect, or the whole repo>
Budget: <e.g. inspect at most 20 files; stop when the questions are answered>
Context packet output: <path to write, or "none">

First: run `pwd`; prefix commands with `cd <root> &&` if needed.

Report:
- Direct answers with file:line evidence.
- Relevant existing patterns and commands.
- Unknowns and risks.
- No recommendations unless asked.
Last: the structured JSON explore report from `references/reports.md`.
```

## Utility brief

```text
Low-effort task only.

Worktree/Root: <absolute path>
Model policy: <default or tier2-only>
Task: <exact command or edit, e.g. "create one commit with message X">
Owned paths: <paths; edit only these>

First: run `pwd`. If it is not the worktree, prefix every command with
`cd <absolute path> &&` or pass workdir=<absolute path>.

Make only the minimal requested change. Do not implement features, change
product logic, push, or read .env/secrets. If no change is needed, report NO-OP.
If you changed files, commit them using the global `user.name` and `user.email`
(no per-repo `user.*` config, no `-c user.*` or `--author` overrides, no
`GIT_AUTHOR_*` / `GIT_COMMITTER_*` env vars) and report the HEAD SHA and
`git status --porcelain` result.

Report: commands run, output, files touched, HEAD SHA and status.
Last: the structured JSON utility report from `references/reports.md`.
```

## Report handling

- `DONE` is a claim, not evidence. Check the diff, scope, clean status, HEAD SHA
  and secret scan yourself.
- `BLOCKED` is a valid outcome. Read the reason, then decide escalation. A
  BLOCKED unit is never merged, even if a review is green.
- `NO-OP` means no diff. Confirm the objective is already satisfied, report it and
  discard the worktree; never merge an empty commit.
- A worker report without exit codes, acceptance evidence or HEAD SHA did not
  produce usable evidence; treat the attempt as failed.
- If HEAD moved after the review, the review is stale. Re-review or discard.
