#!/usr/bin/env python3
"""Distribution and measurement-quality diagnostics for benchmark campaigns."""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate benchmark diagnostics")
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--histogram-bins", type=int, default=12)
    parser.add_argument(
        "--min-perf-percent-running",
        type=float,
        default=95.0,
        help="flag perf events multiplexed below this percentage",
    )
    return parser.parse_args()


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = p * (len(ordered) - 1)
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def describe(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else math.nan
    variance = statistics.variance(values) if len(values) > 1 else math.nan
    median = statistics.median(values)
    q1 = percentile(ordered, 0.25)
    q3 = percentile(ordered, 0.75)
    if len(values) >= 3 and sd and math.isfinite(sd) and sd > 0:
        skew = sum(((x - mean) / sd) ** 3 for x in values) / len(values)
    else:
        skew = math.nan
    return {
        "n": len(values),
        "mean": mean,
        "median": median,
        "sd": sd,
        "variance": variance,
        "min": min(values),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "max": max(values),
        "skewness_moment": skew,
    }


def histogram(values: list[float], bins: int) -> list[tuple[float, float, int]]:
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return [(lo, hi, len(values))]
    width = (hi - lo) / bins
    counts = [0] * bins
    for value in values:
        index = min(int((value - lo) / width), bins - 1)
        counts[index] += 1
    return [
        (lo + i * width, lo + (i + 1) * width, count)
        for i, count in enumerate(counts)
    ]


def main() -> int:
    args = parse_args()
    if args.histogram_bins <= 0:
        raise SystemExit("--histogram-bins must be positive")

    campaign = args.campaign.resolve()
    db_path = campaign / "benchmarks.sqlite3"
    manifest_path = campaign / "manifest.csv"
    if not db_path.is_file() or not manifest_path.is_file():
        raise SystemExit("campaign must contain benchmarks.sqlite3 and manifest.csv")

    manifest: dict[int, dict[str, str]] = {}
    with manifest_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            manifest[int(row["experiment_id"])] = row

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    groups: dict[tuple[float, int], list[dict[str, float | int]]] = defaultdict(list)
    perf_flags: list[dict[str, object]] = []

    run_query = """
    SELECT
        e.id AS experiment_id,
        e.work_units,
        r.id AS run_id,
        r.run_index,
        r.elapsed_ns,
        r.work_units_per_second,
        MAX(CASE WHEN pm.event_name = 'task-clock' THEN pm.counter_value END) AS task_clock,
        MAX(CASE WHEN pm.event_name = 'cycles' THEN pm.counter_value END) AS cycles,
        MAX(CASE WHEN pm.event_name = 'context-switches' THEN pm.counter_value END) AS context_switches,
        MAX(CASE WHEN pm.event_name = 'cpu-migrations' THEN pm.counter_value END) AS cpu_migrations
    FROM experiment e
    JOIN run r ON r.experiment_id = e.id
    LEFT JOIN perf_metric pm ON pm.run_id = r.id
    WHERE r.success = 1
    GROUP BY e.id, e.work_units, r.id, r.run_index, r.elapsed_ns, r.work_units_per_second
    ORDER BY e.id, r.run_index
    """

    for row in connection.execute(run_query):
        experiment_id = int(row["experiment_id"])
        if experiment_id not in manifest:
            continue
        meta = manifest[experiment_id]
        rho = float(meta["rho"])
        tau_us = int(meta["spin_us"])
        work_units = int(row["work_units"])
        groups[(rho, tau_us)].append(
            {
                "block": int(meta["block"]),
                "global_order": int(meta["global_order"]),
                "run_index": int(row["run_index"]),
                "elapsed_ms": int(row["elapsed_ns"]) / 1e6,
                "throughput": float(row["work_units_per_second"]),
                "task_clock_per_work_unit": float(row["task_clock"]) / work_units
                if row["task_clock"] is not None
                else math.nan,
                "cycles_per_work_unit": float(row["cycles"]) / work_units
                if row["cycles"] is not None
                else math.nan,
                "context_switches_per_work_unit": float(row["context_switches"]) / work_units
                if row["context_switches"] is not None
                else math.nan,
                "cpu_migrations_per_work_unit": float(row["cpu_migrations"]) / work_units
                if row["cpu_migrations"] is not None
                else math.nan,
            }
        )

    perf_query = """
    SELECT
        e.id AS experiment_id,
        r.id AS run_id,
        r.run_index,
        pm.event_name,
        pm.percent_running,
        pm.status
    FROM experiment e
    JOIN run r ON r.experiment_id = e.id
    JOIN perf_metric pm ON pm.run_id = r.id
    ORDER BY e.id, r.run_index, pm.event_name
    """
    for row in connection.execute(perf_query):
        experiment_id = int(row["experiment_id"])
        if experiment_id not in manifest:
            continue
        percent = row["percent_running"]
        status = row["status"]
        if status != "ok" or (
            percent is not None and float(percent) < args.min_perf_percent_running
        ):
            meta = manifest[experiment_id]
            perf_flags.append(
                {
                    "experiment_id": experiment_id,
                    "run_id": int(row["run_id"]),
                    "run_index": int(row["run_index"]),
                    "block": int(meta["block"]),
                    "rho": float(meta["rho"]),
                    "tau_us": int(meta["spin_us"]),
                    "event_name": row["event_name"],
                    "percent_running": percent,
                    "status": status,
                }
            )
    connection.close()

    metrics = (
        "elapsed_ms",
        "throughput",
        "task_clock_per_work_unit",
        "cycles_per_work_unit",
        "context_switches_per_work_unit",
        "cpu_migrations_per_work_unit",
    )

    descriptive_path = campaign / "diagnostic_descriptive.csv"
    with descriptive_path.open("w", newline="") as handle:
        fields = ["rho", "tau_us", "metric", "n", "mean", "median", "sd", "variance", "min", "q1", "q3", "iqr", "max", "skewness_moment"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for (rho, tau_us), rows in sorted(groups.items()):
            for metric in metrics:
                values = [float(row[metric]) for row in rows if math.isfinite(float(row[metric]))]
                if not values:
                    continue
                writer.writerow({"rho": rho, "tau_us": tau_us, "metric": metric, **describe(values)})

    histogram_path = campaign / "diagnostic_histograms.csv"
    with histogram_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rho", "tau_us", "metric", "bin_low", "bin_high", "count"])
        for (rho, tau_us), rows in sorted(groups.items()):
            for metric in metrics:
                values = [float(row[metric]) for row in rows if math.isfinite(float(row[metric]))]
                if not values:
                    continue
                for lo, hi, count in histogram(values, args.histogram_bins):
                    writer.writerow([rho, tau_us, metric, lo, hi, count])

    order_path = campaign / "diagnostic_run_order.csv"
    with order_path.open("w", newline="") as handle:
        fields = ["rho", "tau_us", "block", "global_order", "run_index", *metrics]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for (rho, tau_us), rows in sorted(groups.items()):
            for row in rows:
                writer.writerow({"rho": rho, "tau_us": tau_us, **row})

    perf_path = campaign / "diagnostic_perf_flags.csv"
    with perf_path.open("w", newline="") as handle:
        fields = ["experiment_id", "run_id", "run_index", "block", "rho", "tau_us", "event_name", "percent_running", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(perf_flags)

    print("Diagnostics complete")
    print(f"  descriptive stats : {descriptive_path}")
    print(f"  histogram bins    : {histogram_path}")
    print(f"  run order         : {order_path}")
    print(f"  perf flags        : {perf_path} ({len(perf_flags)} flagged event-runs)")
    print()
    print("Histogram output is intentionally raw/tabular: inspect shape and possible")
    print("multimodality visually rather than applying automatic outlier filtering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
