#!/usr/bin/env python3
"""Tests for the task manifest and structured report foundation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tasklib  # noqa: E402

SHA = "a" * 40
SHA2 = "b" * 40


def worker_report(unit: str = "u1", sha: str = SHA, status: str = "DONE") -> dict:
    return {
        "version": 1,
        "unit": unit,
        "role": "worker",
        "status": status,
        "sha": sha,
        "files": ["src/x.py"],
        "commands": [{"cmd": "python3 -m unittest", "exit": 0}],
        "acceptance": [{"criterion": "tests pass", "pass": True}],
        "findings": [],
        "usage": {"tokens_in": 1000, "tokens_out": 500},
        "model": "opencode-go/deepseek-v4.1-flash#max",
        "tier": 2,
        "family": "deepseek",
    }


def reviewer_report(
    family: str, sha: str = SHA, status: str = "PASS", findings: list | None = None
) -> dict:
    return {
        "version": 1,
        "unit": "u1",
        "role": "reviewer",
        "status": status,
        "sha": sha,
        "files": ["src/x.py"],
        "commands": [{"cmd": "python3 -m unittest", "exit": 0}],
        "acceptance": [{"criterion": "tests pass", "pass": True}],
        "findings": findings or [],
        "usage": {"tokens_in": 500, "tokens_out": 200},
        "model": f"opencode/{family}-free#max",
        "tier": 0,
        "family": family,
    }


class TaskLibTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root = tasklib.ROOT
        tasklib.ROOT = self.root
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        tasklib.ROOT = self.old_root
        self.temp.cleanup()

    def make_manifest(self, policy: str = "default", budget: float | None = 1.0) -> dict:
        return tasklib.new_manifest(
            "t", str(self.root), "main", policy=policy, budget_usd=budget, start_sha=SHA
        )

    def add_unit(self, manifest: dict, uid: str = "u1", **fields) -> dict:
        unit = tasklib.unit_template(
            uid,
            fields.pop("objective", "do the thing"),
            fields.pop("owned_paths", ["src/x.py"]),
            model=fields.pop("model", "opencode-go/deepseek-v4.1-flash#max"),
            implementer_family=fields.pop("implementer_family", "deepseek"),
            state=fields.pop("state", "running"),
            **fields,
        )
        manifest.setdefault("units", {})[uid] = unit
        return unit

    def test_init_save_load_and_validate(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest)
        errors, warnings = tasklib.validate_manifest(manifest)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        tasklib.save("t", manifest)
        loaded = tasklib.load("t")
        self.assertEqual(loaded["task"], "t")
        self.assertEqual(loaded["policy"]["name"], "default")
        self.assertTrue(tasklib.manifest_path("t").exists())

    def test_manifest_requires_units(self) -> None:
        errors, _ = tasklib.validate_manifest(self.make_manifest())
        self.assertTrue(any("units" in error for error in errors))

    def test_overlapping_owned_paths_fail(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest, "u1", owned_paths=["src"])
        self.add_unit(manifest, "u2", owned_paths=["src/x.py"])
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertTrue(any("overlap" in error for error in errors))

    def test_dependency_cycle_fails(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest, "u1", depends_on=["u2"])
        self.add_unit(manifest, "u2", owned_paths=["src/y.py"], depends_on=["u1"])
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertTrue(any("cycle" in error for error in errors))

    def test_unknown_dependency_fails(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest, "u1", depends_on=["nope"])
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertTrue(any("unknown dependency" in error for error in errors))

    def test_worker_then_reviewer_pass(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest)
        tasklib.save("t", manifest)

        result = tasklib.ingest_report("t", worker_report())
        self.assertEqual(result["state"], "review_ready")
        manifest = tasklib.load("t")
        self.assertEqual(manifest["units"]["u1"]["worker_sha"], SHA)
        self.assertEqual(manifest["budget"]["spent_usd"], 0.00045)

        result = tasklib.ingest_report("t", reviewer_report("beam"))
        self.assertEqual(result["state"], "passed")
        manifest = tasklib.load("t")
        self.assertEqual(manifest["units"]["u1"]["reviewed_sha"], SHA)
        self.assertEqual(len(manifest["calls"]), 2)
        self.assertEqual(len(manifest["units"]["u1"]["reviewer_verdicts"]), 1)

    def test_risk_unit_requires_two_distinct_families(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest, risk="security")
        tasklib.save("t", manifest)

        tasklib.ingest_report("t", worker_report())
        self.assertEqual(tasklib.ingest_report("t", reviewer_report("beam"))["state"], "reviewing")
        self.assertEqual(tasklib.ingest_report("t", reviewer_report("beam"))["state"], "reviewing")
        self.assertEqual(tasklib.ingest_report("t", reviewer_report("glm"))["state"], "passed")
        manifest = tasklib.load("t")
        self.assertEqual(
            sorted(tasklib.approved_review_families(manifest["units"]["u1"])),
            ["beam", "glm"],
        )

    def test_explore_report_with_files_goes_to_review_ready(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest, risk="normal")
        tasklib.save("t", manifest)
        report = {
            "version": 1,
            "unit": "u1",
            "role": "explore",
            "status": "DONE",
            "sha": None,
            "files": ["references/notes.md"],
            "commands": [],
            "acceptance": [],
            "findings": [],
            "model": "opencode/beam-free#high",
            "tier": 0,
            "family": "beam",
        }
        result = tasklib.ingest_report("t", report)
        self.assertEqual(result["state"], "review_ready")
        report["files"] = []
        result = tasklib.ingest_report("t", report)
        self.assertEqual(result["state"], "noop")

    def test_reviewer_from_implementer_family_does_not_count(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest)
        tasklib.save("t", manifest)
        tasklib.ingest_report("t", worker_report())
        self.assertEqual(
            tasklib.ingest_report("t", reviewer_report("deepseek"))["state"], "reviewing"
        )

    def test_failed_review_moves_to_changes_requested(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest)
        tasklib.save("t", manifest)
        tasklib.ingest_report("t", worker_report())
        result = tasklib.ingest_report(
            "t",
            reviewer_report(
                "beam", status="FAIL", findings=[{"severity": "major", "message": "bug"}]
            ),
        )
        self.assertEqual(result["state"], "changes_requested")

    def test_report_validation_rejects_missing_evidence(self) -> None:
        report = worker_report()
        report["commands"] = []
        errors = tasklib.validate_report(report)
        self.assertTrue(any("command" in error for error in errors))
        report = reviewer_report("beam")
        report["family"] = ""
        errors = tasklib.validate_report(report)
        self.assertTrue(any("family" in error for error in errors))

    def test_done_with_failing_command_is_rejected(self) -> None:
        report = worker_report()
        report["commands"] = [{"cmd": "false", "exit": 1}]
        errors = tasklib.validate_report(report)
        self.assertTrue(any("exit 0" in error for error in errors))
        report = worker_report()
        report["acceptance"] = [{"criterion": "x", "pass": False}]
        errors = tasklib.validate_report(report)
        self.assertTrue(any("acceptance item" in error for error in errors))

    def test_pass_with_major_or_blocker_is_rejected(self) -> None:
        report = reviewer_report(
            "beam", findings=[{"severity": "major", "message": "bug"}]
        )
        errors = tasklib.validate_report(report)
        self.assertTrue(any("PASS cannot carry" in error for error in errors))

    def test_reviewer_needs_findings_and_files(self) -> None:
        report = reviewer_report("beam")
        del report["findings"]
        del report["files"]
        errors = tasklib.validate_report(report)
        self.assertTrue(any("findings" in error for error in errors))
        self.assertTrue(any("files" in error for error in errors))

    def test_tier2_only_rejects_non_tier2_calls(self) -> None:
        manifest = self.make_manifest(policy="tier2-only")
        with self.assertRaises(ValueError):
            tasklib.add_call(manifest, None, "utility", "opencode/beam-free#low", 0, "DONE")
        call = tasklib.add_call(
            manifest, None, "utility", "opencode-go/muse-spark-1.3-contributor#high", 2, "DONE"
        )
        self.assertEqual(call["tier"], 2)

    def test_tier2_only_manifest_rejects_free_unit_model(self) -> None:
        manifest = self.make_manifest(policy="tier2-only")
        self.add_unit(manifest, model="opencode/beam-free#max", implementer_family="beam")
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertTrue(any("tier2-only" in error for error in errors))

    def test_budget_is_accumulated_and_checked(self) -> None:
        manifest = self.make_manifest(budget=0.0001)
        tasklib.add_call(
            manifest,
            None,
            "worker",
            "opencode-go/deepseek-v4.1-flash#max",
            2,
            "DONE",
            tokens_in=1000,
            tokens_out=1000,
        )
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertTrue(any("budget exceeded" in error for error in errors))

    def test_owner_check_requires_terminal_units(self) -> None:
        manifest = self.make_manifest()
        self.add_unit(manifest)
        errors, _ = tasklib.owner_check(manifest)
        self.assertTrue(any("not terminal" in error for error in errors))
        unit = manifest["units"]["u1"]
        unit["state"] = "merged"
        unit["reviewed_sha"] = SHA
        unit["merge_sha"] = SHA2
        errors, _ = tasklib.owner_check(manifest)
        self.assertTrue(any("required independent PASS" in error for error in errors))

    def test_price_and_cost(self) -> None:
        self.assertEqual(tasklib.price_for("opencode-go/deepseek-v4.1-flash#max"), (0.15, 0.60))
        self.assertEqual(tasklib.price_for("opencode/beam-free#max"), (0.0, 0.0))
        self.assertAlmostEqual(
            tasklib.cost_usd("opencode-go/deepseek-v4.1-flash#max", 1_000_000, 1_000_000),
            0.75,
        )


class TaskCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = Path(__file__).resolve().parents[1] / "task.py"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "ORCHESTRATE_ROOT": str(self.root)}
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    def test_cli_init_add_status_render_check(self) -> None:
        result = self.run_cli("init", "--task", "cli", "--repo", str(self.root), "--ref", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli(
            "add-unit",
            "--task",
            "cli",
            "--id",
            "u1",
            "--objective",
            "do it",
            "--owned",
            "src/x.py",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("status", "--task", "cli")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("u1", result.stdout)
        result = self.run_cli("check", "--task", "cli")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("render", "--task", "cli")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Orchestration report", result.stdout)

    def test_cli_set_unit_worktree_fields(self) -> None:
        self.assertEqual(
            self.run_cli("init", "--task", "cli3", "--repo", str(self.root), "--ref", "main").returncode,
            0,
        )
        self.assertEqual(
            self.run_cli(
                "add-unit",
                "--task",
                "cli3",
                "--id",
                "u1",
                "--objective",
                "do it",
                "--owned",
                "src/x.py",
            ).returncode,
            0,
        )
        result = self.run_cli(
            "set-unit",
            "--task",
            "cli3",
            "--id",
            "u1",
            "--state",
            "running",
            "--worktree",
            str(self.root / "wt"),
            "--branch",
            "orch/cli3/u1",
            "--base-sha",
            SHA,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("orch/cli3/u1", result.stdout)

    def test_cli_report_file(self) -> None:
        self.assertEqual(
            self.run_cli("init", "--task", "cli2", "--repo", str(self.root), "--ref", "main").returncode,
            0,
        )
        self.assertEqual(
            self.run_cli(
                "add-unit",
                "--task",
                "cli2",
                "--id",
                "u1",
                "--objective",
                "do it",
                "--owned",
                "src/x.py",
            ).returncode,
            0,
        )
        report = self.root / "worker.json"
        report.write_text(json.dumps(worker_report()))
        result = self.run_cli("report", "--task", "cli2", "--file", str(report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("review_ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
