"""Plan-level checks for paired pinned BLS offered-load acquisition.

These tests do not run BLS verification, perf or the interference process.
"""
import unittest

from experiments.common.campaign import make_plan
from experiments.studies.run_bls_pinned_offered_load import (
    ARMS, BLOCKS, CONFIGS, MODES, RATES, REPEATS, WARMUPS, WORKERS,
    WORK_UNITS, build_definition, selected,
)


class PinnedOfferedLoadTests(unittest.TestCase):
    def test_identical_clean_and_interference_conditions(self):
        for workers in WORKERS:
            for mode in MODES:
                clean, _, _ = build_definition(workers, mode, "clean")
                stressed, _, _ = build_definition(workers, mode, "interference")
                self.assertEqual(clean["workload"], stressed["workload"])
                self.assertEqual(clean["delivery"], stressed["delivery"])
                self.assertEqual(clean["repetitions"], stressed["repetitions"])
                self.assertEqual(clean["randomization"], stressed["randomization"])
                self.assertEqual(clean["configurations"], stressed["configurations"])
                self.assertEqual(clean["perf"], stressed["perf"])
                self.assertEqual(make_plan(clean, None), make_plan(stressed, None))

    def test_fixed_protocol_and_bound(self):
        for workers in WORKERS:
            for mode in MODES:
                data, _, _ = build_definition(workers, mode, ARMS[0])
                self.assertEqual(data["workload"]["work_units"], WORK_UNITS)
                self.assertEqual(data["workload"]["dataset_work_units"], 2500)
                self.assertEqual(data["repetitions"]["blocks"], BLOCKS)
                self.assertEqual(data["repetitions"]["warmup_runs_per_block"], WARMUPS)
                self.assertEqual(data["repetitions"]["measured_runs_per_block"], REPEATS)
                self.assertEqual(len(data["perf"]["passes"]), 1)
                self.assertEqual(data["perf"]["passes"][0]["name"], "cpu-cache")
                self.assertEqual({x["id"] for x in data["configurations"]}, CONFIGS)
                plan = make_plan(data, None)
                self.assertEqual(len(plan), BLOCKS * 5 * len(CONFIGS))
                self.assertEqual(
                    sorted({float(x["arrival_rate"]) for x in plan}),
                    sorted(RATES[workers]),
                )
                self.assertGreaterEqual(
                    (WORK_UNITS - 1) / max(RATES[workers]), 0.2
                )

    def test_worker_mode_selection(self):
        entries = selected((2, 4), MODES, ARMS)
        self.assertEqual(len(entries), 8)
        self.assertEqual(
            {x[0]["workload"]["workers"] for x in entries}, {2, 4}
        )
        self.assertEqual(
            {x[0]["delivery"]["mode"] for x in entries}, set(MODES)
        )
        for definition, _, _ in entries:
            self.assertEqual(
                len(make_plan(definition, None)), BLOCKS * 5 * len(CONFIGS)
            )

    def test_baseline_anchor_rates(self):
        self.assertIn(900.0, RATES[2])
        for workers in (4, 8, 16):
            self.assertIn(900.0, RATES[workers])
            self.assertIn(1500.0, RATES[workers])


if __name__ == "__main__":
    unittest.main()
