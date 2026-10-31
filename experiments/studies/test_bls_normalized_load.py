"""Pure planning tests; no Rust, perf or dataset generation required."""
import unittest

from experiments.studies.bls_normalized_load import (
    DATASET_WORK_UNITS, LOAD_FRACTIONS, plan, configurations,
)


class NormalizedLoadStudyTests(unittest.TestCase):
    def test_grid_and_identical_load_levels(self):
        rows = plan()
        self.assertEqual(len(rows), 4 * len(LOAD_FRACTIONS) * 2)
        for worker in (2, 4, 8, 16):
            self.assertEqual(
                {r["load_fraction"] for r in rows if r["workers"] == worker},
                set(LOAD_FRACTIONS),
            )

    def test_window_dataset_and_matched_modes(self):
        rows = plan()
        for row in rows:
            self.assertGreaterEqual(row["scheduled_window_seconds"], 0.2 - 1e-12)
            self.assertLessEqual(row["work_units"], DATASET_WORK_UNITS)
        for a, b in zip(rows[::2], rows[1::2]):
            self.assertEqual(a["work_units"], b["work_units"])
            self.assertEqual(a["arrival_rate"], b["arrival_rate"])

    def test_highest_load(self):
        row = next(r for r in plan() if r["workers"] == 16
                   and r["load_fraction"] == 2.0)
        self.assertEqual(row["work_units"], 2091)

    def test_reject_overlarge_capacity(self):
        with self.assertRaisesRegex(ValueError, "dataset too small"):
            plan({2: 1294, 4: 2583, 8: 4792, 16: 10000})

    def test_no_duplicate_configuration_ids(self):
        ids = [r["id"] for r in configurations()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 5)


if __name__ == "__main__":
    unittest.main()
