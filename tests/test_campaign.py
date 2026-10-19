"""Smoke tests for the shared experiment definition and randomized blocks."""
import unittest
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, command


class CampaignTests(unittest.TestCase):
    def test_builtin_experiments_parse_and_randomize(self):
        for name in ("fixed_wait", "queue_topology", "compute_heavy", "max_throughput"):
            with self.subTest(name=name):
                data = read_definition(ROOT / "experiments" / name / "experiment.toml")
                rate = 100.0 if data["delivery"].get("rho") else None
                rows = make_plan(data, rate)
                blocks = int(data["repetitions"]["blocks"])
                self.assertEqual(len(rows) % blocks, 0)
                conditions = len(rows) // blocks
                keys = set()
                for block in range(1, blocks + 1):
                    group = [row for row in rows if row["block"] == block]
                    self.assertEqual(len(group), conditions)
                    self.assertEqual({r["order_in_block"] for r in group},
                                     set(range(1, conditions + 1)))
                    for row in group:
                        keys.add((row["configuration"], row["spin_us"], row["arrival_rate"]))
                self.assertEqual(len(keys), conditions)
                self.assertEqual(rows, make_plan(data, rate))

    def test_commands_use_workload_specific_parameters(self):
        for name in ("compute_heavy", "queue_topology"):
            with self.subTest(name=name):
                data = read_definition(ROOT / "experiments" / name / "experiment.toml")
                rows = make_plan(data, 100.0 if data["delivery"].get("rho") else None)
                argv = command(data, rows[0], Path("/tmp/benchmarks.sqlite3"), "test")
                self.assertIn("--workload", argv)
                self.assertIn("--runs", argv)
                if name == "compute_heavy":
                    self.assertIn("--operations-per-work-unit", argv)
                    self.assertNotIn("--validators", argv)
                else:
                    self.assertIn("--validators", argv)
                    self.assertNotIn("--operations-per-work-unit", argv)


if __name__ == "__main__":
    unittest.main()
