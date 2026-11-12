"""Pure-planning checks for the clean MPMC wait/wake study (no perf needed)."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.common.campaign import make_plan
from experiments.studies import run_mpmc_wait_policy_study as study


class WaitPolicyStudyTests(unittest.TestCase):
    def fake_reference(self, workload, workers, ops):
        return {
            "workload": workload,
            "workers": workers,
            "reference_wu_s": 1200.0 if workload == "bls-aggregate-verify" else 800.0,
            "operations_per_work_unit": ops if workload == "compute-heavy" else None,
        }

    def test_wait_policy_is_only_changing_scheduler_variable(self):
        configs = study.configurations()
        self.assertEqual(len(configs), 5)
        self.assertEqual([p["id"] for p in configs],
                         ["mpmc-sp0", "mpmc-sp10", "mpmc-sp40",
                          "mpmc-sp100", "mpmc-agave"])
        self.assertTrue(all(p["scheduler"] == "threadance" for p in configs))
        self.assertEqual({p["queue_capacity"] for p in configs}, {64})
        self.assertEqual([p["spin_us"] for p in configs[:-1]], [0, 10, 40, 100])
        self.assertEqual(configs[-1]["wait_policy"], "agave-backoff")

    @patch.object(study, "load_reference")
    def test_calibrated_loads_and_minimum_windows(self, reference):
        reference.side_effect = self.fake_reference
        for workload in study.WORKLOADS:
            for workers in study.WORKERS:
                pts = study.points(workload, workers, 256, 200, 1024)
                self.assertEqual(tuple(x[0] for x in pts), study.LOADS)
                self.assertEqual(len(pts), len(set((x[0], x[2]) for x in pts)))
                for pct, wu, rate, seconds in pts:
                    expected = self.fake_reference(workload, workers, 256)["reference_wu_s"] * pct/100
                    self.assertAlmostEqual(rate, expected)
                    self.assertEqual(wu, math.ceil(.2 * rate) + 1)
                    self.assertGreaterEqual(seconds, .2-1e-9)

    @patch.object(study, "load_reference")
    def test_full_definition_and_randomized_blocks(self, reference):
        reference.side_effect = self.fake_reference
        for workload in study.WORKLOADS:
            data, path, rendered = study.definition(workload, 8, "bursty-arrivals",
                                                   50, 256, 200, 1024)
            self.assertTrue(path.name.endswith("bursty-arrivals.toml"))
            self.assertIn("mpmc-wait-policy-study", str(path))
            self.assertEqual(data["experiment"]["workload"], workload)
            self.assertEqual(data["delivery"]["mode"], "bursty-arrivals")
            self.assertEqual(len(data["configurations"]), 5)
            self.assertEqual(data["repetitions"],
                             {"blocks": 40, "warmup_runs_per_block": 4,
                              "measured_runs_per_block": 2})
            self.assertEqual(len(data["perf"]["passes"]), 1)
            self.assertEqual(data["perf"]["passes"][0]["name"], "cpu-cache")
            planned = make_plan(data, None)
            self.assertEqual(len(planned), 40 * 5)
            for block in range(1, 41):
                subset = [r for r in planned if r["block"] == block]
                self.assertEqual({r["configuration"] for r in subset},
                                 {c["id"] for c in study.configurations()})
                self.assertEqual(len({r["arrival_seed"] for r in subset}), 1)
                self.assertEqual(len({r["arrival_rate"] for r in subset}), 1)

    @patch.object(study, "load_reference")
    def test_compute_memory_limits_and_campaign_order(self, reference):
        reference.side_effect = self.fake_reference
        with self.assertRaises(ValueError):
            study.window_seconds(250000.0, 256, 128, 200)
        a = study.entries((2, 8), study.WORKLOADS, study.MODES, 256, 200, 1024)
        b = study.entries((2, 8), study.WORKLOADS, study.MODES, 256, 200, 1024)
        self.assertEqual(len(a), 2 * 2 * 2 * len(study.LOADS))
        self.assertEqual([d["experiment"]["name"] for d, _, _ in a],
                         [d["experiment"]["name"] for d, _, _ in b])
        self.assertEqual(len({d["experiment"]["name"] for d, _, _ in a}), len(a))


if __name__ == "__main__":
    unittest.main()
