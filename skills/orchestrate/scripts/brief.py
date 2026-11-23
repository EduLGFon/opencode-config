#!/usr/bin/env python3
"""Render self-contained subagent briefs from an orchestration task manifest.

A subagent starts with a fresh context and sees only this packet, never the
orchestrator's conversation. Every brief opens with the `pwd` assertion because
the `subagent` tool has no working-directory parameter.

Examples:
    brief.py --task demo --unit u1 --role worker
    brief.py --task demo --unit u1 --role reviewer \
        --model opencode/beam-free#max --implementer-family beam
    brief.py --task demo --unit u1 --role utility \
        --out /tmp/opencode/orchestrate/demo/briefs/u1-utility.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import tasklib

STATUS_TEXT = {
    "worker": "DONE, BLOCKED or NO-OP",
    "utility": "DONE, BLOCKED or NO-OP",
    "explore": "DONE, BLOCKED or NO-OP",
    "reviewer": "PASS, FAIL, or BLOCKED (only when you cannot review independently)",
}
STATUS_SCHEMA = {
    "worker": "DONE | BLOCKED | NO-OP",
    "utility": "DONE | BLOCKED | NO-OP",
    "explore": "DONE | BLOCKED | NO-OP",
    "reviewer": "PASS | FAIL | BLOCKED",
}
SHA_TEXT = {
    "worker": "HEAD SHA or null",
    "reviewer": "reviewed SHA or null",
    "utility": "HEAD SHA or null (null when you changed nothing)",
    "explore": "null (read-only)",
}

# Fields the manifest must carry before a brief of this role can be rendered.
REQUIRED_FIELDS = {
    "worker": ("objective", "owned_paths", "worktree", "branch"),
    "reviewer": ("objective", "owned_paths", "worktree", "worker_sha"),
    "explore": ("objective",),
    "utility": ("objective", "owned_paths"),
}

FORBIDDEN_WRITER = (
    "editing outside the worktree or outside owned paths",
    "changing files owned by another unit",
    "merging the integration target into the unit branch",
    "git push, deploy, migrations against shared data, history rewrite",
    "reading .env or secret files",
    "launching subagents",
    "sharing ports, databases or caches with another worktree",
)
FORBIDDEN_READONLY = (
    "editing any file",
    "merging the integration target into the unit branch",
    "git push, deploy, migrations against shared data, history rewrite",
    "reading .env or secret files",
    "launching subagents",
)


# --------------------------------------------------------------------- context


def resolve(
    task: str,
    uid: str,
    role: str,
    model: str | None = None,
    implementer_family: str | None = None,
) -> dict:
    """Load the manifest, check required fields and build the render context."""
    if role not in tasklib.ROLES:
        raise ValueError(f"role must be one of {sorted(tasklib.ROLES)}: {role!r}")
    manifest = tasklib.load(task)
    units = manifest.get("units") or {}
    if uid not in units:
        available = ", ".join(sorted(units)) or "no units"
        raise ValueError(f"unit {uid!r} not found in task {task!r} (available: {available})")
    unit = units[uid]

    missing: list[str] = []
    for field in REQUIRED_FIELDS[role]:
        value = unit.get(field)
        if value is None or value == "" or (isinstance(value, list) and not value):
            missing.append(field)
    unit_model = model or unit.get("model")
    unit_family = implementer_family or unit.get("implementer_family")
    if role == "reviewer":
        # The reviewer must know the implementer to prove independence.
        if not unit_model:
            missing.append("model")
        if not unit_family:
            missing.append("implementer_family")
    if missing:
        message = (
            f"unit {uid!r} is missing fields required for a {role} brief: "
            + ", ".join(missing)
        )
        if role == "reviewer" and ("model" in missing or "implementer_family" in missing):
            message += " (pass --model / --implementer-family)"
        raise ValueError(message)

    target = manifest.get("target") or {}
    policy_name = (manifest.get("policy") or {}).get("name") or "default"
    worktree = unit.get("worktree")
    repo = target.get("repo")
    root = worktree or repo
    base_ref = target.get("ref")
    base_sha = unit.get("base_sha")
    if base_sha:
        base = f"{base_ref} @ {base_sha}" if base_ref else str(base_sha)
    else:
        base = f"{base_ref} (base SHA not recorded; branch from the ref)" if base_ref else "unknown"
    effort = unit.get("effort") or "medium"
    return {
        "task": manifest.get("task"),
        "unit": uid,
        "role": role,
        "objective": str(unit.get("objective") or "").strip(),
        "worktree": worktree,
        "repo": repo,
        "root": root,
        "base": base,
        "branch": unit.get("branch"),
        "depends_on": list(unit.get("depends_on") or []),
        "contracts": list(unit.get("contracts") or []),
        "resource_isolation": unit.get("resource_isolation") or "",
        "context": unit.get("context") or "",
        "acceptance": list(unit.get("acceptance") or []),
        "owned_paths": list(unit.get("owned_paths") or []),
        "effort": effort,
        "effort_bound": tasklib.EFFORT_MINUTES.get(effort, 30),
        "risk": unit.get("risk") or "normal",
        "reviews_needed": tasklib.required_reviews(unit),
        "policy": policy_name,
        "model": unit_model,
        "family": unit_family,
        "tier": tasklib.model_tier(unit_model),
        "worker_sha": unit.get("worker_sha"),
    }


# --------------------------------------------------------------------- helpers


def pwd_assertion(path: str, label: str = "worktree") -> str:
    return (
        f"First: run `pwd`. If it is not the {label}, every shell command must either\n"
        f"pass workdir={path} or start with `cd {path} &&`."
    )


def model_line(ctx: dict) -> str | None:
    if not ctx["model"]:
        return None
    tier = ctx["tier"] if ctx["tier"] is not None else "unknown"
    family = ctx["family"] or "unknown"
    return f"Model: {ctx['model']} (family {family}, tier {tier})"


def report_contract(ctx: dict) -> str:
    example = {
        "version": 1,
        "unit": ctx["unit"],
        "role": ctx["role"],
        "status": STATUS_SCHEMA[ctx["role"]],
        "sha": SHA_TEXT[ctx["role"]],
        "files": ["changed/path"],
        "commands": [{"cmd": "exact command", "exit": 0, "duration_s": 0.0}],
        "acceptance": [{"criterion": "criterion", "evidence": "observed evidence", "pass": True}],
        "findings": [
            {
                "severity": "blocker | major | minor | nit",
                "file": "path",
                "line": 0,
                "message": "what and where",
            }
        ],
        "usage": {"tokens_in": 0, "tokens_out": 0},
        "model": ctx["model"] or "provider/model#variant actually used",
        "tier": ctx["tier"] if ctx["tier"] is not None else 0,
        "family": ctx["family"] or "model family",
        "notes": "short factual note",
        "next": "what should happen next",
    }
    lines = [
        "Report contract (the JSON block is the last thing in the response, in one",
        "fenced json block):",
        "",
        "```json",
        json.dumps(example, indent=2),
        "```",
        "",
        "Rules:",
        "- DONE requires every command to exit 0 and every acceptance item to pass.",
        "- PASS requires no blocker and no major finding; BLOCKED is never counted as a PASS.",
    ]
    if ctx["role"] == "reviewer":
        lines.extend(
            (
                "- findings is required, ordered by severity with file:line; family is required (your model family).",
                "- State the reviewed SHA you verified; a report for another SHA is void.",
            )
        )
    elif ctx["role"] == "worker":
        lines.append(
            "- Report the HEAD SHA and a clean `git status --porcelain`; BLOCKED means no partial commit."
        )
    else:
        lines.append(
            "- commands and acceptance are optional for this role; sha is null unless you committed."
        )
    lines.extend(
        (
            "- Use severities blocker, major, minor, nit; cap minor and nit findings at 5 each.",
            "- Never include secrets, tokens, customer data or .env content; redact sensitive output.",
        )
    )
    return "\n".join(lines)


def finish(lines: list[str]) -> str:
    text = "\n".join(lines).rstrip()
    return text + "\n"


# -------------------------------------------------------------------- renderer


def render_worker(ctx: dict) -> str:
    lines = [
        f"# Worker brief: {ctx['task']} / {ctx['unit']}",
        "",
        f"Objective: {ctx['objective']}",
        "",
        f"Worktree: {ctx['worktree']}",
        f"Base: {ctx['base']}",
        f"Branch: {ctx['branch']}",
        f"Depends on: {', '.join(ctx['depends_on']) or 'none'}",
        f"Contracts: {'; '.join(ctx['contracts']) or 'none'}",
        f"Resource isolation: {ctx['resource_isolation'] or 'none'}",
        f"Model policy: {ctx['policy']}",
    ]
    model = model_line(ctx)
    if model:
        lines.append(model)
    lines.extend(
        (
            "",
            pwd_assertion(ctx["worktree"]),
            "",
            "Owned paths (edit only these):",
        )
    )
    lines.extend(f"- {path}" for path in ctx["owned_paths"])
    lines.extend(
        (
            "",
            f"Context: {ctx['context'] or '(none provided)'}",
            "",
            "Constraints:",
            "- Read the repo instruction files (AGENTS.md or docs/AGENTS.md) and follow them.",
            "- No new packages unless stated.",
            f"- Effort {ctx['effort']}: treat {ctx['effort_bound']} minutes as the stall bound; report BLOCKED before it slips.",
            f"- Risk {ctx['risk']}: this unit needs {ctx['reviews_needed']} independent review(s) from families other than the implementer's.",
            "- Commit all intended files in the worktree with a clear message; do not push.",
            "- All commits must use the global `user.name` and `user.email`: no per-repo",
            "  `user.*` config, no `-c user.*` or `--author` overrides, no `GIT_AUTHOR_*` /",
            "  `GIT_COMMITTER_*` env vars. If the global identity is missing, report BLOCKED.",
            "- Do not merge the integration target into your branch. The orchestrator rebases.",
            "- If you need a file outside your owned paths, or a contract must change, stop and",
            "  report BLOCKED instead of expanding scope.",
            "- Before reporting DONE, `git status --porcelain` must be empty and you must report",
            "  the HEAD SHA.",
            "- If the objective is already satisfied with no changes, report NO-OP.",
            "- If BLOCKED, do not commit a partial guess.",
            "",
            "Acceptance:",
        )
    )
    if ctx["acceptance"]:
        lines.extend(f"- {item} must exit 0." for item in ctx["acceptance"])
    else:
        lines.append("- (none recorded; agree the acceptance checks with the owner before reporting DONE)")
    lines.extend(
        (
            "",
            "Forbidden:",
        )
    )
    lines.extend(f"- {item}" for item in FORBIDDEN_WRITER)
    lines.extend(
        (
            "",
            "Report:",
            f"Line 1: {STATUS_TEXT[ctx['role']]}.",
            "Then: files changed, commands run with exit codes, acceptance evidence, HEAD SHA,",
            "`git status --porcelain` result, open issues, and anything the reviewer must know.",
            "",
            report_contract(ctx),
        )
    )
    return finish(lines)


def render_reviewer(ctx: dict) -> str:
    if ctx["reviews_needed"] > 1:
        risk_rule = (
            f"Risk rule: this is a {ctx['risk']} unit. Two reviewers from two distinct families "
            f"other than the implementer's ({ctx['family']}) review it independently; they do not "
            "share findings; both must return PASS; any disagreement is FAIL."
        )
    else:
        risk_rule = (
            "Risk rule: normal-risk units need one independent reviewer. Security, persistent-data "
            "and public-api units require two reviewers from two distinct families other than the "
            "implementer's; they never share findings and any disagreement is FAIL."
        )
    lines = [
        f"# Reviewer brief: {ctx['task']} / {ctx['unit']}",
        "",
        "Review this worktree read-only. Do not edit files.",
        "",
        f"Objective: {ctx['objective']}",
        "",
        f"Worktree: {ctx['worktree']}",
        f"Base SHA: {ctx['base']}",
        f"Reviewed SHA: {ctx['worker_sha']}",
        f"Branch: {ctx['branch'] or '(branch not recorded)'}",
        f"Implementer model: {ctx['model']}",
        f"Implementer family: {ctx['family']}",
        f"Model policy: {ctx['policy']}",
        f"Depends on: {', '.join(ctx['depends_on']) or 'none'}",
        f"Contracts: {'; '.join(ctx['contracts']) or 'none'}",
        f"Resource isolation: {ctx['resource_isolation'] or 'none'}",
        "Owned paths:",
    ]
    lines.extend(f"- {path}" for path in ctx["owned_paths"])
    lines.extend(
        (
            "",
            pwd_assertion(ctx["worktree"]),
            "",
            "Do:",
            "- Confirm `git rev-parse HEAD` equals the reviewed SHA before starting.",
            "- Confirm your model family differs from the implementer's. If it does not, return",
            "  BLOCKED; do not run a non-independent review.",
            f"- Inspect the diff {ctx['base']}..{ctx['worker_sha']} and the changed files; do not",
            "  re-read the whole repo. Never trust the implementer's report.",
            "- Run the project's build, format (check mode), lint/analyze and test commands yourself.",
            "- Treat a behavior change without a new test or explicit manual acceptance evidence as",
            "  a major finding.",
            "- Do not modify tracked files. Clean up untracked artifacts you create; never delete",
            "  pre-existing untracked files you did not create.",
            "- Use only the assigned ports/temp DBs/dirs; never touch shared data.",
            "- Rerun a failing check once to distinguish a flake from a real failure; fail a check",
            "  that fails twice.",
            "- Verify acceptance criteria one by one.",
            "- Classify findings: blocker (wrong result, security, data loss, acceptance failure),",
            "  major (bug, regression, missing requirement), minor (quality, no behavior change),",
            "  nit (style).",
            "- Treat pre-existing issues outside the diff as follow-ups, not failures.",
            "",
            risk_rule,
            "",
            "Acceptance:",
        )
    )
    if ctx["acceptance"]:
        lines.extend(f"- {item} must exit 0." for item in ctx["acceptance"])
    else:
        lines.append("- (none recorded; verify the objective directly from the diff)")
    lines.extend(
        (
            "",
            "Forbidden:",
        )
    )
    lines.extend(f"- {item}" for item in FORBIDDEN_READONLY)
    lines.extend(
        (
            "",
            "Report:",
            f"Line 1: {STATUS_TEXT[ctx['role']]}.",
            "PASS requires all commands green and no blocker or major finding.",
            "Then: the reviewed SHA, every command with its exit code, findings by severity with",
            "file:line, acceptance evidence, flaky reruns, and out-of-scope follow-ups.",
            "",
            report_contract(ctx),
        )
    )
    return finish(lines)


def render_explore(ctx: dict) -> str:
    lines = [
        f"# Explore brief: {ctx['task']} / {ctx['unit']}",
        "",
        "Read-only investigation. Do not edit files.",
        "",
        f"Objective: {ctx['objective']}",
        "",
        f"Root: {ctx['root']}",
        f"Model policy: {ctx['policy']}",
    ]
    model = model_line(ctx)
    if model:
        lines.append(model)
    lines.extend(
        (
            "",
            pwd_assertion(ctx["root"], "root"),
            "",
            "Scope:",
        )
    )
    if ctx["owned_paths"]:
        lines.extend(f"- {path}" for path in ctx["owned_paths"])
    else:
        lines.append("- the whole repo")
    lines.extend(
        (
            "",
            "Budget: stop when the objective is answered; inspect at most 20 files.",
            "",
            "Report:",
            f"Line 1: {STATUS_TEXT[ctx['role']]}.",
            "Then: direct answers with file:line evidence, relevant existing patterns and",
            "commands, unknowns and risks, no recommendations unless asked.",
            "",
            report_contract(ctx),
        )
    )
    return finish(lines)


def render_utility(ctx: dict) -> str:
    lines = [
        f"# Utility brief: {ctx['task']} / {ctx['unit']}",
        "",
        "Low-effort task only.",
        "",
        f"Task: {ctx['objective']}",
        f"Worktree/Root: {ctx['root']}",
        f"Model policy: {ctx['policy']}",
    ]
    model = model_line(ctx)
    if model:
        lines.append(model)
    lines.extend(
        (
            "",
            pwd_assertion(ctx["root"]),
            "",
            "Owned paths (edit only these):",
        )
    )
    lines.extend(f"- {path}" for path in ctx["owned_paths"])
    lines.extend(
        (
            "",
            "Make only the minimal requested change. Do not implement features, change product",
            "logic, push, or read .env/secrets. If no change is needed, report NO-OP. If you",
            "changed files, commit them using the global `user.name` and `user.email` (no",
            "per-repo `user.*` config, no `-c user.*` or `--author` overrides, no `GIT_AUTHOR_*` /",
            "`GIT_COMMITTER_*` env vars) and report the HEAD SHA and `git status --porcelain`.",
            "",
            "Constraints:",
            "- Commit changed files with a clear message; do not push.",
            "- All commits must use the global `user.name` and `user.email` (same overrides ban as above).",
            "- Do not merge the integration target into any branch.",
            "",
            "Forbidden:",
        )
    )
    lines.extend(f"- {item}" for item in FORBIDDEN_WRITER)
    lines.extend(
        (
            "- implementing features or changing product logic",
            "",
            "Report:",
            f"Line 1: {STATUS_TEXT[ctx['role']]}.",
            "Then: commands run with exit codes, files touched, HEAD SHA and status.",
            "",
            report_contract(ctx),
        )
    )
    return finish(lines)


RENDERERS = {
    "worker": render_worker,
    "reviewer": render_reviewer,
    "explore": render_explore,
    "utility": render_utility,
}


def build(
    task: str,
    uid: str,
    role: str,
    model: str | None = None,
    implementer_family: str | None = None,
) -> str:
    """Render one brief as Markdown."""
    ctx = resolve(task, uid, role, model=model, implementer_family=implementer_family)
    return RENDERERS[role](ctx)


# ------------------------------------------------------------------------- CLI


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", required=True, help="task slug in ORCHESTRATE_ROOT")
    parser.add_argument("--unit", required=True, help="unit id in the manifest")
    parser.add_argument("--role", required=True, choices=sorted(tasklib.ROLES), help="brief role")
    parser.add_argument(
        "--model",
        help="implementer model for reviewer briefs; model override for other roles",
    )
    parser.add_argument(
        "--implementer-family",
        help="implementer family for reviewer briefs; overrides the manifest",
    )
    parser.add_argument(
        "--out",
        help="write the brief to this exact path instead of stdout (never defaults into the worktree)",
    )


def run(args: argparse.Namespace) -> int:
    text = build(
        args.task,
        args.unit,
        args.role,
        model=args.model,
        implementer_family=args.implementer_family,
    )
    if args.out:
        out = Path(args.out)
        if str(out.parent) not in ("", "."):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(args.out)
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brief.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
