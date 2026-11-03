"""Planning tests for boost cross-over without changing CPU settings."""
import unittest
from experiments.studies.run_bls_w2_boost_control import (
    PHASE_BOOST, definition, mode, run_phase,
)


class BoostControlPlanTests(unittest.TestCase):
    def test_power_order_balanced(self):
        self.assertEqual(PHASE_BOOST, ("on", "off", "off", "on", "on", "off", "off", "on"))
        self.assertEqual([mode(i) for i in range(1, 9)], list(PHASE_BOOST))

    def test_all_phases_have_two_schedulers_and_five_blocks(self):
        total = 0
        for phase in range(1, 9):
            data, _, _, _, n = definition(phase)
            self.assertEqual(data["repetitions"]["blocks"], 5)
            self.assertEqual(len(data["configurations"]), 2)
            self.assertEqual(n, 10)
            self.assertEqual(data["workload"]["workers"], 2)
            self.assertEqual(data["workload"]["work_units"], 256)
            self.assertEqual(data["delivery"]["mode"], "fixed-arrivals")
            self.assertAlmostEqual(data["delivery"]["arrival_rates"][0], 647.0)
            total += run_phase(phase, check=True)["physical_runs"]
        self.assertEqual(total, 320)

    def test_one_block_smoke_is_separate(self):
        data, _, _, _, n = definition(1, smoke=True)
        self.assertEqual(n, 2)
        self.assertEqual(data["repetitions"]["blocks"], 1)
        self.assertIn("smoke", data["experiment"]["name"])


if __name__ == "__main__":
    unittest.main()
