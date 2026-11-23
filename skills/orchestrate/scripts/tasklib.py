#!/usr/bin/env python3
"""Shared task manifest, report schema and accounting helpers.

Standard library only. Every tool in this directory imports this module so the
manifest and report contracts stay in one place.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("ORCHESTRATE_ROOT", "/tmp/opencode/orchestrate"))

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
RATE_RE = re.compile(r"429|rate.?limit|quota|too many requests|resource_exhausted", re.I)

POLICIES = {"default", "tier2-only"}
STATES = {
    "planned",
    "ready",
    "running",
    "review_ready",
    "reviewing",
    "changes_requested",
    "passed",
    "blocked",
    "noop",
    "merged",
    "discarded",
}
ACTIVE_STATES = {
    "ready",
    "running",
    "review_ready",
    "reviewing",
    "changes_requested",
}
TERMINAL_STATES = {"merged", "blocked", "noop", "discarded"}
EFFORT_MINUTES = {"low": 15, "medium": 30, "high": 60, "xhigh": 60, "max": 90}
RISK_REVIEWS = {"normal": 1, "security": 2, "persistent-data": 2, "public-api": 2}
ROLES = {"worker", "reviewer", "explore", "utility"}
REPORT_STATUSES = {
    "worker": {"DONE", "BLOCKED", "NO-OP"},
    "utility": {"DONE", "BLOCKED", "NO-OP"},
    "explore": {"DONE", "BLOCKED", "NO-OP"},
    "reviewer": {"PASS", "FAIL", "BLOCKED"},
}
SEVERITIES = {"blocker", "major", "minor", "nit"}

_MODEL_CACHE: tuple[dict[str, int], dict[str, tuple[float, float]]] | None = None


# --------------------------------------------------------------------------- time


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_since(value: str | None, at: datetime | None = None) -> float | None:
    start = parse_time(value)
    if start is None:
        return None
    end = at or datetime.now(timezone.utc)
    return max(0.0, (end - start).total_seconds() / 60.0)


# ------------------------------------------------------------------------- paths


def task_dir(slug: str) -> Path:
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid task slug: {slug!r}")
    return ROOT / slug


def manifest_path(slug: str) -> Path:
    return task_dir(slug) / "task.json"


def reports_dir(slug: str) -> Path:
    return task_dir(slug) / "reports"


def briefs_dir(slug: str) -> Path:
    return task_dir(slug) / "briefs"


def evidence_dir(slug: str) -> Path:
    return task_dir(slug) / "evidence"


def ensure_dirs(slug: str) -> None:
    for path in (task_dir(slug), reports_dir(slug), briefs_dir(slug), evidence_dir(slug)):
        path.mkdir(parents=True, exist_ok=True)


def load(slug: str) -> dict:
    path = manifest_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"no manifest for {slug}: {path}")
    return json.loads(path.read_text())


def save(slug: str, manifest: dict) -> None:
    manifest["updated_at"] = now()
    path = manifest_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".task.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_event(slug: str, event: dict) -> None:
    ensure_dirs(slug)
    payload = {"at": now(), **event}
    with (task_dir(slug) / "events.jsonl").open("a") as handle:
        handle.write(json.dumps(payload) + "\n")


# ---------------------------------------------------------------------- manifest


def new_manifest(
    task: str,
    repo: str,
    ref: str,
    policy: str = "default",
    budget_usd: float | None = None,
    start_sha: str | None = None,
) -> dict:
    if not SLUG_RE.match(task):
        raise ValueError(f"invalid task slug: {task!r}")
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {sorted(POLICIES)}")
    if budget_usd is not None and budget_usd < 0:
        raise ValueError("budget must be >= 0")
    return {
        "version": 1,
        "task": task,
        "created_at": now(),
        "updated_at": now(),
        "target": {
            "repo": str(Path(repo).expanduser().resolve()),
            "ref": ref,
            "start_sha": start_sha,
        },
        "policy": {
            "name": policy,
            "max_tier": 2 if policy == "tier2-only" else 3,
            "allow_free": policy != "tier2-only",
        },
        "budget": {"limit_usd": budget_usd, "spent_usd": 0.0},
        "approvals": [],
        "contracts": [],
        "units": {},
        "calls": [],
    }


def unit_template(uid: str, objective: str, owned_paths: list[str], **fields) -> dict:
    unit = {
        "id": uid,
        "objective": objective,
        "worktree": None,
        "branch": None,
        "base_sha": None,
        "depends_on": [],
        "owned_paths": list(owned_paths),
        "contracts": [],
        "resource_isolation": "",
        "context": "",
        "acceptance": [],
        "effort": "medium",
        "risk": "normal",
        "state": "planned",
        "attempts": 0,
        "model": None,
        "implementer_family": None,
        "session": None,
        "worker_sha": None,
        "reviewed_sha": None,
        "merge_sha": None,
        "reviewer_verdicts": [],
        "reports": [],
        "started_at": None,
        "ended_at": None,
        "note": "",
        "follow_ups": [],
    }
    unit.update(fields)
    return unit


def required_reviews(unit: dict) -> int:
    return RISK_REVIEWS.get(unit.get("risk", "normal"), 1)


def approved_review_families(unit: dict) -> list[str]:
    """PASS verdicts from independent families for the current worker SHA."""
    implementer = unit.get("implementer_family")
    worker_sha = unit.get("worker_sha")
    families: list[str] = []
    for verdict in unit.get("reviewer_verdicts", []):
        family = verdict.get("family")
        if verdict.get("verdict") != "PASS" or not family:
            continue
        if implementer and family == implementer:
            continue
        if worker_sha and verdict.get("sha") != worker_sha:
            continue
        if family not in families:
            families.append(family)
    return families


# ---------------------------------------------------------------------- models


def _model_tables() -> tuple[dict[str, int], dict[str, tuple[float, float]]]:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    tiers: dict[str, int] = {}
    prices: dict[str, tuple[float, float]] = {}
    section: int | None = None
    path = SKILL_DIR / "references" / "models.md"
    for line in path.read_text().splitlines():
        header = re.match(r"^## Tier (\d)", line)
        if header:
            section = int(header.group(1))
            continue
        if section is None:
            continue
        priced = re.match(
            r"^\| `([^`]+)` \| [^|]+ \| [^|]+ \| ([\d.]+) / ([\d.]+) \|$", line
        )
        if priced:
            name = priced.group(1)
            tiers[name] = section
            prices[name] = (float(priced.group(2)), float(priced.group(3)))
            continue
        free = re.match(r"^\| `([^`]+)` \| [^|]+ \| [^|]+ \|$", line)
        if free:
            name = free.group(1)
            tiers.setdefault(name, section)
            prices.setdefault(name, (0.0, 0.0))
    _MODEL_CACHE = (tiers, prices)
    return _MODEL_CACHE


def model_base(model: str | None) -> str:
    if not model:
        return ""
    return model.split("#", 1)[0].rsplit("/", 1)[-1]


def model_tier(model: str | None) -> int | None:
    return _model_tables()[0].get(model_base(model))


def price_for(model: str | None) -> tuple[float, float]:
    return _model_tables()[1].get(model_base(model), (0.0, 0.0))


def cost_usd(model: str | None, tokens_in: int, tokens_out: int) -> float:
    price_in, price_out = price_for(model)
    return round((tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out, 6)


# ------------------------------------------------------------------- validation


def path_overlap(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        value = value.strip().replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        return value.rstrip("/")

    left, right = norm(left), norm(right)
    wildcard = min(
        [i for i in (left.find("*"), left.find("?"), left.find("[")) if i >= 0],
        default=len(left),
    )
    left_prefix = left[:wildcard].rstrip("/")
    wildcard = min(
        [i for i in (right.find("*"), right.find("?"), right.find("[")) if i >= 0],
        default=len(right),
    )
    right_prefix = right[:wildcard].rstrip("/")
    if left == right:
        return True
    return (
        left.startswith(right + "/")
        or right.startswith(left + "/")
        or (left_prefix and right_prefix and (
            left_prefix == right_prefix
            or left_prefix.startswith(right_prefix + "/")
            or right_prefix.startswith(left_prefix + "/")
        ))
    )


def _cycles(units: dict) -> list[list[str]]:
    found: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(uid: str) -> None:
        state[uid] = 1
        stack.append(uid)
        for dep in units[uid].get("depends_on", []):
            if state.get(dep) == 1:
                start = stack.index(dep)
                found.append(stack[start:] + [dep])
            elif state.get(dep, 0) == 0 and dep in units:
                visit(dep)
        stack.pop()
        state[uid] = 2

    for uid in units:
        if state.get(uid, 0) == 0:
            visit(uid)
    return found


def validate_manifest(manifest: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if manifest.get("version") != 1:
        errors.append("version must be 1")
    if not SLUG_RE.match(str(manifest.get("task", ""))):
        errors.append("task slug is invalid")
    policy = manifest.get("policy", {}).get("name")
    if policy not in POLICIES:
        errors.append(f"policy.name must be one of {sorted(POLICIES)}")
    target = manifest.get("target", {})
    if not target.get("repo") or not target.get("ref"):
        errors.append("target.repo and target.ref are required")
    start_sha = target.get("start_sha")
    if start_sha and not SHA_RE.match(str(start_sha)):
        errors.append("target.start_sha is not a SHA")

    units = manifest.get("units")
    if not isinstance(units, dict) or not units:
        errors.append("units must be a non-empty object")
        units = {}

    for uid, unit in units.items():
        prefix = f"unit {uid}"
        if unit.get("id") != uid:
            errors.append(f"{prefix}: id does not match its key")
        if not unit.get("objective"):
            errors.append(f"{prefix}: objective is required")
        if unit.get("state") not in STATES:
            errors.append(f"{prefix}: invalid state {unit.get('state')!r}")
        if unit.get("effort") not in EFFORT_MINUTES:
            errors.append(f"{prefix}: invalid effort {unit.get('effort')!r}")
        if unit.get("risk") not in RISK_REVIEWS:
            errors.append(f"{prefix}: invalid risk {unit.get('risk')!r}")
        if not isinstance(unit.get("owned_paths"), list) or not unit.get("owned_paths"):
            errors.append(f"{prefix}: owned_paths must be a non-empty list")
        for dep in unit.get("depends_on", []):
            if dep == uid:
                errors.append(f"{prefix}: depends on itself")
            elif dep not in units:
                errors.append(f"{prefix}: unknown dependency {dep!r}")
        if unit.get("state") == "merged":
            if not unit.get("merge_sha") or not SHA_RE.match(str(unit["merge_sha"])):
                errors.append(f"{prefix}: merged unit needs merge_sha")
            if not unit.get("reviewed_sha"):
                errors.append(f"{prefix}: merged unit needs reviewed_sha")
        if policy == "tier2-only":
            value = unit.get("model")
            if value and model_tier(value) not in (None, 2):
                errors.append(
                    f"{prefix}: model {value!r} is tier {model_tier(value)}, policy is tier2-only"
                )

    ids = list(units)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            for lpath in units[left].get("owned_paths", []):
                for rpath in units[right].get("owned_paths", []):
                    if path_overlap(lpath, rpath):
                        errors.append(
                            f"owned path overlap: {left}:{lpath} and {right}:{rpath}"
                        )

    for cycle in _cycles(units):
        errors.append("dependency cycle: " + " -> ".join(cycle))

    calls = manifest.get("calls", [])
    if not isinstance(calls, list):
        errors.append("calls must be a list")
        calls = []
    for index, call in enumerate(calls):
        prefix = f"call {index}"
        if call.get("unit") and call.get("unit") not in units:
            errors.append(f"{prefix}: unknown unit {call.get('unit')!r}")
        if call.get("role") not in ROLES:
            errors.append(f"{prefix}: invalid role {call.get('role')!r}")
        tier = call.get("tier")
        if not isinstance(tier, int) or tier not in (0, 1, 2, 3):
            errors.append(f"{prefix}: tier must be 0-3")
        elif policy == "tier2-only" and tier != 2:
            errors.append(f"{prefix}: tier {tier} violates tier2-only")
        if tier == 3 and not any("tier 3" in str(a.get("scope", "")).lower() for a in manifest.get("approvals", [])):
            warnings.append(f"{prefix}: tier 3 call without a recorded tier 3 approval")

    budget = manifest.get("budget", {})
    limit = budget.get("limit_usd")
    spent = budget.get("spent_usd", 0.0)
    if not isinstance(spent, (int, float)) or spent < 0:
        errors.append("budget.spent_usd must be >= 0")
    if limit is not None and spent > float(limit) + 1e-9:
        errors.append(f"budget exceeded: spent {spent:.6f} > limit {limit}")

    return errors, warnings


def validate_report(report: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be a JSON object"]
    if report.get("version") != 1:
        errors.append("version must be 1")
    role = report.get("role")
    if role not in ROLES:
        return errors + [f"role must be one of {sorted(ROLES)}"]
    if not report.get("unit"):
        errors.append("unit is required")
    if report.get("status") not in REPORT_STATUSES[role]:
        errors.append(f"status for {role} must be one of {sorted(REPORT_STATUSES[role])}")
    sha = report.get("sha")
    if sha is not None and not SHA_RE.match(str(sha)):
        errors.append("sha must be null or 7-40 lowercase hex characters")
    if "files" not in report:
        errors.append("files is required")
    for key in ("files", "commands", "acceptance", "findings"):
        if key in report and not isinstance(report[key], list):
            errors.append(f"{key} must be a list")
    if role == "reviewer" and "findings" not in report:
        errors.append("reviewer reports need a findings list")
    for command in report.get("commands", []):
        if not isinstance(command, dict) or not command.get("cmd"):
            errors.append("each command needs a cmd")
        elif not isinstance(command.get("exit"), int):
            errors.append(f"command has no integer exit: {command.get('cmd')!r}")
    for item in report.get("acceptance", []):
        if not isinstance(item, dict) or not item.get("criterion"):
            errors.append("each acceptance item needs a criterion")
        elif not isinstance(item.get("pass"), bool):
            errors.append(f"acceptance item has no boolean pass: {item.get('criterion')!r}")
    for finding in report.get("findings", []):
        if not isinstance(finding, dict):
            errors.append("each finding must be an object")
            continue
        if finding.get("severity") not in SEVERITIES:
            errors.append(f"invalid finding severity: {finding.get('severity')!r}")
        if not finding.get("message"):
            errors.append("each finding needs a message")
    usage = report.get("usage", {})
    if usage and not isinstance(usage, dict):
        errors.append("usage must be an object")
    elif isinstance(usage, dict):
        for key in ("tokens_in", "tokens_out"):
            if not isinstance(usage.get(key, 0), int) or usage.get(key, 0) < 0:
                errors.append(f"usage.{key} must be a non-negative integer")
    if role == "reviewer" and not report.get("family"):
        errors.append("reviewer reports need a family")
    if role in ("worker", "reviewer"):
        if not report.get("commands"):
            errors.append(f"{role} reports need at least one command")
        if not report.get("acceptance"):
            errors.append(f"{role} reports need acceptance evidence")
    if role in ("worker", "utility") and report.get("status") == "DONE":
        if any(command.get("exit") != 0 for command in report.get("commands", [])):
            errors.append("DONE requires every command to exit 0")
        if not all(item.get("pass") for item in report.get("acceptance", [])):
            errors.append("DONE requires every acceptance item to pass")
    if role == "reviewer" and report.get("status") == "PASS":
        blocking = [
            finding
            for finding in report.get("findings", [])
            if finding.get("severity") in ("blocker", "major")
        ]
        if blocking:
            errors.append("PASS cannot carry blocker or major findings")
    return errors


# ---------------------------------------------------------------------- reports


def add_call(
    manifest: dict,
    unit: str | None,
    role: str,
    model: str | None,
    tier: int,
    outcome: str,
    session: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error: str | None = None,
) -> dict:
    if role not in ROLES:
        raise ValueError(f"invalid role: {role!r}")
    if tier not in (0, 1, 2, 3):
        raise ValueError("tier must be 0-3")
    if manifest.get("policy", {}).get("name") == "tier2-only" and tier != 2:
        raise ValueError("tier2-only policy permits tier 2 calls only")
    if unit and unit not in manifest.get("units", {}):
        raise ValueError(f"unknown unit: {unit!r}")
    call = {
        "at": now(),
        "unit": unit,
        "role": role,
        "model": model,
        "tier": tier,
        "session": session,
        "outcome": outcome,
        "tokens_in": max(0, int(tokens_in)),
        "tokens_out": max(0, int(tokens_out)),
        "cost_usd": cost_usd(model, max(0, int(tokens_in)), max(0, int(tokens_out))),
        "error": error,
    }
    manifest.setdefault("calls", []).append(call)
    manifest.setdefault("budget", {})["spent_usd"] = round(
        float(manifest.get("budget", {}).get("spent_usd", 0.0)) + call["cost_usd"], 6
    )
    return call


def ingest_report(slug: str, report: dict, source: str | None = None) -> dict:
    errors = validate_report(report)
    if errors:
        raise ValueError("invalid report:\n- " + "\n- ".join(errors))
    manifest = load(slug)
    unit_id = report["unit"]
    unit = manifest.get("units", {}).get(unit_id)
    if unit is None:
        raise ValueError(f"unknown unit: {unit_id!r}")

    ensure_dirs(slug)
    stamp = now().replace(":", "").replace("-", "")
    sequence = len(unit.get("reports", [])) + 1
    filename = f"{unit_id}-{report['role']}-{stamp}-{sequence:02d}.json"
    path = reports_dir(slug) / filename
    path.write_text(json.dumps(report, indent=2) + "\n")
    unit.setdefault("reports", []).append(str(path.relative_to(task_dir(slug))))
    if source:
        report = {**report, "source": source}

    model = report.get("model") or unit.get("model")
    tier = report.get("tier")
    if tier is None:
        tier = model_tier(model) if model else 0
    add_call(
        manifest,
        unit_id,
        report["role"],
        model,
        int(tier),
        report["status"],
        session=report.get("session"),
        tokens_in=report.get("usage", {}).get("tokens_in", 0),
        tokens_out=report.get("usage", {}).get("tokens_out", 0),
        error=report.get("error"),
    )

    status = report["status"]
    if report["role"] in ("worker", "utility"):
        unit["attempts"] = int(unit.get("attempts", 0)) + 1
        unit["ended_at"] = now()
        unit["note"] = report.get("notes", "") or unit.get("note", "")
        if status == "DONE":
            unit["worker_sha"] = report.get("sha")
            unit["state"] = "review_ready"
        elif status == "BLOCKED":
            unit["state"] = "blocked"
        elif status == "NO-OP":
            unit["state"] = "noop"
    elif report["role"] == "explore":
        unit["attempts"] = int(unit.get("attempts", 0)) + 1
        unit["ended_at"] = now()
        unit["note"] = report.get("notes", "") or unit.get("note", "")
        if status == "DONE":
            unit["worker_sha"] = report.get("sha")
            unit["state"] = "review_ready" if report.get("files") else "noop"
        elif status == "BLOCKED":
            unit["state"] = "blocked"
        else:
            unit["state"] = "noop"
    elif report["role"] == "reviewer":
        verdict = {
            "at": now(),
            "verdict": status,
            "family": report.get("family"),
            "sha": report.get("sha"),
            "model": model,
            "notes": report.get("notes", ""),
        }
        unit.setdefault("reviewer_verdicts", []).append(verdict)
        if status == "PASS":
            needed = required_reviews(unit)
            if len(approved_review_families(unit)) >= needed and unit.get("worker_sha"):
                unit["state"] = "passed"
                unit["reviewed_sha"] = unit["worker_sha"]
            else:
                unit["state"] = "reviewing"
        elif status == "FAIL":
            unit["state"] = "changes_requested"
        else:
            unit.setdefault("note", "")
            note = f"reviewer blocked: {report.get('notes', '')}".strip()
            unit["note"] = (unit.get("note", "") + " | " + note).strip(" |")

    append_event(
        slug,
        {
            "type": "report",
            "unit": unit_id,
            "role": report["role"],
            "status": status,
            "path": str(path),
        },
    )
    save(slug, manifest)
    return {"path": str(path), "state": unit["state"]}


def latest_reports(slug: str, unit: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for relative in unit.get("reports", []):
        path = task_dir(slug) / relative
        if not path.exists():
            continue
        report = json.loads(path.read_text())
        result[report.get("role", "")] = report
    return result


# ------------------------------------------------------------------------ status


def status(manifest: dict, at: datetime | None = None, rate_window_min: int = 30) -> dict:
    at = at or datetime.now(timezone.utc)
    rows = []
    attention = []
    for uid, unit in manifest.get("units", {}).items():
        elapsed = (
            minutes_since(unit.get("started_at"), at)
            if unit.get("state") in ACTIVE_STATES
            else None
        )
        bound = EFFORT_MINUTES.get(unit.get("effort", "medium"), 30)
        overdue = elapsed is not None and elapsed > bound
        stalled = elapsed is not None and elapsed > bound * 1.5
        row = {
            "id": uid,
            "state": unit.get("state"),
            "effort": unit.get("effort"),
            "risk": unit.get("risk"),
            "model": unit.get("model"),
            "attempts": unit.get("attempts", 0),
            "elapsed_min": round(elapsed, 1) if elapsed is not None else None,
            "bound_min": bound,
            "overdue": overdue,
            "stalled": stalled,
            "reviews": f"{len(approved_review_families(unit))}/{required_reviews(unit)}",
            "note": unit.get("note", ""),
        }
        rows.append(row)
        if stalled:
            attention.append(f"{uid}: stalled ({elapsed:.0f}m > {bound * 1.5:.0f}m)")
        elif overdue:
            attention.append(f"{uid}: overdue ({elapsed:.0f}m > {bound}m)")

    cutoff = at - timedelta(minutes=rate_window_min)
    rates: dict[str, dict] = {}
    for call in manifest.get("calls", []):
        error = call.get("error")
        if not error or not RATE_RE.search(str(error)):
            continue
        called_at = parse_time(call.get("at"))
        if called_at and called_at < cutoff:
            continue
        key = call.get("model") or "unknown"
        entry = rates.setdefault(key, {"model": key, "count": 0, "last": call.get("at")})
        entry["count"] += 1
        entry["last"] = call.get("at")
    for entry in rates.values():
        attention.append(f"{entry['model']}: {entry['count']} rate-limit errors")

    budget = manifest.get("budget", {})
    limit = budget.get("limit_usd")
    spent = float(budget.get("spent_usd", 0.0))
    pct = (spent / float(limit) * 100.0) if limit else None
    if limit and spent > float(limit):
        attention.append(f"budget exceeded: ${spent:.4f} > ${float(limit):.2f}")
    elif pct is not None and pct >= 80:
        attention.append(f"budget at {pct:.0f}%: ${spent:.4f} / ${float(limit):.2f}")

    return {
        "task": manifest.get("task"),
        "policy": manifest.get("policy", {}).get("name"),
        "target": manifest.get("target"),
        "units": rows,
        "rate_limits": list(rates.values()),
        "budget": {"limit_usd": limit, "spent_usd": spent, "percent": pct},
        "attention": attention,
        "active": sum(1 for row in rows if row["state"] in ACTIVE_STATES),
        "blocked": sum(1 for row in rows if row["state"] == "blocked"),
        "merged": sum(1 for row in rows if row["state"] == "merged"),
    }


def format_status(data: dict) -> str:
    lines = [
        f"Task: {data['task']}  policy: {data['policy']}  target: "
        f"{data['target'].get('repo')}@{data['target'].get('ref')}",
    ]
    budget = data["budget"]
    if budget["limit_usd"]:
        lines.append(
            f"Budget: ${budget['spent_usd']:.4f} / ${float(budget['limit_usd']):.2f} "
            f"({budget['percent']:.0f}%)"
        )
    else:
        lines.append(f"Budget: ${budget['spent_usd']:.4f} / no limit")
    lines.append("")
    lines.append(f"{'UNIT':<12} {'STATE':<16} {'EFFORT':<8} {'ELAPSED':<9} {'REVIEWS':<8} NOTE")
    for row in data["units"]:
        elapsed = "-" if row["elapsed_min"] is None else f"{row['elapsed_min']:.0f}m"
        lines.append(
            f"{row['id']:<12} {row['state']:<16} {row['effort']:<8} {elapsed:<9} "
            f"{row['reviews']:<8} {row['note'][:40]}"
        )
    lines.append("")
    if data["attention"]:
        lines.append("ATTENTION")
        lines.extend(f"- {item}" for item in data["attention"])
    else:
        lines.append("No overdue units, rate limits or budget warnings.")
    return "\n".join(lines)


# ------------------------------------------------------------------------ render


def render_markdown(manifest: dict) -> str:
    target = manifest.get("target", {})
    budget = manifest.get("budget", {})
    lines = [
        f"# Orchestration report: {manifest.get('task')}",
        "",
        f"- Target: `{target.get('repo')}` @ `{target.get('ref')}` (start `{target.get('start_sha')}`)",
        f"- Model policy: `{manifest.get('policy', {}).get('name')}`",
        f"- Created: {manifest.get('created_at')}  Updated: {manifest.get('updated_at')}",
        f"- Spend: ${float(budget.get('spent_usd', 0.0)):.4f}"
        + (f" / ${float(budget['limit_usd']):.2f}" if budget.get("limit_usd") else " / no limit"),
        "",
        "## Units",
        "",
        "| Unit | State | Risk | Effort | Model | Reviews | Reviewed SHA | Merge SHA |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for uid, unit in manifest.get("units", {}).items():
        lines.append(
            f"| {uid} | {unit.get('state')} | {unit.get('risk')} | {unit.get('effort')} | "
            f"`{unit.get('model') or '-'}` | {len(approved_review_families(unit))}/"
            f"{required_reviews(unit)} | `{unit.get('reviewed_sha') or '-'}` | "
            f"`{unit.get('merge_sha') or '-'}` |"
        )
    lines.append("")
    calls = manifest.get("calls", [])
    if calls:
        by_role: dict[str, float] = {}
        for call in calls:
            by_role[call.get("role", "?")] = round(
                by_role.get(call.get("role", "?"), 0.0) + float(call.get("cost_usd", 0.0)), 6
            )
        lines.append("## Spend by role")
        lines.append("")
        for role, cost in sorted(by_role.items()):
            lines.append(f"- {role}: ${cost:.4f}")
        lines.append("")
    blockers = [
        (uid, unit)
        for uid, unit in manifest.get("units", {}).items()
        if unit.get("state") == "blocked"
    ]
    if blockers:
        lines.append("## Blockers")
        lines.append("")
        for uid, unit in blockers:
            lines.append(f"- **{uid}**: {unit.get('note') or 'no reason recorded'}")
        lines.append("")
    follow_ups = [
        (uid, item)
        for uid, unit in manifest.get("units", {}).items()
        for item in unit.get("follow_ups", [])
    ]
    if follow_ups:
        lines.append("## Follow-ups")
        lines.append("")
        for uid, item in follow_ups:
            lines.append(f"- **{uid}**: {item}")
        lines.append("")
    if manifest.get("approvals"):
        lines.append("## Approvals")
        lines.append("")
        for approval in manifest["approvals"]:
            lines.append(
                f"- {approval.get('at')}: {approval.get('scope')} "
                f"{('— ' + approval['note']) if approval.get('note') else ''}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def owner_check(manifest: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    units = manifest.get("units", {})
    if not units:
        errors.append("no units recorded")
    for uid, unit in units.items():
        state = unit.get("state")
        if state == "passed":
            errors.append(f"{uid}: passed but not merged")
        elif state not in TERMINAL_STATES:
            errors.append(f"{uid}: not terminal ({state})")
        if state == "merged":
            if not unit.get("reviewed_sha"):
                errors.append(f"{uid}: merged without reviewed_sha")
            if len(approved_review_families(unit)) < required_reviews(unit):
                errors.append(f"{uid}: merged without the required independent PASS reviews")
        if state == "blocked" and not unit.get("note"):
            errors.append(f"{uid}: blocked without a reason")
    if not manifest.get("calls"):
        warnings.append("no calls recorded; spend is unverifiable")
    limit = manifest.get("budget", {}).get("limit_usd")
    spent = float(manifest.get("budget", {}).get("spent_usd", 0.0))
    if limit is not None and spent > float(limit):
        errors.append(f"budget exceeded: ${spent:.4f} > ${float(limit):.2f}")
    return errors, warnings
