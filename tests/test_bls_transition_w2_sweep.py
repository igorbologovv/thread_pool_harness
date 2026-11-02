"""Planning tests for the 2-worker transition sweep; no benchmarks."""
import unittest
import tomllib
from unittest.mock import patch

from experiments.studies.run_bls_transition_w2_sweep import (
    LOADS, WORK_UNITS, spec, execute,
)


class TransitionSweepTests(unittest.TestCase):
    def test_eight_cells_256_tasks_and_equal_physical_rates_by_mode(self):
        source = {
            "experiment": {"name": "bls-normalized-w2-load05000-fixed-arrivals",
                           "workload": "bls-aggregate-verify", "profile": "standard"},
            "workload": {"workers": 2, "work_units": 130, "dataset_work_units": 2500,
                         "validators": 2000, "signers_per_certificate": 1600,
                         "certificates_per_work_unit": 1, "workload_seed": 1},
            "delivery": {"mode": "fixed-arrivals", "arrival_rates": [647.0],
                         "arrival_seed_base": 42},
            "repetitions": {"blocks": 20, "warmup_runs_per_block": 1,
                            "measured_runs_per_block": 2},
            "randomization": {"seed": 123},
            "analysis": {"reference_configuration": "rayon"},
            "execution": {"settle_seconds": 0},
            "artifacts": {"output_root": "results/old"},
            "configurations": [
                {"id": "rayon", "scheduler": "rayon"},
                {"id": "mpmc-sp0", "scheduler": "threadance", "queue_capacity": 64,
                 "wait_policy": "spin-then-park", "spin_us": 0},
                {"id": "mpmc-ab", "scheduler": "threadance",
                 "wait_policy": "agave-backoff", "queue_capacity": 64},
                {"id": "rtrb-sp0-b1", "scheduler": "threadance-hot-first-rtrb",
                 "hot_first_backlog": 1, "queue_capacity": 64,
                 "wait_policy": "spin-then-park", "spin_us": 0},
                {"id": "rtrb-sp0-b2", "scheduler": "threadance-hot-first-rtrb",
                 "hot_first_backlog": 2, "queue_capacity": 64,
                 "wait_policy": "spin-then-park", "spin_us": 0},
            ],
        }
        burst = {**source,
                 "experiment": {**source["experiment"],
                     "name": "bls-normalized-w2-load05000-bursty-arrivals"},
                 "delivery": {**source["delivery"], "mode": "bursty-arrivals"}}
        perf = {"perf": {"passes": [
            {"name": "cpu-cache", "events": ["cycles:uk", "task-clock"]},
            {"name": "l1-branches", "events": ["branches:uk", "task-clock"]},
        ]}}
        original_loads = tomllib.loads
        with patch("experiments.studies.run_bls_transition_w2_sweep.archived_campaigns",
                   return_value=[(None, source), (None, burst)]), patch(
                       "experiments.studies.run_bls_transition_w2_sweep.tomllib.loads",
                       side_effect=lambda value: original_loads(value) if value.lstrip().startswith("[") else perf
                   ):
            entries, configs = spec()
        self.assertEqual(len(entries), 8)
        self.assertEqual(len(configs), 8)
        self.assertEqual(sum(row["measured_runs"] for row in entries), 3840)
        self.assertEqual({row["load_fraction"] for row in entries}, set(LOADS))
        self.assertTrue(all(row["work_units"] == WORK_UNITS for row in entries))
        self.assertEqual(
            sorted(r["arrival_rate"] for r in entries if r["mode"] == "fixed-arrivals"),
            sorted(r["arrival_rate"] for r in entries if r["mode"] == "bursty-arrivals"),
        )


if __name__ == "__main__":
    unittest.main()
