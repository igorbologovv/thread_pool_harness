"""Guard the Bevy-inclusive finite-batch saturation experiment design."""
import unittest

from experiments.common.campaign import ROOT
from experiments.common.study import expand_study, read_study, root_path
from experiments.common.campaign import read_definition


class SaturatedThroughputStudyTests(unittest.TestCase):
    def test_bls_saturation_matrix(self):
        spec = read_study(ROOT / "experiments/studies/bls_saturated_throughput.toml")
        template = read_definition(root_path(spec["study"]["template"]))
        campaigns, skipped = expand_study(spec, template)
        self.assertFalse(skipped)
        self.assertEqual(len(campaigns), 4)
        self.assertEqual(
            {data["workload"]["workers"] for _, data, _ in campaigns},
            {2, 4, 8, 16},
        )

        # The generic study expander intentionally assigns canonical IDs
        # encoding topology, queue capacity, wait policy and backlog.
        expected_ids = {
            "rayon",
            "bevy",
            "threadance__spin-then-park__q64__s0",
            "threadance__agave-backoff__q64",
            "threadance-hot-first-rtrb__spin-then-park__q64__b1__s0",
            "threadance-hot-first-rtrb__spin-then-park__q64__b2__s0",
        }
        for _, data, stats in campaigns:
            self.assertEqual(data["delivery"]["mode"], "all-at-once")
            self.assertEqual(data["workload"]["work_units"], 2500)
            self.assertEqual(data["workload"]["dataset_work_units"], 2500)
            self.assertEqual(data["experiment"]["profile"], "standard")
            self.assertEqual(
                {config["id"] for config in data["configurations"]},
                expected_ids,
            )
            self.assertEqual(stats["invocations"], 180)
            self.assertEqual(stats["measured_runs"], 360)
            self.assertEqual(stats["warmup_runs"], 180)


if __name__ == "__main__":
    unittest.main()
