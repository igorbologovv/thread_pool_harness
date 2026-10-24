"""All scheduler families must be represented without invalid queue/wake pairs."""
import unittest

from experiments.common.campaign import ROOT, read_definition
from experiments.common.study import expand_study, read_study, render_toml


class FullPoolStudyTests(unittest.TestCase):
    def test_complete_scheduler_policy_coverage(self):
        spec = read_study(ROOT / "experiments/studies/full_pool_pilot.toml")
        template = read_definition(ROOT / spec["study"]["template"])
        campaigns, skipped = expand_study(spec, template)
        self.assertEqual(len(campaigns), 6)
        self.assertGreater(len(skipped), 0)  # Crossbeam wait cannot drive SPSC segments
        for _, definition, _ in campaigns:
            configs = definition["configurations"]
            schedulers = {x["scheduler"] for x in configs}
            self.assertEqual(schedulers, {
                "rayon", "bevy", "threadance",
                "threadance-hot-first", "threadance-hot-first-rtrb",
            })
            for config in configs:
                scheduler = config["scheduler"]
                policy = config.get("wait_policy")
                if scheduler in ("rayon", "bevy"):
                    self.assertNotIn("spin_us", config)
                if "hot-first" in scheduler:
                    self.assertNotEqual(policy, "crossbeam-wait")
                    self.assertLessEqual(
                        config["hot_first_backlog"] * definition["workload"]["workers"],
                        config["queue_capacity"],
                    )
                if policy != "spin-then-park" and policy is not None:
                    self.assertEqual(config["spin_us"], 0)
            assert "rayon" in {x["id"] for x in configs}
            read_definition_roundtrip = __import__("tomllib").loads(render_toml(definition))
            self.assertEqual(read_definition_roundtrip, definition)


if __name__ == "__main__":
    unittest.main()
