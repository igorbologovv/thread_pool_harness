"""Planning tests; no actual BLS measurement is executed."""
import unittest
from unittest.mock import patch
from experiments.studies.run_bls_thread_perf_diagnostic import spec, IDS, EVENTS


class ThreadPerfDiagnosticTests(unittest.TestCase):
    def test_two_configurations_and_two_perf_passes(self):
        self.assertEqual(set(IDS), {"mpmc-sp0", "rtrb-sp0-b2"})
        self.assertEqual([x["name"] for x in EVENTS], ["cpu-cache", "l1-branches"])
        self.assertTrue(all("task-clock" in p["events"] for p in EVENTS))

    def test_campaign_uses_256_bls_wu_and_20_randomized_blocks(self):
        from experiments.studies.run_bls_transition_w2_sweep import spec as transition
        with patch("experiments.studies.run_bls_thread_perf_diagnostic.parent_spec",
                   side_effect=transition):
            summary, data, text = spec()
        self.assertEqual(summary["invocations"], 40)
        self.assertEqual(summary["physical_runs"], 160)
        self.assertEqual(data["workload"]["workers"], 2)
        self.assertEqual(data["workload"]["work_units"], 256)
        self.assertEqual(data["repetitions"]["blocks"], 20)
        self.assertEqual(data["delivery"]["mode"], "fixed-arrivals")
        self.assertEqual({x["id"] for x in data["configurations"]}, set(IDS))
        self.assertIn("cpu-cache", text)


if __name__ == "__main__":
    unittest.main()
