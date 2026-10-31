"""Guard the Bevy-inclusive finite-batch saturation experiment design."""
import unittest
from pathlib import Path

from experiments.common.campaign import ROOT
from experiments.common.study import read_study, read_definition, root_path, expand_study


class SaturatedThroughputStudyTests(unittest.TestCase):
    def test_bls_saturation_matrix(self):
        spec = read_study(ROOT / "experiments/studies/bls_saturated_throughput.toml")
        template = read_definition(root_path(spec["study"]["template"]))
        campaigns, skipped = expand_study(spec, template)
        self.assertFalse(skipped)
        self.assertEqual(len(campaigns), 4)
        for filename, data, stats in campaigns:
            self.assertEqual(data["delivery"]["mode"], "all-at-once")
            self.assertEqual(data["workload"]["work_units"], 2500)
            self.assertEqual(data["workload"]["dataset_work_units"], 2500)
            self.assertEqual(data["experiment"]["profile"], "standard")
            self.assertEqual(
                {x["id"] for x in data["configurations"]},
                {"rayon", "bevy", "mpmc-sp0", "mpmc-ab", "rtrb-sp0-b1", "rtrb-sp0-b2"},
            )
            self.assertEqual(stats["invocations"], 180)
            self.assertEqual(stats["measured_runs"], 360)


if __name__ == "__main__":
    unittest.main()
