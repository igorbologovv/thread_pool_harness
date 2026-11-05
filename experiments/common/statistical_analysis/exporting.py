"""Numerical and figure exports into a new, exclusive destination."""
import importlib.metadata
import json
import math
from pathlib import Path
import sys

from .io import csv_write


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def export(result, output, *, figures=True):
    """Export into a new directory; refuse to overwrite any existing results."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    result['software'] = {'python': sys.version, **{p: importlib.metadata.version(p) for p in ('numpy', 'scipy', 'diptest', 'matplotlib')}}
    (output / 'results.json').write_text(json.dumps(clean(result), indent=2, allow_nan=False) + '\n')
    csv_write(output / 'observations.csv', result['observations'])
    csv_write(output / 'estimates.csv', [dict(stratum=json.dumps(c['stratum']), scheduler=c['scheduler'], x=c['x'],
              estimate=c['estimate'], ci95_low=c['ci95'][0], ci95_high=c['ci95'][1], n_blocks=c['n_blocks'],
              **c['diagnostics']['descriptive']) for c in result['conditions']])
    if figures:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from .plotting import plot_distribution, plot_estimates, plot_regression, plot_temporal
        def save(fig, name):
            for suffix in ('png', 'svg'):
                fig.savefig(output / (name + '.' + suffix), dpi=150)
            plt.close(fig)
        for i, c in enumerate(result['conditions']):
            save(plot_distribution(c), f'distribution_{i:03}')
            save(plot_temporal(c), f'temporal_{i:03}')
        strata = list({json.dumps(c['stratum'], sort_keys=True): c['stratum'] for c in result['conditions']}.values())
        for i, facet in enumerate(strata):
            save(plot_estimates(result, stratum=facet), f'estimates_{i:03}')
        for i, model in enumerate(result.get('regressions', [])):
            if model['status'] == 'ok':
                save(plot_regression(model), f'regression_{i:03}')

