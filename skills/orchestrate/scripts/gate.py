#!/usr/bin/env python3
"""Read-only G2/G3/G4 gates. This tool never merges, rebases, commits or edits.

Examples:
    gate.py unit --task demo --unit u1
    gate.py premerge --task demo --unit u1 --json
    gate.py owner --task demo

Exit code 0 when the gate passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

import tasklib

LOCK_DIR = Path("/tmp/opencode/orchestrate")
SECRET_ALLOW = {".env.example", ".env.sample", ".env.template"}
SECRET_PATH_PATTERNS = (
    ".env*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    ".npmrc",
    ".pypirc",
    "credentials.*",
    "service-account*.json",
)

SECRET_LINE_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("sk-token", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(?<![\w.-])(api[_-]?key|secret|token|password|passwd|pwd)\b"
            r"\s*[:=]\s*(?:['\"]([^'\"]{8,})['\"]|([^\s'\"#;,]{8,}))"
        ),
    ),
)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
PLACEHOLDERS = (
    "$",
    "{{",
    "<",
    "os.",
    "process.env",
    "env.",
    "getenv",
    "none",
    "null",
    "changeme",
    "example",
    "redacted",
)


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)


def git(cwd: Path | str, *args: str) -> tuple[int, str, str]:
    return run(["git", "-C", str(cwd), *args])


def norm_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.rstrip("/")


def path_in_scope(path: str, owned: str) -> bool:
    """True when path is the owned file, inside an owned directory, or matches a glob."""
    path = norm_path(path)
    owned = norm_path(owned)
    if not owned:
        return False
    if not any(char in owned for char in "*?["):
        return path == owned or path.startswith(owned + "/")
    if owned.endswith("/**"):
        prefix = owned[:-3].rstrip("/")
        candidates = (path, path.rsplit("/", 1)[0] if "/" in path else "")
        return any(item == prefix or item.startswith(prefix + "/") for item in candidates)
    return PurePosixPath(path).match(owned)


def secret_path_label(path: str) -> str | None:
    name = norm_path(path).rsplit("/", 1)[-1].lower()
    if name in SECRET_ALLOW:
        return None
    for pattern in SECRET_PATH_PATTERNS:
        if fnmatch.fnmatchcase(name, pattern):
            return pattern
    return None


def line_label(line: str) -> str | None:
    for label, pattern in SECRET_LINE_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        if label == "credential-assignment":
            value = next((group for group in match.groups()[1:] if group), "").strip().lower()
            if value.startswith(PLACEHOLDERS):
                continue
            if any(char in value for char in "()"):
                continue
            if value.startswith(("self.", "this.")):
                continue
        return label
    return None


def added_lines(diff_text: str):
    current: str | None = None
    new_line = 0
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            current = None
            continue
        hunk = HUNK_RE.match(line)
        if hunk:
            in_hunk = True
            new_line = int(hunk.group(1))
            continue
        if not in_hunk:
            if line.startswith("+++ "):
                value = line[4:].strip()
                current = None if value == "/dev/null" else value
            continue
        if current is None:
            continue
        if line.startswith("+"):
            yield current, new_line, line[1:]
            new_line += 1
        elif line.startswith(" "):
            new_line += 1


def load_reports(slug: str, unit: dict) -> tuple[list[dict], list[str]]:
    reports: list[dict] = []
    missing: list[str] = []
    relpaths = unit.get("reports", [])
    if not isinstance(relpaths, list):
        return reports, ["reports is not a list"]
    for relative in relpaths:
        if not isinstance(relative, str):
            missing.append(repr(relative))
            continue
        path = tasklib.task_dir(slug) / relative
        if not path.is_file():
            missing.append(str(relative))
            continue
        try:
            report = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            missing.append(str(relative))
            continue
        if isinstance(report, dict):
            report = {**report, "_path": str(relative)}
            reports.append(report)
    return reports, missing


def latest_role(reports: list[dict], role: str) -> dict | None:
    found = None
    for report in reports:
        if report.get("role") == role:
            found = report
    return found


def valid_sha(value) -> bool:
    """Only a 7-40 lowercase hex string may reach a git revision argument."""
    return value is not None and tasklib.SHA_RE.fullmatch(str(value)) is not None


def verdicts(unit: dict) -> list[dict]:
    values = unit.get("reviewer_verdicts", [])
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def counted_verdicts(unit: dict) -> list[tuple[str, object]]:
    """PASS verdicts from independent families pinned to the reviewed SHA."""
    implementer = unit.get("implementer_family")
    reviewed = unit.get("reviewed_sha")
    result: list[tuple[str, object]] = []
    for verdict in verdicts(unit):
        if verdict.get("verdict") != "PASS":
            continue
        family = verdict.get("family")
        if not family:
            continue
        if implementer and family == implementer:
            continue
        if not reviewed or verdict.get("sha") != reviewed:
            continue
        result.append((str(family), verdict.get("sha")))
    return result


def verdict_summary(unit: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (independent families, same-family families, stale-sha families)."""
    implementer = unit.get("implementer_family")
    reviewed = unit.get("reviewed_sha")
    families: list[str] = []
    same: list[str] = []
    stale: list[str] = []
    for verdict in verdicts(unit):
        if verdict.get("verdict") != "PASS":
            continue
        family = verdict.get("family")
        if not family:
            continue
        if implementer and family == implementer:
            same.append(str(family))
            continue
        if not reviewed or verdict.get("sha") != reviewed:
            stale.append(f"{family}@{verdict.get('sha')}")
            continue
        if family not in families:
            families.append(str(family))
    return families, same, stale


def verdict_report_problems(unit: dict, reviewer_reports: list[dict]) -> list[str]:
    """Every counted PASS verdict must have an ingested PASS report with the same family and SHA."""
    supported = {
        (report.get("family"), report.get("sha"))
        for report in reviewer_reports
        if report.get("status") == "PASS"
    }
    problems: list[str] = []
    seen: set[tuple[str, object]] = set()
    for family, sha in counted_verdicts(unit):
        if (family, sha) in supported or (family, sha) in seen:
            continue
        seen.add((family, sha))
        problems.append(f"verdict {family}@{sha} has no matching PASS reviewer report")
    return problems


def g2_checks(manifest: dict, slug: str, uid: str) -> tuple[list[dict], list[str], dict | None]:
    checks: list[dict] = []
    findings: list[str] = []

    def ok(name: str, detail: str) -> None:
        checks.append({"name": name, "pass": True, "detail": detail})

    def fail(name: str, detail: str) -> None:
        checks.append({"name": name, "pass": False, "detail": detail})
        findings.append(f"{name}: {detail}")

    if not isinstance(manifest, dict):
        fail("manifest", "manifest must be a JSON object")
        return checks, findings, None
    try:
        errors, _warnings = tasklib.validate_manifest(manifest)
    except (AttributeError, TypeError, ValueError) as error:
        errors = [f"malformed manifest: {error}"]
    if errors:
        fail("manifest", "; ".join(errors))
    else:
        ok("manifest", "manifest valid")

    units = manifest.get("units")
    if not isinstance(units, dict) or not units:
        fail("unit_state", "units is not a non-empty object")
        return checks, findings, None
    unit = units.get(uid)
    if not isinstance(unit, dict):
        fail("unit_state", f"unknown or malformed unit: {uid}")
        return checks, findings, None
    state = unit.get("state")
    if state in ("review_ready", "reviewing", "passed"):
        ok("unit_state", f"{uid} state is {state}")
    else:
        fail("unit_state", f"{uid} state is {state!r}, expected review_ready/reviewing/passed")

    reports, missing = load_reports(slug, unit)
    worker = latest_role(reports, "worker")
    reviewer_reports = [report for report in reports if report.get("role") == "reviewer"]
    if missing:
        fail("reports", f"missing/unreadable report(s): {', '.join(missing)}")
    elif worker is None:
        fail("reports", "no worker report")
    elif not any(report.get("status") == "PASS" for report in reviewer_reports):
        fail("reports", "no reviewer PASS report")
    elif worker.get("status") != "DONE":
        fail("reports", f"latest worker report is {worker.get('status')!r}, expected DONE")
    else:
        ok(
            "reports",
            f"worker DONE, {len(reviewer_reports)} reviewer report(s)",
        )

    worker_sha = unit.get("worker_sha")
    reviewed_sha = unit.get("reviewed_sha")
    base_sha = unit.get("base_sha")
    # Revisions are interpolated into git arguments, so only validated hex may reach git.
    sha_ok = True
    if not valid_sha(worker_sha):
        sha_ok = False
        fail("sha_pin", f"worker_sha invalid: {worker_sha!r}")
    if not valid_sha(reviewed_sha):
        sha_ok = False
        fail("sha_pin", f"reviewed_sha invalid: {reviewed_sha!r}")
    if sha_ok and worker_sha != reviewed_sha:
        sha_ok = False
        fail("sha_pin", f"worker_sha {worker_sha} != reviewed_sha {reviewed_sha}")
    if sha_ok:
        ok("sha_pin", f"worker and reviewed SHA {reviewed_sha}")

    base_ok = valid_sha(base_sha)
    if not base_ok:
        fail("base_sha", f"base_sha invalid: {base_sha!r}")
    else:
        ok("base_sha", f"base SHA {base_sha}")

    worktree_value = unit.get("worktree")
    worktree = (
        Path(worktree_value).expanduser()
        if isinstance(worktree_value, str) and worktree_value
        else None
    )
    worktree_ok = worktree is not None and worktree.is_dir()
    if not worktree_ok:
        fail("worktree_clean", f"worktree missing: {worktree_value!r}")
        fail("head_match", "cannot resolve HEAD without a worktree")
    else:
        code, out, err = git(worktree, "status", "--porcelain")
        if code != 0:
            fail("worktree_clean", f"git status failed: {err or out}")
        elif out:
            fail("worktree_clean", f"dirty tree: {len(out.splitlines())} change(s)")
        else:
            ok("worktree_clean", "clean tree")

        code, head, err = git(worktree, "rev-parse", "HEAD")
        head = head.splitlines()[0] if head else ""
        if code != 0 or not head:
            fail("head_match", f"git rev-parse HEAD failed: {err or out}")
        else:
            def matches(sha) -> bool:
                if not valid_sha(sha):
                    return False
                return head == sha or (len(str(sha)) < 40 and head.startswith(str(sha)))

            if matches(reviewed_sha) and matches(worker_sha):
                ok("head_match", f"HEAD {head}")
            else:
                fail(
                    "head_match",
                    f"HEAD {head} != worker {worker_sha} / reviewed {reviewed_sha}",
                )

    worktree_path = worktree if worktree_ok and worktree is not None else None
    changed: list[str] = []
    if not base_ok:
        fail("diff_scope", "not run: base_sha is not a valid 7-40 lowercase hex SHA")
    elif not sha_ok:
        fail("diff_scope", "not run: worker/reviewed SHA invalid or unequal")
    elif worktree_path is None:
        fail("diff_scope", "not run: worktree missing")
    else:
        code, out, err = git(
            worktree_path,
            "-c",
            "core.quotepath=off",
            "diff",
            "--name-only",
            f"{base_sha}..{reviewed_sha}",
        )
        if code != 0:
            fail("diff_scope", f"git diff failed: {err or out}")
        else:
            changed = [line for line in out.splitlines() if line.strip()]
            if not changed:
                fail("diff_scope", f"empty diff {base_sha}..{reviewed_sha}")
            else:
                owned_value = unit.get("owned_paths", [])
                owned = (
                    [str(item) for item in owned_value]
                    if isinstance(owned_value, list)
                    else []
                )
                outside = [
                    path
                    for path in changed
                    if not any(path_in_scope(path, item) for item in owned)
                ]
                other_units: dict[str, list[str]] = {}
                for other_uid, other in manifest.get("units", {}).items():
                    if other_uid == uid or not isinstance(other, dict):
                        continue
                    other_owned = other.get("owned_paths", [])
                    if isinstance(other_owned, list):
                        other_units[other_uid] = [str(item) for item in other_owned]
                foreign = [
                    f"{path} ({other_uid})"
                    for path in changed
                    for other_uid, other_owned in other_units.items()
                    for item in other_owned
                    if path_in_scope(path, item)
                ]
                if outside:
                    fail("diff_scope", "outside owned paths: " + ", ".join(outside))
                elif foreign:
                    fail("diff_scope", "inside another unit's paths: " + ", ".join(foreign))
                else:
                    ok("diff_scope", f"{len(changed)} changed path(s) in scope")

    secret_hits: list[str] = []
    if not base_ok or not sha_ok or worktree_path is None:
        fail("secret_scan", "not run: invalid base/SHA pin or missing worktree")
    else:
        code, paths_out, err = git(
            worktree_path,
            "-c",
            "core.quotepath=off",
            "diff",
            "--name-only",
            f"{base_sha}..{reviewed_sha}",
        )
        paths = paths_out.splitlines() if code == 0 else changed
        for path in paths:
            label = secret_path_label(path)
            if label:
                secret_hits.append(f"{path} (path:{label})")
        code, diff_out, err = git(
            worktree_path,
            "-c",
            "core.quotepath=off",
            "diff",
            "--no-prefix",
            "--unified=0",
            f"{base_sha}..{reviewed_sha}",
        )
        if code != 0:
            secret_hits.append(f"line scan failed: {err or diff_out}")
        else:
            for path, line_no, text in added_lines(diff_out):
                if secret_path_label(path):
                    continue
                label = line_label(text)
                if label:
                    secret_hits.append(f"{path}:{line_no} ({label})")
        if secret_hits:
            fail("secret_scan", "; ".join(secret_hits))
        else:
            ok("secret_scan", "no secret-looking paths or added lines")

    required = tasklib.required_reviews(unit)
    families, same, stale = verdict_summary(unit)
    review_problems: list[str] = []
    if len(families) < required:
        review_problems.append(
            f"{len(families)}/{required} independent PASS families (got "
            f"{', '.join(families) or 'none'})"
        )
    if same:
        review_problems.append("implementer family verdict(s): " + ", ".join(same))
    # Stale PASS verdicts are history and never count toward the current SHA.
    # A unit that earns fresh independent PASSes after a fix must not fail
    # because earlier revisions also had PASSes. Only current-SHA families are
    # counted (see verdict_summary); stale entries are informational.
    if stale and len(families) < required:
        review_problems.append("verdict(s) for another SHA: " + ", ".join(stale))
    review_problems.extend(verdict_report_problems(unit, reviewer_reports))
    for report in reviewer_reports:
        # A report for an older revision is history. It must not fail a unit
        # whose current reviewed SHA has a fresh independent PASS; the skill
        # contract says a report for an older SHA never counts toward a PASS.
        report_sha = report.get("sha")
        if (
            reviewed_sha
            and isinstance(report_sha, str)
            and report_sha != reviewed_sha
        ):
            continue
        report_findings = report.get("findings", [])
        if not isinstance(report_findings, list):
            review_problems.append(
                f"{report.get('_path', 'reviewer report')}: findings is not a list"
            )
            continue
        for finding in report_findings:
            if isinstance(finding, dict) and finding.get("severity") in ("blocker", "major"):
                review_problems.append(
                    f"{report.get('_path', 'reviewer report')}: "
                    f"{finding.get('severity')} finding: {finding.get('message', '')}"
                )
    if review_problems:
        fail("review_verdicts", "; ".join(review_problems))
    else:
        ok(
            "review_verdicts",
            f"{len(families)}/{required} independent PASS families: {', '.join(families)}",
        )

    evidence_problems: list[str] = []
    if worker is None:
        evidence_problems.append("no worker report")
    else:
        commands = worker.get("commands", [])
        acceptance = worker.get("acceptance", [])
        if not isinstance(commands, list):
            evidence_problems.append("worker report commands is not a list")
            commands = []
        if not isinstance(acceptance, list):
            evidence_problems.append("worker report acceptance is not a list")
            acceptance = []
        if not commands:
            evidence_problems.append("worker report has no commands")
        for command in commands:
            if not isinstance(command, dict) or command.get("exit") != 0:
                detail = command.get("cmd", "?") if isinstance(command, dict) else "?"
                exit_code = command.get("exit", "?") if isinstance(command, dict) else "?"
                evidence_problems.append(
                    f"worker command failed: {detail} exit {exit_code}"
                )
        if not acceptance:
            evidence_problems.append("worker report has no acceptance items")
        for item in acceptance:
            if not isinstance(item, dict) or item.get("pass") is not True:
                criterion = item.get("criterion", "?") if isinstance(item, dict) else "?"
                evidence_problems.append(
                    f"worker acceptance not passed: {criterion}"
                )
    if evidence_problems:
        fail("worker_evidence", "; ".join(evidence_problems))
    else:
        ok("worker_evidence", "worker commands exit 0 and acceptance passed")

    policy_value = manifest.get("policy", {})
    policy = policy_value.get("name") if isinstance(policy_value, dict) else None
    if policy != "tier2-only":
        ok("tier_policy", f"policy is {policy}, tier 2 not required")
    else:
        problems: list[str] = []
        model = unit.get("model")
        if not model:
            problems.append("unit model missing")
        elif tasklib.model_tier(model) != 2:
            problems.append(f"unit model {model} is tier {tasklib.model_tier(model)}")
        calls = manifest.get("calls", [])
        if not isinstance(calls, list):
            problems.append("calls is not a list")
            calls = []
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                problems.append(f"call {index} is not an object")
                continue
            if call.get("unit") == uid and call.get("tier") != 2:
                problems.append(
                    f"call {index} ({call.get('role')}) is tier {call.get('tier')}"
                )
        if problems:
            fail("tier_policy", "; ".join(problems))
        else:
            ok("tier_policy", "unit model and all unit calls are tier 2")

    return checks, findings, unit


def lock_path(repo: str) -> Path:
    digest = hashlib.sha256(str(Path(repo).expanduser().resolve()).encode()).hexdigest()
    return LOCK_DIR / f"{digest}.lock"


def emit(args: argparse.Namespace, gate: str, checks: list[dict], findings: list[str], **extra) -> int:
    passed = bool(checks) and all(item["pass"] for item in checks) and not extra.get("errors")
    if args.json:
        payload = {"gate": gate, "pass": passed, "checks": checks, "findings": findings}
        payload.update(extra)
        print(json.dumps(payload, indent=2))
    else:
        print(f"{gate} gate")
        for item in checks:
            print(f"  [{'PASS' if item['pass'] else 'FAIL'}] {item['name']}: {item['detail']}")
        for warning in extra.get("warnings", []):
            print(f"WARN: {warning}")
        for error in extra.get("errors", []):
            print(f"ERROR: {error}")
        if findings:
            print("FINDINGS")
            for finding in findings:
                print(f"- {finding}")
        print("PASS" if passed else "FAIL")
    return 0 if passed else 1


def cmd_unit(args: argparse.Namespace) -> int:
    manifest = tasklib.load(args.task)
    checks, findings, _unit = g2_checks(manifest, args.task, args.unit)
    return emit(args, "G2", checks, findings)


def cmd_premerge(args: argparse.Namespace) -> int:
    manifest = tasklib.load(args.task)
    if not isinstance(manifest, dict):
        manifest = {}
    checks, findings, unit = g2_checks(manifest, args.task, args.unit)
    g2_pass = bool(checks) and all(item["pass"] for item in checks)
    checks.insert(
        0,
        {
            "name": "g2",
            "pass": g2_pass,
            "detail": "G2 passed" if g2_pass else "G2 failed",
        },
    )

    target = manifest.get("target", {})
    target_repo = target.get("repo", "") if isinstance(target, dict) else ""
    repo = Path(target_repo).expanduser() if isinstance(target_repo, str) and target_repo else None
    if repo is None or not repo.is_dir():
        checks.append({"name": "target_clean", "pass": False, "detail": "target repo missing"})
    else:
        code, out, err = git(repo, "status", "--porcelain")
        if code != 0:
            checks.append(
                {"name": "target_clean", "pass": False, "detail": f"git status failed: {err or out}"}
            )
        elif out:
            checks.append(
                {
                    "name": "target_clean",
                    "pass": False,
                    "detail": f"target is dirty: {len(out.splitlines())} change(s)",
                }
            )
        else:
            checks.append({"name": "target_clean", "pass": True, "detail": "target is clean"})

    reviewed = unit.get("reviewed_sha") if isinstance(unit, dict) else None
    reviewed_ok = valid_sha(reviewed)
    checks.append(
        {
            "name": "reviewed_sha",
            "pass": reviewed_ok,
            "detail": f"reviewed SHA {reviewed}" if reviewed_ok else "reviewed SHA missing or invalid",
        }
    )

    unit_worktree = unit.get("worktree") if isinstance(unit, dict) else None
    if isinstance(unit_worktree, str) and unit_worktree and Path(unit_worktree).is_dir():
        code, out, err = git(unit_worktree, "status", "--porcelain")
        clean = code == 0 and not out
        checks.append(
            {
                "name": "worktree_clean",
                "pass": clean,
                "detail": "unit worktree clean" if clean else f"unit worktree dirty: {err or out}",
            }
        )
    else:
        checks.append(
            {"name": "worktree_clean", "pass": False, "detail": "unit worktree missing"}
        )

    if repo is not None:
        path = lock_path(str(repo))
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        try:
            handle = path.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                checks.append(
                    {"name": "merge_lock", "pass": True, "detail": f"acquired {path.name}"}
                )
            except (BlockingIOError, OSError):
                checks.append(
                    {
                        "name": "merge_lock",
                        "pass": False,
                        "detail": f"another orchestrator holds {path.name}",
                    }
                )
        except OSError as error:
            checks.append(
                {"name": "merge_lock", "pass": False, "detail": f"cannot open lock: {error}"}
            )
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()
    else:
        checks.append({"name": "merge_lock", "pass": False, "detail": "no target repo"})

    return emit(args, "G3", checks, findings)


def cmd_owner(args: argparse.Namespace) -> int:
    manifest = tasklib.load(args.task)
    if not isinstance(manifest, dict):
        errors, warnings, units = ["manifest must be a JSON object"], [], {}
    else:
        units_value = manifest.get("units")
        if not isinstance(units_value, dict):
            errors, warnings, units = ["manifest units is not an object"], [], {}
        else:
            units = units_value
            try:
                errors, warnings = tasklib.owner_check(manifest)
            except (AttributeError, TypeError, ValueError) as error:
                errors, warnings = [f"malformed manifest: {error}"], []
    checks: list[dict] = [
        {
            "name": "owner_check",
            "pass": not errors,
            "detail": "owner_check clean" if not errors else "; ".join(errors),
        }
    ]
    findings: list[str] = list(errors)

    merged_problems: list[str] = []
    merged_count = 0
    for uid, unit in units.items():
        if not isinstance(unit, dict):
            merged_problems.append(f"{uid}: malformed unit")
            continue
        if unit.get("state") != "merged":
            continue
        merged_count += 1
        reviewed = unit.get("reviewed_sha")
        if not valid_sha(reviewed):
            merged_problems.append(f"{uid}: merged without reviewed_sha")
        families, same, _stale = verdict_summary(unit)
        required = tasklib.required_reviews(unit)
        if len(families) < required:
            merged_problems.append(
                f"{uid}: {len(families)}/{required} independent PASS families"
            )
        if same:
            merged_problems.append(
                f"{uid}: implementer family counted: {', '.join(same)}"
            )
    checks.append(
        {
            "name": "merged_units",
            "pass": not merged_problems,
            "detail": (
                f"{merged_count} merged unit(s) verified"
                if not merged_problems
                else "; ".join(merged_problems)
            ),
        }
    )
    findings.extend(merged_problems)
    return emit(args, "G4", checks, findings, errors=errors, warnings=warnings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    unit = sub.add_parser("unit", help="G2 unit gate")
    unit.add_argument("--task", required=True)
    unit.add_argument("--unit", required=True)
    unit.add_argument("--json", action="store_true")
    unit.set_defaults(func=cmd_unit)

    premerge = sub.add_parser("premerge", help="G3 read-only premerge gate")
    premerge.add_argument("--task", required=True)
    premerge.add_argument("--unit", required=True)
    premerge.add_argument("--json", action="store_true")
    premerge.set_defaults(func=cmd_premerge)

    owner = sub.add_parser("owner", help="G4 owner gate")
    owner.add_argument("--task", required=True)
    owner.add_argument("--json", action="store_true")
    owner.set_defaults(func=cmd_owner)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
