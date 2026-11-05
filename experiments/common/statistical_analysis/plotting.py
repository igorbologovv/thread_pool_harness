"""Plot precomputed results only; plotting never fits models or changes X data."""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt


def plot_distribution(condition):
    d = condition['diagnostics']
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    h = d['histogram']
    axes[0].stairs(h['counts'], h['edges'], fill=True, alpha=.35)
    axes[0].set(xlabel='Metric', ylabel='Count')
    twin = axes[0].twinx()
    if d['kde']['x']:
        twin.plot(d['kde']['x'], d['kde']['density'], color='tab:orange', label='Scott KDE')
    twin.set_ylabel('Density')
    axes[1].step(d['ecdf']['x'], d['ecdf']['probability'], where='post')
    axes[1].set(xlabel='Metric', ylabel='ECDF', ylim=(0, 1.02))
    axes[2].scatter(np.arange(len(d['raw'])), d['raw'], s=12)
    flagged = d['outlier_indices']
    if flagged:
        axes[2].scatter(flagged, np.array(d['raw'])[flagged], facecolors='none', edgecolors='red', s=45)
    axes[2].set(xlabel='Observation index (not assumed temporal)', ylabel='Metric')
    dip = d['dip']
    p = f"{dip['p_value']:.3g}" if dip['p_value'] is not None else 'unavailable'
    fig.suptitle(f"{condition['scheduler']} | X={condition['x']} | {condition['stratum']}\nDip p={p} ({dip['status']}); nonsignificance does not establish unimodality", fontsize=9)
    fig.tight_layout()
    return fig


def plot_estimates(result, *, stratum=None, offsets=True, display_span=None, labels=None):
    stratum = stratum or {}
    conditions = [c for c in result['conditions'] if c['stratum'] == stratum]
    if not conditions:
        raise ValueError('No matching stratum; specify the full stratum, including perf_pass')
    schedulers = sorted({c['scheduler'] for c in conditions})
    levels = sorted({c['x'] for c in conditions})
    spacing = min(np.diff(levels)) if len(levels) > 1 else 1.0
    fig, ax = plt.subplots(figsize=(8, 5))
    for i, scheduler in enumerate(schedulers):
        color = f'C{i % 10}'
        span = spacing * .12 if display_span is None else display_span
        shift = (i - (len(schedulers) - 1)/2) * span / max(1, len(schedulers)-1) if offsets else 0
        cells = sorted((c for c in conditions if c['scheduler'] == scheduler), key=lambda c: c['x'])
        x = np.array([c['x'] for c in cells]) + shift
        ax.scatter(x, [c['estimate'] for c in cells], color=color, label=(labels or {}).get(scheduler, scheduler), s=28)
        # Draw endpoints directly: a percentile interval need not contain its estimate.
        for display_x, c in zip(x, cells):
            lo, hi = c['ci95']
            if lo is not None:
                ax.vlines(display_x, lo, hi, color=color)
                ax.plot([display_x, display_x], [lo, hi], linestyle='none', marker='_', color=color)
        for model in result.get('regressions', []):
            if model['stratum'] == stratum and model['scheduler'] == scheduler and model['status'] == 'ok':
                ax.plot(model['curve']['x'], model['curve']['y'], color=color, alpha=.75)
    ax.set(xlabel=result['design']['x'] + (' (groups visually offset)' if offsets else ''),
           ylabel=result['design']['metric'], title=f'Equal-block estimates and pointwise 95% CIs | {stratum}')
    ax.legend()
    fig.tight_layout()
    return fig


def plot_regression(model, *, axes=None):
    if model['status'] != 'ok':
        raise ValueError('No fitted model')
    standalone = axes is None
    if standalone:
        fig, axes = plt.subplots(1, 4, figsize=(16, 3.5))
    else:
        fig = axes[0].figure
    residuals = model['residuals']
    axes[0].scatter([r['fitted'] for r in residuals], [r['residual'] for r in residuals], s=15)
    axes[0].axhline(0, color='gray')
    axes[0].set(xlabel='Fitted', ylabel='Block-mean residual')
    axes[1].scatter([r['x'] for r in residuals], [r['residual'] for r in residuals], s=15)
    axes[1].axhline(0, color='gray')
    axes[1].set(xlabel='X', ylabel='Block-mean residual')
    axes[2].hist([r['residual'] for r in residuals], bins='auto')
    axes[2].set(xlabel='Block-mean residual', ylabel='Count')
    cv = model['nested_cv']
    axes[3].scatter([r['x'] for r in cv], [r['residual'] for r in cv], s=25)
    axes[3].axhline(0, color='gray')
    axes[3].set(xlabel='Held-out X', ylabel='Nested CV residual')
    if standalone:
        fig.suptitle(model['scheduler'] + ' | ' + str(model['stratum']))
        fig.tight_layout()
    return fig


def plot_temporal(condition):
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.5))
    for ax, label in zip(axes, ('order', 'time')):
        diagnostic = condition['temporal'].get(label)
        if diagnostic:
            ax.scatter(diagnostic['axis'], diagnostic['values'], s=15)
            ax.set(xlabel=diagnostic['column'], ylabel='Metric', title=f"Spearman rho={diagnostic['spearman_rho']}")
        else:
            ax.text(.1, .5, label + ' metadata unavailable', transform=ax.transAxes)
    fig.tight_layout()
    return fig
