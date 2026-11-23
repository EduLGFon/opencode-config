#!/usr/bin/env python3
"""Create and update an orchestration task manifest.

Examples:
    task.py init --task demo --repo . --ref main
    task.py add-unit --task demo --id u1 --objective "add x" --owned src/x
    task.py report --task demo --file worker.json
    task.py status --task demo
    task.py render --task demo --out owner.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import tasklib


def split_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


def dump(value) -> None:
    print(json.dumps(value, indent=2))


def load(task: str) -> dict:
    return tasklib.load(task)


def cmd_init(args: argparse.Namespace) -> int:
    path = tasklib.manifest_path(args.task)
    if path.exists() and not args.force:
        raise ValueError(f"manifest already exists: {path} (use --force to overwrite)")
    manifest = tasklib.new_manifest(
        args.task,
        args.repo,
        args.ref,
        policy=args.policy,
        budget_usd=args.budget,
        start_sha=args.start_sha,
    )
    tasklib.ensure_dirs(args.task)
    tasklib.save(args.task, manifest)
    tasklib.append_event(args.task, {"type": "init", "policy": args.policy})
    print(path)
    return 0


def cmd_add_unit(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    if args.id in manifest.get("units", {}):
        raise ValueError(f"unit already exists: {args.id}")
    unit = tasklib.unit_template(
        args.id,
        args.objective,
        split_list(args.owned),
        depends_on=split_list(args.depends_on),
        effort=args.effort,
        risk=args.risk,
        model=args.model,
        worktree=args.worktree,
        branch=args.branch,
        base_sha=args.base_sha,
        contracts=split_list(args.contract),
        acceptance=split_list(args.acceptance),
        context=args.context or "",
        resource_isolation=args.resource_isolation or "",
        implementer_family=args.implementer_family,
        state=args.state,
    )
    if args.state in tasklib.ACTIVE_STATES and not unit["started_at"]:
        unit["started_at"] = tasklib.now()
    manifest.setdefault("units", {})[args.id] = unit
    errors, warnings = tasklib.validate_manifest(manifest)
    if errors:
        raise ValueError("manifest invalid:\n- " + "\n- ".join(errors))
    tasklib.save(args.task, manifest)
    tasklib.append_event(args.task, {"type": "unit_add", "unit": args.id})
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    dump(unit)
    return 0


def cmd_set_unit(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    unit = manifest.get("units", {}).get(args.id)
    if unit is None:
        raise ValueError(f"unknown unit: {args.id}")
    scalar = {
        "state": args.state,
        "model": args.model,
        "session": args.session,
        "worktree": args.worktree,
        "branch": args.branch,
        "base_sha": args.base_sha,
        "worker_sha": args.worker_sha,
        "reviewed_sha": args.reviewed_sha,
        "merge_sha": args.merge_sha,
        "note": args.note,
        "attempts": args.attempts,
        "implementer_family": args.implementer_family,
    }
    for key, value in scalar.items():
        if value is not None:
            unit[key] = value
    if args.follow_up:
        unit.setdefault("follow_ups", []).extend(args.follow_up)
    if args.state:
        if unit.get("state") in tasklib.ACTIVE_STATES:
            if not unit.get("started_at"):
                unit["started_at"] = tasklib.now()
            unit["ended_at"] = None
        elif unit.get("state") in tasklib.TERMINAL_STATES | {"passed"}:
            unit["ended_at"] = tasklib.now()
    if unit.get("state") == "passed" and not unit.get("reviewed_sha"):
        unit["reviewed_sha"] = unit.get("worker_sha")
    errors, warnings = tasklib.validate_manifest(manifest)
    if errors:
        raise ValueError("manifest invalid:\n- " + "\n- ".join(errors))
    tasklib.save(args.task, manifest)
    tasklib.append_event(
        args.task, {"type": "unit_set", "unit": args.id, "state": unit.get("state")}
    )
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    dump(unit)
    return 0


def cmd_target(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    if args.start_sha is not None:
        manifest["target"]["start_sha"] = args.start_sha
    if args.ref is not None:
        manifest["target"]["ref"] = args.ref
    errors, _ = tasklib.validate_manifest(manifest)
    if errors:
        raise ValueError("manifest invalid:\n- " + "\n- ".join(errors))
    tasklib.save(args.task, manifest)
    dump(manifest["target"])
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    call = tasklib.add_call(
        manifest,
        args.unit,
        args.role,
        args.model,
        args.tier,
        args.outcome,
        session=args.session,
        tokens_in=args.tokens_in,
        tokens_out=args.tokens_out,
        error=args.error,
    )
    tasklib.save(args.task, manifest)
    tasklib.append_event(args.task, {"type": "call", "unit": args.unit, "role": args.role})
    dump(call)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    if bool(args.file) == bool(args.json):
        raise ValueError("pass exactly one of --file or --json")
    if args.file:
        report = json.loads(Path(args.file).read_text())
        source = str(Path(args.file).resolve())
    else:
        report = json.loads(args.json)
        source = None
    result = tasklib.ingest_report(args.task, report, source=source)
    dump(result)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    at = tasklib.parse_time(args.at) if args.at else None
    data = tasklib.status(manifest, at=at, rate_window_min=args.rate_window)
    if args.json:
        dump(data)
    else:
        print(tasklib.format_status(data))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    errors, warnings = tasklib.validate_manifest(manifest)
    if args.json:
        dump({"errors": errors, "warnings": warnings})
    else:
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        print("OK" if not errors else "FAILED")
    return 1 if errors else 0


def cmd_owner_check(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    errors, warnings = tasklib.owner_check(manifest)
    if args.json:
        dump({"errors": errors, "warnings": warnings})
    else:
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        print("OK" if not errors else "FAILED")
    return 1 if errors else 0


def cmd_render(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    text = tasklib.render_markdown(manifest)
    if args.out:
        Path(args.out).write_text(text)
        print(args.out)
    else:
        print(text, end="")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    manifest = load(args.task)
    manifest.setdefault("approvals", []).append(
        {"at": tasklib.now(), "scope": args.scope, "note": args.note or ""}
    )
    tasklib.save(args.task, manifest)
    tasklib.append_event(args.task, {"type": "approval", "scope": args.scope})
    dump(manifest["approvals"][-1])
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    print(tasklib.task_dir(args.task))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a task manifest")
    init.add_argument("--task", required=True)
    init.add_argument("--repo", required=True)
    init.add_argument("--ref", required=True)
    init.add_argument("--policy", choices=sorted(tasklib.POLICIES), default="default")
    init.add_argument("--budget", type=float)
    init.add_argument("--start-sha")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    add = sub.add_parser("add-unit", help="add one unit")
    add.add_argument("--task", required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--objective", required=True)
    add.add_argument("--owned", action="append", required=True, help="path or comma list")
    add.add_argument("--depends-on", action="append")
    add.add_argument("--effort", choices=sorted(tasklib.EFFORT_MINUTES), default="medium")
    add.add_argument("--risk", choices=sorted(tasklib.RISK_REVIEWS), default="normal")
    add.add_argument("--model")
    add.add_argument("--implementer-family")
    add.add_argument("--worktree")
    add.add_argument("--branch")
    add.add_argument("--base-sha")
    add.add_argument("--contract", action="append")
    add.add_argument("--acceptance", action="append")
    add.add_argument("--context")
    add.add_argument("--resource-isolation")
    add.add_argument("--state", choices=sorted(tasklib.STATES), default="planned")
    add.set_defaults(func=cmd_add_unit)

    set_unit = sub.add_parser("set-unit", help="update one unit")
    set_unit.add_argument("--task", required=True)
    set_unit.add_argument("--id", required=True)
    set_unit.add_argument("--state", choices=sorted(tasklib.STATES))
    set_unit.add_argument("--model")
    set_unit.add_argument("--session")
    set_unit.add_argument("--worktree")
    set_unit.add_argument("--branch")
    set_unit.add_argument("--base-sha")
    set_unit.add_argument("--worker-sha")
    set_unit.add_argument("--reviewed-sha")
    set_unit.add_argument("--merge-sha")
    set_unit.add_argument("--note")
    set_unit.add_argument("--attempts", type=int)
    set_unit.add_argument("--implementer-family")
    set_unit.add_argument("--follow-up", action="append")
    set_unit.set_defaults(func=cmd_set_unit)

    target = sub.add_parser("target", help="update target ref or start SHA")
    target.add_argument("--task", required=True)
    target.add_argument("--ref")
    target.add_argument("--start-sha")
    target.set_defaults(func=cmd_target)

    call = sub.add_parser("call", help="record one model call")
    call.add_argument("--task", required=True)
    call.add_argument("--unit")
    call.add_argument("--role", required=True, choices=sorted(tasklib.ROLES))
    call.add_argument("--model")
    call.add_argument("--tier", required=True, type=int, choices=(0, 1, 2, 3))
    call.add_argument("--outcome", required=True)
    call.add_argument("--session")
    call.add_argument("--tokens-in", type=int, default=0)
    call.add_argument("--tokens-out", type=int, default=0)
    call.add_argument("--error")
    call.set_defaults(func=cmd_call)

    report = sub.add_parser("report", help="validate and ingest a structured report")
    report.add_argument("--task", required=True)
    group = report.add_mutually_exclusive_group(required=True)
    group.add_argument("--file")
    group.add_argument("--json")
    report.set_defaults(func=cmd_report)

    status = sub.add_parser("status", help="show unit, rate-limit and budget status")
    status.add_argument("--task", required=True)
    status.add_argument("--json", action="store_true")
    status.add_argument("--at", help="ISO time for deterministic tests")
    status.add_argument("--rate-window", type=int, default=30)
    status.set_defaults(func=cmd_status)

    check = sub.add_parser("check", help="validate the manifest")
    check.add_argument("--task", required=True)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    owner = sub.add_parser("owner-check", help="validate the G4 owner report")
    owner.add_argument("--task", required=True)
    owner.add_argument("--json", action="store_true")
    owner.set_defaults(func=cmd_owner_check)

    render = sub.add_parser("render", help="render the owner report as Markdown")
    render.add_argument("--task", required=True)
    render.add_argument("--out")
    render.set_defaults(func=cmd_render)

    approve = sub.add_parser("approve", help="record an owner approval")
    approve.add_argument("--task", required=True)
    approve.add_argument("--scope", required=True)
    approve.add_argument("--note")
    approve.set_defaults(func=cmd_approve)

    path = sub.add_parser("path", help="print the task directory")
    path.add_argument("--task", required=True)
    path.set_defaults(func=cmd_path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
