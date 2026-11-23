#!/usr/bin/env python3
"""Read-only G0 preflight checker.

Examples:
    preflight.py
    preflight.py --repo /work/app --task checkout-retry
    preflight.py --repo /work/app --task checkout-retry --json --strict

Never writes to the repository, the manifest or any task file.
Exit code 1 on any failure; --strict turns warnings into failures.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import tasklib

SKILL_DIR = Path(__file__).resolve().parent.parent
INSTRUCTION_FILES = ("AGENTS.md", "docs/AGENTS.md", "docs/PRINCIPLES.md")
MIN_FREE_BYTES = 1024**3
MIN_PYTHON = (3, 10)


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)


def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def git(repo: Path, *args: str) -> tuple[int, str, str]:
    return run(["git", "-C", str(repo), *args])


def norm_repo(value: str | None) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        try:
            return Path(value).expanduser().absolute()
        except (OSError, TypeError, ValueError):
            return None


def git_checks(repo: Path, git_ready: bool) -> list[dict]:
    checks: list[dict] = []

    if not git_ready:
        checks.append(check("git_status", "warn", "skipped: not a git repository"))
        checks.append(check("worktrees", "warn", "skipped: not a git repository"))
        checks.append(check("orch_branches", "warn", "skipped: not a git repository"))
        return checks

    code, out, err = git(repo, "status", "--porcelain")
    if code != 0:
        checks.append(check("git_status", "warn", f"git status failed: {err or out}"))
    elif out:
        lines = out.splitlines()
        sample = ", ".join(line[3:] if len(line) > 3 else line for line in lines[:5])
        checks.append(
            check(
                "git_status",
                "warn",
                f"dirty tree: {len(lines)} change(s) (allowed, do not disturb): {sample}",
            )
        )
    else:
        checks.append(check("git_status", "ok", "clean tree"))

    code, out, err = git(repo, "worktree", "list", "--porcelain")
    if code != 0:
        checks.append(check("worktrees", "warn", f"git worktree list failed: {err or out}"))
    else:
        paths = [
            line[len("worktree ") :]
            for line in out.splitlines()
            if line.startswith("worktree ")
        ]
        checks.append(
            check(
                "worktrees",
                "ok",
                f"{len(paths)} worktree(s): {', '.join(paths) or '-'}",
            )
        )

    code, out, err = git(
        repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/orch/"
    )
    if code != 0:
        checks.append(
            check("orch_branches", "warn", f"git for-each-ref failed: {err or out}")
        )
    else:
        branches = [line for line in out.splitlines() if line.strip()]
        if branches:
            checks.append(
                check(
                    "orch_branches",
                    "warn",
                    f"{len(branches)} orchestration branch(es): {', '.join(branches[:10])}",
                )
            )
        else:
            checks.append(check("orch_branches", "ok", "no orch/ branches"))
    return checks


def instruction_check(repo: Path) -> dict:
    found = [name for name in INSTRUCTION_FILES if (repo / name).is_file()]
    missing = [name for name in INSTRUCTION_FILES if name not in found]
    if not found:
        return check(
            "instruction_files",
            "warn",
            f"none of {', '.join(INSTRUCTION_FILES)} found under {repo}",
        )
    detail = "found: " + ", ".join(found)
    if missing:
        detail += "; missing: " + ", ".join(missing)
    return check("instruction_files", "ok", detail)


def validator_check() -> dict:
    script = SKILL_DIR / "scripts" / "validate-models.py"
    if not script.is_file():
        return check("model_validator", "fail", f"validator missing: {script}")
    code, out, err = run(["python3", str(script), "--strict"])
    if code == 0:
        detail = "; ".join(out.splitlines()) if out else "OK"
        return check("model_validator", "ok", detail[:300])
    tail = (err or out).strip().splitlines()
    reason = tail[-1] if tail else f"exit {code}"
    return check("model_validator", "fail", f"validate-models.py --strict failed: {reason}")


def manifest_units(manifest: dict | None) -> dict[str, dict]:
    if not isinstance(manifest, dict):
        return {}
    units = manifest.get("units")
    if not isinstance(units, dict):
        return {}
    return {uid: unit for uid, unit in units.items() if isinstance(unit, dict)}


def other_tasks_check(
    repo: Path, task: str | None, current_manifest: dict | None
) -> dict:
    if current_manifest is None:
        current_paths: list[tuple[str, str]] = []
    else:
        current_paths = [
            (uid, path)
            for uid, unit in manifest_units(current_manifest).items()
            for path in unit.get("owned_paths", [])
            if isinstance(path, str)
        ]

    root = tasklib.ROOT
    active: list[str] = []
    overlaps: list[str] = []
    unreadable: list[str] = []
    if root.is_dir():
        for path in sorted(root.glob("*/task.json")):
            slug = path.parent.name
            if task and slug == task:
                continue
            try:
                manifest = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                unreadable.append(f"{slug}: {error}")
                continue
            if not isinstance(manifest, dict):
                unreadable.append(f"{slug}: manifest is not an object")
                continue
            units = manifest_units(manifest)
            active_units = [
                (uid, unit)
                for uid, unit in units.items()
                if unit.get("state") in tasklib.ACTIVE_STATES
            ]
            if not active_units:
                continue
            active.append(f"{slug} ({len(active_units)} active unit(s))")
            target = manifest.get("target")
            target_repo = target.get("repo") if isinstance(target, dict) else None
            if norm_repo(target_repo) != repo:
                continue
            for uid, unit in active_units:
                owned = unit.get("owned_paths", [])
                if not isinstance(owned, list):
                    continue
                for other_path in owned:
                    if not isinstance(other_path, str):
                        continue
                    for cur_uid, cur_path in current_paths:
                        if tasklib.path_overlap(other_path, cur_path):
                            overlaps.append(
                                f"{slug}/{uid}:{other_path} vs {task}/{cur_uid}:{cur_path}"
                            )

    pieces: list[str] = []
    if active:
        pieces.append("other active task(s): " + ", ".join(active))
    if overlaps:
        pieces.append("owned-path overlap: " + "; ".join(overlaps))
    if unreadable:
        pieces.append("unreadable manifest(s): " + "; ".join(unreadable))
    if overlaps:
        return check("other_tasks", "fail", " | ".join(pieces))
    if active or unreadable:
        return check("other_tasks", "warn", " | ".join(pieces))
    return check("other_tasks", "ok", "no other active tasks")


def disk_check(repo: Path) -> dict:
    target = repo if repo.exists() else repo.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as error:
        return check("disk_free", "fail", f"cannot stat {target}: {error}")
    free_gib = usage.free / 1024**3
    status = "ok" if usage.free >= MIN_FREE_BYTES else "warn"
    return check(
        "disk_free",
        status,
        f"{free_gib:.2f} GiB free at {target} (minimum 1 GiB)",
    )


def python_check() -> dict:
    version = platform.python_version()
    status = "ok" if sys.version_info[:2] >= MIN_PYTHON else "fail"
    return check(
        "python_version",
        status,
        f"Python {version} (minimum {MIN_PYTHON[0]}.{MIN_PYTHON[1]})",
    )


def manifest_check(task: str, manifest: dict | None, load_error: str | None) -> dict:
    if load_error:
        return check("manifest", "fail", load_error)
    if not isinstance(manifest, dict):
        return check("manifest", "fail", "manifest must be a JSON object")
    try:
        errors, warnings = tasklib.validate_manifest(manifest)
    except (AttributeError, TypeError, ValueError) as error:
        return check("manifest", "fail", f"malformed manifest: {error}")
    if errors:
        return check("manifest", "fail", "; ".join(errors))
    if warnings:
        return check("manifest", "warn", "; ".join(warnings))
    return check("manifest", "ok", f"manifest valid for {task}")


def gather(repo: Path, task: str | None) -> tuple[list[dict], list[str], list[str], bool]:
    checks: list[dict] = []

    code, out, err = git(repo, "rev-parse", "--show-toplevel")
    git_ready = code == 0 and bool(out)
    if not repo.exists():
        checks.append(check("git_root", "fail", f"repo path does not exist: {repo}"))
        git_ready = False
    elif git_ready:
        checks.append(check("git_root", "ok", out.splitlines()[0]))
    else:
        checks.append(
            check("git_root", "warn", f"not a git repository: {repo} ({err or out})")
        )

    checks.extend(git_checks(repo, git_ready and repo.exists()))
    checks.append(instruction_check(repo))
    checks.append(validator_check())

    current_manifest: dict | None = None
    load_error: str | None = None
    if task:
        try:
            current_manifest = tasklib.load(task)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            load_error = f"cannot load task {task!r}: {error}"
        checks.append(manifest_check(task, current_manifest, load_error))

    checks.append(other_tasks_check(repo, task, current_manifest))
    checks.append(disk_check(repo))
    checks.append(python_check())

    errors = [f"{item['name']}: {item['detail']}" for item in checks if item["status"] == "fail"]
    warnings = [
        f"{item['name']}: {item['detail']}" for item in checks if item["status"] == "warn"
    ]
    return checks, errors, warnings, git_ready


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="target repository (default: cwd)")
    parser.add_argument("--task", help="task slug to validate and compare")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    checks, errors, warnings, _ = gather(repo, args.task)
    ok = not errors and not (args.strict and warnings)

    if args.json:
        print(json.dumps({"ok": ok, "checks": checks, "errors": errors, "warnings": warnings}, indent=2))
    else:
        print(f"G0 preflight  repo: {repo}")
        for item in checks:
            print(f"  [{item['status']}] {item['name']}: {item['detail']}")
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
