#!/usr/bin/env python3
"""Validate a task spec, compute execution waves and write the task manifest.

Examples:
    plan.py plan SPEC.json                  # validate and print the wave plan (dry-run)
    plan.py plan SPEC.json --json           # machine-readable plan
    plan.py plan SPEC.json --write          # create the task manifest
    plan.py plan SPEC.json --write --force  # overwrite an existing manifest
    plan.py brief --task SLUG --unit UID --role worker
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import brief
import tasklib

UID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def path_is_safe(value: str) -> bool:
    """Reject absolute, parent-traversing and whole-repo owned paths."""
    if not isinstance(value, str):
        return False
    path = value.strip().replace("\\", "/")
    if path in {"", ".", "*", "**", "**/*"}:
        return False
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return False
    parts = [part for part in path.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ------------------------------------------------------------------- spec input


def load_spec(path: str) -> dict:
    spec = json.loads(Path(path).read_text())
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    return spec


def validate_spec(spec: dict) -> dict:
    """Validate SPEC structure; raise ValueError with every problem listed."""
    errors: list[str] = []

    task = spec.get("task")
    if not isinstance(task, str) or not tasklib.SLUG_RE.match(task):
        errors.append(f"task slug is invalid: {task!r}")

    policy = spec.get("policy", "default")
    if policy not in tasklib.POLICIES:
        errors.append(f"policy must be one of {sorted(tasklib.POLICIES)}: {policy!r}")

    target = spec.get("target")
    if not isinstance(target, dict):
        errors.append("target must be an object with non-empty repo and ref")
        target = {}
    else:
        if not str(target.get("repo") or "").strip():
            errors.append("target.repo is required")
        if not str(target.get("ref") or "").strip():
            errors.append("target.ref is required")

    budget = spec.get("budget_usd")
    if budget is not None:
        if not _is_number(budget):
            errors.append("budget_usd must be a number")
        elif budget < 0:
            errors.append("budget_usd must be >= 0")

    raw_units = spec.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        errors.append("units must be a non-empty list")
        raw_units = []

    units: dict[str, dict] = {}
    for index, raw in enumerate(raw_units):
        prefix = f"units[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} must be an object")
            continue
        uid = raw.get("id")
        if not isinstance(uid, str) or not UID_RE.match(uid):
            errors.append(f"{prefix}: invalid unit id {uid!r}")
            continue
        if uid in units:
            errors.append(f"{prefix}: duplicate unit id {uid!r}")
            continue
        unit = dict(raw)
        unit["id"] = uid
        units[uid] = unit

        objective = raw.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            errors.append(f"unit {uid}: objective is required")
        owned = raw.get("owned_paths")
        if (
            not isinstance(owned, list)
            or not owned
            or not all(isinstance(p, str) and p.strip() for p in owned)
        ):
            errors.append(f"unit {uid}: owned_paths must be a non-empty list of path strings")
        elif isinstance(owned, list):
            for owned_path in owned:
                if isinstance(owned_path, str) and not path_is_safe(owned_path):
                    errors.append(f"unit {uid}: unsafe owned path {owned_path!r}")
        if raw.get("effort", "medium") not in tasklib.EFFORT_MINUTES:
            errors.append(
                f"unit {uid}: effort must be one of {sorted(tasklib.EFFORT_MINUTES)}: "
                f"{raw.get('effort')!r}"
            )
        if raw.get("risk", "normal") not in tasklib.RISK_REVIEWS:
            errors.append(
                f"unit {uid}: risk must be one of {sorted(tasklib.RISK_REVIEWS)}: "
                f"{raw.get('risk')!r}"
            )
        deps = raw.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) and d.strip() for d in deps):
            errors.append(f"unit {uid}: depends_on must be a list of unit ids")
        elif uid in deps:
            errors.append(f"unit {uid}: depends on itself")
        for key in ("model", "implementer_family", "context", "resource_isolation"):
            value = raw.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"unit {uid}: {key} must be a string")
        for key in ("acceptance", "contracts"):
            value = raw.get(key)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(v, str) for v in value)
            ):
                errors.append(f"unit {uid}: {key} must be a list of strings")
        estimate = raw.get("estimate")
        if estimate is not None:
            if not isinstance(estimate, dict):
                errors.append(f"unit {uid}: estimate must be an object")
            else:
                for key in ("tokens_in", "tokens_out"):
                    value = estimate.get(key)
                    if value is not None and (not _is_number(value) or value < 0):
                        errors.append(f"unit {uid}: estimate.{key} must be a non-negative number")

    for uid in sorted(units):
        deps = units[uid].get("depends_on")
        if not isinstance(deps, list):
            continue  # shape already flagged above
        for dep in deps:
            if isinstance(dep, str) and dep not in units:
                errors.append(f"unit {uid}: unknown dependency {dep!r}")

    ids = sorted(units)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            for lpath in units[left].get("owned_paths", []) or []:
                if not isinstance(lpath, str):
                    continue
                for rpath in units[right].get("owned_paths", []) or []:
                    if isinstance(rpath, str) and tasklib.path_overlap(lpath, rpath):
                        errors.append(
                            f"owned path overlap: {left}:{lpath} and {right}:{rpath}"
                        )

    # tasklib._cycles expects {uid: {"depends_on": [...]}} with clean lists.
    graph = {
        uid: {
            "depends_on": [
                dep for dep in (units[uid].get("depends_on") or []) if isinstance(dep, str)
            ]
            if isinstance(units[uid].get("depends_on"), list)
            else []
        }
        for uid in units
    }
    for cycle in tasklib._cycles(graph):
        errors.append("dependency cycle: " + " -> ".join(cycle))

    if errors:
        raise ValueError("plan invalid:\n- " + "\n- ".join(errors))
    return {"units": units, "policy": policy, "target": target, "budget_usd": budget}


# ------------------------------------------------------------------------ waves


def unit_deps(unit: dict) -> list[str]:
    deps = unit.get("depends_on")
    if not isinstance(deps, list):
        return []
    return [dep for dep in deps if isinstance(dep, str)]


def build_waves(
    units: dict[str, dict], max_concurrency: int
) -> tuple[list[list[str]], list[str]]:
    """Topological waves; a unit is ready once all deps sit in earlier waves.

    Ready units are taken in unit-id order, up to max_concurrency per wave; the
    excess is queued into later waves. Deterministic for a given spec.
    """
    warnings: list[str] = []
    placed: set[str] = set()
    remaining = set(units)
    waves: list[list[str]] = []
    while remaining:
        ready = sorted(
            uid for uid in remaining if all(dep in placed for dep in unit_deps(units[uid]))
        )
        if not ready:  # pragma: no cover - cycles are rejected in validate_spec
            raise ValueError("dependency cycle among: " + ", ".join(sorted(remaining)))
        if len(ready) > max_concurrency:
            warnings.append(
                f"wave {len(waves) + 1}: {len(ready)} ready units exceed "
                f"max-concurrency {max_concurrency}; {len(ready) - max_concurrency} "
                "queued into later waves"
            )
        wave = ready[:max_concurrency]
        waves.append(wave)
        placed.update(wave)
        remaining.difference_update(wave)
    return waves, warnings


def unit_estimate(unit: dict) -> float | None:
    estimate = unit.get("estimate")
    if not isinstance(estimate, dict):
        return None
    tokens_in = estimate.get("tokens_in")
    tokens_out = estimate.get("tokens_out")
    if (
        not _is_number(tokens_in)
        or not _is_number(tokens_out)
        or tokens_in < 0
        or tokens_out < 0
    ):
        return None
    return tasklib.cost_usd(unit.get("model"), int(tokens_in), int(tokens_out))


def build_plan(spec: dict, max_concurrency: int) -> dict:
    checked = validate_spec(spec)
    units: dict[str, dict] = checked["units"]
    policy = checked["policy"]
    waves, warnings = build_waves(units, max_concurrency)

    estimates = {uid: unit_estimate(units[uid]) for uid in units}
    missing = sorted(uid for uid, value in estimates.items() if value is None)
    if missing:
        if len(missing) == len(units):
            warnings.append("no unit has a token estimate; estimate_usd is null")
        else:
            warnings.append(
                f"{len(missing)} of {len(units)} units have no token estimate "
                f"({', '.join(missing)}); estimate_usd is null"
            )
    total = round(sum(estimates.values()), 6) if not missing and units else None

    budget = checked["budget_usd"]
    if total is not None and budget is not None and total > budget:
        warnings.append(f"estimated cost ${total:.6f} exceeds budget_usd ${budget:.2f}")

    for uid in sorted(units):
        model = units[uid].get("model")
        if not model:
            if policy == "tier2-only":
                warnings.append(f"unit {uid}: no model set; policy is tier2-only")
            continue
        tier = tasklib.model_tier(model)
        if tier is None:
            warnings.append(
                f"unit {uid}: model {model!r} is not in references/models.md; verify before launch"
            )
        elif policy == "tier2-only" and tier != 2:
            warnings.append(f"unit {uid}: model {model!r} is tier {tier}; policy is tier2-only")

    wave_units = [[{**units[uid], "estimate_usd": estimates[uid]} for uid in wave] for wave in waves]
    return {
        "task": spec.get("task"),
        "policy": policy,
        "waves": wave_units,
        "order": [uid for wave in waves for uid in wave],
        "warnings": warnings,
        "estimate_usd": total,
    }


# ---------------------------------------------------------------------- writing


def write_manifest(spec: dict, plan_data: dict) -> Path:
    target = spec.get("target") or {}
    manifest = tasklib.new_manifest(
        plan_data["task"],
        target["repo"],
        target["ref"],
        policy=plan_data["policy"],
        budget_usd=spec.get("budget_usd"),
        start_sha=target.get("start_sha"),
    )
    for unit in (entry for wave in plan_data["waves"] for entry in wave):
        fields: dict = {
            "depends_on": list(unit.get("depends_on") or []),
            "effort": unit.get("effort", "medium"),
            "risk": unit.get("risk", "normal"),
            "acceptance": list(unit.get("acceptance") or []),
            "contracts": list(unit.get("contracts") or []),
            "context": unit.get("context") or "",
            "resource_isolation": unit.get("resource_isolation") or "",
        }
        if unit.get("model"):
            fields["model"] = unit["model"]
        if unit.get("implementer_family"):
            fields["implementer_family"] = unit["implementer_family"]
        if isinstance(unit.get("estimate"), dict):
            fields["estimate"] = dict(unit["estimate"])
        manifest["units"][unit["id"]] = tasklib.unit_template(
            unit["id"], unit["objective"], list(unit.get("owned_paths") or []), **fields
        )
    errors, _warnings = tasklib.validate_manifest(manifest)
    if errors:
        raise ValueError("manifest invalid:\n- " + "\n- ".join(errors))
    tasklib.ensure_dirs(plan_data["task"])
    tasklib.save(plan_data["task"], manifest)
    tasklib.append_event(
        plan_data["task"],
        {
            "type": "plan",
            "units": len(manifest["units"]),
            "waves": len(plan_data["waves"]),
        },
    )
    return tasklib.manifest_path(plan_data["task"])


# ---------------------------------------------------------------------- output


def format_plan(plan_data: dict, spec: dict, max_concurrency: int) -> str:
    target = spec.get("target") or {}
    lines = [
        f"Task: {plan_data['task']}  policy: {plan_data['policy']}  "
        f"target: {target.get('repo')} @ {target.get('ref')}",
        f"Max concurrency: {max_concurrency}",
        "",
    ]
    for index, wave in enumerate(plan_data["waves"], start=1):
        lines.append(f"Wave {index} ({len(wave)} unit{'s' if len(wave) != 1 else ''}):")
        for unit in wave:
            cost = unit.get("estimate_usd")
            cost_text = f"${cost:.6f}" if cost is not None else "-"
            deps = unit_deps(unit)
            dep_text = f" after {', '.join(deps)}" if deps else ""
            lines.append(
                f"  {unit['id']}  [{unit.get('effort')}/{unit.get('risk')}]  "
                f"{unit.get('model') or '-'}  est {cost_text}{dep_text}"
            )
            lines.append(f"      {unit.get('objective')}")
        lines.append("")
    total = plan_data["estimate_usd"]
    lines.append("Order: " + ", ".join(plan_data["order"]))
    lines.append(
        "Estimated cost: "
        + (f"${total:.6f}" if total is not None else "unknown (missing token estimates)")
    )
    if plan_data["warnings"]:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in plan_data["warnings"])
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------- CLI


def resolve_concurrency(args: argparse.Namespace, spec: dict) -> int:
    value = args.max_concurrency
    if value is None:
        value = spec.get("max_concurrency", 8)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"max_concurrency must be a positive integer: {value!r}")
    return value


def cmd_plan(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    max_concurrency = resolve_concurrency(args, spec)
    plan_data = build_plan(spec, max_concurrency)
    manifest_path: Path | None = None
    if args.write:
        path = tasklib.manifest_path(plan_data["task"])
        if path.exists() and not args.force:
            raise ValueError(f"manifest already exists: {path} (use --force to overwrite)")
        manifest_path = write_manifest(spec, plan_data)
    if args.json:
        if manifest_path:
            plan_data["manifest"] = str(manifest_path)
        print(json.dumps(plan_data, indent=2))
    else:
        print(format_plan(plan_data, spec, max_concurrency), end="")
        if manifest_path:
            print(f"Manifest: {manifest_path}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    return brief.run(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plan.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan", help="validate SPEC.json and compute execution waves")
    plan_cmd.add_argument("spec", help="path to SPEC.json")
    plan_cmd.add_argument("--json", action="store_true", help="print the plan as JSON")
    plan_cmd.add_argument("--write", action="store_true", help="create the task manifest")
    plan_cmd.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    plan_cmd.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        metavar="N",
        help="units per wave (default: SPEC max_concurrency, else 8)",
    )
    plan_cmd.set_defaults(func=cmd_plan)

    brief_cmd = sub.add_parser(
        "brief", help="render a self-contained subagent brief from the manifest"
    )
    brief.add_arguments(brief_cmd)
    brief_cmd.set_defaults(func=cmd_brief)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, json.JSONDecodeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
