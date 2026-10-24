"""Study runner preflight and resume selection tests (no real benchmarks)."""
import csv
import tempfile
import unittest
from pathlib import Path

from experiments.common.study_run import completed, existing_campaign
from experiments.common.campaign import ROOT
from experiments.common.study import read_study, expand_study
from experiments.common.campaign import read_definition


class StudyRunnerTests(unittest.TestCase):
    def test_end_to_end_pilot_covers_every_scheduler(self):
        spec = read_study(ROOT / "experiments/studies/bls_end_to_end.toml")
        definition = read_definition(ROOT / spec["study"]["template"])
        campaigns, skipped = expand_study(spec, definition)
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(campaigns[0][2]["invocations"], 18)
        self.assertEqual(campaigns[0][2]["measured_runs"], 36)
        self.assertEqual({
            c["scheduler"] for c in campaigns[0][1]["configurations"]
        }, {
            "rayon", "bevy", "threadance",
            "threadance-hot-first", "threadance-hot-first-rtrb",
        })
        self.assertTrue(skipped)

    def test_matching_campaign_is_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            definition = root / "definition.toml"
            definition.write_text("workload = 'bls'\n")
            prior = root / "study-name-20261024-130000"
            prior.mkdir()
            (prior / "experiment.toml").write_bytes(definition.read_bytes())
            self.assertEqual(existing_campaign("study-name", definition, root), prior)

    def test_different_definitions_are_not_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            definition = root / "definition.toml"
            definition.write_text("version = 2\n")
            prior = root / "study-name-20261024-130000"
            prior.mkdir()
            (prior / "experiment.toml").write_text("version = 1\n")
            self.assertIsNone(existing_campaign("study-name", definition, root))

    def test_completed_counts_manifest_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.assertFalse(completed(folder, 2))
            with (folder / "manifest.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["experiment_id"])
                writer.writerow([1])
                writer.writerow([2])
            self.assertTrue(completed(folder, 2))
            self.assertFalse(completed(folder, 3))


if __name__ == "__main__":
    unittest.main()
