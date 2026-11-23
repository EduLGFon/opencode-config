#!/usr/bin/env python3
"""Tests for scripts/preflight.py (T3)."""

from __future__ import annotations

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

SCRIPT = Path(__file__).resolve().parents[1] / "preflight.py"


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], text=True, capture_output=True, check=False
    )


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="preflight-", dir="/tmp/opencode"))
        self.work = self.root / "work"
        self.work.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def make_repo(self, name: str = "repo") -> Path:
        repo = self.work / name
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(repo)],
            text=True,
            capture_output=True,
            check=True,
        )
        git(repo, "config", "user.name", "Test User")
        git(repo, "config", "user.email", "test@example.com")
        (repo / "README.md").write_text("base\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "base")
        return repo

    def run_preflight(
        self, *args: str, repo: Path | None = None
    ) -> tuple[subprocess.CompletedProcess, dict]:
        env = {**os.environ, "ORCHESTRATE_ROOT": str(self.root / "state")}
        cmd = [sys.executable, str(SCRIPT), "--json"]
        if repo is not None:
            cmd.extend(["--repo", str(repo)])
        cmd.extend(args)
        proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc, payload

    def check(self, payload: dict, name: str) -> dict:
        return next(item for item in payload["checks"] if item["name"] == name)

    def write_manifest(
        self,
        slug: str,
        repo: Path,
        units: dict[str, dict],
    ) -> Path:
        manifest = tasklib.new_manifest(slug, str(repo), "main")
        manifest["units"] = units
        directory = self.root / "state" / slug
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "task.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        return path

    def unit(self, uid: str, owned: list[str]) -> dict:
        return tasklib.unit_template(uid, "work", owned, state="running")

    def test_non_git_is_warning(self) -> None:
        plain = self.work / "plain"
        plain.mkdir()
        proc, payload = self.run_preflight(repo=plain)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.check(payload, "git_root")["status"], "warn")
        self.assertTrue(payload["warnings"])

    def test_non_git_is_failure_under_strict(self) -> None:
        plain = self.work / "plain"
        plain.mkdir()
        proc, payload = self.run_preflight("--strict", repo=plain)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])

    def test_git_repo_basics(self) -> None:
        repo = self.make_repo()
        proc, payload = self.run_preflight(repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        names = [item["name"] for item in payload["checks"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(self.check(payload, "git_root")["status"], "ok")
        self.assertEqual(self.check(payload, "git_status")["status"], "ok")
        self.assertEqual(self.check(payload, "worktrees")["status"], "ok")
        self.assertEqual(self.check(payload, "orch_branches")["status"], "ok")
        self.assertEqual(self.check(payload, "instruction_files")["status"], "warn")
        self.assertEqual(self.check(payload, "model_validator")["status"], "ok")
        self.assertEqual(self.check(payload, "python_version")["status"], "ok")

        (repo / "AGENTS.md").write_text("# Instructions\n")
        git(repo, "add", "AGENTS.md")
        git(repo, "commit", "-m", "docs")
        proc, payload = self.run_preflight(repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        instructions = self.check(payload, "instruction_files")
        self.assertEqual(instructions["status"], "ok")
        self.assertIn("AGENTS.md", instructions["detail"])

    def test_dirty_tree_is_allowed_warning(self) -> None:
        repo = self.make_repo()
        (repo / "AGENTS.md").write_text("# Instructions\n")
        (repo / "scratch.txt").write_text("uncommitted\n")
        proc, payload = self.run_preflight(repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = self.check(payload, "git_status")
        self.assertEqual(status["status"], "warn")
        self.assertIn("dirty tree", status["detail"])

    def test_orchestration_branches_warn(self) -> None:
        repo = self.make_repo()
        git(repo, "branch", "orch/demo/u1-20261117-1700")
        proc, payload = self.run_preflight(repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        branches = self.check(payload, "orch_branches")
        self.assertEqual(branches["status"], "warn")
        self.assertIn("orch/demo/u1", branches["detail"])

    def test_model_validator_check_present(self) -> None:
        repo = self.make_repo()
        proc, payload = self.run_preflight(repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        validator = self.check(payload, "model_validator")
        self.assertEqual(validator["status"], "ok")
        self.assertIn("model rows", validator["detail"])

    def test_overlapping_active_task_fails(self) -> None:
        repo = self.make_repo()
        self.write_manifest("current", repo, {"u1": self.unit("u1", ["src/app.py"])})
        self.write_manifest("other", repo, {"u1": self.unit("u1", ["src/app.py"])})
        proc, payload = self.run_preflight("--task", "current", repo=repo)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])
        other = self.check(payload, "other_tasks")
        self.assertEqual(other["status"], "fail")
        self.assertIn("overlap", other["detail"])
        self.assertIn("other/u1", other["detail"])

    def test_non_overlapping_active_task_warns(self) -> None:
        repo = self.make_repo()
        self.write_manifest("current", repo, {"u1": self.unit("u1", ["src/app.py"])})
        self.write_manifest("other", repo, {"u1": self.unit("u1", ["docs/other.md"])})
        proc, payload = self.run_preflight("--task", "current", repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(payload["ok"])
        other = self.check(payload, "other_tasks")
        self.assertEqual(other["status"], "warn")
        self.assertIn("other active task", other["detail"])

        proc, payload = self.run_preflight("--task", "current", "--strict", repo=repo)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])

    def test_skips_current_task_in_scan(self) -> None:
        repo = self.make_repo()
        self.write_manifest("current", repo, {"u1": self.unit("u1", ["src/app.py"])})
        proc, payload = self.run_preflight("--task", "current", repo=repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        other = self.check(payload, "other_tasks")
        self.assertEqual(other["status"], "ok")
        self.assertNotIn("current", other["detail"])

    def test_invalid_manifest_surfaces_errors(self) -> None:
        repo = self.make_repo()
        manifest = tasklib.new_manifest("broken", str(repo), "main")
        directory = self.root / "state" / "broken"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task.json").write_text(json.dumps(manifest))
        proc, payload = self.run_preflight("--task", "broken", repo=repo)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])
        manifest_check = self.check(payload, "manifest")
        self.assertEqual(manifest_check["status"], "fail")
        self.assertIn("units", manifest_check["detail"])

    def test_malformed_units_list_returns_clean_failure(self) -> None:
        repo = self.make_repo()
        manifest = tasklib.new_manifest("broken", str(repo), "main")
        manifest["units"] = []
        directory = self.root / "state" / "broken"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "task.json").write_text(json.dumps(manifest))
        proc, payload = self.run_preflight("--task", "broken", repo=repo)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(self.check(payload, "manifest")["status"], "fail")
        self.assertIn("units", self.check(payload, "manifest")["detail"])

    def test_missing_repo_path_fails(self) -> None:
        missing = self.work / "does-not-exist"
        proc, payload = self.run_preflight(repo=missing)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertFalse(payload["ok"])
        self.assertEqual(self.check(payload, "git_root")["status"], "fail")


if __name__ == "__main__":
    unittest.main()
