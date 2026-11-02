"""Fast planning tests; does not start BLS or perf."""
import unittest
from pathlib import Path
from experiments.studies.run_bls_thread_perf_diagnostic import CONFIGS, EVENTS, command


class ThreadPerfDiagnosticTests(unittest.TestCase):
    def test_two_scheduler_configurations(self):
        self.assertEqual(set(CONFIGS), {"mpmc-sp0", "rtrb-b2"})
        for name in CONFIGS:
            argv = command(name, 40, Path("/tmp/thread-perf-test.sqlite3"))
            self.assertIn("--fixed-arrivals", argv)
            self.assertEqual(argv[argv.index("--workers") + 1], "2")
            self.assertEqual(argv[argv.index("--work-units") + 1], "256")
            self.assertEqual(argv[argv.index("--arrival-rate") + 1], "647.0")
            self.assertEqual(argv[argv.index("--warmup") + 1], "4")
            self.assertEqual(argv[argv.index("--profile") + 1], "none")
            self.assertEqual(argv[argv.index("--runs") + 1], "40")
        self.assertIn("--hot-first-backlog", CONFIGS["rtrb-b2"])
        self.assertIn("--threadance-spin-us", CONFIGS["mpmc-sp0"])

    def test_events_include_time_and_cycles(self):
        self.assertIn("task-clock", EVENTS)
        self.assertIn("cycles:uk", EVENTS)
        self.assertIn("instructions:uk", EVENTS)
        self.assertIn("context-switches", EVENTS)


if __name__ == "__main__":
    unittest.main()
