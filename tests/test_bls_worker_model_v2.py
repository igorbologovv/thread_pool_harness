"""Regression tests: lock 12 models and validate exact five-count acquisition plan."""
import math
import unittest

from experiments.common.campaign import make_plan
from experiments.studies.bls_worker_model_v2 import (
    TRAIN, HOLDOUT, MODELS, load_frozen, fit, predict, leave_one_worker_out
)
from experiments.studies.run_bls_worker_model_v2 import (
    definitions, MODES, CONFIGS, BLOCKS
)


class WorkerModelV2Test(unittest.TestCase):
    def setUp(self):
        self.frozen = load_frozen()
        self.rates = {3:1920.0, 7:4530.0, 9:4960.0, 11:5070.0, 14:5200.0}

    def test_frozen_models_recompute(self):
        self.assertEqual(len(self.frozen["models"]), 12)
        self.assertEqual(tuple(self.frozen["training_workers"]), TRAIN)
        self.assertEqual(tuple(self.frozen["holdout_workers"]), HOLDOUT)
        for key, entry in self.frozen["models"].items():
            points = {int(w):float(v) for w,v in entry["observed_means_ms_per_wu"].items()}
            self.assertEqual(set(points), set(TRAIN))
            model = entry["model"]
            scores = {m:leave_one_worker_out(m, points) for m in MODELS}
            self.assertEqual(min(scores,key=scores.get), model)
            coefs=fit(model,points)
            for w in HOLDOUT:
                self.assertAlmostEqual(predict(model,coefs,w),
                                       entry["holdout_predictions_ms_per_wu"][str(w)],
                                       places=7)

    def test_five_workers_two_modes(self):
        entries=definitions(self.rates)
        self.assertEqual(len(entries),10)
        self.assertEqual({(e["workers"],e["mode"]) for e,_,_ in entries},
                         {(w,mode) for w in HOLDOUT for mode in MODES})
        self.assertEqual(sum(e["invocations"] for e,_,_ in entries),2400)
        for entry,path,serialized in entries:
            import tomllib
            spec=tomllib.loads(serialized)
            w=entry["workers"]
            rate=self.rates[w]/2
            self.assertAlmostEqual(spec["delivery"]["arrival_rates"][0],rate)
            self.assertEqual(spec["workload"]["work_units"],math.ceil(0.2*rate)+1)
            self.assertEqual(spec["workload"]["dataset_work_units"],2500)
            self.assertEqual(spec["repetitions"]["warmup_runs_per_block"],4)
            self.assertEqual(spec["repetitions"]["measured_runs_per_block"],2)
            self.assertEqual(spec["repetitions"]["blocks"],BLOCKS)
            self.assertEqual(len(make_plan(spec,None)),240)
            self.assertEqual({cfg["id"] for cfg in spec["configurations"]},CONFIGS)
            self.assertEqual([p["name"] for p in spec["perf"]["passes"]],
                             ["cpu-cache","l1-branches"])
            self.assertGreaterEqual((spec["workload"]["work_units"]-1)/rate,0.2)

    def test_missing_or_invalid_calibration_is_rejected(self):
        with self.assertRaises(ValueError):
            definitions({w:r for w,r in self.rates.items() if w!=11})
        with self.assertRaises(ValueError):
            definitions(dict(self.rates,**{} ) | {14:float("nan")})
        with self.assertRaises(ValueError):
            definitions(dict(self.rates,**{} ) | {14:1000000.0})

    def test_reproducible_paced_definitions(self):
        first=[(e["name"],txt) for e,_,txt in definitions(self.rates)]
        second=[(e["name"],txt) for e,_,txt in definitions(self.rates)]
        self.assertEqual(first,second)


if __name__=="__main__":
    unittest.main()
