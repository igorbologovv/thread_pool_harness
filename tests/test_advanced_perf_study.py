"""Validate the advanced-PMU study without running benchmarks."""
import json
import tomllib
import unittest

from experiments.common.campaign import ROOT, command, make_plan, read_definition
from experiments.common.study import expand_study, read_study, render_toml


class AdvancedPerfStudyTests(unittest.TestCase):
    def test_matrix_and_profile_passes(self):
        spec = read_study(ROOT / "experiments/studies/bls_advanced_perf.toml")
        base = read_definition(ROOT / spec["study"]["template"])
        campaigns, skipped = expand_study(spec, base)

        self.assertEqual(len(campaigns), 12)
        self.assertEqual(sum(row[2]["invocations"] for row in campaigns), 7200)
        self.assertEqual(sum(row[2]["measured_runs"] for row in campaigns), 28800)
        self.assertEqual(sum(row[2]["warmup_runs"] for row in campaigns), 7200)
        self.assertEqual(sum(row[2]["settle_floor_seconds"] for row in campaigns), 0)
        self.assertGreater(len(skipped), 0)

        for _, definition, stats in campaigns:
            self.assertEqual(tomllib.loads(render_toml(definition)), definition)
            self.assertEqual(definition["experiment"]["profile"], "deep")
            self.assertEqual(definition["execution"]["settle_seconds"], 0)
            self.assertEqual(definition["repetitions"]["warmup_runs_per_block"], 1)
            self.assertEqual(definition["repetitions"]["measured_runs_per_block"], 2)
            self.assertEqual(len(definition["perf"]["passes"]), 2)
            self.assertEqual(len(definition["configurations"]), 18)
            self.assertIn(definition["workload"]["workers"], (2, 4, 8, 16))
            self.assertEqual(len(make_plan(definition, None)), stats["invocations"])

            configs = definition["configurations"]
            spins = {
                x["spin_us"] for x in configs if x.get("wait_policy") == "spin-then-park"
            }
            self.assertEqual(spins, {0, 20, 40, 80})

            first_block = next(x for x in make_plan(definition, None) if x["block"] == 1)
            second_block = next(x for x in make_plan(definition, None) if x["block"] == 2)
            args1 = command(definition, first_block, ROOT / "dummy.sqlite3", "test")
            args2 = command(definition, second_block, ROOT / "dummy.sqlite3", "test")
            self.assertNotIn("--reverse-perf-passes", args1)
            self.assertIn("--reverse-perf-passes", args2)
            event_groups = json.loads(args1[args1.index("--perf-passes-json") + 1])
            self.assertEqual(len(event_groups), 2)
            self.assertTrue(all(len(group["events"]) == 9 for group in event_groups))


if __name__ == "__main__":
    unittest.main()
