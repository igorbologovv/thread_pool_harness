"""Frozen, auditable CPU-time models for independent worker-count holdouts.

Basis uses z=(workers-8)/8. An eight-core knot is a modeling hypothesis,
not evidence that thread placement was physically pinned to SMT siblings.
All model classes and predictions are frozen before 3/7/9/11/14 collection.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

TRAIN = (2, 4, 6, 8, 10, 12, 16)
HOLDOUT = (3, 7, 9, 11, 14)
MODELS = ("linear", "quadratic", "cubic", "piecewise_quadratic8")
FROZEN = Path(__file__).with_name("bls_worker_model_v2_frozen.json")


def basis(model: str, workers: float) -> tuple[float, ...]:
    z = (workers - 8.0) / 8.0
    if model == "linear":
        return 1.0, z
    if model == "quadratic":
        return 1.0, z, z*z
    if model == "cubic":
        return 1.0, z, z*z, z*z*z
    if model == "piecewise_quadratic8":
        left, right = min(z, 0.0), max(z, 0.0)
        return 1.0, left, left*left, right, right*right
    raise ValueError(f"Unknown model: {model}")


def fit(model: str, points: dict[int, float]) -> list[float]:
    if len(points) < len(basis(model, 8)):
        raise ValueError("Too few worker counts to fit selected model")
    rows = [basis(model, w) for w in sorted(points)]
    ys = [points[w] for w in sorted(points)]
    if not all(math.isfinite(float(v)) and v > 0 for v in ys):
        raise ValueError("Invalid positive measurements")
    n = len(rows[0])
    matrix = [[sum(row[i]*row[j] for row in rows) for j in range(n)]
              + [sum(row[i]*y for row, y in zip(rows, ys))] for i in range(n)]
    # Gaussian elimination with partial pivoting on X^T X; well scaled z in [-.75,1].
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(matrix[row][col]))
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        if abs(matrix[col][col]) < 1e-12:
            raise ValueError("Degenerate fitting matrix")
        div = matrix[col][col]
        matrix[col] = [v/div for v in matrix[col]]
        for row in range(n):
            if row == col:
                continue
            factor = matrix[row][col]
            matrix[row] = [a-factor*b for a,b in zip(matrix[row], matrix[col])]
    return [row[-1] for row in matrix]


def predict(model: str, coefficients: list[float], workers: float) -> float:
    row = basis(model, workers)
    if len(row) != len(coefficients):
        raise ValueError("Coefficient count does not match basis")
    value = sum(a*b for a,b in zip(row, coefficients))
    if not math.isfinite(value):
        raise ValueError("Nonfinite prediction")
    return value


def leave_one_worker_out(model: str, data: dict[int, float]) -> float:
    errors = []
    for workers, observation in sorted(data.items()):
        other = {w: y for w, y in data.items() if w != workers}
        estimate = predict(model, fit(model, other), workers)
        errors.append(100*(estimate-observation)/observation)
    return math.sqrt(statistics.mean(e*e for e in errors))


def load_frozen() -> dict:
    frozen = json.loads(FROZEN.read_text())
    if (frozen["schema_version"] != 1
        or tuple(frozen["training_workers"]) != TRAIN
        or tuple(frozen["holdout_workers"]) != HOLDOUT
        or tuple(frozen["methods"]) != MODELS
        or len(frozen["models"]) != 12):
        raise RuntimeError("Invalid frozen model lock")
    for key, obj in frozen["models"].items():
        if "|" not in key or obj["model"] not in MODELS:
            raise RuntimeError(f"Invalid model identity: {key}")
        means = {int(w): float(v) for w,v in obj["observed_means_ms_per_wu"].items()}
        if set(means) != set(TRAIN):
            raise RuntimeError(f"Incomplete historical values: {key}")
        cv = {m: leave_one_worker_out(m, means) for m in MODELS}
        best = min(MODELS, key=lambda m: cv[m])
        if best != obj["model"]:
            raise RuntimeError(f"Frozen selection does not reproduce: {key}")
        for model, error in cv.items():
            if abs(error - obj["candidate_loocv_rmspe_percent"][model]) > 1e-7:
                raise RuntimeError(f"Frozen CV scores changed: {key}")
        coeffs = fit(best, means)
        if any(abs(a-b) > 1e-7 for a,b in zip(coeffs, obj["coefficients"])):
            raise RuntimeError(f"Frozen coefficients do not reproduce: {key}")
        if any(abs(predict(best, coeffs, w) - obj["holdout_predictions_ms_per_wu"][str(w)]) > 1e-7
               for w in HOLDOUT):
            raise RuntimeError(f"Frozen predictions do not reproduce: {key}")
    return frozen


def percentile(samples: list[float], p: float) -> float:
    values = sorted(samples)
    place = p*(len(values)-1)
    low, high = math.floor(place), math.ceil(place)
    return values[low] + (values[high]-values[low])*(place-low)


def validate_observations(frozen: dict, blocks: dict[int, list[float]],
                          *, seed: int = 20261109, repeats: int = 4000) -> list[dict]:
    """Bootstrap whole randomized block means independently at each worker count.

    Select model structure once (frozen); refit training coefficients only inside
    the bootstrap to propagate estimation uncertainty, without reselecting form.
    """
    if set(blocks) != set(TRAIN) | set(HOLDOUT):
        raise RuntimeError("Need seven training and five holdout worker counts")
    rng = random.Random(seed)
    draws = {}
    means = {}
    for w, samples in sorted(blocks.items()):
        if len(samples) < 10 or any(not math.isfinite(x) or x <= 0 for x in samples):
            raise RuntimeError(f"Bad block observations at w{w}")
        means[w] = statistics.mean(samples)
        draws[w] = [statistics.mean(rng.choices(samples, k=len(samples)))
                    for _ in range(repeats)]
    model = frozen["model"]
    frozen_means = {int(k):v for k,v in frozen["observed_means_ms_per_wu"].items()}
    for w in TRAIN:
        if abs(means[w] - frozen_means[w]) > 1e-6:
            raise RuntimeError(
                f"Historical w{w} mean changed relative to frozen source; "
                "do not validate against a different acquisition set"
            )
    rows = []
    for w in HOLDOUT:
        expected = frozen["holdout_predictions_ms_per_wu"][str(w)]
        observed = means[w]
        errors = []
        for index in range(repeats):
            training = {x:draws[x][index] for x in TRAIN}
            refit = fit(model, training)
            errors.append(draws[w][index] - predict(model, refit, w))
        low, high = percentile(errors,.025),percentile(errors,.975)
        o_lo,o_hi = percentile(draws[w],.025),percentile(draws[w],.975)
        rows.append(dict(workers=w, observed_ms_per_wu=observed,
                         observed_ci95_low=o_lo,observed_ci95_high=o_hi,
                         frozen_predicted_ms_per_wu=expected,
                         error_ms_per_wu=observed-expected,
                         error_percent=100*(observed-expected)/observed,
                         error_ci95_low=low,error_ci95_high=high,
                         error_ci95_includes_zero=low<=0<=high))
    return rows
