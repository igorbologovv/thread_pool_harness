"""Pure planning/statistical tests for the boost-on load transition."""
import math
import unittest
from unittest.mock import patch

from experiments.studies.run_bls_w2_boost_transition import (
    LOADS,
    ordered_cells,
)
from experiments.studies.analyze_bls_w2_boost_transition import (
    aggregate,
    summarize,
)


class TransitionPlanTests(unittest.TestCase):
    def test_complete_balanced_bidirectional_order(self):
        cells = ordered_cells()
        self.assertEqual(len(cells), 22)
        self.assertEqual(sorted({load for _, load in cells}), list(LOADS))
        self.assertEqual([v for w, v in cells if w == 2],
                         list(reversed([v for w, v in cells if w == 1])))
        self.assertEqual(LOADS[3:6], (0.475, 0.50, 0.525))

    def test_wave_stratified_resampling_is_repeatable(self):
        x = [1, 2, 3, 10, 11, 12]
        groups = [1, 1, 1, 2, 2, 2]
        a = summarize(x, 12, strata=groups)
        b = summarize(x, 12, strata=groups)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a[0], 6.5)
        self.assertTrue(a[2] < a[0] < a[3])

    def test_complete_synthetic_paired_blocks(self):
        runs = []
        for load in LOADS:
            for wave in (1, 2):
                for block in range(1, 11):
                    for config in ("mpmc-sp0", "rtrb-sp0-b2"):
                        for p in ("cpu-cache", "l1-branches"):
                            for rep in range(2):
                                base = 2.0 if config == "mpmc-sp0" else 1.8
                                runs.append({
                                    "wave": wave, "load": load, "block": block,
                                    "configuration": config, "pass": p,
                                    "cpu_ms_per_wu": base + rep * 0.01,
                                    "effective_ghz": 4.0 if config == "mpmc-sp0" else 4.5,
                                    "worker1_active_gt_1ms": float(config == "mpmc-sp0"),
                                    "branch_misses_per_wu": 30.0 if p == "l1-branches" else None,
                                    "cache_misses_per_wu": 50.0 if p == "cpu-cache" else None,
                                    "cycles_per_wu": 1e6,
                                    "worker0_cpu_ms_per_wu": base,
                                    "worker1_cpu_ms_per_wu": 0.5,
                                    "context_switches_per_wu": 1.0,
                                    "throughput": 500.0,
                                    "mean_lateness_us": 12.0,
                                    "max_lateness_us": 40.0,
                                })
        self.assertEqual(len(runs), 1760)
        with patch('experiments.studies.analyze_bls_w2_boost_transition.BOOTSTRAPS', 100):
            blocks, summaries, contrasts = aggregate(runs)
        self.assertGreater(len(blocks), 1000)
        contrast = next(r for r in contrasts if r["load"] == 0.50
                        and r["pass"] == "cpu-cache"
                        and r["metric"] == "cpu_ms_per_wu")
        self.assertEqual(contrast["n_paired_blocks"], 20)
        self.assertAlmostEqual(contrast["rtrb_minus_mpmc"], -0.2)
        self.assertAlmostEqual(contrast["ci95_low"], -0.2)
        self.assertAlmostEqual(contrast["ci95_high"], -0.2)
        self.assertTrue(math.isfinite(summaries[0]["mean"]))

    def test_refuses_partial_repeat(self):
        with self.assertRaises(ValueError):
            aggregate([{
                "wave": 1, "load": 0.50, "block": 1,
                "configuration": "mpmc-sp0", "pass": "cpu-cache",
                "cpu_ms_per_wu": 1.0,
            }])


if __name__ == "__main__":
    unittest.main()
