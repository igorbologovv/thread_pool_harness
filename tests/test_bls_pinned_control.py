"""Ensure both pinned BLS control arms use identical measured conditions."""
import unittest
from pathlib import Path

from experiments.common.campaign import ROOT, read_definition
from experiments.common.study import design, expand_study, read_study


class PinnedControlStudies(unittest.TestCase):
    def test_paired_arms(self):
        names = ("bls-pinned-control-clean", "bls-pinned-control-interference")
        plans = []
        for name in names:
            path = ROOT / "experiments/studies" / ("bls_pinned_control_" + name.rsplit("-", 1)[-1] + ".toml")
            specification = read_study(path)
            summary = design(path, write=False)
            self.assertEqual(len(summary["campaigns"]), 12)
            self.assertEqual(summary["totals"]["invocations"], 2800)
            self.assertEqual(summary["totals"]["measured_runs"], 11200)
            self.assertEqual(summary["totals"]["warmup_runs"], 11200)
            template = read_definition(ROOT / specification["study"]["template"])
            campaigns, skipped = expand_study(specification, template)
            self.assertFalse(skipped)
            fingerprint = []
            for _, experiment, details in campaigns:
                configs = experiment["configurations"]
                self.assertEqual(len(configs), 7)
                self.assertEqual(experiment["repetitions"]["warmup_runs_per_block"], 4)
                self.assertEqual(experiment["repetitions"]["measured_runs_per_block"], 2)
                self.assertEqual(len(experiment["perf"]["passes"]), 2)
                labels = {cfg["id"] for cfg in configs}
                self.assertIn("mpmc-sp0", labels)
                self.assertIn("mpmc-sp40", labels)
                fingerprint.append((
                    experiment["workload"]["workers"],
                    experiment["delivery"]["mode"],
                    tuple(experiment["delivery"].get("arrival_rates", [])),
                    tuple(sorted(labels)),
                    details["invocations"],
                ))
            plans.append(fingerprint)
        self.assertEqual(plans[0], plans[1])


if __name__ == "__main__":
    unittest.main()
