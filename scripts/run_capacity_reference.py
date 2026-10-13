#!/usr/bin/env python3
"""Measure empirical four-worker saturated capacity for the BLS workload."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import tomllib
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from bls_dataset_identity import config_from_experiment, expected_dataset_path, identify_dataset

BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20261013


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure saturated four-worker capacity")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path("experiments/capacity_4w.toml"),
    )
    parser.add_argument("--allow-dirty", action="store_true")
    return parser.parse_args()


def capture(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(args, text=True, capture_output=True)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = p * (len(ordered) - 1)
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    weight = position - lo
    return ordered[lo] * (1 - weight) + ordered[hi] * weight


def bootstrap_mean_ci(values: list[float]) -> tuple[float, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    estimates = [
        statistics.mean(values[rng.randrange(n)] for _ in range(n))
        for _ in range(BOOTSTRAP_REPLICATES)
    ]
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def main() -> int:
    args = parse_args()
    root = Path(capture(["git", "rev-parse", "--show-toplevel"])).resolve()
    os.chdir(root)

    if not args.allow_dirty:
        dirty = capture(["git", "status", "--porcelain", "--untracked-files=no"])
        if dirty:
            raise RuntimeError("tracked files are dirty; commit or restore before calibration")

    experiment_path = args.experiment.resolve()
    data = tomllib.loads(experiment_path.read_text())
    experiment = data["experiment"]
    workload = data["workload"]
    repetitions = data["repetitions"]
    artifacts = data["artifacts"]

    if experiment["workload"] != "bls-aggregate-verify":
        raise ValueError("capacity reference requires BLS workload")
    if experiment["scheduler"] != "threadance":
        raise ValueError("capacity reference requires Threadance")
    if int(workload["workers"]) != 4:
        raise ValueError("capacity reference must use exactly four workers")

    cache_dir, dataset_config = config_from_experiment(experiment_path)
    dataset_path = expected_dataset_path(cache_dir, dataset_config)
    dataset_identity = identify_dataset(dataset_path, dataset_config)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    campaign_id = f"capacity-4w-{stamp}"
    out = Path(artifacts["output_root"]) / campaign_id
    out.mkdir(parents=True, exist_ok=False)
    db_path = out / "benchmarks.sqlite3"

    (out / "experiment.toml").write_text(experiment_path.read_text())
    (out / "dataset_identity.json").write_text(
        json.dumps(asdict(dataset_identity), indent=2, sort_keys=True) + "\n"
    )

    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "threadance-harness",
            "--bin",
            "threadance-harness",
        ],
        check=True,
    )

    binary = root / "target" / "release" / "threadance-harness"
    command = [
        str(binary),
        "--workload",
        experiment["workload"],
        "--scheduler",
        experiment["scheduler"],
        "--workers",
        str(workload["workers"]),
        "--queue-capacity",
        str(workload["queue_capacity"]),
        "--threadance-spin-us",
        str(workload["threadance_spin_us"]),
        "--work-units",
        str(workload["work_units"]),
        "--validators",
        str(workload["validators"]),
        "--signers-per-certificate",
        str(workload["signers_per_certificate"]),
        "--certificates-per-work-unit",
        str(workload["certificates_per_work_unit"]),
        "--seed",
        str(workload["workload_seed"]),
        "--dataset-cache-dir",
        str(Path(artifacts["dataset_cache_dir"]).resolve()),
        "--all-at-once",
        "--warmup",
        str(repetitions["warmup_runs"]),
        "--runs",
        str(repetitions["measured_runs"]),
        "--profile",
        experiment["profile"],
        "--campaign-id",
        campaign_id,
        "--database",
        str(db_path.resolve()),
    ]

    log_path = out / "run.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        if process.wait() != 0:
            raise RuntimeError(f"capacity benchmark failed; see {log_path}")

    connection = sqlite3.connect(db_path)
    try:
        rows = list(
            connection.execute(
                """
                SELECT r.run_index, r.elapsed_ns, r.work_units_per_second
                FROM run r
                JOIN experiment e ON e.id = r.experiment_id
                WHERE r.success = 1
                ORDER BY r.run_index
                """
            )
        )
    finally:
        connection.close()

    expected_runs = int(repetitions["measured_runs"])
    if len(rows) != expected_runs:
        raise RuntimeError(f"expected {expected_runs} runs, found {len(rows)}")

    raw_path = out / "raw_runs.csv"
    with raw_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["run_index", "elapsed_ns", "work_units_per_second"])
        writer.writerows(rows)

    throughput = [float(row[2]) for row in rows]
    ci_low, ci_high = bootstrap_mean_ci(throughput)
    summary = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "workers": int(workload["workers"]),
        "work_units": int(workload["work_units"]),
        "measured_runs": len(throughput),
        "throughput_work_units_per_second": {
            "mean": statistics.mean(throughput),
            "median": statistics.median(throughput),
            "sd": statistics.stdev(throughput) if len(throughput) > 1 else 0.0,
            "variance": statistics.variance(throughput) if len(throughput) > 1 else 0.0,
            "min": min(throughput),
            "max": max(throughput),
            "bootstrap_mean_ci95_low": ci_low,
            "bootstrap_mean_ci95_high": ci_high,
        },
        "dataset_sha256": dataset_identity.sha256,
        "git_commit": capture(["git", "rev-parse", "HEAD"]),
    }
    summary_path = out / "capacity.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    print()
    print("Four-worker saturated capacity reference complete")
    print(f"  mean mu_4   = {summary['throughput_work_units_per_second']['mean']:.3f} WU/s")
    print(f"  median      = {summary['throughput_work_units_per_second']['median']:.3f} WU/s")
    print(f"  95% CI mean = [{ci_low:.3f}, {ci_high:.3f}] WU/s")
    print(f"  artifact    = {summary_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
