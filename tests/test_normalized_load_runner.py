"""Smoke-test the normalized-load runner without executing any benchmarks."""
import unittest
from unittest.mock import patch

from experiments.studies.run_bls_normalized_load import run


class NormalizedLoadRunnerTests(unittest.TestCase):
    def test_dry_run_has_correct_count_and_never_collects(self):
        with patch("experiments.studies.run_bls_normalized_load.subprocess.run") as execute:
            report = run(check=True, reference=None, blocks=20)
            self.assertEqual(len(report["campaigns"]), 48)
            self.assertEqual(report["total_invocations"], 4800)
            self.assertEqual(report["total_measured_runs"], 9600)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
