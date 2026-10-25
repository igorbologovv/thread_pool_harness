"""Shared, deterministic definitions for the retrospective BLS analysis."""
from pathlib import Path
import hashlib
import os
os.environ.setdefault('MPLCONFIGDIR', '/tmp/bls-matplotlib')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / 'results/studies/bls-full/campaigns'
OUT = ROOT / 'results/reports/bls_full'
TABLES = OUT / 'tables'
FIGURES = OUT / 'figures'
B = 20000
SEED = 20261025
SCHEDULERS = ('rayon', 'bevy', 'threadance', 'threadance-hot-first-rtrb')
EVENTS = ('task-clock', 'cycles', 'instructions', 'branches', 'branch-misses',
          'context-switches', 'cpu-migrations', 'page-faults')
METRICS = ['elapsed_ms', 'throughput', 'task_clock_ms', 'cycles', 'instructions',
           'branches', 'branch_misses', 'context_switches', 'cpu_migrations', 'page_faults',
           'ipc', 'branch_miss_pct', 'cpu_equivalents', 'effective_ghz',
           'task_clock_ms_per_wu', 'cycles_per_wu', 'instructions_per_wu',
           'branches_per_wu', 'branch_misses_per_wu', 'context_switches_per_wu',
           'cpu_migrations_per_wu', 'page_faults_per_wu',
           'drain_delay_ms', 'schedule_excess_ms', 'last_lateness_ms',
           'mean_lateness_ms', 'p95_lateness_ms', 'max_lateness_ms']
CORE = ['elapsed_ms', 'task_clock_ms', 'drain_delay_ms', 'schedule_excess_ms',
        'context_switches', 'cycles', 'ipc', 'branch_miss_pct', 'p95_lateness_ms']

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def rng_for(key):
    return np.random.default_rng(SEED + int(hashlib.sha256(str(key).encode()).hexdigest()[:8],16))

def weights(key, n=20, b=B, length=1):
    rng = rng_for(key)
    if length == 1:
        indices = rng.integers(n, size=(b,n))
    else:
        starts = rng.integers(n, size=(b,int(np.ceil(n/length))))
        indices = ((starts[:,:,None]+np.arange(length)) % n).reshape(b,-1)[:,:n]
    return np.apply_along_axis(lambda x: np.bincount(x,minlength=n), 1, indices)/n

def save(rows, name):
    df = rows if isinstance(rows,pd.DataFrame) else pd.DataFrame(rows)
    df.to_csv(TABLES / (name+'.csv'), index=False, float_format='%.12g')
    return df

def short(config):
    if config in ('rayon','bevy'):
        return config.title()
    s = config.replace('threadance-hot-first-rtrb','R').replace('threadance','M')
    s = s.replace('crossbeam-wait','CW').replace('agave-backoff','AB').replace('spin-then-park','SP')
    return s.replace('__',' ')

def regime(workers, mode, rate):
    return f'w{workers}_'+{'all-at-once':'all','fixed-arrivals':'fixed','bursty-arrivals':'bursty'}[mode]+(f'_{int(rate)}' if rate else '')

def pretty(s):
    w, mode, *r = s.split('_')
    return f'{w[1:]} workers · '+{'all':'all at once','fixed':'fixed','bursty':'bursty'}[mode]+(f' · {r[0]} WU/s' if r else '')

def read(name):
    return pd.read_csv(TABLES/(name+'.csv'))
