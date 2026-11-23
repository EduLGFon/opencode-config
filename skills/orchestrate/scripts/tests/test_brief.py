"""Tests for scripts/brief.py: brief rendering from the task manifest."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Override before tasklib is imported; setUp patches tasklib.ROOT per test anyway.
os.environ.setdefault("ORCHESTRATE_ROOT", tempfile.mkdtemp(prefix="orchestrate-brief-tests-"))

import brief  # noqa: E402
import tasklib  # noqa: E402


class BriefTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orchestrate-brief-root-"))
        self._old_root = tasklib.ROOT
        tasklib.ROOT = self.root
        self.slug_counter = 0

    def tearDown(self):
        tasklib.ROOT = self._old_root
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------------------------------------------------------------- helpers

    def unique_slug(self) -> str:
        self.slug_counter += 1
        return f"t-brief-{self.slug_counter:02d}-{os.getpid():x}"

    def make_manifest(
        self,
        units: dict[str, dict],
        policy: str = "default",
        repo: str = "/tmp/repo",
    ) -> str:
        slug = self.unique_slug()
        manifest = tasklib.new_manifest(slug, repo, "main", policy=policy, budget_usd=2.0)
        for uid, fields in units.items():
            fields = dict(fields)
            objective = fields.pop("objective")
            owned = fields.pop("owned_paths")
            manifest["units"][uid] = tasklib.unit_template(uid, objective, owned, **fields)
        errors, _ = tasklib.validate_manifest(manifest)
        assert errors == [], errors
        tasklib.ensure_dirs(slug)
        tasklib.save(slug, manifest)
        return slug

    def worker_fields(self, **overrides) -> dict:
        fields = {
            "objective": "add retry to checkout",
            "owned_paths": ["src/checkout/retry.ts"],
            "worktree": "/tmp/wt/u1",
            "branch": "orch/demo/u1-20261117-1700",
            "base_sha": "abc1234",
            "depends_on": [],
            "contracts": ["api/openapi.yaml"],
            "resource_isolation": "PORT=4101, DB=checkout_u1",
            "context": "checkout module lives in src/checkout; retry must stay idempotent.",
            "acceptance": ["npm test -- checkout", "npm run lint"],
            "effort": "high",
            "risk": "normal",
            "model": "opencode-go/deepseek-v4.1-flash#max",
            "implementer_family": "deepseek",
        }
        fields.update(overrides)
        return fields

    def run_brief(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = brief.main(argv)
        return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------------ tests

    def test_worker_brief_is_self_contained(self):
        slug = self.make_manifest({"u1": self.worker_fields(), "u2": self.worker_fields(
            objective="extend the cart totals",
            owned_paths=["src/checkout/cart.ts"],
            worktree="/tmp/wt/u2",
            branch="orch/demo/u2-20261117-1701",
            depends_on=["u1"],
        )})
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "worker"])
        self.assertEqual(code, 0, err)
        text = out
        # pwd assertion with the absolute worktree path
        self.assertIn("First: run `pwd`.", text)
        self.assertIn("pass workdir=/tmp/wt/u1 or start with `cd /tmp/wt/u1 &&`.", text)
        # header facts
        self.assertIn("Objective: add retry to checkout", text)
        self.assertIn("Worktree: /tmp/wt/u1", text)
        self.assertIn("Branch: orch/demo/u1-20261117-1700", text)
        self.assertIn("Base: main @ abc1234", text)
        self.assertIn("Depends on: none", text)
        self.assertIn("Contracts: api/openapi.yaml", text)
        self.assertIn("Resource isolation: PORT=4101, DB=checkout_u1", text)
        self.assertIn("Model policy: default", text)
        self.assertIn("Model: opencode-go/deepseek-v4.1-flash#max", text)
        # owned paths
        self.assertIn("- src/checkout/retry.ts", text)
        # context, constraints, acceptance
        self.assertIn("Context: checkout module lives in src/checkout", text)
        self.assertIn("Commit all intended files", text)
        self.assertIn("- npm test -- checkout must exit 0.", text)
        # forbidden actions
        self.assertIn("git push, deploy, migrations against shared data, history rewrite", text)
        self.assertIn("reading .env or secret files", text)
        self.assertIn("launching subagents", text)
        self.assertIn("merging the integration target", text)
        # report contract as fenced JSON schema
        self.assertIn("```json", text)
        self.assertIn('"role": "worker"', text)
        self.assertIn('"status": "DONE | BLOCKED | NO-OP"', text)
        self.assertIn('"commands"', text)
        self.assertIn('"acceptance"', text)
        self.assertIn('"findings"', text)
        self.assertIn('"usage"', text)
        self.assertIn('"next"', text)
        self.assertIn("DONE requires every command to exit 0", text)
        self.assertIn("PASS requires no blocker and no major finding", text)

    def test_worker_brief_lists_dependencies(self):
        slug = self.make_manifest({"u1": self.worker_fields(), "u2": self.worker_fields(
            objective="extend the cart totals",
            owned_paths=["src/checkout/cart.ts"],
            worktree="/tmp/wt/u2",
            branch="orch/demo/u2-20261117-1701",
            depends_on=["u1"],
        )})
        code, out, err = self.run_brief(["--task", slug, "--unit", "u2", "--role", "worker"])
        self.assertEqual(code, 0, err)
        self.assertIn("Depends on: u1", out)
        self.assertNotIn("- src/checkout/retry.ts", out)  # only owned paths listed

    def test_worker_brief_flags_missing_fields(self):
        slug = self.make_manifest({"u1": {"objective": "no worktree yet", "owned_paths": ["src/a"]}})
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "worker"])
        self.assertEqual(code, 2)
        self.assertIn("missing fields required for a worker brief", err)
        self.assertIn("worktree", err)
        self.assertIn("branch", err)

    def test_reviewer_brief_requires_independence(self):
        slug = self.make_manifest(
            {"u1": self.worker_fields(worker_sha="def5678")}
        )
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "reviewer"])
        self.assertEqual(code, 0, err)
        text = out
        self.assertIn("Implementer model: opencode-go/deepseek-v4.1-flash#max", text)
        self.assertIn("Implementer family: deepseek", text)
        self.assertIn("Reviewed SHA: def5678", text)
        self.assertIn("Confirm `git rev-parse HEAD` equals the reviewed SHA", text)
        self.assertIn("differs from the implementer's", text)
        self.assertIn("BLOCKED; do not run a non-independent review", text)
        self.assertIn("do not\n  re-read the whole repo", text)
        self.assertIn("Review this worktree read-only", text)
        self.assertIn("two reviewers from two distinct families other than the implementer's", text)
        self.assertIn('"role": "reviewer"', text)
        self.assertIn('"status": "PASS | FAIL | BLOCKED"', text)
        self.assertIn("PASS requires no blocker and no major finding", text)
        self.assertIn("no blocker or major finding", text)

    def test_reviewer_brief_risk_unit_names_two_reviewers(self):
        slug = self.make_manifest(
            {"u1": self.worker_fields(risk="security", worker_sha="def5678")}
        )
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "reviewer"])
        self.assertEqual(code, 0, err)
        self.assertIn("this is a security unit", out)
        self.assertIn("both must return PASS", out)
        self.assertIn("any disagreement is FAIL", out)

    def test_reviewer_brief_model_flags_fill_missing_implementer(self):
        fields = self.worker_fields(worker_sha="def5678")
        del fields["model"]
        del fields["implementer_family"]
        slug = self.make_manifest({"u1": fields})
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "reviewer"])
        self.assertEqual(code, 2)
        self.assertIn("model", err)
        self.assertIn("implementer_family", err)
        self.assertIn("--model / --implementer-family", err)
        code, out, err = self.run_brief(
            [
                "--task", slug,
                "--unit", "u1",
                "--role", "reviewer",
                "--model", "opencode/beam-free#max",
                "--implementer-family", "beam",
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Implementer model: opencode/beam-free#max", out)
        self.assertIn("Implementer family: beam", out)

    def test_reviewer_worker_sha_is_required(self):
        slug = self.make_manifest({"u1": self.worker_fields()})
        code, out, err = self.run_brief(["--task", slug, "--unit", "u1", "--role", "reviewer"])
        self.assertEqual(code, 2)
        self.assertIn("worker_sha", err)

    def test_missing_unit_fails(self):
        slug = self.make_manifest({"u1": self.worker_fields()})
        code, out, err = self.run_brief(["--task", slug, "--unit", "ghost", "--role", "worker"])
        self.assertEqual(code, 2)
        self.assertIn("unit 'ghost' not found in task", err)
        self.assertIn("u1", err)

    def test_missing_manifest_fails(self):
        code, out, err = self.run_brief(
            ["--task", "no-such-task-xyz", "--unit", "u1", "--role", "worker"]
        )
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", err)
        self.assertIn("no manifest", err)

    def test_invalid_role_fails(self):
        slug = self.make_manifest({"u1": self.worker_fields()})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                brief.main(["--task", slug, "--unit", "u1", "--role", "boss"])
        self.assertEqual(ctx.exception.code, 2)

    def test_explore_brief_uses_explore_status(self):
        slug = self.make_manifest(
            {
                "u1": self.worker_fields(),
                "e1": {
                    "objective": "find where retries are configured",
                    "owned_paths": ["docs/research/retries.md"],
                },
            }
        )
        code, out, err = self.run_brief(["--task", slug, "--unit", "e1", "--role", "explore"])
        self.assertEqual(code, 0, err)
        text = out
        self.assertIn("Read-only investigation. Do not edit files.", text)
        self.assertIn(f"Root: /tmp/repo", text)
        self.assertIn("- docs/research/retries.md", text)
        self.assertIn("Line 1: DONE, BLOCKED or NO-OP.", text)
        self.assertIn('"role": "explore"', text)
        self.assertIn('"sha": "null (read-only)"', text)
        self.assertIn("commands and acceptance are optional for this role", text)
        self.assertIn("First: run `pwd`.", text)

    def test_utility_brief_uses_utility_status(self):
        slug = self.make_manifest(
            {
                "u1": self.worker_fields(),
                "s1": self.worker_fields(
                    objective="create one commit with message 'chore: tidy logs'",
                    owned_paths=["docs/log.md"],
                    worktree="/tmp/wt/s1",
                    branch="orch/demo/s1-20261117-1702",
                    effort="low",
                    model="opencode/big-pickle",
                    implementer_family=None,
                ),
            }
        )
        code, out, err = self.run_brief(["--task", slug, "--unit", "s1", "--role", "utility"])
        self.assertEqual(code, 0, err)
        text = out
        self.assertIn("Low-effort task only.", text)
        self.assertIn("Task: create one commit with message 'chore: tidy logs'", text)
        self.assertIn("Worktree/Root: /tmp/wt/s1", text)
        self.assertIn("- docs/log.md", text)
        self.assertIn("Line 1: DONE, BLOCKED or NO-OP.", text)
        self.assertIn('"role": "utility"', text)
        self.assertIn("Make only the minimal requested change", text)
        self.assertIn("Do not implement features", text)
        self.assertIn("launching subagents", text)
        self.assertIn("First: run `pwd`.", text)

    def test_utility_falls_back_to_repo_root(self):
        slug = self.make_manifest(
            {
                "s1": {
                    "objective": "rename one function",
                    "owned_paths": ["src/rename.py"],
                }
            }
        )
        code, out, err = self.run_brief(["--task", slug, "--unit", "s1", "--role", "utility"])
        self.assertEqual(code, 0, err)
        self.assertIn("Worktree/Root: /tmp/repo", out)

    def test_out_writes_exact_path(self):
        slug = self.make_manifest({"u1": self.worker_fields()})
        target = self.root / "briefs-out" / "u1-worker.md"
        code, out, err = self.run_brief(
            ["--task", slug, "--unit", "u1", "--role", "worker", "--out", str(target)]
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(target))
        text = target.read_text()
        self.assertTrue(text.startswith("# Worker brief:"))
        self.assertIn("First: run `pwd`.", text)
        # stdout carries the path, not the whole brief
        self.assertNotIn("Objective:", out)
        # the brief was never written into the worktree implicitly
        self.assertFalse(Path("/tmp/wt/u1").exists())

    def test_plan_py_brief_subcommand_renders(self):
        import plan

        slug = self.make_manifest({"u1": self.worker_fields()})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = plan.main(["brief", "--task", slug, "--unit", "u1", "--role", "worker"])
        self.assertEqual(code, 0, err)
        self.assertIn("First: run `pwd`.", out.getvalue())

    def test_brief_help(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                brief.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("usage:", out.getvalue())
        self.assertIn("--implementer-family", out.getvalue())


if __name__ == "__main__":
    unittest.main()
