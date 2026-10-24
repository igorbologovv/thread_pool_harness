"""Planning tests require no perf permission and do not start benchmarks."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from experiments.common.campaign import ROOT, read_definition, make_plan
from experiments.common.study import (
    design, expand_study, read_study, render_toml, expand_configurations,
)


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.spec = read_study(ROOT / "experiments/studies/pilot.toml")
        self.template = read_definition(ROOT / self.spec["study"]["template"])

    def test_expansion_is_deterministic_and_valid(self):
        campaigns, skipped = expand_study(self.spec, self.template)
        self.assertEqual(len(campaigns), 4)  # 2 worker counts x 2 delivery modes
        self.assertEqual(len(skipped), 0)
        self.assertEqual(sum(c[2]["invocations"] for c in campaigns), 4 * 3 * 2 * 2 * 6)
        for filename, config, stats in campaigns:
            self.assertEqual(tomllib.loads(render_toml(config)), config)
            self.assertEqual(len(make_plan(config, None)), stats["invocations"])
            self.assertEqual(
                len([c for c in config["configurations"] if c["scheduler"] == "rayon"]), 1
            )
            self.assertTrue(filename.endswith(".toml"))
        self.assertEqual(campaigns, expand_study(self.spec, self.template))

    def test_invalid_capacity_backlog_filtered(self):
        axes = {"queue_capacities": [4, 8], "hot_first_backlogs": [1, 2]}
        configurations, skipped = expand_configurations(self.template, axes, workers=4)
        self.assertEqual(len(skipped), 1)
        self.assertTrue(any(c["scheduler"] == "rayon" for c in configurations))

    def test_budget_limit_enforced(self):
        spec = {**self.spec, "study": {**self.spec["study"], "max_invocations": 1}}
        with self.assertRaisesRegex(ValueError, "limit"):
            expand_study(spec, self.template)

    def test_check_does_not_write(self):
        report = design(ROOT / "experiments/studies/pilot.toml", write=False)
        self.assertEqual(report["totals"]["invocations"], 288)
        self.assertEqual(len(report["campaigns"]), 4)

    def test_workload_axes_validation(self):
        spec = {**self.spec, "axes": {"operations_per_work_unit": [1, 2]}}
        with self.assertRaisesRegex(ValueError, "incompatible"):
            expand_study(spec, self.template)


if __name__ == "__main__":
    unittest.main()
