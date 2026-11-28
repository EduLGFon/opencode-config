#!/usr/bin/env python3
"""Tests for scripts/gate.py (T3)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tasklib  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "gate.py"
LOCK_DIR = Path("/tmp/opencode/orchestrate")


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, text=True, capture_output=True, check=False, cwd=str(cwd) if cwd else None
    )


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd), *args])


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gate-", dir="/tmp/opencode"))
        self.state = self.root / "state"
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.assertEqual(run(["git", "init", "-b", "main", str(self.repo)]).returncode, 0)
        self.assertEqual(git(self.repo, "config", "user.name", "Test User").returncode, 0)
        self.assertEqual(
            git(self.repo, "config", "user.email", "test@example.com").returncode, 0
        )
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "-A")
        self.assertEqual(git(self.repo, "commit", "-m", "base").returncode, 0)
        self.base = git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def add_worktree(self, name: str = "wt") -> Path:
        wt = self.root / name
        proc = git(
            self.repo,
            "worktree",
            "add",
            "-b",
            f"orch/demo/{name}-20261117-1700",
            str(wt),
            self.base,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return wt

    def commit(self, wt: Path, relative: str, content: str, message: str = "change") -> str:
        path = wt / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(wt, "add", "--", relative)
        proc = git(wt, "commit", "-m", message)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return git(wt, "rev-parse", "HEAD").stdout.strip()

    def report_dir(self, slug: str = "demo") -> Path:
        path = self.state / slug / "reports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_reports(
        self,
        head: str,
        *,
        families: tuple[str, ...] = ("beam",),
        findings: list[dict] | None = None,
        worker_exit: int = 0,
        acceptance_pass: bool = True,
        slug: str = "demo",
    ) -> list[str]:
        reports = self.report_dir(slug)
        worker = {
            "version": 1,
            "unit": "u1",
            "role": "worker",
            "status": "DONE",
            "sha": head,
            "files": ["src/feature.py"],
            "commands": [{"cmd": "python3 -m pytest", "exit": worker_exit}],
            "acceptance": [{"criterion": "feature works", "pass": acceptance_pass}],
            "findings": [],
            "usage": {"tokens_in": 10, "tokens_out": 5},
            "model": "opencode/deepseek-v4.1-flash-free#max",
            "tier": 0,
            "family": "deepseek",
        }
        paths = []
        (reports / "u1-worker.json").write_text(json.dumps(worker, indent=2) + "\n")
        paths.append("reports/u1-worker.json")
        for index, family in enumerate(families):
            reviewer = {
                "version": 1,
                "unit": "u1",
                "role": "reviewer",
                "status": "PASS",
                "sha": head,
                "family": family,
                "files": [],
                "commands": [{"cmd": "python3 -m pytest", "exit": 0}],
                "acceptance": [{"criterion": "reviewed", "pass": True}],
                "findings": findings or [],
                "usage": {"tokens_in": 10, "tokens_out": 5},
                "model": "opencode/beam-free#max",
                "tier": 0,
            }
            relative = f"reports/u1-reviewer-{index}.json"
            (self.state / slug / relative).write_text(json.dumps(reviewer, indent=2) + "\n")
            paths.append(relative)
        return paths

    def build_task(
        self,
        wt: Path,
        head: str,
        *,
        base: str | None = None,
        owned: tuple[str, ...] = ("src/",),
        risk: str = "normal",
        policy: str = "default",
        model: str = "opencode/deepseek-v4.1-flash-free#max",
        implementer: str = "deepseek",
        families: tuple[str, ...] = ("beam",),
        findings: list[dict] | None = None,
        worker_exit: int = 0,
        acceptance_pass: bool = True,
        state: str = "passed",
        merged: bool = False,
        reviewed: str | None = None,
        worker: str | None = None,
        calls: list[dict] | None = None,
        write_reports: bool = True,
    ) -> dict:
        slug = "demo"
        reviewed = head if reviewed is None else reviewed
        worker = reviewed if worker is None else worker
        manifest = tasklib.new_manifest(slug, str(self.repo), "main", policy=policy)
        reports: list[str] = []
        if write_reports:
            reports = self.write_reports(
                reviewed,
                families=families,
                findings=findings,
                worker_exit=worker_exit,
                acceptance_pass=acceptance_pass,
            )
        unit = tasklib.unit_template(
            "u1",
            "add feature",
            list(owned),
            state=state,
            risk=risk,
            effort="low",
            model=model,
            implementer_family=implementer,
            worktree=str(wt),
            branch="orch/demo/u1-20261117-1700",
            base_sha=base or self.base,
            worker_sha=worker,
            reviewed_sha=reviewed,
            reports=reports,
            merge_sha=head if merged else None,
        )
        unit["reviewer_verdicts"] = [
            {
                "at": "2026-11-17T17:30:00Z",
                "verdict": "PASS",
                "family": family,
                "sha": reviewed,
                "model": "opencode/beam-free#max",
                "notes": "",
            }
            for family in families
        ]
        manifest["units"]["u1"] = unit
        manifest["calls"] = calls or []
        directory = self.state / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    def save_manifest(self, manifest: dict, slug: str = "demo") -> None:
        directory = self.state / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def gate(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        env = {**os.environ, "ORCHESTRATE_ROOT": str(self.state)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--json"],
            text=True,
            capture_output=True,
            env=env,
        )
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc, payload

    def unit_gate(self) -> tuple[subprocess.CompletedProcess, dict]:
        return self.gate("unit", "--task", "demo", "--unit", "u1")

    def check(self, payload: dict, name: str) -> dict:
        return next(item for item in payload["checks"] if item["name"] == name)

    def test_pass_case(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "def feature():\n    return 1\n")
        self.build_task(wt, head)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(payload["pass"])
        self.assertEqual(payload["gate"], "G2")
        self.assertTrue(all(item["pass"] for item in payload["checks"]))
        self.assertEqual(payload["findings"], [])

    def test_base_sha_option_injection_clean_and_dirty(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        pwn = self.root / "pwn"
        pwn.write_text("sentinel\n")
        sentinel = pwn.read_bytes()
        for index, base in enumerate((f"--output={pwn}", f"--output {pwn}")):
            with self.subTest(base=base):
                self.build_task(wt, head, base=base)
                proc, payload = self.unit_gate()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertFalse(payload["pass"])
                base_check = self.check(payload, "base_sha")
                self.assertFalse(base_check["pass"])
                self.assertIn("invalid", base_check["detail"])
                self.assertTrue(self.check(payload, "sha_pin")["pass"])
                self.assertEqual(pwn.read_bytes(), sentinel)
                self.assertEqual(
                    [path for path in pwn.parent.glob(pwn.name + "*") if path != pwn], []
                )

                (wt / f"dirty-{index}.txt").write_text("dirty\n")
                proc, payload = self.unit_gate()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertFalse(self.check(payload, "base_sha")["pass"])
                self.assertFalse(self.check(payload, "worktree_clean")["pass"])
                self.assertEqual(pwn.read_bytes(), sentinel)
                self.assertEqual(
                    [path for path in pwn.parent.glob(pwn.name + "*") if path != pwn], []
                )
                (wt / f"dirty-{index}.txt").unlink()

    def test_worker_and_reviewed_sha_option_injection(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        for field in ("worker_sha", "reviewed_sha"):
            with self.subTest(field=field):
                pwn = self.root / f"pwn-{field}"
                manifest = self.build_task(wt, head)
                manifest["units"]["u1"][field] = f"--output={pwn}"
                self.save_manifest(manifest)
                proc, payload = self.unit_gate()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                sha_pin = self.check(payload, "sha_pin")
                self.assertFalse(sha_pin["pass"])
                self.assertIn("invalid", sha_pin["detail"])
                self.assertFalse(pwn.exists())
                self.assertEqual(list(pwn.parent.glob(pwn.name + "*")), [])

    def test_malformed_units_list_returns_clean_failure(self) -> None:
        manifest = tasklib.new_manifest("demo", str(self.repo), "main")
        manifest["units"] = []
        self.save_manifest(manifest)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["pass"])
        self.assertFalse(self.check(payload, "manifest")["pass"])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("units", payload["checks"][0]["detail"])

    def test_non_review_state_is_rejected(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, state="running")
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        state = self.check(payload, "unit_state")
        self.assertFalse(state["pass"])
        self.assertIn("running", state["detail"])

    def test_phantom_reviewer_verdict_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        manifest = self.build_task(wt, head, families=("beam",))
        manifest["units"]["u1"]["reviewer_verdicts"][0]["family"] = "ghost"
        self.save_manifest(manifest)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("ghost", reviews["detail"])
        self.assertIn("no matching PASS reviewer report", reviews["detail"])

    def test_stale_reviewer_verdict_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        manifest = self.build_task(wt, head, families=("beam",))
        manifest["units"]["u1"]["reviewer_verdicts"][0]["sha"] = self.base
        self.save_manifest(manifest)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("another SHA", reviews["detail"])

    def test_sha_pin_invalid_and_unequal(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        cases = (
            ("invalid-worker", "BAD", head, "invalid"),
            ("invalid-reviewed", head, "BAD", "invalid"),
            ("unequal", self.base, head, "!="),
        )
        for name, worker, reviewed, expected in cases:
            with self.subTest(name=name):
                manifest = self.build_task(wt, head)
                manifest["units"]["u1"]["worker_sha"] = worker
                manifest["units"]["u1"]["reviewed_sha"] = reviewed
                self.save_manifest(manifest)
                proc, payload = self.unit_gate()
                self.assertEqual(proc.returncode, 1, proc.stderr)
                sha_pin = self.check(payload, "sha_pin")
                self.assertFalse(sha_pin["pass"])
                self.assertIn(expected, sha_pin["detail"])

    def test_non_ascii_filename_is_in_scope(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/café.py", "x = 1\n")
        self.build_task(wt, head, owned=("src/",))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "diff_scope")["pass"])
        self.assertTrue(self.check(payload, "secret_scan")["pass"])

    def test_dirty_worktree_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head)
        (wt / "untracked.txt").write_text("dirty\n")
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["pass"])
        self.assertFalse(self.check(payload, "worktree_clean")["pass"])
        self.assertIsInstance(payload["findings"], list)
        self.assertTrue(all(isinstance(item, str) for item in payload["findings"]))
        self.assertTrue(
            any("worktree_clean" in item for item in payload["findings"]),
            payload["findings"],
        )

    def test_head_mismatch_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.assertNotEqual(head, self.base)
        self.build_task(wt, head, reviewed=self.base, worker=self.base)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(self.check(payload, "head_match")["pass"])

    def test_out_of_scope_path_fails(self) -> None:
        wt = self.add_worktree()
        self.commit(wt, "src/feature.py", "x = 1\n")
        head = self.commit(wt, "docs/notes.md", "outside\n")
        self.build_task(wt, head, owned=("src/",))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        scope = self.check(payload, "diff_scope")
        self.assertFalse(scope["pass"])
        self.assertIn("docs/notes.md", scope["detail"])

    def test_empty_diff_fails(self) -> None:
        wt = self.add_worktree()
        self.build_task(wt, self.base)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        scope = self.check(payload, "diff_scope")
        self.assertFalse(scope["pass"])
        self.assertIn("empty diff", scope["detail"])

    def test_secret_path_fails(self) -> None:
        wt = self.add_worktree()
        self.commit(wt, "src/feature.py", "x = 1\n")
        head = self.commit(wt, ".env", "EXAMPLE=1\n")
        self.build_task(wt, head, owned=("src/", ".env"))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        secret = self.check(payload, "secret_scan")
        self.assertFalse(secret["pass"])
        self.assertIn(".env", secret["detail"])

    def test_env_template_allowed_but_scanned(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/.env.example", "SAFE=1\n")
        self.build_task(wt, head, owned=("src/",))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "secret_scan")["pass"])

    def test_secret_line_is_redacted(self) -> None:
        wt = self.add_worktree()
        secret_value = "abcdefgh12345678"
        head = self.commit(
            wt, "src/config.py", f'api_key = "{secret_value}"\n'
        )
        self.build_task(wt, head, owned=("src/",))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertNotIn(secret_value, proc.stdout)
        self.assertNotIn(secret_value, proc.stderr)
        secret = self.check(payload, "secret_scan")
        self.assertFalse(secret["pass"])
        self.assertIn("src/config.py:1", secret["detail"])
        self.assertIn("credential-assignment", secret["detail"])
        for finding in payload["findings"]:
            self.assertNotIn(secret_value, finding)

    def test_reviewer_family_equal_to_implementer_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, families=("deepseek",), implementer="deepseek")
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("implementer family", reviews["detail"])

    def test_major_finding_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(
            wt,
            head,
            findings=[{"severity": "major", "message": "missing test for behavior"}],
        )
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("major", reviews["detail"])
        self.assertIsInstance(payload["findings"], list)
        self.assertTrue(all(isinstance(item, str) for item in payload["findings"]))
        self.assertTrue(any("review_verdicts" in item for item in payload["findings"]))

    def test_reviewer_findings_non_list_is_clean_failure(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head)
        report_path = self.state / "demo" / "reports" / "u1-reviewer-0.json"
        report = json.loads(report_path.read_text())
        report["findings"] = "oops"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse(payload["pass"])
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("findings is not a list", reviews["detail"])
        self.assertIsInstance(payload["findings"], list)
        self.assertTrue(all(isinstance(item, str) for item in payload["findings"]))

    def test_missing_second_risk_reviewer_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, risk="security", families=("beam",))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        reviews = self.check(payload, "review_verdicts")
        self.assertFalse(reviews["pass"])
        self.assertIn("1/2", reviews["detail"])

    def test_risk_unit_with_two_families_passes(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, risk="public-api", families=("beam", "qwen"))
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "review_verdicts")["pass"])

    def test_worker_failed_command_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, worker_exit=1)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(self.check(payload, "worker_evidence")["pass"])

    def test_default_paid_tier_needs_owner_approval(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(
            wt,
            head,
            policy="default",
            model="opencode-go/deepseek-v4.1-flash#max",
        )
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(self.check(payload, "tier_policy")["pass"])
        manifest = json.loads((self.state / "demo" / "task.json").read_text())
        manifest["approvals"].append({"at": "now", "scope": "tier 2 hard unit"})
        self.save_manifest(manifest)
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "tier_policy")["pass"])

    def test_tier2_only_policy_enforced(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(
            wt,
            head,
            policy="tier2-only",
            model="opencode-go/qwen3.8-max#max",
        )
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(self.check(payload, "tier_policy")["pass"])

        self.build_task(
            wt,
            head,
            policy="tier2-only",
            model="opencode-go/deepseek-v4.1-flash#max",
        )
        proc, payload = self.unit_gate()
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "tier_policy")["pass"])

    def test_premerge_passes_and_does_not_merge(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head)
        target_before = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        proc, payload = self.gate("premerge", "--task", "demo", "--unit", "u1")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(payload["gate"], "G3")
        self.assertTrue(payload["pass"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").stdout.strip(), target_before)

    def test_premerge_target_dirty_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head)
        (self.repo / "untracked.txt").write_text("dirty target\n")
        proc, payload = self.gate("premerge", "--task", "demo", "--unit", "u1")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        target = self.check(payload, "target_clean")
        self.assertFalse(target["pass"])
        self.assertIn("dirty", target["detail"])

    def test_premerge_missing_reviewed_sha_fails(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        manifest = self.build_task(wt, head)
        manifest["units"]["u1"]["reviewed_sha"] = None
        self.save_manifest(manifest)
        proc, payload = self.gate("premerge", "--task", "demo", "--unit", "u1")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["pass"])
        self.assertFalse(self.check(payload, "reviewed_sha")["pass"])
        self.assertFalse(self.check(payload, "g2")["pass"])

    def test_premerge_lock_contention(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head)
        digest = hashlib.sha256(str(self.repo.resolve()).encode()).hexdigest()
        path = LOCK_DIR / f"{digest}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(path.unlink, missing_ok=True)
        try:
            proc, payload = self.gate("premerge", "--task", "demo", "--unit", "u1")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            lock = self.check(payload, "merge_lock")
            self.assertFalse(lock["pass"])
            self.assertIn("holds", lock["detail"])
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

        proc, payload = self.gate("premerge", "--task", "demo", "--unit", "u1")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(self.check(payload, "merge_lock")["pass"])

    def test_owner_gate_pass(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        self.build_task(wt, head, state="merged", merged=True)
        proc, payload = self.gate("owner", "--task", "demo")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(payload["gate"], "G4")
        self.assertTrue(payload["pass"])

    def test_owner_gate_requires_reviewed_sha(self) -> None:
        wt = self.add_worktree()
        head = self.commit(wt, "src/feature.py", "x = 1\n")
        manifest = self.build_task(wt, head, state="merged", merged=True)
        manifest["units"]["u1"]["reviewed_sha"] = None
        manifest["units"]["u1"]["reviewer_verdicts"] = []
        path = self.state / "demo" / "task.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        proc, payload = self.gate("owner", "--task", "demo")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["pass"])
        self.assertTrue(any("reviewed_sha" in error for error in payload["errors"]))

    def test_help(self) -> None:
        proc = run([sys.executable, str(SCRIPT), "--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("unit", proc.stdout)


if __name__ == "__main__":
    unittest.main()
