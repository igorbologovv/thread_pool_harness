"""Guard the independent six-scheduler saturation rerun matrix."""
import unittest
from experiments.common.campaign import ROOT, read_definition
from experiments.common.study import expand_study, read_study, root_path


class SaturationRecheckTests(unittest.TestCase):
    def test_matrix(self):
        spec=read_study(ROOT/"experiments/studies/bls_saturation_recheck.toml")
        template=read_definition(root_path(spec["study"]["template"]))
        campaigns,skipped=expand_study(spec,template)
        self.assertEqual(skipped,[])
        self.assertEqual(len(campaigns),4)
        self.assertEqual({data["workload"]["workers"] for _,data,_ in campaigns},
                         {2,4,8,16})
        self.assertEqual(sum(s["invocations"] for _,_,s in campaigns),720)
        self.assertEqual(sum(s["measured_runs"] for _,_,s in campaigns),2160)
        self.assertEqual(sum(s["warmup_runs"] for _,_,s in campaigns),2880)
        for _,data,stats in campaigns:
            self.assertEqual(len(data["configurations"]),6)
            self.assertEqual({c["scheduler"] for c in data["configurations"]},
                {"rayon","bevy","threadance","threadance-hot-first-rtrb"})
            self.assertEqual(data["repetitions"]["warmup_runs_per_block"],4)
            self.assertEqual(data["repetitions"]["measured_runs_per_block"],3)
            self.assertEqual(data["experiment"]["profile"],"standard")
            self.assertEqual(data["workload"]["work_units"],2500)
            self.assertEqual(data["workload"]["dataset_work_units"],2500)
            self.assertEqual(stats["invocations"],180)


if __name__=="__main__":
    unittest.main()
