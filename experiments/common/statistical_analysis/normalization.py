"""Ratios to independently measured baselines, with uncertainty in both samples."""
from copy import deepcopy
import numpy as np


def normalize_by_baseline(result, baselines, *, match=('workers',), factor=100,
                          metric='relative_throughput_pct', seed=20261106):
    """Return a new result; baseline and numerator acquisitions must be independent.

    Match conditions by scheduler and the named stratum fields. Each match must
    have exactly one baseline. Positive baseline means are required. Resample
    independent whole-block means in each acquisition. Shared block ID vectors
    share resampling indices, preserving paired schedulers and the common
    denominator across load levels. Globally unique block IDs are required.
    Raw-run diagnostics condition on the measured denominator; only the CI
    propagates baseline sampling uncertainty. No pairing is invented between
    baseline and numerator campaigns.
    """
    if not np.isfinite(factor) or factor <= 0:
        raise ValueError('Normalization factor must be finite and positive')
    def key(c):
        return (c['scheduler'], *(c['stratum'][field] for field in match))
    lookup = {}
    for baseline in baselines['conditions']:
        k = key(baseline)
        if k in lookup:
            raise ValueError(f'Ambiguous baseline: {k}')
        lookup[k] = baseline
    output = deepcopy(result)
    output.pop('regressions', None)
    output['design']['metric'] = metric
    rng = np.random.default_rng(seed)
    replicates = result['design']['bootstrap_replicates']
    indices = {}
    def draws(c):
        blocks = sorted(c['block_means'], key=lambda b: str(b['block']))
        signature = tuple(b['block'] for b in blocks)
        if signature not in indices:
            indices[signature] = rng.integers(0, len(blocks), (replicates, len(blocks)))
        return np.array([b['mean'] for b in blocks])[indices[signature]].mean(axis=1)
    for c in output['conditions']:
        if key(c) not in lookup:
            raise ValueError(f'Missing baseline: {key(c)}')
        baseline = lookup[key(c)]
        if baseline['estimate'] <= 0 or any(b['mean'] <= 0 for b in baseline['block_means']):
            raise ValueError('Baseline block means must be positive')
        if {b['block'] for b in c['block_means']} & {b['block'] for b in baseline['block_means']}:
            raise ValueError('Numerator and baseline blocks overlap; independent ratio bootstrap is inappropriate')
        numerator_draws, denominator_draws = draws(c), draws(baseline)
        scale = factor / baseline['estimate']
        c['baseline'] = dict(estimate=baseline['estimate'], ci95=baseline['ci95'],
                             stratum=baseline['stratum'], n_blocks=baseline['n_blocks'])
        c['estimate'] *= scale
        c['ci95'] = np.quantile(factor*numerator_draws/denominator_draws, [.025, .975]).tolist() if min(c['n_blocks'], baseline['n_blocks']) > 1 else [None, None]
        for b in c['block_means']:
            b['mean'] *= scale
        d = c['diagnostics']
        for field in ('raw',):
            d[field] = (np.array(d[field])*scale).tolist()
        for field in tuple(k for k in d['descriptive'] if k in ('mean', 'median', 'sd', 'min', 'q1', 'q3', 'iqr', 'max') or (k.startswith('q') and k[1:].isdigit())):
            d['descriptive'][field] *= scale
        d['descriptive']['variance'] *= scale**2
        for width in ('bin_width', 'fd_reference_width'):
            if width in d['histogram']:
                d['histogram'][width] *= scale
        d['histogram']['edges'] = (np.array(d['histogram']['edges'])*scale).tolist()
        d['kde']['x'] = (np.array(d['kde']['x'])*scale).tolist()
        d['kde']['density'] = (np.array(d['kde']['density'])/scale).tolist()
        d['ecdf']['x'] = (np.array(d['ecdf']['x'])*scale).tolist()
        for temporal in c['temporal'].values():
            temporal['values'] = (np.array(temporal['values'])*scale).tolist()
        d['warnings'].append('Raw relative distribution conditions on the measured baseline. Ratio CI resamples independent numerator and baseline blocks; drift/shared host-state bias is not removed.')
    # Source observations keep the original metric as well as the derived metric.
    for r in output['observations']:
        k = (str(r[result['design']['scheduler']]), *(r[field] for field in match))
        r[metric] = factor * float(r[result['design']['metric']]) / lookup[k]['estimate']
        r['_y'] = r[metric]
    output['normalization'] = dict(match=list(match), factor=factor, seed=seed,
                                   method='independent whole-block percentile ratio bootstrap')
    return output
