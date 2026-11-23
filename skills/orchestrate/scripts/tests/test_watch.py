#!/usr/bin/env python3
"""Tests for scripts/watch.py (T1)."""

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

SCRIPT = Path(__file__).resolve().parents[1] / "watch.py"
AT = "2026-11-17T18:00:00Z"


class WatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="watch-", dir="/tmp/opencode"))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write_manifest(
        self,
        slug: str = "demo",
        *,
        policy: str = "default",
        state: str = "running",
        started_at: str = "2026-11-17T17:40:00Z",
        effort: str = "low",
        budget_limit: float | None = None,
        spent: float = 0.0,
        calls: list[dict] | None = None,
        risk: str = "normal",
    ) -> Path:
        manifest = tasklib.new_manifest(slug, str(self.repo), "main", policy=policy)
        unit = tasklib.unit_template(
            "u1",
            "do the thing",
            ["src/"],
            state=state,
            started_at=started_at,
            effort=effort,
            risk=risk,
        )
        manifest["units"]["u1"] = unit
        manifest["budget"] = {"limit_usd": budget_limit, "spent_usd": spent}
        manifest["calls"] = calls or []
        directory = self.root / slug
        directory.mkdir(parents=True)
        path = directory / "task.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        return path

    def run_watch(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        env = {**os.environ, "ORCHESTRATE_ROOT": str(self.root)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--task", "demo", "--json", *args],
            text=True,
            capture_output=True,
            env=env,
        )
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
        return proc, payload

    def test_healthy(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:55:00Z", budget_limit=5.0, spent=0.5)
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(payload["healthy"])
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["attention"], [])
        self.assertEqual(payload["suggestions"], [])
        self.assertTrue(all(item["status"] == "ok" for item in payload["checks"]))

    def test_overdue_exits_one(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:40:00Z", effort="low")
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(payload["exit_code"], 1)
        self.assertFalse(payload["healthy"])
        overdue = next(item for item in payload["checks"] if item["name"] == "overdue")
        self.assertEqual(overdue["status"], "attention")
        self.assertTrue(any("overdue" in item for item in payload["attention"]))

    def test_stalled_exits_two(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:20:00Z", effort="low")
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        stalls = next(item for item in payload["checks"] if item["name"] == "stalls")
        self.assertEqual(stalls["status"], "fail")
        self.assertTrue(any("stalled" in item for item in payload["attention"]))
        self.assertTrue(
            any("blocked" in item and "fresh worktree" in item for item in payload["suggestions"])
        )

    def test_custom_stall_factor_is_deterministic(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:20:00Z", effort="low")
        proc, _payload = self.run_watch("--at", AT, "--stall-factor", "3")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        proc, _payload = self.run_watch("--at", AT, "--stall-factor", "2")
        self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_rate_limit_429_attention(self) -> None:
        calls = [
            {
                "at": "2026-11-17T17:50:00Z",
                "unit": "u1",
                "role": "worker",
                "model": "opencode-go/deepseek-v4.1-flash#max",
                "tier": 2,
                "outcome": "error",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": "429 Too Many Requests",
            },
            {
                "at": "2026-11-17T17:55:00Z",
                "unit": "u1",
                "role": "worker",
                "model": "opencode-go/deepseek-v4.1-flash#max",
                "tier": 2,
                "outcome": "error",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": "quota exceeded",
            },
        ]
        self.write_manifest(calls=calls, started_at="2026-11-17T17:55:00Z")
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        rates = next(item for item in payload["checks"] if item["name"] == "rate_limits")
        self.assertEqual(rates["status"], "attention")
        self.assertTrue(any("free twin" in item for item in payload["suggestions"]))
        self.assertTrue(any("tier-2" in item for item in payload["suggestions"]))

    def test_single_rate_limit_below_threshold(self) -> None:
        call = {
            "at": "2026-11-17T17:55:00Z",
            "unit": "u1",
            "role": "worker",
            "model": "opencode-go/deepseek-v4.1-flash#max",
            "tier": 2,
            "outcome": "error",
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_usd": 0.0,
            "error": "429 Too Many Requests",
        }
        self.write_manifest(calls=[call], started_at="2026-11-17T17:55:00Z")
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rates = next(item for item in payload["checks"] if item["name"] == "rate_limits")
        self.assertEqual(rates["status"], "ok")

    def test_rate_limit_outside_window_ignored(self) -> None:
        calls = [
            {
                "at": "2026-11-17T16:00:00Z",
                "unit": "u1",
                "role": "worker",
                "model": "opencode-go/deepseek-v4.1-flash#max",
                "tier": 2,
                "outcome": "error",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": "429 rate limit",
            }
        ] * 3
        self.write_manifest(calls=calls, started_at="2026-11-17T17:55:00Z")
        proc, payload = self.run_watch("--at", AT, "--rate-window", "30")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["status"]["rate_limits"], [])

    def test_budget_warning(self) -> None:
        self.write_manifest(
            started_at="2026-11-17T17:55:00Z", budget_limit=1.0, spent=0.85
        )
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        budget = next(item for item in payload["checks"] if item["name"] == "budget")
        self.assertEqual(budget["status"], "attention")
        self.assertTrue(any("Budget" in item for item in payload["suggestions"]))

    def test_budget_warning_threshold_is_configurable(self) -> None:
        self.write_manifest(
            started_at="2026-11-17T17:55:00Z", budget_limit=1.0, spent=0.85
        )
        proc, _payload = self.run_watch("--at", AT, "--budget-warn", "0.9")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_budget_exceeded(self) -> None:
        self.write_manifest(
            started_at="2026-11-17T17:55:00Z", budget_limit=1.0, spent=1.2
        )
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        budget = next(item for item in payload["checks"] if item["name"] == "budget")
        self.assertEqual(budget["status"], "fail")

    def test_tier2_only_suggestion_never_free_tier(self) -> None:
        calls = [
            {
                "at": "2026-11-17T17:50:00Z",
                "unit": "u1",
                "role": "worker",
                "model": "opencode-go/glm-5.3-flash#max",
                "tier": 2,
                "outcome": "error",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": "429 Too Many Requests",
            },
            {
                "at": "2026-11-17T17:55:00Z",
                "unit": "u1",
                "role": "worker",
                "model": "opencode-go/glm-5.3-flash#max",
                "tier": 2,
                "outcome": "error",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "error": "429 Too Many Requests",
            },
        ]
        self.write_manifest(
            policy="tier2-only", calls=calls, started_at="2026-11-17T17:55:00Z"
        )
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        text = " ".join(payload["suggestions"])
        self.assertIn("tier-2", text)
        self.assertIn("never", text)
        self.assertIn("free tier", text)
        self.assertNotIn("free twin", text)

    def test_json_shape(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:55:00Z")
        proc, payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            set(payload),
            {"healthy", "exit_code", "checks", "attention", "suggestions", "status"},
        )
        self.assertEqual(payload["status"]["task"], "demo")
        self.assertEqual(payload["status"]["policy"], "default")
        for item in payload["checks"]:
            self.assertEqual(set(item), {"name", "status", "detail"})

    def test_human_output(self) -> None:
        self.write_manifest(started_at="2026-11-17T17:20:00Z")
        env = {**os.environ, "ORCHESTRATE_ROOT": str(self.root)}
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--task", "demo", "--at", AT],
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("Task: demo", proc.stdout)
        self.assertIn("CHECKS", proc.stdout)
        self.assertIn("SUGGESTIONS", proc.stdout)

    def test_watch_does_not_write_manifest(self) -> None:
        path = self.write_manifest(started_at="2026-11-17T17:55:00Z")
        before = path.read_bytes()
        proc, _payload = self.run_watch("--at", AT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_at(self) -> None:
        self.write_manifest()
        proc, _payload = self.run_watch("--at", "not-a-time")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ERROR", proc.stderr)


if __name__ == "__main__":
    unittest.main()
