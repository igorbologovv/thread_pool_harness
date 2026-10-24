"""Legacy perf event names with :u must remain analyzable."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from experiments.common.campaign import verify_campaign_database
from experiments.common.analysis import EVENTS


class LegacyPerfNameTests(unittest.TestCase):
    def test_preserved_sqlite_accepts_user_space_task_clock(self):
        from experiments.common.campaign import ROOT, read_definition
        definition = read_definition(
            ROOT / "experiments/queue_topology/experiment.toml"
        )
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
                db.executescript("""
                    CREATE TABLE experiment (id INTEGER PRIMARY KEY, scheduler TEXT);
                    CREATE TABLE run (
                        id INTEGER PRIMARY KEY, experiment_id INTEGER,
                        success INTEGER, completed_work_units INTEGER
                    );
                    CREATE TABLE perf_metric (
                        run_id INTEGER, event_name TEXT, counter_value REAL,
                        status TEXT
                    );
                    INSERT INTO experiment VALUES (1, 'rayon');
                    INSERT INTO run VALUES (1, 1, 1, 1000);
                    INSERT INTO run VALUES (2, 1, 1, 1000);
                    INSERT INTO perf_metric VALUES
                        (1, 'task-clock:u', 100.0, 'ok'),
                        (2, 'task-clock:u', 100.0, 'ok');
                """)
            verify_campaign_database(folder, definition, 1)

    def test_event_list_has_expected_standard_metric(self):
        self.assertIn("task-clock", EVENTS)


if __name__ == "__main__":
    unittest.main()
