"""Unit tests for the capacity-normalization data pipeline."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.studies.plot_bls_capacity_normalized import (
    CONFIGS, WORKERS, build, capacity_data, paced_data, compare_to_rayon,
)


class CapacityNormalizedTests(unittest.TestCase):
    def test_normalization_preserves_scheduled_arrival_rate(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        capacities = {(w, cfg): [100.0 + w] * 30 for w in WORKERS for cfg in CONFIGS}
        rates = (50.0, 100.0, 150.0, 200.0, 250.0, 300.0)
        paced = {}
        for w in WORKERS:
            for mode in ("fixed-arrivals", "bursty-arrivals"):
                for rate in rates:
                    paced[w, mode, rate] = {
                        cfg: {
                            block: {
                                "throughput": str(min(rate, 100.0 + w)),
                                "task_clock_per_wu": "100",
                                "cycles_per_wu": "200",
                                "drain_delay_ms": "1",
                                "branch_misses_per_wu": "2",
                            }
                            for block in range(1, 21)
                        }
                        for cfg in CONFIGS
                    }
        cap_rows, points = build(capacities, paced, 1000)
        paired = compare_to_rayon(paced, capacities, 1000)
        self.assertEqual(len(paired), 4 * 2 * 6 * 4 * 2)
        comp = next(r for r in paired if r["workers"] == 8
                    and r["mode"] == "fixed-arrivals"
                    and r["scheduler"] == "mpmc-sp0"
                    and r["scheduled_arrival_rate"] == 50.0
                    and r["metric"] == "throughput")
        self.assertAlmostEqual(comp["common_load_percent"], 100 * 50 / 108)
        self.assertAlmostEqual(comp["change_percent"], 0.0)
        self.assertAlmostEqual(comp["ci95_low"], 0.0)
        self.assertEqual(len(cap_rows), len(WORKERS) * len(CONFIGS))
        self.assertEqual(len(points), 4 * 2 * 6 * 5 * 5)
        reference = next(p for p in points
                         if p["workers"] == 8
                         and p["mode"] == "fixed-arrivals"
                         and p["scheduler"] == "rayon"
                         and p["scheduled_arrival_rate"] == 50.0
                         and p["metric"] == "achieved_over_capacity")
        self.assertAlmostEqual(reference["relative_load"], 50 / 108)
        self.assertAlmostEqual(reference["value"], 50 / 108)

    def test_no_campaigns_fails_without_touching_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "Expected 4 saturation campaigns"):
                capacity_data(root)
            with self.assertRaisesRegex(ValueError, "Expected 48 paced campaigns"):
                paced_data(root)


if __name__ == "__main__":
    unittest.main()
