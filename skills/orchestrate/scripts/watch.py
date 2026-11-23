#!/usr/bin/env python3
"""Read-only stall, rate-limit and budget detector.

Examples:
    watch.py --task demo
    watch.py --task demo --json --at 2026-11-17T18:00:00Z
    watch.py --task demo --rate-window 15 --stall-factor 2 --budget-warn 0.9

Exit codes:
    0  healthy
    1  attention (overdue unit, repeated rate-limit errors, budget warning)
    2  stalled unit or budget exceeded

This tool never writes the manifest, reports, events or any other file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import tasklib


RATE_ATTENTION = 2


def parse_at(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = tasklib.parse_time(value)
    if parsed is None:
        raise ValueError(f"invalid --at timestamp: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def policy_suggestions(policy: str, rate_limited: bool) -> list[str]:
    if not rate_limited:
        return []
    if policy == "tier2-only":
        return [
            "Rate limits: retry once with the same model and variant, then switch to "
            "another tier-2 family (see references/models.md); never fall back to a "
            "free tier or tier 3."
        ]
    return [
        "Rate limits: retry once with the same model and variant, then switch to the "
        "free twin, then the tier-2 same family (see references/models.md)."
    ]


def stalled_suggestion(row: dict) -> str:
    return (
        f"{row['id']}: mark the unit and branch blocked; keep the old worktree and "
        "relaunch in a fresh worktree with a tighter brief."
    )


def budget_suggestion(data: dict, exceeded: bool) -> str:
    budget = data["budget"]
    if budget.get("limit_usd") is None:
        return ""
    if exceeded:
        return (
            f"Budget exceeded: ${budget['spent_usd']:.4f} / "
            f"${float(budget['limit_usd']):.2f}. Stop paid launches now, keep the "
            "spend trail and report to the owner."
        )
    return (
        f"Budget at {budget['percent']:.0f}%: pause new paid launches, record the "
        "spend trail and report to the owner before continuing."
    )


def evaluate(
    manifest: dict,
    at: datetime | None,
    rate_window: int,
    stall_factor: float,
    budget_warn: float,
) -> tuple[dict, int, list[str], list[str]]:
    data = tasklib.status(manifest, at=at, rate_window_min=rate_window)
    rows = data["units"]

    stalled_rows = [
        row
        for row in rows
        if row["elapsed_min"] is not None
        and row["elapsed_min"] > row["bound_min"] * stall_factor
    ]
    stalled_ids = {row["id"] for row in stalled_rows}
    for row in rows:
        row["stalled"] = row["id"] in stalled_ids
    overdue_rows = [
        row
        for row in rows
        if row["elapsed_min"] is not None
        and row["elapsed_min"] > row["bound_min"]
        and row["id"] not in stalled_ids
    ]

    rate_total = sum(entry["count"] for entry in data["rate_limits"])
    budget = data["budget"]
    exceeded = bool(
        budget.get("limit_usd") is not None
        and budget["spent_usd"] > float(budget["limit_usd"]) + 1e-9
    )
    warned = bool(
        budget.get("percent") is not None
        and budget["percent"] >= budget_warn * 100.0
        and not exceeded
    )

    attention: list[str] = []
    checks: list[dict] = []

    if stalled_rows:
        detail = "; ".join(
            f"{row['id']} {row['elapsed_min']:.0f}m > "
            f"{row['bound_min'] * stall_factor:.0f}m (effort {row['effort']})"
            for row in stalled_rows
        )
        checks.append(check("stalls", "fail", detail))
        attention.extend(
            f"{row['id']}: stalled ({row['elapsed_min']:.0f}m > "
            f"{row['bound_min'] * stall_factor:.0f}m)"
            for row in stalled_rows
        )
    else:
        checks.append(check("stalls", "ok", "no stalled units"))

    if overdue_rows:
        detail = "; ".join(
            f"{row['id']} {row['elapsed_min']:.0f}m > {row['bound_min']}m"
            for row in overdue_rows
        )
        checks.append(check("overdue", "attention", detail))
        attention.extend(
            f"{row['id']}: overdue ({row['elapsed_min']:.0f}m > {row['bound_min']}m)"
            for row in overdue_rows
        )
    else:
        checks.append(check("overdue", "ok", "no overdue units"))

    rate_detail = ", ".join(
        f"{entry['model']}: {entry['count']}" for entry in data["rate_limits"]
    )
    if rate_total >= RATE_ATTENTION:
        checks.append(
            check(
                "rate_limits",
                "attention",
                f"{rate_total} error(s) in {rate_window}m: {rate_detail}",
            )
        )
        attention.extend(
            f"{entry['model']}: {entry['count']} rate-limit errors in {rate_window}m"
            for entry in data["rate_limits"]
        )
    elif rate_total:
        checks.append(
            check(
                "rate_limits",
                "ok",
                f"{rate_total} error in {rate_window}m below attention threshold "
                f"{RATE_ATTENTION}: {rate_detail}",
            )
        )
    else:
        checks.append(
            check("rate_limits", "ok", f"no rate-limit errors in {rate_window}m")
        )

    if exceeded:
        checks.append(
            check(
                "budget",
                "fail",
                f"${budget['spent_usd']:.4f} > ${float(budget['limit_usd']):.2f}",
            )
        )
        attention.append(
            f"budget exceeded: ${budget['spent_usd']:.4f} > "
            f"${float(budget['limit_usd']):.2f}"
        )
    elif warned:
        checks.append(
            check(
                "budget",
                "attention",
                f"{budget['percent']:.0f}% >= {budget_warn * 100:.0f}% "
                f"(${budget['spent_usd']:.4f} / ${float(budget['limit_usd']):.2f})",
            )
        )
        attention.append(
            f"budget at {budget['percent']:.0f}%: ${budget['spent_usd']:.4f} / "
            f"${float(budget['limit_usd']):.2f}"
        )
    elif budget.get("percent") is not None:
        checks.append(
            check(
                "budget",
                "ok",
                f"{budget['percent']:.0f}% of ${float(budget['limit_usd']):.2f}",
            )
        )
    else:
        checks.append(check("budget", "ok", f"${budget['spent_usd']:.4f} / no usable limit"))

    suggestions: list[str] = []
    for row in stalled_rows:
        suggestions.append(stalled_suggestion(row))
    suggestions.extend(policy_suggestions(data["policy"], rate_total >= RATE_ATTENTION))
    if exceeded or warned:
        suggestions.append(budget_suggestion(data, exceeded))

    data["attention"] = attention
    if stalled_rows or exceeded:
        exit_code = 2
    elif overdue_rows or rate_total >= RATE_ATTENTION or warned:
        exit_code = 1
    else:
        exit_code = 0

    result = {
        "healthy": exit_code == 0,
        "exit_code": exit_code,
        "checks": checks,
        "attention": attention,
        "suggestions": suggestions,
        "status": data,
    }
    return result, exit_code, attention, suggestions


def render(result: dict) -> str:
    data = result["status"]
    lines = [tasklib.format_status(data), "", "CHECKS"]
    for item in result["checks"]:
        lines.append(f"- {item['name']} [{item['status']}] {item['detail']}")
    if result["suggestions"]:
        lines.append("")
        lines.append("SUGGESTIONS")
        lines.extend(f"- {item}" for item in result["suggestions"])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="task slug")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--at", help="ISO8601 timestamp for deterministic output")
    parser.add_argument("--rate-window", type=int, default=30, metavar="MIN")
    parser.add_argument("--stall-factor", type=float, default=1.5)
    parser.add_argument("--budget-warn", type=float, default=0.8, metavar="FRACTION")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.rate_window < 0:
        print("ERROR: --rate-window must be >= 0", file=sys.stderr)
        return 2
    if args.stall_factor <= 0:
        print("ERROR: --stall-factor must be > 0", file=sys.stderr)
        return 2
    if not 0 <= args.budget_warn <= 1:
        print("ERROR: --budget-warn must be between 0 and 1", file=sys.stderr)
        return 2
    try:
        at = parse_at(args.at)
        manifest = tasklib.load(args.task)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    result, exit_code, _, _ = evaluate(
        manifest, at, args.rate_window, args.stall_factor, args.budget_warn
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render(result))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
