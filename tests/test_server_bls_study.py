"""Pure planning checks for the server BLS study (no benchmarks executed)."""
import math
import tomllib
import unittest

from experiments import server_bls_study as study
from experiments.common.campaign import read_definition


class ServerStudyPlanTests(unittest.TestCase):
    def test_grid(self):
        self.assertEqual(study.WORKERS, (2, 4, 8, 12, 16, 20, 24))
        self.assertEqual(len(study.LOADS), 15)
        self.assertEqual(study.LOADS[0], 0.25)
        self.assertEqual(study.LOADS[-1], 0.95)
        self.assertTrue(all(
            abs(b - a - 0.05) < 1e-9
            for a, b in zip(study.LOADS, study.LOADS[1:])
        ))

    def test_work_unit_sizing(self):
        for workers in study.WORKERS:
            n, rate = study.work_units_for(workers, 150.0)
            self.assertGreaterEqual(n, 1000)
            self.assertGreaterEqual((n - 1) / rate, 0.2 * 1.2)

    def test_generated_matrix(self):
        files, summary = study.definitions(150.0)
        self.assertEqual(len(files), 21)
        self.assertEqual(summary["conditions"], 1302)
        self.assertEqual(summary["invocations"], 26040)
        self.assertEqual(summary["measured_runs"], 52080)
        for path, data in files:
            self.assertEqual(data["repetitions"]["warmup_runs_per_block"], 4)
            self.assertEqual(data["repetitions"]["blocks"], 20)
            self.assertEqual(data["repetitions"]["measured_runs_per_block"], 2)
            self.assertEqual(len(data["configurations"]), 6)
            self.assertEqual(data["workload"]["dataset_work_units"],
                             summary["dataset_work_units"])
            self.assertLessEqual(data["workload"]["work_units"],
                                 data["workload"]["dataset_work_units"])
            self.assertEqual(data["workload"]["calibration_work_units"], 200)
            self.assertEqual(tomllib.loads(study.render_toml(data)), data)
            mode = data["delivery"]["mode"]
            if mode == "all-at-once":
                self.assertNotIn("rho", data["delivery"])
            else:
                self.assertEqual(data["delivery"]["rho"], list(study.LOADS))
            self.assertEqual(path.suffix, ".toml")

    def test_excessive_dataset_is_refused(self):
        with self.assertRaises(RuntimeError):
            study.definitions(1.0)


if __name__ == "__main__":
    unittest.main()
