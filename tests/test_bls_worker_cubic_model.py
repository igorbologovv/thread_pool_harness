"""Pure mathematical tests: no benchmark host required."""
import unittest

from experiments.studies.bls_worker_cubic_model import (
    HOLDOUT_WORKERS, TRAIN_WORKERS, evaluate_holdouts, fit_cubic, predict,
)


class WorkerCubicModelTest(unittest.TestCase):
    def test_interpolates_only_training_nodes(self):
        target = lambda w: 1.4 + 0.003*w + 0.00015*w*w - 0.000002*w**3
        coeffs = fit_cubic({w: target(w) for w in TRAIN_WORKERS})
        for workers in (*TRAIN_WORKERS, *HOLDOUT_WORKERS):
            self.assertAlmostEqual(predict(coeffs, workers), target(workers), places=10)

    def test_rejects_holdouts_as_training_inputs(self):
        with self.assertRaises(ValueError):
            fit_cubic({w: 1.0 for w in (*TRAIN_WORKERS, 6)})

    def test_holdouts_are_evaluated_after_fit(self):
        base = {w: [1.5 + w/100 + (j%5-2)*0.001 for j in range(40)]
                for w in (*TRAIN_WORKERS, *HOLDOUT_WORKERS)}
        report = evaluate_holdouts(base, replicates=200)
        self.assertEqual([r['workers'] for r in report['holdouts']], list(HOLDOUT_WORKERS))
        self.assertTrue(all(abs(r['error_ms_per_wu']) < 1e-8 for r in report['holdouts']))


if __name__ == '__main__':
    unittest.main()