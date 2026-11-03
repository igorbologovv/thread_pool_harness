"""Plan-only tests: never run benchmark processes."""
import tomllib
import unittest
from unittest.mock import patch

from experiments.studies.run_bls_transition_w2_lowload import (
    BLOCKS,
    LOADS,
    MODE,
    STUDY,
    WORK_UNITS,
    execute,
    spec,
)


def archived_50_percent_source():
    return {
        "experiment": {
            "name": "bls-normalized-w2-load05000-fixed-arrivals",
            "workload": "bls-aggregate-verify",
            "profile": "standard",
        },
        "workload": {
            "workers": 2, "work_units": 130, "dataset_work_units": 2500,
            "validators": 2000, "signers_per_certificate": 1600,
            "certificates_per_work_unit": 1, "workload_seed": 1,
        },
        "delivery": {
            "mode": MODE, "arrival_rates": [647.0], "arrival_seed_base": 42,
        },
        "repetitions": {
            "blocks": 20, "warmup_runs_per_block": 1,
            "measured_runs_per_block": 2,
        },
        "randomization": {"seed": 123},
        "analysis": {"reference_configuration": "rayon"},
        "execution": {"settle_seconds": 0},
        "artifacts": {"output_root": "results/old"},
        "configurations": [
            {"id": "rayon", "scheduler": "rayon"},
            {"id": "mpmc-sp0", "scheduler": "threadance",
             "queue_capacity": 64, "wait_policy": "spin-then-park",
             "spin_us": 0},
            {"id": "mpmc-ab", "scheduler": "threadance",
             "queue_capacity": 64, "wait_policy": "agave-backoff"},
            {"id": "rtrb-sp0-b1", "scheduler": "threadance-hot-first-rtrb",
             "queue_capacity": 64, "hot_first_backlog": 1,
             "wait_policy": "spin-then-park", "spin_us": 0},
            {"id": "rtrb-sp0-b2", "scheduler": "threadance-hot-first-rtrb",
             "queue_capacity": 64, "hot_first_backlog": 2,
             "wait_policy": "spin-then-park", "spin_us": 0},
        ],
    }


PERF_TOML = """
[[perf.passes]]
name = "cpu-cache"
events = ["cycles:uk", "task-clock"]

[[perf.passes]]
name = "l1-branches"
events = ["branches:uk", "task-clock"]
"""


class LowLoadSweepTests(unittest.TestCase):
    def setUp(self):
        self.source_patch = patch(
            "experiments.studies.run_bls_transition_w2_lowload.archived_campaigns",
            return_value=[(None, archived_50_percent_source())],
        )
        self.perf_patch = patch(
            "experiments.studies.run_bls_transition_w2_lowload.PERF_SPEC"
        )
        self.source_patch.start()
        self.mock_perf_path = self.perf_patch.start()
        self.mock_perf_path.read_text.return_value = PERF_TOML
        self.addCleanup(self.source_patch.stop)
        self.addCleanup(self.perf_patch.stop)

    def test_six_fixed_arrival_cells_with_original_methodology(self):
        entries, definitions = spec()
        self.assertEqual(len(entries), 6)
        self.assertEqual(len(definitions), 6)
        self.assertEqual([e["load_fraction"] for e in entries], list(LOADS))
        self.assertEqual(
            [e["arrival_rate"] for e in entries],
            [323.5, 388.2, 452.9, 517.6, 582.3, 647.0],
        )
        self.assertTrue(all(e["mode"] == MODE for e in entries))
        self.assertTrue(all(e["invocations"] == BLOCKS * 6 for e in entries))
        self.assertTrue(all(e["measured_runs"] == BLOCKS * 6 * 4 for e in entries))
        for path, serialized in definitions.items():
            data = tomllib.loads(serialized)
            self.assertTrue(path.name.startswith(STUDY))
            self.assertEqual(data["workload"]["workers"], 2)
            self.assertEqual(data["workload"]["work_units"], WORK_UNITS)
            self.assertEqual(data["repetitions"]["warmup_runs_per_block"], 4)
            self.assertEqual(data["repetitions"]["measured_runs_per_block"], 2)
            self.assertEqual(data["delivery"]["mode"], MODE)
            self.assertEqual([p["name"] for p in data["perf"]["passes"]],
                             ["cpu-cache", "l1-branches"])
            self.assertEqual(
                {c["id"] for c in data["configurations"]},
                {"rayon", "bevy", "mpmc-sp0", "mpmc-ab",
                 "rtrb-sp0-b1", "rtrb-sp0-b2"},
            )
            self.assertEqual(
                data["artifacts"]["output_root"],
                "results/studies/bls-transition-w2-lowload/campaigns",
            )

    def test_check_only_plans_no_execution(self):
        with patch("experiments.studies.run_bls_transition_w2_lowload.subprocess.run",
                   side_effect=AssertionError("check executed a subprocess")):
            summary = execute(check=True)
        self.assertEqual(summary["study"], STUDY)
        self.assertEqual(summary["campaign_count"], 6)
        self.assertEqual(summary["process_invocations"], 720)
        self.assertEqual(summary["measured_physical_runs"], 2880)
        self.assertEqual(summary["discarded_warmup_runs"], 2880)

    def test_requires_unique_archived_50_percent_source(self):
        with patch(
            "experiments.studies.run_bls_transition_w2_lowload.archived_campaigns",
            return_value=[],
        ):
            with self.assertRaisesRegex(ValueError, "Expected one archived"):
                spec()


if __name__ == "__main__":
    unittest.main()
