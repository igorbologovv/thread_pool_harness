"""Dry-run tests for stressed/pinned BLS precision extension."""
import unittest

from experiments.common.campaign import make_plan
from experiments.studies import run_bls_pinned_interference_load_sweep as original
from experiments.studies import run_bls_pinned_interference_ci_extension as followup


class StressCIExtensionTests(unittest.TestCase):
    def test_target_validation(self):
        self.assertEqual(followup.parse_target("2:fixed:45"), (2, "fixed-arrivals", 45))
        self.assertEqual(followup.parse_target("16:bursty:50"), (16, "bursty-arrivals", 50))
        for invalid in ("2:fixed:46", "3:fixed:50", "16:all-at-once:50",
                        "2:fixed", "2:fixed:hi"):
            with self.assertRaises(ValueError):
                followup.parse_target(invalid)

    def test_original_settings_retained(self):
        for target in followup.DEFAULT_TARGETS:
            w, mode, pct = target
            prior, _, _ = original.definition(w, mode, pct)
            new, path, serialized = followup.build_definition(target, 1, 40)
            self.assertEqual(new["workload"], prior["workload"])
            self.assertEqual(new["configurations"], prior["configurations"])
            self.assertEqual(new["perf"], prior["perf"])
            self.assertEqual(new["delivery"]["arrival_rates"], prior["delivery"]["arrival_rates"])
            self.assertEqual(new["delivery"]["mode"], mode)
            self.assertNotEqual(new["delivery"]["arrival_seed_base"],
                                prior["delivery"]["arrival_seed_base"])
            self.assertEqual(new["repetitions"]["warmup_runs_per_block"], 4)
            self.assertEqual(new["repetitions"]["measured_runs_per_block"], 2)
            self.assertEqual(new["repetitions"]["blocks"], 40)
            self.assertNotEqual(new["experiment"]["name"], prior["experiment"]["name"])
            self.assertTrue(str(path).startswith(str(followup.GENERATED)))
            self.assertEqual(len(make_plan(new, None)), 40 * 7)
            self.assertTrue(serialized)

    def test_separate_batch_identity(self):
        target = (8, "bursty-arrivals", 25)
        a, pa, _ = followup.build_definition(target, 1, 40)
        b, pb, _ = followup.build_definition(target, 2, 40)
        self.assertNotEqual(pa, pb)
        self.assertNotEqual(a["randomization"]["seed"], b["randomization"]["seed"])
        self.assertNotEqual(a["delivery"]["arrival_seed_base"],
                            b["delivery"]["arrival_seed_base"])

    def test_ten_targeted_campaigns(self):
        items = followup.make_entries(followup.DEFAULT_TARGETS, 1, 40)
        self.assertEqual(len(items), 10)
        self.assertEqual(sum(len(make_plan(d, None)) for d, _, _ in items), 2800)
        for data, path, _ in items:
            self.assertIn("interference-ci-extension", data["experiment"]["name"])
            self.assertNotIn("clean", str(path))
            self.assertEqual(len(data["perf"]["passes"]), 1)
            self.assertEqual(data["perf"]["passes"][0]["name"], "cpu-cache")


if __name__ == "__main__":
    unittest.main()
