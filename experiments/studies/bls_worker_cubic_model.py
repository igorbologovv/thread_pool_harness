"""Small standard-library utilities for worker-count cubic holdout validation."""
from __future__ import annotations

import math
import random
import statistics

TRAIN_WORKERS = (2, 4, 8, 16)
HOLDOUT_WORKERS = (6, 10, 12)


def fit_cubic(training: dict[int, float]) -> tuple[float, ...]:
    """Fit exactly four training means; z=(workers-8)/8 improves conditioning."""
    if set(training) != set(TRAIN_WORKERS):
        raise ValueError("training must contain exactly 2, 4, 8 and 16 workers")
    matrix = []
    for workers in TRAIN_WORKERS:
        value = float(training[workers])
        if not math.isfinite(value):
            raise ValueError("Nonfinite training value")
        z = (workers - 8.0) / 8.0
        matrix.append([1.0, z, z*z, z*z*z, value])
    for col in range(4):
        pivot = max(range(col, 4), key=lambda row: abs(matrix[row][col]))
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        if abs(matrix[col][col]) < 1e-12:
            raise ValueError("Singular training design")
        scale = matrix[col][col]
        matrix[col] = [item / scale for item in matrix[col]]
        for row in range(4):
            if row == col:
                continue
            factor = matrix[row][col]
            matrix[row] = [a - factor*b for a,b in zip(matrix[row],matrix[col])]
    return tuple(matrix[row][-1] for row in range(4))


def predict(coefficients: tuple[float, ...] | list[float], workers: int) -> float:
    if len(coefficients) != 4:
        raise ValueError("Cubic model requires four coefficients")
    z = (workers - 8.0) / 8.0
    return sum(float(coef) * z**index for index, coef in enumerate(coefficients))


def bootstrap_summary(block_means: dict[int, list[float]], *, seed: int = 20261108,
                      replicates: int = 4000) -> dict:
    """Independent-block bootstrap; 2 repeats remain together within each block."""
    if not block_means or replicates < 100:
        raise ValueError("Need block observations and >=100 resamples")
    by_worker = {}
    for workers, values in block_means.items():
        if len(values) < 2 or not all(math.isfinite(value) for value in values):
            raise ValueError("Insufficient or nonfinite block means")
        by_worker[workers] = tuple(values)
    estimates = {w: statistics.mean(samples) for w, samples in by_worker.items()}
    rng = random.Random(seed)
    draws = {w: [] for w in by_worker}
    for _ in range(replicates):
        for w, samples in by_worker.items():
            draws[w].append(statistics.mean(rng.choices(samples, k=len(samples))))
    return {"estimates": estimates, "draws": draws}


def interval(samples: list[float], alpha: float = 0.05) -> tuple[float, float]:
    sorted_values = sorted(samples)
    lo = int((alpha/2) * len(sorted_values))
    hi = min(len(sorted_values)-1, int((1-alpha/2) * len(sorted_values)))
    return sorted_values[lo], sorted_values[hi]


def evaluate_holdouts(block_means: dict[int, list[float]], *, seed: int = 20261108,
                      replicates: int = 4000) -> dict:
    expected = set(TRAIN_WORKERS) | set(HOLDOUT_WORKERS)
    if set(block_means) != expected:
        raise ValueError("All seven worker counts required")
    summary = bootstrap_summary(block_means, seed=seed, replicates=replicates)
    estimates, draws = summary["estimates"], summary["draws"]
    coeffs = fit_cubic({w: estimates[w] for w in TRAIN_WORKERS})
    rows = []
    for holdout in HOLDOUT_WORKERS:
        predicted = predict(coeffs, holdout)
        observed = estimates[holdout]
        error_draws = []
        for index in range(replicates):
            train = {w: draws[w][index] for w in TRAIN_WORKERS}
            error_draws.append(draws[holdout][index] - predict(fit_cubic(train), holdout))
        error_ci = interval(error_draws)
        observed_ci = interval(draws[holdout])
        rows.append({
            "workers": holdout, "observed_ms_per_wu": observed,
            "ci95_low": observed_ci[0], "ci95_high": observed_ci[1],
            "predicted_ms_per_wu": predicted, "error_ms_per_wu": observed-predicted,
            "error_percent": 100*(observed-predicted)/observed,
            "error_ci95_low": error_ci[0], "error_ci95_high": error_ci[1],
            "error_ci_includes_zero": error_ci[0] <= 0 <= error_ci[1],
        })
    return {"coefficients": coeffs, "training_means": {w: estimates[w] for w in TRAIN_WORKERS},
            "holdouts": rows}