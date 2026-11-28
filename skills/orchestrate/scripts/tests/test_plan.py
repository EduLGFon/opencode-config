"""Tests for scripts/plan.py: spec validation, wave planning and manifest writing."""

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
os.environ.setdefault("ORCHESTRATE_ROOT", tempfile.mkdtemp(prefix="orchestrate-plan-tests-"))

import plan  # noqa: E402
import tasklib  # noqa: E402


def base_unit(uid: str, **overrides) -> dict:
    unit = {
        "id": uid,
        "objective": f"objective for {uid}",
        "owned_paths": [f"src/{uid}.ts"],
        "depends_on": [],
        "effort": "medium",
        "risk": "normal",
        "model": "opencode/beam-free#high",
        "implementer_family": "beam",
        "acceptance": ["npm test"],
        "contracts": [],
        "context": "context for " + uid,
        "resource_isolation": "PORT=4100",
        "estimate": {"tokens_in": 10000, "tokens_out": 2000},
    }
    unit.update(overrides)
    return unit


class PlanTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="orchestrate-plan-root-"))
        self._old_root = tasklib.ROOT
        tasklib.ROOT = self.root
        self.slug_counter = 0

    def tearDown(self):
        tasklib.ROOT = self._old_root
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------------------------------------------------------------- helpers

    def unique_slug(self, prefix: str = "t-plan") -> str:
        self.slug_counter += 1
        return f"{prefix}-{self.slug_counter:02d}-{os.getpid():x}"

    def make_spec(self, units: list[dict], **overrides) -> dict:
        spec = {
            "task": self.unique_slug(),
            "target": {"repo": "/tmp/repo", "ref": "main"},
            "policy": "default",
            "budget_usd": 2.0,
            "max_concurrency": 8,
            "units": units,
        }
        spec.update(overrides)
        return spec

    def write_spec(self, spec: dict) -> str:
        path = self.root / "specs" / f"{spec['task']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(spec))
        return str(path)

    def run_plan(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = plan.main(argv)
        return code, out.getvalue(), err.getvalue()

    def wave_ids(self, plan_data: dict) -> list[list[str]]:
        return [[unit["id"] for unit in wave] for wave in plan_data["waves"]]

    # ------------------------------------------------------------------ tests

    def test_valid_three_unit_graph(self):
        spec = self.make_spec(
            [
                base_unit("u1"),
                base_unit("u2", owned_paths=["src/other.ts"]),
                base_unit("u3", owned_paths=["src/third.ts"], depends_on=["u1", "u2"]),
            ]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(
            set(data),
            {"task", "policy", "waves", "order", "warnings", "estimate_usd"},
        )
        self.assertEqual(data["task"], spec["task"])
        self.assertEqual(data["policy"], "default")
        self.assertEqual(self.wave_ids(data), [["u1", "u2"], ["u3"]])
        self.assertEqual(data["order"], ["u1", "u2", "u3"])
        self.assertEqual(data["warnings"], [])
        # beam-free is a free model: estimated cost is 0.0.
        self.assertEqual(data["estimate_usd"], 0.0)
        for wave in data["waves"]:
            for unit in wave:
                self.assertEqual(unit["estimate_usd"], 0.0)
        # A unit's dependencies always sit in earlier waves.
        ids = {unit["id"]: unit for wave in data["waves"] for unit in wave}
        self.assertEqual(set(ids["u3"]["depends_on"]), {"u1", "u2"})

    def test_concurrency_split_is_deterministic(self):
        spec = self.make_spec([base_unit(f"u{n}") for n in range(1, 6)])
        code, out, err = self.run_plan(
            ["plan", self.write_spec(spec), "--json", "--max-concurrency", "2"]
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(self.wave_ids(data), [["u1", "u2"], ["u3", "u4"], ["u5"]])
        self.assertEqual(data["order"], ["u1", "u2", "u3", "u4", "u5"])
        self.assertTrue(any("max-concurrency" in warning for warning in data["warnings"]))

    def test_dependency_units_wait_for_earlier_waves(self):
        spec = self.make_spec(
            [
                base_unit("a"),
                base_unit("b", depends_on=["a"]),
                base_unit("c", depends_on=["b"]),
            ]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(self.wave_ids(data), [["a"], ["b"], ["c"]])

    def test_human_output(self):
        spec = self.make_spec(
            [base_unit("u1"), base_unit("u2", owned_paths=["src/other.ts"])]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec)])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Task: {spec['task']}", out)
        self.assertIn("Wave 1 (2 units):", out)
        self.assertIn("Order: u1, u2", out)
        self.assertIn("Estimated cost:", out)
        self.assertNotIn("Manifest:", out)  # dry-run by default

    def test_unknown_dependency(self):
        spec = self.make_spec([base_unit("u1", depends_on=["ghost"])])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("unknown dependency 'ghost'", err)

    def test_cycle(self):
        spec = self.make_spec(
            [
                base_unit("u1", depends_on=["u2"]),
                base_unit("u2", owned_paths=["src/other.ts"], depends_on=["u1"]),
            ]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("dependency cycle", err)

    def test_duplicate_id(self):
        spec = self.make_spec([base_unit("u1"), base_unit("u1", owned_paths=["src/other.ts"])])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("duplicate unit id", err)

    def test_overlapping_owned_paths(self):
        spec = self.make_spec(
            [base_unit("u1", owned_paths=["src/api"]), base_unit("u2", owned_paths=["src/api/v2"])]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("owned path overlap", err)

    def test_unsafe_owned_paths_are_rejected(self):
        for bad in ("../escape", "/etc/passwd", "*", "src/../../x"):
            spec = self.make_spec([base_unit("u1", owned_paths=[bad])])
            code, _, err = self.run_plan(["plan", self.write_spec(spec)])
            self.assertEqual(code, 2, bad)
            self.assertIn("unsafe owned path", err, bad)

    def test_invalid_effort(self):
        spec = self.make_spec([base_unit("u1", effort="extreme")])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("effort must be one of", err)

    def test_invalid_risk(self):
        spec = self.make_spec([base_unit("u1", risk="cosmic")])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("risk must be one of", err)

    def test_invalid_header_fields(self):
        cases = [
            (self.make_spec([base_unit("u1")], task="Bad Slug!"), "task slug is invalid"),
            (self.make_spec([base_unit("u1")], policy="turbo"), "policy must be one of"),
            (
                self.make_spec([base_unit("u1")], target={"repo": "/tmp/repo"}),
                "target.ref is required",
            ),
            (self.make_spec([]), "units must be a non-empty list"),
            (self.make_spec([base_unit("u1", objective="   ")]), "objective is required"),
            (self.make_spec([base_unit("u1", owned_paths=[])]), "owned_paths must be a non-empty"),
            (self.make_spec([base_unit("u1")], budget_usd=-1), "budget_usd must be >= 0"),
            (self.make_spec([base_unit("u1")], max_concurrency=0), "max_concurrency must be"),
        ]
        for spec, expected in cases:
            with self.subTest(expected=expected):
                code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
                self.assertEqual(code, 2)
                self.assertIn(expected, err)

    def test_missing_objective_and_empty_owned_paths(self):
        spec = self.make_spec([base_unit("u1", objective=None, owned_paths=[""])] )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("objective is required", err)
        self.assertIn("owned_paths must be a non-empty list of path strings", err)

    def test_no_estimates_total_is_null(self):
        spec = self.make_spec([base_unit("u1"), base_unit("u2", owned_paths=["src/other.ts"])])
        for unit in spec["units"]:
            del unit["estimate"]
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIsNone(data["estimate_usd"])
        self.assertEqual([u["estimate_usd"] for wave in data["waves"] for u in wave], [None, None])
        self.assertTrue(any("no unit has a token estimate" in w for w in data["warnings"]))

    def test_partial_estimates_total_is_null(self):
        spec = self.make_spec([base_unit("u1"), base_unit("u2", owned_paths=["src/other.ts"])])
        del spec["units"][1]["estimate"]
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertIsNone(data["estimate_usd"])
        flat = [u for wave in data["waves"] for u in wave]
        costs = {u["id"]: u["estimate_usd"] for u in flat}
        self.assertEqual(costs["u1"], 0.0)
        self.assertIsNone(costs["u2"])

    def test_paid_model_estimate(self):
        spec = self.make_spec(
            [
                base_unit(
                    "u1",
                    model="opencode-go/deepseek-v4.1-flash#max",
                    implementer_family="deepseek",
                    estimate={"tokens_in": 10000, "tokens_out": 2000},
                )
            ]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        # 10000/1M * 0.15 + 2000/1M * 0.60 = 0.0027
        self.assertAlmostEqual(data["estimate_usd"], 0.0027, places=9)

    def test_default_paid_model_warns_for_approval(self):
        spec = self.make_spec(
            [base_unit("u1", model="opencode-go/deepseek-v4.1-flash#max")]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(any("tier 2" in w and "approval" in w for w in data["warnings"]))

    def test_tier2_only_policy_warns_but_does_not_error(self):
        spec = self.make_spec([base_unit("u1", model="opencode/beam-free#high")], policy="tier2-only")
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(any("tier2-only" in w for w in data["warnings"]))

    def test_tier2_only_write_rejects_free_model(self):
        spec = self.make_spec([base_unit("u1", model="opencode/beam-free#high")], policy="tier2-only")
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--write"])
        self.assertEqual(code, 2)
        self.assertIn("manifest invalid", err)

    def test_write_creates_valid_manifest(self):
        spec = self.make_spec(
            [
                base_unit("u1"),
                base_unit("u2", owned_paths=["src/other.ts"]),
                base_unit("u3", owned_paths=["src/third.ts"], depends_on=["u1", "u2"]),
            ]
        )
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--write"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"Manifest: {tasklib.manifest_path(spec['task'])}", out)
        manifest = tasklib.load(spec["task"])
        errors, _ = tasklib.validate_manifest(manifest)
        self.assertEqual(errors, [])
        self.assertEqual(manifest["task"], spec["task"])
        self.assertEqual(manifest["policy"]["name"], "default")
        self.assertEqual(manifest["budget"]["limit_usd"], 2.0)
        self.assertEqual(
            manifest["target"]["repo"], str(Path("/tmp/repo").expanduser().resolve())
        )
        for uid, unit in manifest["units"].items():
            self.assertEqual(unit["state"], "planned")
            self.assertIsNone(unit["worktree"])
            self.assertIsNone(unit["branch"])
            self.assertEqual(unit["id"], uid)
            self.assertTrue(unit["owned_paths"])
        self.assertEqual(manifest["units"]["u3"]["depends_on"], ["u1", "u2"])
        self.assertEqual(
            manifest["units"]["u1"]["model"], "opencode/beam-free#high"
        )

    def test_write_json_includes_manifest_path(self):
        spec = self.make_spec([base_unit("u1")])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--json", "--write"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["manifest"], str(tasklib.manifest_path(spec["task"])))

    def test_write_refuses_overwrite_without_force(self):
        spec = self.make_spec([base_unit("u1")])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--write"])
        self.assertEqual(code, 0, err)
        first = tasklib.load(spec["task"])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--write"])
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertIn("--force", err)
        self.assertEqual(tasklib.load(spec["task"]), first)  # untouched
        forced = dict(spec)
        forced["units"] = [base_unit("u1", objective="rewritten objective")]
        code, out, err = self.run_plan(["plan", self.write_spec(forced), "--write", "--force"])
        self.assertEqual(code, 0, err)
        second = tasklib.load(spec["task"])
        self.assertEqual(second["units"]["u1"]["objective"], "rewritten objective")

    def test_force_flag_without_write_is_harmless(self):
        spec = self.make_spec([base_unit("u1")])
        code, out, err = self.run_plan(["plan", self.write_spec(spec), "--force", "--json"])
        self.assertEqual(code, 0, err)
        self.assertFalse(tasklib.manifest_path(spec["task"]).exists())

    def test_missing_spec_file(self):
        code, out, err = self.run_plan(["plan", str(self.root / "nope.json"), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", err)

    def test_spec_must_be_object(self):
        path = self.root / "specs" / "list.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")
        code, out, err = self.run_plan(["plan", str(path), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("spec must be a JSON object", err)

    def test_help(self):
        for argv in (["--help"], ["plan", "--help"], ["brief", "--help"]):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    with self.assertRaises(SystemExit) as ctx:
                        plan.main(argv)
                self.assertEqual(ctx.exception.code, 0)
                self.assertIn("usage:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
