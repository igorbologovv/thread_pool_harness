"""Metric-independent distribution diagnostics and randomized-block estimation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import defaultdict
import math

import numpy as np
from scipy import stats
from diptest import diptest, dipstat

from experiments.fixed_wait.diagnostics import describe


@dataclass(frozen=True)
class Design:
    metric: str
    x: str
    scheduler: str = 'scheduler'
    block: str | None = 'block'
    repeat: str | None = 'run_index'
    strata: tuple[str, ...] = ()
    order: str | None = None
    time: str | None = None
    seed: int = 20261105
    bootstrap_replicates: int = 2000


def number(value, name):
    try:
        result = float(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{name}: expected a finite number, got {value!r}') from error
    if not math.isfinite(result):
        raise ValueError(f'{name}: expected a finite number, got {value!r}')
    return result


def correlation(x, y):
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    return float(stats.spearmanr(x, y).statistic)


def distribution(values, *, dependent=False):
    """Raw-run shape; dip p-values are calibrated for IID continuous samples only."""
    y = np.asarray(values, dtype=float)
    if y.ndim != 1 or not len(y) or not np.all(np.isfinite(y)):
        raise ValueError('Distribution requires a nonempty vector of finite observations')
    notes = ['KDE depends on bandwidth; IQR fences flag observations, never remove them.',
             'A nonsignificant dip test does not prove unimodality; power may be low.']
    histogram, edges = np.histogram(y, bins='auto')
    grid, density = [], []
    if len(y) >= 3 and np.ptp(y) > 0:
        grid = np.linspace(y.min(), y.max(), 200)
        try:
            density = stats.gaussian_kde(y)(grid).tolist()
            grid = grid.tolist()
        except np.linalg.LinAlgError:
            grid = []
            notes.append('KDE unavailable: singular covariance.')
    else:
        notes.append('KDE unavailable: fewer than three observations or constant sample.')
    dip = {'statistic': None, 'p_value': None, 'status': 'inconclusive'}
    if len(y) >= 4 and np.ptp(y) > 0:
        statistic, p = diptest(y)  # deterministic tabulated calibration
        dip.update(statistic=float(statistic), p_value=float(p),
                   status='exploratory' if dependent or len(np.unique(y)) < len(y) else 'iid_calibration')
        if dependent:
            notes.append('Dip p-value is uncalibrated for dependent repeated/block observations; exploratory only.')
        if len(np.unique(y)) < len(y):
            notes.append('Ties/discrete measurements violate continuous dip calibration; exploratory only.')
        if len(y) < 20:
            notes.append('Small sample: dip test has limited power.')
    else:
        dip['statistic'] = float(dipstat(y)) if len(y) else None
        notes.append('Dip p-value unavailable: too few observations or constant sample.')
    q1, q3 = np.quantile(y, [.25, .75])
    iqr = q3 - q1
    outliers = np.flatnonzero((y < q1 - 1.5 * iqr) | (y > q3 + 1.5 * iqr)).tolist()
    descriptive = describe(y.tolist())
    descriptive['skewness_bias_corrected'] = float(stats.skew(y, bias=False)) if len(y) >= 3 and np.ptp(y) > 0 else None
    descriptive['excess_kurtosis_bias_corrected'] = float(stats.kurtosis(y, fisher=True, bias=False)) if len(y) >= 4 and np.ptp(y) > 0 else None
    return dict(descriptive=descriptive, raw=y.tolist(),
                histogram={'edges': edges.tolist(), 'counts': histogram.tolist(),
                           'selection': 'numpy auto: finer of Freedman-Diaconis and Sturges; Sturges fallback at zero IQR',
                           'bin_width': float(edges[1]-edges[0]),
                           'fd_reference_width': float(2*iqr/len(y)**(1/3))},
                kde={'x': grid, 'density': density, 'bandwidth': 'Scott'},
                ecdf={'x': np.sort(y).tolist(), 'probability': (np.arange(1, len(y)+1)/len(y)).tolist()},
                dip=dip, outlier_indices=outliers, warnings=notes)


def analyze(rows, design: Design):
    """Estimate equal-block means; resample whole blocks jointly across conditions.

    Repeats remain intact in each selected block. No second resampling of repeats:
    their sampling variation is already present in the block means. Missing cells
    are rejected instead of silently breaking the paired randomized design.
    """
    if design.bootstrap_replicates < 100:
        raise ValueError('At least 100 bootstrap replicates are required')
    if not rows:
        raise ValueError('No measurements')
    strata = tuple(dict.fromkeys((*design.strata, *(('perf_pass',) if any('perf_pass' in r for r in rows) else ()))))
    groups = defaultdict(list)
    preserved = []
    identities = set()
    for index, source in enumerate(rows):
        r = dict(source)
        if 'success' in r and number(r['success'], 'success') != 1:
            raise ValueError(f'Failed run at row {index}; filter explicitly before analysis')
        if 'completed_work_units' in r and 'work_units' in r and number(r['completed_work_units'], 'completed_work_units') != number(r['work_units'], 'work_units'):
            raise ValueError(f'Incomplete run at row {index}')
        x, y = number(r.get(design.x), design.x), number(r.get(design.metric), design.metric)
        if r.get(design.scheduler) in (None, ''):
            raise ValueError('Missing scheduler group')
        facet = tuple(r.get(c) for c in strata)
        if any(c not in r for c in strata):
            raise ValueError('Missing stratification column')
        key = (*facet, str(r[design.scheduler]), x)
        block = r.get(design.block) if design.block else index
        if block in (None, ''):
            raise ValueError('Missing block; use block=None only for independent observations')
        if design.repeat and design.block:
            if r.get(design.repeat) in (None, ''):
                raise ValueError('Missing repeat identity; specify repeat=None if unavailable')
            identity = (key, block, r[design.repeat])
            if identity in identities:
                raise ValueError('Duplicate condition/block/repeat identity')
            identities.add(identity)
        r['_x'], r['_y'], r['_block'] = x, y, block
        groups[key].append(r)
        preserved.append(r)
    rng = np.random.default_rng(design.seed)
    conditions = []
    facets = sorted({key[:-2] for key in groups}, key=repr)
    for facet in facets:
        keys = sorted((k for k in groups if k[:-2] == facet), key=repr)
        block_sets = [{r['_block'] for r in groups[k]} for k in keys]
        if design.block and any(b != block_sets[0] for b in block_sets):
            raise ValueError(f'Incomplete randomized blocks in stratum {facet}; split independent campaigns explicitly')
        shared_blocks = sorted(block_sets[0], key=repr)
        draws = rng.integers(0, len(shared_blocks), size=(design.bootstrap_replicates, len(shared_blocks))) if design.block else None
        for key in keys:
            records = groups[key]
            y = [r['_y'] for r in records]
            by_block = defaultdict(list)
            for r in records:
                by_block[r['_block']].append(r['_y'])
            blocks = shared_blocks if design.block else sorted(by_block, key=repr)
            means = np.array([np.mean(by_block[b]) for b in blocks])
            local_draws = draws if design.block else rng.integers(0, len(blocks), size=(design.bootstrap_replicates, len(blocks)))
            ci = np.quantile(means[local_draws].mean(axis=1), [.025, .975]).tolist() if len(blocks) > 1 else [None, None]
            diag = distribution(y, dependent=design.block is not None)
            notes = diag['warnings']
            if design.bootstrap_replicates < 1000:
                notes.append('Fewer than 1000 bootstrap draws: CI endpoints may have substantial Monte Carlo error.')
            quality = [r.get(design.metric + '.percent_running') for r in records]
            if any(v not in (None, '') and number(v, 'percent_running') < 95 for v in quality):
                notes.append('Selected perf counter ran less than 95% of the time in some runs; inspect multiplexing quality before inference.')
            if len(blocks) < 10:
                notes.append('Few independent units: percentile bootstrap coverage may be poor; one unit gives no CI.')
            if design.block:
                notes.append('CI assumes independent exchangeable randomized blocks; serial dependence between blocks invalidates it.')
            if len({len(v) for v in by_block.values()}) > 1:
                notes.append('Unequal repeat counts: estimate weights blocks equally, not individual runs.')
            temporal = {}
            for label, column in [('order', design.order), ('time', design.time)]:
                if column:
                    axis = [number(r.get(column), column) for r in records]
                    order = np.argsort(axis, kind='stable')
                    temporal[label] = {'column': column, 'axis': axis, 'values': y,
                                       'spearman_rho': correlation(axis, y),
                                       'lag1_spearman': correlation(np.array(y)[order][:-1], np.array(y)[order][1:])}
                    notes.append(f'{label} correlations are descriptive, with no IID p-value; ties, block effects and confounding may explain association.')
                else:
                    notes.append(f'{label} diagnostic unavailable: no column specified; row order is not assumed chronological.')
            conditions.append(dict(stratum=dict(zip(strata, facet)), scheduler=key[-2], x=key[-1],
                                   estimate=float(means.mean()), ci95=ci, n_blocks=len(blocks),
                                   block_means=[{'block': b, 'mean': float(m), 'n': len(by_block[b])} for b, m in zip(blocks, means)],
                                   diagnostics=diag, temporal=temporal))
    return dict(design=asdict(design), strata=list(strata), observations=preserved, conditions=conditions,
                warnings=['CIs are pointwise percentile intervals for equal-block means, not simultaneous intervals.',
                          'Outlier, modality and drift diagnostics are exploratory; no automatic exclusions or Gaussian assumptions.',
                          'Verify nuisance variables are fixed or included in strata; metadata are preserved but not inferred as covariates.'])
