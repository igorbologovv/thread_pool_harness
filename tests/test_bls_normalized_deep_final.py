"""No-benchmark checks for the final two-pass BLS study."""
import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from experiments.studies.run_bls_normalized_deep_final import (
    FINAL_CONFIGS, BLOCKS, WARMUPS, REPETITIONS_PER_PASS,
    adjusted, archived_campaigns, plan, run,
)
from experiments.common.campaign import read_definition, make_plan
from experiments.common.study import render_toml


class FinalBlsPmuStudyTests(unittest.TestCase):
    def test_adjustment_keeps_rate_dataset_and_original_scheduler_config(self):
        data = {
            "experiment": {"name": "bls-normalized-w2-load02500-fixed-arrivals",
                           "workload": "bls-aggregate-verify", "profile": "standard"},
            "workload": {"workers": 2, "work_units": 100, "dataset_work_units": 2500,
                         "validators": 2000, "signers_per_certificate": 1600,
                         "certificates_per_work_unit": 1, "workload_seed": 1},
            "delivery": {"mode": "fixed-arrivals", "arrival_rates": [314.5],
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
                 "spin_us": 0},
                {"id": "mpmc-ab", "scheduler": "threadance",
                 "wait_policy": "agave-backoff", "queue_capacity": 64},
                {"id": "rtrb-sp0-b1", "scheduler": "threadance-hot-first-rtrb",
                 "hot_first_backlog": 1, "queue_capacity": 64},
                {"id": "rtrb-sp0-b2", "scheduler": "threadance-hot-first-rtrb",
                 "hot_first_backlog": 2, "queue_capacity": 64},
            ],
        }
        old = copy.deepcopy(data)
        passes = [
            {"name": "cpu-cache", "events": ["cycles:uk", "task-clock"]},
            {"name": "l1-branches", "events": ["branches:uk", "task-clock"]},
        ]
        new = adjusted(data, passes)
        self.assertEqual(data, old)
        self.assertEqual(new["workload"], data["workload"])
        self.assertEqual(new["delivery"], data["delivery"])
        self.assertEqual({c["id"] for c in new["configurations"]}, FINAL_CONFIGS)
        self.assertEqual(new["experiment"]["profile"], "deep")
        self.assertEqual(new["repetitions"]["warmup_runs_per_block"], WARMUPS)
        self.assertEqual(new["repetitions"]["measured_runs_per_block"], REPETITIONS_PER_PASS)
        self.assertEqual(len(make_plan(new, None)), BLOCKS * 6)
        self.assertEqual(new["perf"]["passes"], passes)

    def test_check_never_prepares_or_collects(self):
        fake_report = {
            "totals": {"campaigns": 48, "invocations": 5760, "warmups": 23040,
                       "measured": 23040},
            "passes": [{"name": "cpu-cache"}, {"name": "l1-branches"}],
            "campaigns": [None] * 48,
        }
        with patch("experiments.studies.run_bls_normalized_deep_final.plan",
                   return_value=(fake_report, {})), patch(
                       "experiments.studies.run_bls_normalized_deep_final.subprocess.run"
                   ) as execute:
            result = run(check=True)
            self.assertEqual(result["totals"]["measured"], 23040)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
