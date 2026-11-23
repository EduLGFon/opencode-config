# Integration details (G3 and G4)

Units and reviews run in parallel. Integration is a single serial critical section.
This file is the detailed procedure behind `SKILL.md` §7.

## 1. Serial integration

Run exactly one of these at a time:

```text
gate.py premerge → re-check shared state → rebase → rerun checks → merge → post-merge smoke
```

`gate.py premerge --task <slug> --unit <unit>` checks G2, a clean target, the
reviewed SHA and a non-blocking serial lock. It never merges.

Never interleave two merges. Never merge while another orchestrator is writing to
the target. The merge itself is quick; the risk is the shared-state check and the
post-merge check racing another writer.

## 2. Clean integration target

Merge only when the target is clean. If the main worktree has unrelated changes or
another orchestrator is active there, use a dedicated integration worktree:

```text
git worktree add <integration-wt> <integration-ref>
```

Record in the manifest before the first merge:

```text
integration worktree | target ref | target start SHA | contracts | approvals
```

Use `task.py set-unit` to record each unit's reviewed and merge SHA, and
`task.py target` when the target ref or start SHA changes. After every merge,
update the target SHA. The final report must show the start SHA and final SHA so
the merged state can be reconstructed.

## 3. Rebase and patch integrity

- Rebasing a unit's unshared branch is allowed.
- Never rewrite the integration target's history.
- Prefer merging the exact reviewed SHA. Use `--no-ff` so a merge commit gives you
  a one-command rollback; if the target policy requires fast-forward, rollback
  means reverting the unit's commit range.
- If a rebase changes the SHA, rerun the required checks and compare the patches:

```text
git range-diff <old-base>..<reviewed> <new-base>..<rebased>
```

Require the unit commit to show `=` (patch unchanged). Any `!`, `-` or `+` means
the conflict resolution changed the patch and the reviewer must re-review. Do not
use `git diff <reviewed> <rebased>` as the check: it includes the target's new
commits and is non-empty whenever the target advanced.

An equivalent patch check is:

```text
diff <(git diff <old-base>..<reviewed>) <(git diff <new-base>..<rebased>)
```

Empty output means the unit patch is unchanged.

## 4. Merge exactness

Record the target SHA immediately before the merge as `<target-before>`, then
verify:

```text
git merge-base --is-ancestor <rebased SHA> <merge commit>   # after the merge
git diff <target-before>..<merge>                            # must equal the unit patch
```

The reviewed/rebased unit tip must be an ancestor of the merge, and the merge must
add only that unit's diff. If the diff contains anything else, stop and investigate;
do not merge another orchestrator's work or an unrelated change.

After the merge commit exists:

```text
task.py set-unit --task <slug> --id <unit> --state merged --merge-sha <merge SHA>
```

## 5. Conflicts

Any conflict goes back to the unit's worker session. The orchestrator may abort a
rebase, but never resolves code conflicts itself. If the worker cannot resolve
within its caps, block the unit and keep its worktree for the report.

## 6. Post-merge checks and rollback

Post-merge checks run in the integration checkout through a reviewer or a utility
doing read-only checks. Prefer the repo's fastest integration check; if none exists,
use the full test command.

If they fail:

1. Revert the unit's merge with a new commit immediately so the target stays green
   (`git revert -m 1 <merge>` for a merge commit; revert the commit range for a
   fast-forward).
2. Keep the unit's branch and worktree for diagnosis.
3. Report the unit as blocked with the failing evidence.
4. Keep independent units moving.

Never leave a known-broken integration target while other units merge on top.

## 7. Cleanup

- After merge or explicit discard: `worktree_remove`.
- Delete the merged unit branch if the repository allows it; otherwise record why it
  is retained.
- Run `git worktree prune` after removal.
- Blocked units: keep the worktree until the report is written; it is the failure
  evidence. Remove it afterward.
- Forced removal requires owner approval through the `question` tool.
- Never remove a worktree another orchestrator created.

## 8. Concurrent orchestrators

Before each merge:

- re-check `git status --porcelain`, `git worktree list` and the target SHA
- confirm the target still contains the reviewed base plus only expected merges
- never reset, clean or rebase another orchestrator's work
- if the target moved in a way you cannot attribute, stop that unit and report

## G3 checklist

- [ ] G2 passed for this unit
- [ ] `gate.py premerge` PASS; serial lock acquired
- [ ] shared state re-checked; target clean and attributable
- [ ] rebase done if needed; `git range-diff` shows `=`
- [ ] checks rerun after rebase
- [ ] reviewed/rebased unit tip is an ancestor of the merge
- [ ] merge adds only this unit's diff
- [ ] post-merge checks green, or merge reverted
- [ ] worktree removed, branch deleted or retained with a reason, worktree pruned

## G4 task-done checklist

- [ ] every unit merged, blocked, no-op or discarded with a reason
- [ ] integration target green after the final merge
- [ ] ledger complete, including target start/final SHA, reviewed and merge SHAs,
      reviewer families, paid calls and cost tier
- [ ] approvals recorded with their scope
- [ ] blocked worktrees removed at task end, or explicitly retained
