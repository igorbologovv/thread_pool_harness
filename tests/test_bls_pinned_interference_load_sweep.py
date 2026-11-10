"""Dry-run validation for the new stressed-only 200 ms BLS load sweep."""
import math
import unittest

from experiments.common.campaign import make_plan, read_definition
from experiments.common.study_run import command
from experiments.studies.run_bls_pinned_interference_load_sweep import (
    BASE, BLOCKS, CONFIGURATIONS, DATASET_WORK_UNITS, GENERATED,
    LOAD_PERCENT, MODES, REPEATS, SEED, STRESSED_CAPACITY_WU_S,
    TARGET_WINDOW_SECONDS, WARMUPS, WORKERS, definition, entries, points,
)


class BLSStressedLoadSweepTests(unittest.TestCase):
    def test_exact_200ms_scheduled_window_at_every_rate(self):
        for workers in WORKERS:
            expected = LOAD_PERCENT[workers]
            actual = points(workers)
            self.assertEqual(tuple(p[0] for p in actual), expected)
            self.assertEqual(len({p[1] for p in actual}), len(expected))
            for pct, rate, count, window in actual:
                self.assertEqual(
                    rate, math.floor(STRESSED_CAPACITY_WU_S[workers] * pct / 100 + 0.5)
                )
                self.assertEqual(count, math.ceil(TARGET_WINDOW_SECONDS * rate) + 1)
                self.assertGreaterEqual(window, TARGET_WINDOW_SECONDS - 1e-12)
                self.assertLess(window, TARGET_WINDOW_SECONDS + 1 / rate + 1e-12)
                self.assertLessEqual(count, DATASET_WORK_UNITS)

    def test_bls_and_schedulers_match_original_stressed_control(self):
        original = read_definition(BASE)
        for workers in WORKERS:
            for mode in MODES:
                for pct, rate, count, _ in points(workers):
                    d, path, _ = definition(workers, mode, pct)
                    self.assertEqual(d["experiment"]["workload"], original["experiment"]["workload"])
                    self.assertEqual(d["workload"]["work_units"], count)
                    self.assertEqual(d["workload"]["dataset_work_units"], DATASET_WORK_UNITS)
                    self.assertEqual(d["workload"]["workers"], workers)
                    for key in ("validators", "signers_per_certificate",
                                "certificates_per_work_unit", "workload_seed", "queue_capacity"):
                        self.assertEqual(d["workload"][key], original["workload"][key])
                    self.assertEqual(d["configurations"], original["configurations"])
                    self.assertEqual({c["id"] for c in d["configurations"]}, CONFIGURATIONS)
                    self.assertEqual(d["delivery"]["mode"], mode)
                    self.assertEqual(d["delivery"]["arrival_rates"], [float(rate)])
                    self.assertEqual(d["repetitions"]["blocks"], BLOCKS)
                    self.assertEqual(d["repetitions"]["warmup_runs_per_block"], WARMUPS)
                    self.assertEqual(d["repetitions"]["measured_runs_per_block"], REPEATS)
                    self.assertEqual(len(d["perf"]["passes"]), 1)
                    self.assertEqual(d["perf"]["passes"][0], original["perf"]["passes"][0])
                    self.assertEqual(len(make_plan(d, None)), BLOCKS * len(CONFIGURATIONS))
                    self.assertTrue(str(path).startswith(str(GENERATED)))
                    self.assertNotIn("clean", str(path))
                    self.assertNotIn("all-at-once", d["delivery"]["mode"])
                    self.assertGreaterEqual(d["randomization"]["seed"], SEED)

    def test_no_duplicate_original_fixed_900_1500_rates(self):
        for w in WORKERS:
            measured = {rate for _, rate, _, _ in points(w)}
            self.assertTrue(measured.isdisjoint({900, 1500}))

    def test_stress_and_affinity_are_explicit_in_collection_command(self):
        d, path, _ = definition(2, "fixed-arrivals", 45)
        cmd = command("collect", path, cache_interference=True, pin_workers=True)
        self.assertIn("--cache-interference", cmd)
        self.assertIn("--pin-workers", cmd)
        self.assertIn("interference", d["experiment"]["name"])

    def test_group_size_and_one_rate_per_campaign(self):
        plan = entries((2, 4, 8), MODES)
        self.assertEqual(len(plan), 2 * sum(len(LOAD_PERCENT[w]) for w in (2, 4, 8)))
        self.assertEqual(
            sum(len(make_plan(d, None)) for d, _, _ in plan),
            len(plan) * BLOCKS * len(CONFIGURATIONS),
        )
        self.assertEqual(
            len({d["experiment"]["name"] for d, _, _ in plan}), len(plan)
        )


if __name__ == "__main__":
    unittest.main()
