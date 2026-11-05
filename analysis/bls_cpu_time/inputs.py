"""Attach CPU counters to the throughput collector's canonical physical runs."""
from collections import defaultdict
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

FACTORS_US = {'nsec': .001, 'ns': .001, 'usec': 1., 'us': 1., 'µsec': 1.,
              'msec': 1000., 'ms': 1000., 'sec': 1e6, 's': 1e6}


def cpu_per_wu(value, unit, completed):
    if unit not in FACTORS_US:
        raise ValueError(f'Unknown task-clock unit: {unit!r}')
    value, completed = float(value), int(completed)
    if not math.isfinite(value) or value <= 0 or completed <= 0:
        raise ValueError('Nonpositive/nonfinite task-clock or completed WU')
    return value*FACTORS_US[unit]/completed


class CpuEnricher:
    def __init__(self, root):
        self.root = Path(root)
        self.capture_cache = {}
        self.csv_cache = {}
        self.audit = []
        self.availability = {}
        from experiments.common.statistical_analysis import load_csv
        for r in load_csv(next((self.root/'bls-deep-analysis').rglob('counter_availability.csv'))):
            if r['event'].split(':')[0] == 'task-clock':
                self.availability[r['campaign']] = r

    def captures(self, path):
        if path not in self.capture_cache:
            result = {}
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
                for run_id, command, doc, exit_code in db.execute('SELECT run_id,perf_command,raw_json,perf_exit_code FROM perf_capture'):
                    j = json.loads(doc)
                    clocks = [r for r in j.get('records', []) if r['event'].split(':')[0] == 'task-clock']
                    units = {r.get('unit') for r in clocks}
                    values = [float(r['accounting_value']) if 'accounting_value' in r else float(r['counter-value']) for r in clocks]
                    result[run_id] = dict(raw_total=sum(values), units=units, exit_code=exit_code,
                                          scope='explicit per-TID sum (pacer + workers)' if '--per-thread' in command else 'process attachment',
                                          per_thread='--per-thread' in command)
            self.capture_cache[path] = result
        return self.capture_cache[path]

    def __call__(self, canonical, raw, source):
        completed = int(raw['completed_work_units'] if raw.get('completed_work_units') is not None else canonical['work_units'])
        out = dict(completed_work_units=completed, cpu_available=True, cpu_counter_status='ok',
                   cpu_unit_evidence='', cpu_quality_warning='', cpu_scope='not independently recoverable',
                   cpu_counter_unit='msec', cpu_counter_value=None, cpu_us_per_wu=None,
                   cpu_percent_running=None, cycles_per_cpu_ms_proxy=None)
        try:
            if source.suffix == '.sqlite3':
                events = [k for k in raw if k.startswith('perf.task-clock') and k.count('.') == 1]
                if len(events) != 1:
                    raise ValueError(f'Expected one task-clock event; found {events}')
                name = events[0]
                value, unit = raw[name], raw[name+'.unit']
                status = raw[name+'.status']
                if status != 'ok' or value is None:
                    raise ValueError(f'Task-clock status {status}, value {value}')
                capture = self.captures(source)[canonical['run_id']]
                if capture['exit_code'] != 0 or capture['units'] != {unit}:
                    raise ValueError('Raw capture exit status/units disagree')
                if not math.isclose(capture['raw_total'], value, rel_tol=1e-9, abs_tol=1e-6):
                    raise ValueError('Raw capture and stored task-clock disagree')
                out.update(cpu_counter_unit=unit, cpu_unit_evidence='verified in SQLite and raw perf JSON',
                           cpu_scope=capture['scope'] + '; ' + name.removeprefix('perf.'),
                           cpu_percent_running=raw[name+'.percent_running'])
                cycles = next((raw[k] for k in ('perf.cycles:uk', 'perf.cycles:u', 'perf.cycles') if k in raw), None)
            else:
                per_wu = float(raw['task_clock_per_wu'])
                value = float(raw['task_clock']) if raw.get('task_clock') not in (None, '') else per_wu*completed
                if not math.isclose(value/completed, per_wu, rel_tol=1e-10):
                    raise ValueError('Raw counter / WU disagrees with archived per-WU value')
                unit = 'msec'
                out.update(cpu_unit_evidence='inferred archived millisecond convention; raw unit records absent',
                           cpu_counter_status='CSV numeric value present; original status unavailable',
                           cpu_quality_warning='Original perf unit/capture/status cannot be freshly verified from CSV.')
                avail = self.availability.get(canonical['campaign'])
                if avail:
                    out.update(cpu_percent_running=float(avail['min_running']),
                               cpu_scope='archived process attachment; unqualified task-clock',
                               cpu_counter_status='archived audit reports available')
                if canonical['cohort'] == 'saturation-original':
                    out['cpu_scope'] = 'historical user-only task-clock; full kernel-inclusive CPU time unavailable'
                if raw.get('cycles') not in (None, ''):
                    cycles = float(raw['cycles'])
                elif raw.get('cycles_per_wu') not in (None, ''):
                    cycles = float(raw['cycles_per_wu'])*completed
                else:
                    cycles = None
            out.update(cpu_counter_value=float(value), cpu_us_per_wu=cpu_per_wu(value, unit, completed))
            if cycles is not None and math.isfinite(cycles):
                milliseconds = float(value)*FACTORS_US[unit]/1000
                out['cycles_per_cpu_ms_proxy'] = cycles/milliseconds/1e6
            if out['cpu_percent_running'] is not None and out['cpu_percent_running'] < 95:
                out['cpu_quality_warning'] += ' Task-clock percent-running below 95%; inspect measurement quality.'
            # Compare all alternative per-run CPU CSV representations to original SQL.
            if source.suffix == '.sqlite3':
                if source not in self.csv_cache:
                    from experiments.common.statistical_analysis import load_csv
                    self.csv_cache[source] = [(p.name, {int(r['run_id']):r for r in load_csv(p)})
                        for p in [source.parent/'analysis_raw_runs.csv', source.parent/'thread_diagnostic_raw_runs.csv'] if p.exists()]
                for name, records in self.csv_cache[source]:
                    alternate = float(records[canonical['run_id']]['task_clock_per_wu'])*1000
                    if not math.isclose(out['cpu_us_per_wu'], alternate, rel_tol=1e-9):
                        raise ValueError(f'CPU discrepancy in duplicate {name}')
        except (ValueError, TypeError, KeyError) as error:
            out.update(cpu_available=False, cpu_counter_status='unavailable/inconsistent', cpu_quality_warning=str(error))
        self.audit.append(dict(campaign=canonical['campaign'], run_id=canonical['run_id'],
                               perf_pass=canonical['perf_pass'], **out))
        return out
