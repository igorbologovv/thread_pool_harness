"""No-benchmark checks for the seven-worker-count holdout experiment."""
import unittest

from experiments.common.campaign import make_plan
from experiments.studies.run_bls_worker_cubic_holdout import (
    BLOCKS, MODES, SCHEDULERS, WORKERS, NEW_WORKERS, MIN_WINDOW_SECONDS, definitions,
)
import tomllib


class WorkerCubicHoldoutStudyTest(unittest.TestCase):
    def setUp(self):
        self.reference = {2: 1294.0, 4: 2583.0, 6: 3670.0, 8: 4792.0,
                          10: 5050.0, 12: 5150.0, 16: 5224.0}

    def test_all_seven_worker_counts_and_modes(self):
        entries = definitions(self.reference)
        self.assertEqual(len(entries), 6)
        self.assertEqual({(e['workers'], e['mode']) for e, _, _ in entries},
                         {(w, m) for w in NEW_WORKERS for m in MODES})
        self.assertEqual(sum(e['invocations'] for e, _, _ in entries), 1440)
        for entry, _, serialized in entries:
            data = tomllib.loads(serialized)
            self.assertEqual(data['workload']['workers'], entry['workers'])
            self.assertEqual(data['workload']['work_units'],
                             __import__('math').ceil(MIN_WINDOW_SECONDS * entry['offered_rate']) + 1)
            self.assertEqual(data['workload']['dataset_work_units'], 2500)
            self.assertEqual(data['experiment']['profile'], 'deep')
            self.assertEqual(data['repetitions']['blocks'], BLOCKS)
            self.assertEqual(data['repetitions']['warmup_runs_per_block'], 4)
            self.assertEqual(data['repetitions']['measured_runs_per_block'], 2)
            self.assertEqual({c['id'] for c in data['configurations']}, SCHEDULERS)
            self.assertEqual([p['name'] for p in data['perf']['passes']],
                             ['cpu-cache', 'l1-branches'])
            self.assertAlmostEqual(data['delivery']['arrival_rates'][0],
                                   self.reference[entry['workers']] / 2)
            self.assertGreaterEqual((data['workload']['work_units']-1)/entry['offered_rate'], MIN_WINDOW_SECONDS)
            self.assertEqual(len(make_plan(data, None)), 240)

    def test_incomplete_and_implausible_rates_rejected(self):
        missing = {w: v for w, v in self.reference.items() if w != 12}
        with self.assertRaises(ValueError):
            definitions(missing)
        overfast = dict(self.reference)
        overfast[12] = 30000.0
        with self.assertRaises(ValueError):
            definitions(overfast)

    def test_plan_is_repeatable(self):
        first = [(e['name'], text) for e, _, text in definitions(self.reference)]
        second = [(e['name'], text) for e, _, text in definitions(self.reference)]
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()