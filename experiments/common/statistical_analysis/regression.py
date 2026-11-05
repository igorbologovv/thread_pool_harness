"""Polynomial selection and nested leave-one-X-level-out validation.

Fits equal-weight condition estimates. Entire X levels (all their blocks and
repeats) are excluded in validation. No normal-error inference is performed.
"""
from __future__ import annotations

from collections import defaultdict
import numpy as np


def fit(x, y, degree):
    center = float(np.mean(x))
    scale = float(np.ptp(x)) or 1.0
    z = (np.asarray(x) - center) / scale
    matrix = np.polynomial.polynomial.polyvander(z, degree)
    coef, _, rank, singular = np.linalg.lstsq(matrix, y, rcond=None)
    if rank != degree + 1 or singular[0] / singular[-1] > 1e10:
        raise ValueError('Rank-deficient or ill-conditioned polynomial')
    return dict(degree=degree, center=center, scale=scale, coefficients=coef.tolist(),
                basis='ascending powers of (x-center)/scale')


def predict(model, x):
    return np.polynomial.polynomial.polyval((np.asarray(x) - model['center']) / model['scale'], model['coefficients'])


def select(x, y, max_degree):
    candidates = []
    # Each training fold must have at least one residual degree of freedom.
    for degree in range(min(max_degree, len(x) - 3) + 1):
        errors = []
        try:
            for i in range(len(x)):
                keep = np.arange(len(x)) != i
                prediction = float(predict(fit(x[keep], y[keep], degree), x[i]))
                errors.append((prediction - y[i]) ** 2)
        except ValueError:
            continue
        candidates.append({'degree': degree, 'mse': float(np.mean(errors))})
    if not candidates:
        raise ValueError('At least three distinct X levels required for selection')
    # Near ties favor the simpler polynomial.
    minimum = min(c['mse'] for c in candidates)
    best = next(c for c in candidates if c['mse'] <= minimum + 1e-12 * max(1, minimum))
    return best['degree'], candidates


def regress(result, *, max_degree=3):
    if not isinstance(max_degree, int) or not 0 <= max_degree <= 10:
        raise ValueError('max_degree must be an integer in [0, 10]')
    groups = defaultdict(list)
    for c in result['conditions']:
        groups[(tuple(c['stratum'].items()), c['scheduler'])].append(c)
    models = []
    for (facet, scheduler), conditions in groups.items():
        conditions = sorted(conditions, key=lambda c: c['x'])
        x = np.array([c['x'] for c in conditions])
        y = np.array([c['estimate'] for c in conditions])
        output = dict(stratum=dict(facet), scheduler=scheduler, status='inconclusive',
                      warnings=['Polynomial fit targets equal-block means, with equal weight per X level.',
                                'Selection CV scores are optimistic after tuning; nested CV evaluates the selection procedure.',
                                'CV predicts new X levels in the observed block population, not new independent campaigns.',
                                'Boundary folds involve extrapolation; curves are shown only over the observed range.',
                                'Inspect residual shape, unequal spread and temporal dependence; no Gaussian-error p-values are used.'])
        if len(x) < 3:
            output['warnings'].append('Fewer than three X levels: degree selection unavailable.')
            models.append(output)
            continue
        degree, candidates = select(x, y, max_degree)
        model = fit(x, y, degree)
        fitted = predict(model, x)
        residuals = []
        for c, f in zip(conditions, fitted):
            for b in c['block_means']:
                residuals.append(dict(x=c['x'], block=b['block'], fitted=float(f),
                                      observed=b['mean'], residual=float(b['mean'] - f)))
        cv = []
        if len(x) >= 4:
            for i in range(len(x)):
                keep = np.arange(len(x)) != i
                fold_degree, _ = select(x[keep], y[keep], max_degree)
                prediction = float(predict(fit(x[keep], y[keep], fold_degree), x[i]))
                cv.append(dict(x=float(x[i]), observed=float(y[i]), predicted=prediction,
                               residual=float(y[i] - prediction), degree=fold_degree,
                               extrapolation=bool(i == 0 or i == len(x) - 1)))
        else:
            output['warnings'].append('Nested CV unavailable: at least four X levels required.')
        grid = np.linspace(x.min(), x.max(), 250)
        output.update(status='ok', model=model, selection_cv=candidates, nested_cv=cv,
                      nested_cv_rmse=float(np.sqrt(np.mean([r['residual']**2 for r in cv]))) if cv else None,
                      residuals=residuals, curve={'x': grid.tolist(), 'y': predict(model, grid).tolist()},
                      training_rmse=float(np.sqrt(np.mean((y - fitted)**2))))
        if len(x) < 6:
            output['warnings'].append('Few X levels: selected degree and CV errors are unstable.')
        models.append(output)
    result['regressions'] = models
    return models
