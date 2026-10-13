#!/usr/bin/env python3
"""Create or reuse scheduler-free BLS service-time calibrations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class CalibrationConfig:
    work_units: int
    validators: int
    signers_per_certificate: int
    certificates_per_work_unit: int
    workload_seed: int
    dataset_cache_dir: Path
    warmup: int = 10
    runs: int = 50


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or reuse a scheduler-free BLS service-time calibration"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create a fresh calibration")
    _add_create_args(create)

    ensure = subparsers.add_parser(
        "ensure",
        help="reuse a compatible cached calibration or create one from an experiment",
    )
    ensure.add_argument(
        "--experiment",
        type=Path,
        default=Path("experiments/fixed_wait.toml"),
    )
    ensure.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow tracked source changes while calibrating",
    )
    ensure.add_argument(
        "--settle-seconds",
        type=float,
        default=10.0,
        help="quiet period before a new calibration (default: 10 seconds)",
    )
    return parser.parse_args()


def _add_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--work-units", type=int, default=200)
    parser.add_argument("--validators", type=int, default=2000)
    parser.add_argument("--signers-per-certificate", type=int, default=1600)
    parser.add_argument("--certificates-per-work-unit", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--runs", type=int, default=50)
    parser.add_argument(
        "--dataset-cache-dir",
        type=Path,
        default=Path("results/datasets"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("results/calibrations"),
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow tracked source changes while calibrating",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=10.0,
        help="quiet period before measurement (default: 10 seconds)",
    )


def run_capture(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"command failed ({' '.join(args)}): {detail}")
    return result.stdout.strip()


def repository_root() -> Path:
    return Path(run_capture(["git", "rev-parse", "--show-toplevel"])).resolve()


def validate_clean_tree(allow_dirty: bool) -> None:
    dirty = run_capture(["git", "status", "--porcelain", "--untracked-files=no"])
    if dirty and not allow_dirty:
        raise RuntimeError(
            "tracked files are dirty; commit or restore them before calibration:\n" + dirty
        )


def current_cpu_model() -> str:
    output = run_capture(["lscpu"])
    for line in output.splitlines():
        if line.startswith("Model name:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("could not determine CPU model from lscpu")


def environment_text(calibration_id: str) -> str:
    sections = [
        f"timestamp={datetime.now().astimezone().isoformat()}",
        f"calibration_id={calibration_id}",
        f"git_commit={run_capture(['git', 'rev-parse', 'HEAD'])}",
        f"git_branch={run_capture(['git', 'branch', '--show-current'])}",
        f"kernel={run_capture(['uname', '-r'])}",
        f"architecture={platform.machine()}",
        f"rustc={run_capture(['rustc', '--version'])}",
        f"cargo={run_capture(['cargo', '--version'])}",
        "",
        "=== CPU ===",
        run_capture(["lscpu"], check=False),
        "",
        "=== CPU governors ===",
        run_capture(
            [
                "bash",
                "-lc",
                "grep -h . /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor "
                "2>/dev/null | sort -u || true",
            ],
            check=False,
        ),
        "",
        "=== SMT ===",
        run_capture(
            ["bash", "-lc", "cat /sys/devices/system/cpu/smt/control 2>/dev/null || true"],
            check=False,
        ),
        "",
        "=== Memory ===",
        run_capture(["free", "-h"], check=False),
        "",
    ]
    return "\n".join(sections)


def settle(seconds: float) -> None:
    if seconds < 0:
        raise ValueError("settle seconds must be non-negative")

    browsers = run_capture(
        ["pgrep", "-af", "firefox|chromium|google-chrome|chrome"], check=False
    )
    if browsers:
        print("WARNING: browser process detected:", file=sys.stderr)
        print(browsers, file=sys.stderr)
        print(file=sys.stderr)

    if seconds > 0:
        print(f"Waiting {seconds:g} seconds for the machine to settle...", file=sys.stderr)
        time.sleep(seconds)


def stream_command(command: list[str], log_path: Path | None = None) -> None:
    log = log_path.open("w") if log_path is not None else None
    try:
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stderr.write(line)
            if log is not None:
                log.write(line)
        returncode = process.wait()
    finally:
        if log is not None:
            log.close()

    if returncode != 0:
        raise RuntimeError(f"command failed with status {returncode}: {' '.join(command)}")


def percentile(values: list[float], probability: float) -> float:
    if len(values) == 1:
        return values[0]

    position = probability * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]

    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def calibration_statistics(raw_path: Path) -> dict[str, float | int]:
    with raw_path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    values_ns = [float(row["service_time_ns"]) for row in rows]
    if not values_ns:
        raise RuntimeError("calibration produced no samples")

    ordered = sorted(values_ns)
    mean_ns = statistics.mean(values_ns)
    median_ns = statistics.median(values_ns)
    sd_ns = statistics.stdev(values_ns) if len(values_ns) > 1 else math.nan
    variance_ns2 = statistics.variance(values_ns) if len(values_ns) > 1 else math.nan
    q1_ns = percentile(ordered, 0.25)
    q3_ns = percentile(ordered, 0.75)

    return {
        "samples": len(values_ns),
        "mean_us": mean_ns / 1_000.0,
        "median_us": median_ns / 1_000.0,
        "sd_us": sd_ns / 1_000.0,
        "variance_us2": variance_ns2 / 1_000_000.0,
        "q1_us": q1_ns / 1_000.0,
        "q3_us": q3_ns / 1_000.0,
        "iqr_us": (q3_ns - q1_ns) / 1_000.0,
    }


def create_calibration(
    root: Path,
    config: CalibrationConfig,
    output_root: Path,
    *,
    allow_dirty: bool,
    settle_seconds: float,
) -> Path:
    validate_clean_tree(allow_dirty)

    if config.work_units <= 0 or config.validators <= 0:
        raise ValueError("work_units and validators must be positive")
    if config.signers_per_certificate <= 0 or config.certificates_per_work_unit <= 0:
        raise ValueError("BLS workload sizes must be positive")
    if config.warmup < 0 or config.runs <= 0:
        raise ValueError("warmup must be non-negative and runs must be positive")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    calibration_id = f"bls-service-time-{stamp}"
    out = output_root.resolve() / calibration_id
    out.mkdir(parents=True, exist_ok=False)

    raw_path = out / "raw_runs.csv"
    summary_path = out / "summary.txt"
    environment_path = out / "environment.txt"
    service_time_path = out / "service_time_us.txt"
    calibration_path = out / "calibration.json"
    log_path = out / "calibration.log"

    print(f"Creating calibration: {calibration_id}", file=sys.stderr)
    print(f"  output = {out}", file=sys.stderr)

    environment_path.write_text(environment_text(calibration_id))

    stream_command(
        [
            "cargo",
            "build",
            "--release",
            "-p",
            "threadance-harness",
            "--bin",
            "service_time",
        ]
    )

    settle(settle_seconds)

    binary = root / "target" / "release" / "service_time"
    stream_command(
        [
            str(binary),
            "--work-units",
            str(config.work_units),
            "--validators",
            str(config.validators),
            "--signers-per-certificate",
            str(config.signers_per_certificate),
            "--certificates-per-work-unit",
            str(config.certificates_per_work_unit),
            "--seed",
            str(config.workload_seed),
            "--warmup",
            str(config.warmup),
            "--runs",
            str(config.runs),
            "--samples-csv",
            str(raw_path),
            "--dataset-cache-dir",
            str(config.dataset_cache_dir.resolve()),
        ],
        log_path,
    )

    stats = calibration_statistics(raw_path)
    service_time_path.write_text(f"{stats['mean_us']:.6f}\n")
    summary_path.write_text(
        "Scheduler-free service-time calibration\n\n"
        f"n={stats['samples']}\n"
        f"mean_service_time_us={stats['mean_us']:.6f}\n"
        f"median_service_time_us={stats['median_us']:.6f}\n"
        f"sd_service_time_us={stats['sd_us']:.6f}\n"
        f"variance_service_time_us2={stats['variance_us2']:.6f}\n"
        f"q1_service_time_us={stats['q1_us']:.6f}\n"
        f"q3_service_time_us={stats['q3_us']:.6f}\n"
        f"iqr_service_time_us={stats['iqr_us']:.6f}\n"
    )

    artifact = {
        "schema_version": 1,
        "calibration_id": calibration_id,
        "workload": "bls-aggregate-verify",
        "workload_config": {
            "calibration_work_units": config.work_units,
            "validators": config.validators,
            "signers_per_certificate": config.signers_per_certificate,
            "certificates_per_work_unit": config.certificates_per_work_unit,
            "workload_seed": config.workload_seed,
        },
        "service_time": {
            "definition": "scheduler-free mean single-thread service time per work unit",
            "mean_us": stats["mean_us"],
            "median_us": stats["median_us"],
            "sd_us": stats["sd_us"],
            "iqr_us": stats["iqr_us"],
            "samples": stats["samples"],
        },
        "provenance": {
            "git_commit": run_capture(["git", "rev-parse", "HEAD"]),
            "architecture": platform.machine(),
            "cpu_model": current_cpu_model(),
        },
    }
    calibration_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    print("Calibration statistics:", file=sys.stderr)
    print(f"  n        = {stats['samples']}", file=sys.stderr)
    print(f"  mean S   = {stats['mean_us']:.3f} us", file=sys.stderr)
    print(f"  median S = {stats['median_us']:.3f} us", file=sys.stderr)
    print(f"  SD       = {stats['sd_us']:.3f} us", file=sys.stderr)
    print(f"  IQR      = {stats['iqr_us']:.3f} us", file=sys.stderr)

    return calibration_path


def validation_command(root: Path, calibration: Path, workload: dict) -> list[str]:
    return [
        sys.executable,
        str(root / "scripts" / "validate_calibration.py"),
        "--calibration",
        str(calibration),
        "--expect-work-units",
        str(workload["work_units"]),
        "--expect-validators",
        str(workload["validators"]),
        "--expect-signers-per-certificate",
        str(workload["signers_per_certificate"]),
        "--expect-certificates-per-work-unit",
        str(workload["certificates_per_work_unit"]),
        "--expect-workload-seed",
        str(workload["workload_seed"]),
    ]


def find_compatible_calibration(root: Path, cache_root: Path, workload: dict) -> Path | None:
    if not cache_root.is_dir():
        return None

    candidates = sorted(cache_root.glob("*/calibration.json"), reverse=True)
    for candidate in candidates:
        result = subprocess.run(
            validation_command(root, candidate, workload),
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            return candidate.resolve()
    return None


def config_from_experiment(data: dict, root: Path) -> tuple[CalibrationConfig, Path]:
    workload = data["workload"]
    artifacts = data["artifacts"]
    dataset_cache_dir = root / Path(artifacts["dataset_cache_dir"])
    calibration_cache_dir = root / Path(
        artifacts.get("calibration_cache_dir", "results/calibrations")
    )

    return (
        CalibrationConfig(
            work_units=int(workload["work_units"]),
            validators=int(workload["validators"]),
            signers_per_certificate=int(workload["signers_per_certificate"]),
            certificates_per_work_unit=int(workload["certificates_per_work_unit"]),
            workload_seed=int(workload["workload_seed"]),
            dataset_cache_dir=dataset_cache_dir,
        ),
        calibration_cache_dir,
    )


def ensure_calibration(
    root: Path,
    experiment_path: Path,
    *,
    allow_dirty: bool,
    settle_seconds: float,
) -> Path:
    data = tomllib.loads(experiment_path.read_text())
    config, cache_root = config_from_experiment(data, root)
    workload = data["workload"]

    cached = find_compatible_calibration(root, cache_root, workload)
    if cached is not None:
        print(f"Reusing calibration: {cached}", file=sys.stderr)
        return cached

    print("No compatible cached calibration found.", file=sys.stderr)
    return create_calibration(
        root,
        config,
        cache_root,
        allow_dirty=allow_dirty,
        settle_seconds=settle_seconds,
    )


def main() -> int:
    args = parse_args()
    root = repository_root()
    os.chdir(root)

    if args.command == "create":
        config = CalibrationConfig(
            work_units=args.work_units,
            validators=args.validators,
            signers_per_certificate=args.signers_per_certificate,
            certificates_per_work_unit=args.certificates_per_work_unit,
            workload_seed=args.seed,
            dataset_cache_dir=args.dataset_cache_dir,
            warmup=args.warmup,
            runs=args.runs,
        )
        calibration = create_calibration(
            root,
            config,
            args.output_root,
            allow_dirty=args.allow_dirty,
            settle_seconds=args.settle_seconds,
        )
    else:
        experiment_path = args.experiment.resolve()
        if not experiment_path.is_file():
            raise RuntimeError(f"missing experiment config: {experiment_path}")
        calibration = ensure_calibration(
            root,
            experiment_path,
            allow_dirty=args.allow_dirty,
            settle_seconds=args.settle_seconds,
        )

    print(calibration)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
