"""Warmup variants are randomized within blocks and forwarded to the harness."""
import csv
import tempfile
import unittest
from pathlib import Path
from experiments.common.campaign import ROOT, command, make_plan, read_definition
from experiments.common.study import read_study, expand_study, root_path
from experiments.studies.analyze_bls_warmup_sweep import analyze


class BlsWarmupSweepTests(unittest.TestCase):
    def test_study_and_per_condition_warmups(self):
        spec=read_study(ROOT/"experiments/studies/bls_warmup_sweep.toml")
        template=read_definition(root_path(spec["study"]["template"]))
        campaigns, skipped=expand_study(spec,template)
        self.assertFalse(skipped)
        self.assertEqual(len(campaigns),3)
        self.assertEqual({c["workload"]["workers"] for _,c,_ in campaigns},{4,8,16})
        self.assertEqual(sum(s["invocations"] for _,_,s in campaigns),240)
        self.assertEqual(sum(s["measured_runs"] for _,_,s in campaigns),1920)
        self.assertEqual(sum(s["warmup_runs"] for _,_,s in campaigns),900)
        for _,data,stats in campaigns:
            plan=make_plan(data,None)
            self.assertEqual(len(plan),80)
            self.assertEqual(stats["warmup_runs"],300)
            for block in range(1,21):
                rows=[r for r in plan if r["block"]==block]
                self.assertEqual({r["warmup_runs"] for r in rows},{1,2,4,8})
                self.assertEqual({r["configuration"] for r in rows},
                                 {"rayon-w1","rayon-w2","rayon-w4","rayon-w8"})
            for row in plan:
                argv=command(data,row,Path("/tmp/unused-benchmark.sqlite3"),"test")
                self.assertEqual(argv[argv.index("--warmup")+1],str(row["warmup_runs"]))

    def test_analyzer_keeps_positions_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for workers in (4,8,16):
                folder=root/f"bls-warmup-sweep-w{workers}-n2500-g1-all-at-once-123"
                folder.mkdir()
                with (folder/"analysis_raw_runs.csv").open("w",newline="") as stream:
                    names=("configuration","block","run_index","perf_pass","throughput",
                           "elapsed_ms","cycles_per_wu","instructions_per_wu",
                           "task_clock_per_wu","branch_misses_per_wu")
                    writer=csv.DictWriter(stream,fieldnames=names)
                    writer.writeheader()
                    for block in range(1,21):
                        for warmup in (1,2,4,8):
                            for index in range(8):
                                writer.writerow(dict(configuration=f"rayon-w{warmup}",block=block,
                                    run_index=index,perf_pass="standard",
                                    throughput=1000+workers+block+warmup+index*5,
                                    elapsed_ms=200-index,
                                    cycles_per_wu=100-index,
                                    instructions_per_wu=400,
                                    task_clock_per_wu=10-index*.1,
                                    branch_misses_per_wu=8-index*.1))
            summaries,paired,between=analyze(root,replicates=100)
            self.assertEqual(len(summaries),3*4*8*6)
            self.assertEqual(len(paired),3*4*7*6)
            self.assertEqual(len(between),3*3*6)
            self.assertTrue((root/"warmup_report.md").is_file())

if __name__=="__main__":
    unittest.main()
