"""Regression tests for the native Crossbeam wait policy SQLite migration.

Tests an existing populated v9 database, not merely a freshly created schema.
"""
import re
import sqlite3
import unittest

from experiments.common.campaign import ROOT


SCHEMA = ROOT / "harness/sql/schema.sql"
MIGRATION = ROOT / "harness/sql/migrations/010_native_crossbeam_wait.sql"
EXPERIMENT_INSERT = """
INSERT INTO experiment (
    session_id, scheduler, workload, profile_mode, workers,
    queue_capacity, threadance_wait_policy, threadance_spin_us,
    delivery_mode, arrival_seed, work_units, workload_seed,
    warmup_runs, measured_runs, command_line
) VALUES (
    1, 'threadance', 'compute-heavy', 'none', 2,
    8, ?, NULL,
    'all-at-once', 1, 8, 1,
    0, 1, 'test'
)
"""


class NativeCrossbeamWaitStorageTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)

    def seed(self, policy):
        self.db.execute(
            "INSERT INTO benchmark_session (git_commit, git_dirty) VALUES ('abc', 0)"
        )
        self.db.execute(EXPERIMENT_INSERT, (policy,))
        self.db.execute(
            "INSERT INTO run (experiment_id, run_index, completed_work_units, "
            "elapsed_ns, work_units_per_second) VALUES (1, 0, 8, 1000, 8000000.0)"
        )
        self.db.commit()

    def test_fresh_schema_accepts_native_crossbeam_wait(self):
        self.db.executescript(SCHEMA.read_text())
        self.seed("crossbeam-wait")
        self.assertEqual(
            self.db.execute(
                "SELECT threadance_wait_policy FROM benchmark_run_view"
            ).fetchone()[0],
            "crossbeam-wait",
        )

    def test_upgrade_populated_v9_preserves_measurements_and_constraints(self):
        current = SCHEMA.read_text()
        # Derive an equivalent v9 schema without coupling the test to
        # whitespace in the current SQL formatting.
        previous, version_edits = re.subn(
            r",\s*\(10, 'Allow native Crossbeam wait policy'\);",
            ";",
            current,
            count=1,
        )
        previous, policy_edits = re.subn(
            r"(?m)^([ \t]*)'agave-backoff',\s*\n[ \t]*'crossbeam-wait'",
            r"\1'agave-backoff'",
            previous,
            count=1,
        )
        self.assertEqual((version_edits, policy_edits), (1, 1))
        self.assertNotIn("'crossbeam-wait'", previous)

        self.db.executescript(previous)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.seed("agave-backoff")
        self.assertEqual(
            self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 9
        )

        self.db.executescript(MIGRATION.read_text())
        self.assertEqual(
            self.db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 10
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM run").fetchone()[0], 1)
        self.assertEqual(
            self.db.execute("SELECT threadance_wait_policy FROM benchmark_run_view").fetchone()[0],
            "agave-backoff",
        )
        self.assertEqual(self.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

        self.db.execute(EXPERIMENT_INSERT, ("crossbeam-wait",))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(EXPERIMENT_INSERT, ("invalid-policy",))
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM experiment").fetchone()[0], 2
        )


if __name__ == "__main__":
    unittest.main()
