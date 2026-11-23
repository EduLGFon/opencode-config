---
description: Create small, focused Conventional Commits per change.
---

# /commit

Commit only the logical change currently in scope. Rules:

1. Inspect `git status` and `git diff` first. Identify exactly one logical change.
2. Stage only files belonging to that change. Never `git add -A` or `git add .`.
3. Make small commits per logical change on the files you modified. Split unrelated changes into separate commits, one at a time, committing each before staging the next.
4. Conventional Commits: `<type>(<scope>): <subject>` where type is one of `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `perf`, `security`. Subject is imperative, lowercase, no trailing period, <= 72 chars.
5. One commit per logical change. If the working tree mixes unrelated changes, split them into multiple commits, one at a time, committing each before staging the next.
6. Do not amend published commits; create new ones. Do not use `--no-verify`.
7. Verify with `git diff --cached` before committing; after, confirm `git status`.
8. Never commit secrets, tokens, keystores, `.env`, or generated artifacts.
9. After creating commits, return each commit text you made.

If the change is ambiguous or touches security/privacy/data, ask the owner first.