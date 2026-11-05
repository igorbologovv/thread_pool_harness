"""Read-only adapters. Preserve source columns; never infer randomized blocks."""
from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from pathlib import Path

from experiments.common.analysis import csv_write


def load_csv(path):
    with Path(path).open(newline='') as handle:
        return list(csv.DictReader(handle))


def load_sqlite(path, *, manifest=None):
    """Return one row per physical run, including all experiment/session metadata.

    Perf columns use exact event names (including privilege suffixes). Unsupported
    counters remain None. Their status, runtime and multiplexing metadata survive.
    A manifest joins by experiment_id and supplies blocks/order/configuration.
    """
    path = Path(path).resolve()
    metadata = {}
    if manifest is not None:
        for row in load_csv(manifest):
            key = int(row['experiment_id'])
            if key in metadata:
                raise ValueError(f'Duplicate manifest experiment_id: {key}')
            metadata[key] = row
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')  # one consistent read snapshot across all source tables
        experiments = {r['id']: dict(r) for r in db.execute('SELECT * FROM experiment')}
        sessions = {r['id']: dict(r) for r in db.execute('SELECT * FROM benchmark_session')}
        session_meta = {}
        for r in db.execute('SELECT * FROM session_metadata'):
            session_meta.setdefault(r['session_id'], {})[r['key']] = r['value']
        perf = {}
        for r in db.execute('SELECT * FROM perf_metric'):
            perf.setdefault(r['run_id'], []).append(dict(r))
        rows = []
        for source in db.execute('SELECT * FROM run ORDER BY id'):
            run = dict(source)
            eid = run['experiment_id']
            if manifest is not None and eid not in metadata:
                raise ValueError(f'Run {run["id"]} has no manifest entry')
            exp = experiments[eid]
            session = sessions[exp['session_id']]
            row = {**exp, **run, 'experiment_id': eid, 'run_id': run['id']}
            row.update({'session.' + k: v for k, v in session.items()})
            row.update({'session_metadata.' + k: v for k, v in session_meta.get(session['id'], {}).items()})
            for k, v in metadata.get(eid, {}).items():
                if k in row and str(row[k]) != str(v):
                    row['manifest.' + k] = v
                else:
                    row[k] = v
            row['throughput'] = run['work_units_per_second']
            row['elapsed_ms'] = run['elapsed_ns'] / 1e6
            for p in perf.get(run['id'], []):
                name = 'perf.' + p['event_name']
                row[name] = p['counter_value'] if p['status'] == 'ok' else None
                for k in ('status', 'unit', 'percent_running', 'event_runtime_ns', 'metric_value', 'metric_unit'):
                    row[name + '.' + k] = p[k]
            rows.append(row)
    return rows


__all__ = ['load_csv', 'load_sqlite', 'csv_write']
