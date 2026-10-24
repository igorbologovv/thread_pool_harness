"""Offline report statistics and SVG smoke checks."""
import math
import unittest

from experiments.common.study_report import plot, summarize


class StudyReportTests(unittest.TestCase):
    def test_run_distribution_retains_outliers(self):
        raw = [
            {
                "configuration": "test", "arrival_rate": "900.0",
                "perf_pass": "standard", "elapsed_ms": str(value),
            }
            for value in (1, 2, 2, 3, 50)
        ]
        rows = summarize(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["n"], 5)
        self.assertEqual(rows[0]["mean"], 11.6)
        self.assertGreater(rows[0]["sample_variance"], 0)
        self.assertEqual(rows[0]["iqr_fence_count"], 1)

    def test_svg_uses_recorded_interval(self):
        effect = {
            "configuration": "threadance", "reference": "rayon",
            "arrival_rate": "900.0", "metric": "task_clock_per_wu",
            "relative_mean": "-0.1", "relative_ci95_low": "-0.2",
            "relative_ci95_high": "0.02",
        }
        chart = plot([effect], "task_clock_per_wu")
        self.assertIn("<svg", chart)
        self.assertIn("threadance", chart)
        self.assertEqual(plot([effect], "elapsed_ms"), "")


if __name__ == "__main__":
    unittest.main()
