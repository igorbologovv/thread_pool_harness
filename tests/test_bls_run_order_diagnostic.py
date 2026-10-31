"""Check the narrow study matrix and deterministic run-order summaries."""
import csv
import tempfile
import unittest
from pathlib import Path

from experiments.common.campaign import ROOT, read_definition
from experiments.common.study import expand_study, read_study, root_path
from experiments.studies.analyze_bls_run_order import analyze


class BlsRunOrderDiagnosticTests(unittest.TestCase):
    def test_design(self):
        spec = read_study(ROOT / "experiments/studies/bls_run_order_diagnostic.toml")
        template = read_definition(root_path(spec["study"]["template"]))
        campaigns, skipped = expand_study(spec, template)
        self.assertEqual(len(campaigns), 3)
        self.assertEqual(skipped, [])
        self.assertEqual({d["workload"]["workers"] for _, d, _ in campaigns}, {4, 8, 16})
        self.assertEqual(sum(stats["invocations"] for _, _, stats in campaigns), 60)
        self.assertEqual(sum(stats["measured_runs"] for _, _, stats in campaigns), 480)
        for _, data, stats in campaigns:
            self.assertEqual(data["configurations"], [{"id": "rayon", "scheduler": "rayon"}])
            self.assertEqual(data["experiment"]["profile"], "standard")
            self.assertEqual(data["repetitions"]["blocks"], 20)
            self.assertEqual(data["repetitions"]["warmup_runs_per_block"], 1)
            self.assertEqual(data["repetitions"]["measured_runs_per_block"], 8)
            self.assertEqual(stats["invocations"], 20)
            self.assertEqual(stats["measured_runs"], 160)

    def test_postprocess_preserves_positions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for workers in (4, 8, 16):
                folder = root / f"bls-run-order-diagnostic-w{workers}-n2500-g1-all-at-once-example"
                folder.mkdir()
                with (folder / "analysis_raw_runs.csv").open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=[
                        "block", "run_index", "configuration", "perf_pass",
                        "throughput", "elapsed_ms", "task_clock_per_wu",
                        "cycles_per_wu", "instructions_per_wu",
                        "branch_misses_per_wu",
                    ])
                    writer.writeheader()
                    for block in range(1, 21):
                        for index in range(8):
                            writer.writerow(dict(block=block, run_index=index,
                                configuration="rayon", perf_pass="standard",
                                throughput=1000 + workers*100 + index*10 + block,
                                elapsed_ms=100-index, task_clock_per_wu=10-index*.1,
                                cycles_per_wu=100-index, instructions_per_wu=100,
                                branch_misses_per_wu=5-index*.1))
            summary, deltas = analyze(root, replicates=100)
            self.assertEqual(len(summary), 3 * 8 * 6)
            self.assertEqual(len(deltas), 3 * 7 * 6)
            self.assertTrue((root / "run_position_report.md").exists())
            self.assertTrue((root / "run_position_throughput.svg").exists())
            self.assertTrue(all(row["positive_pairs"]==20 for row in deltas if row["metric"]=="throughput"))

    def test_rejects_missing_repetition(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "Expected 3 worker campaigns"):
                analyze(Path(temp), replicates=100)


if __name__ == "__main__":
    unittest.main()
