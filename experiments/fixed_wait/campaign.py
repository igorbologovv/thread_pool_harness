#!/usr/bin/env python3
"""Run the final fixed-arrival Threadance wait-policy campaign.

This replaces the large shell orchestration script. Experimental parameters live
in experiments/fixed_wait.toml; this runner handles preflight validation,
randomized-block planning, execution, provenance capture, integrity checks, and
block-aware post-analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from bls_dataset_identity import (
    config_from_experiment,
    expected_dataset_path,
    identify_dataset,
)

EXPERIMENT_ID_RE = re.compile(r"Stored benchmark: session=\d+ experiment=(\d+) database=")


@dataclass(frozen=True)
class PlanRow:
    block: int
    order_in_block: int
    global_order: int
    rho: float
    tau_ratio: float
    tau_us: int
    arrival_rate: float
    predicted_idle_us: float
    rho_star: float
    prediction: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the randomized-block fixed-wait benchmark campaign"
    )
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path("experiments/fixed_wait.toml"),
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=10.0,
        help="quiet period before measurements (default: 10 seconds)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow tracked source changes during a research campaign",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="collect measurements without running analysis/fixed_wait.py",
    )
    return parser.parse_args()


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


def load_experiment(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def validate_experiment(data: dict) -> None:
    experiment = data["experiment"]
    delivery = data["delivery"]
    repetitions = data["repetitions"]

    if experiment["workload"] != "bls-aggregate-verify":
        raise ValueError("fixed-wait runner requires workload=bls-aggregate-verify")
    if experiment["scheduler"] != "threadance":
        raise ValueError("fixed-wait runner requires scheduler=threadance")
    if experiment["profile"] != "standard":
        raise ValueError("fixed-wait final campaign requires profile=standard")
    if delivery["mode"] != "fixed-arrivals":
        raise ValueError("fixed-wait runner requires delivery.mode=fixed-arrivals")

    blocks = int(repetitions["blocks"])
    runs = int(repetitions["measured_runs_per_block"])
    warmups = int(repetitions["warmup_runs_per_block"])
    if blocks <= 0 or runs <= 0 or warmups < 0:
        raise ValueError("blocks/runs must be positive and warmups non-negative")

    rhos = [float(value) for value in delivery["rho"]]
    taus = [int(value) for value in data["wait_policy"]["spin_us"]]
    if not rhos or not taus:
        raise ValueError("rho and spin_us grids must not be empty")
    if any(not 0.0 < rho < 1.0 for rho in rhos):
        raise ValueError("all nominal rho values must be strictly between 0 and 1")
    if any(tau < 0 for tau in taus):
        raise ValueError("spin durations must be non-negative")
    if 0 not in taus:
        raise ValueError("spin grid must contain tau=0 reference")


def validate_clean_tree(allow_dirty: bool) -> None:
    dirty = run_capture(
        ["git", "status", "--porcelain", "--untracked-files=no"]
    )
    if dirty and not allow_dirty:
        raise RuntimeError(
            "tracked files are dirty; commit or restore them before the campaign:\n"
            + dirty
        )


def validate_calibration(
    root: Path, calibration: Path, workload: dict
) -> dict[str, str]:
    command = [
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
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    values = result.stdout.splitlines()
    if len(values) != 9:
        raise RuntimeError(
            f"calibration validator returned {len(values)} fields; expected 9"
        )

    return {
        "mean_us": values[0],
        "calibration_id": values[1],
        "samples": values[2],
        "median_us": values[3],
        "work_units": values[4],
        "validators": values[5],
        "signers_per_certificate": values[6],
        "certificates_per_work_unit": values[7],
        "workload_seed": values[8],
    }


def build_plan(data: dict, service_time_us: float) -> list[PlanRow]:
    workload = data["workload"]
    delivery = data["delivery"]
    wait_policy = data["wait_policy"]
    repetitions = data["repetitions"]
    randomization = data["randomization"]

    workers = int(workload["workers"])
    blocks = int(repetitions["blocks"])
    seed = int(randomization["seed"])

    base_rows: list[dict] = []
    for rho_raw in delivery["rho"]:
        rho = float(rho_raw)
        arrival_rate = rho * workers * 1_000_000.0 / service_time_us
        predicted_idle_us = service_time_us * (1.0 / rho - 1.0)

        for tau_raw in wait_policy["spin_us"]:
            tau_us = int(tau_raw)
            tau_ratio = tau_us / service_time_us
            rho_star = 1.0 / (1.0 + tau_ratio)
            if tau_us == 0:
                prediction = "immediate-block"
            elif tau_us < predicted_idle_us:
                prediction = "spin-then-block-likely"
            else:
                prediction = "next-work-during-spin-likely"

            base_rows.append(
                {
                    "rho": rho,
                    "tau_ratio": tau_ratio,
                    "tau_us": tau_us,
                    "arrival_rate": arrival_rate,
                    "predicted_idle_us": predicted_idle_us,
                    "rho_star": rho_star,
                    "prediction": prediction,
                }
            )

    rows: list[PlanRow] = []
    global_order = 0
    for block in range(1, blocks + 1):
        randomized = [row.copy() for row in base_rows]
        random.Random(seed + block).shuffle(randomized)
        for order_in_block, row in enumerate(randomized, 1):
            global_order += 1
            rows.append(
                PlanRow(
                    block=block,
                    order_in_block=order_in_block,
                    global_order=global_order,
                    **row,
                )
            )
    return rows


def write_plan(path: Path, rows: list[PlanRow]) -> None:
    fields = list(asdict(rows[0]).keys())
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def environment_text(
    campaign_id: str,
    calibration: Path,
    calibration_info: dict[str, str],
    dataset_json: str,
) -> str:
    perf_paranoid = ""
    paranoid_path = Path("/proc/sys/kernel/perf_event_paranoid")
    if paranoid_path.is_file():
        perf_paranoid = paranoid_path.read_text().strip()

    sections = [
        f"timestamp={datetime.now().astimezone().isoformat()}",
        f"campaign_id={campaign_id}",
        f"calibration_file={calibration.resolve()}",
        f"calibration_id={calibration_info['calibration_id']}",
        f"service_time_mean_us={calibration_info['mean_us']}",
        f"service_time_median_us={calibration_info['median_us']}",
        f"calibration_samples={calibration_info['samples']}",
        f"git_commit={run_capture(['git', 'rev-parse', 'HEAD'])}",
        f"git_branch={run_capture(['git', 'branch', '--show-current'])}",
        f"kernel={run_capture(['uname', '-r'])}",
        f"architecture={run_capture(['uname', '-m'])}",
        f"rustc={run_capture(['rustc', '--version'])}",
        f"cargo={run_capture(['cargo', '--version'])}",
        f"perf={run_capture(['perf', '--version'], check=False)}",
        f"perf_event_paranoid={perf_paranoid}",
        "",
        "=== BLS dataset identity ===",
        dataset_json.rstrip(),
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
    if seconds <= 0:
        return
    browsers = run_capture(
        ["pgrep", "-af", "firefox|chromium|google-chrome|chrome"], check=False
    )
    if browsers:
        print("WARNING: browser process detected:")
        print(browsers)
        print()
    print(f"Waiting {seconds:g} seconds for the machine to settle...")
    time.sleep(seconds)


def run_logged(command: list[str], log_path: Path) -> int:
    experiment_id: int | None = None
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
            match = EXPERIMENT_ID_RE.search(line)
            if match:
                experiment_id = int(match.group(1))
        returncode = process.wait()

    if returncode != 0:
        raise RuntimeError(
            f"benchmark exited with status {returncode}; see {log_path}"
        )
    if experiment_id is None:
        raise RuntimeError(f"could not determine experiment id from {log_path}")
    return experiment_id


def verify_database(db_path: Path, expected_experiments: int, expected_runs: int) -> None:
    connection = sqlite3.connect(db_path)
    try:
        fk_errors = list(connection.execute("PRAGMA foreign_key_check"))
        if fk_errors:
            raise RuntimeError(f"database foreign-key errors: {fk_errors[:5]}")

        experiments = connection.execute("SELECT COUNT(*) FROM experiment").fetchone()[0]
        runs = connection.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        if experiments != expected_experiments:
            raise RuntimeError(
                f"expected {expected_experiments} experiments, found {experiments}"
            )
        if runs != expected_runs:
            raise RuntimeError(f"expected {expected_runs} runs, found {runs}")
    finally:
        connection.close()


def main() -> int:
    args = parse_args()
    if args.settle_seconds < 0:
        raise SystemExit("--settle-seconds must be non-negative")

    root = repository_root()
    os.chdir(root)
    experiment_path = args.experiment.resolve()
    calibration_path = args.calibration.resolve()

    if not experiment_path.is_file():
        raise SystemExit(f"missing experiment config: {experiment_path}")
    if not calibration_path.is_file():
        raise SystemExit(f"missing calibration: {calibration_path}")

    data = load_experiment(experiment_path)
    validate_experiment(data)
    validate_clean_tree(args.allow_dirty)

    workload = data["workload"]
    repetitions = data["repetitions"]
    artifacts = data["artifacts"]

    calibration_info = validate_calibration(root, calibration_path, workload)
    service_time_us = float(calibration_info["mean_us"])

    cache_dir, dataset_config = config_from_experiment(experiment_path)
    dataset_path = expected_dataset_path(cache_dir, dataset_config)
    dataset_identity = identify_dataset(dataset_path, dataset_config)
    dataset_json = json.dumps(asdict(dataset_identity), indent=2, sort_keys=True) + "\n"

    plan = build_plan(data, service_time_us)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    campaign_id = f"fixed-wait-{stamp}"
    out = Path(artifacts["output_root"]) / campaign_id
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=False)

    db_path = out / "benchmarks.sqlite3"
    plan_path = out / "plan.tsv"
    manifest_path = out / "manifest.csv"
    environment_path = out / "environment.txt"
    dataset_identity_path = out / "dataset_identity.json"

    shutil.copy2(experiment_path, out / "experiment.toml")
    shutil.copy2(calibration_path, out / "calibration.json")
    dataset_identity_path.write_text(dataset_json)
    write_plan(plan_path, plan)
    environment_path.write_text(
        environment_text(
            campaign_id,
            calibration_path,
            calibration_info,
            dataset_json,
        )
    )

    print("Fixed-wait campaign")
    print(f"  campaign          = {campaign_id}")
    print(f"  output            = {out}")
    print(f"  service time S    = {service_time_us:.6f} us")
    print(f"  dataset SHA-256   = {dataset_identity.sha256}")
    print(f"  blocks            = {repetitions['blocks']}")
    print(f"  runs/block/config = {repetitions['measured_runs_per_block']}")
    print(f"  warmups/config    = {repetitions['warmup_runs_per_block']}")
    print(f"  configurations    = {len(plan) // int(repetitions['blocks'])}")
    print()

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

    settle(args.settle_seconds)

    manifest_fields = [
        "experiment_id",
        "block",
        "order_in_block",
        "global_order",
        "rho",
        "tau_ratio",
        "tau_us",
        "arrival_rate",
        "predicted_idle_us",
        "rho_star",
        "prediction",
        "log",
    ]

    with manifest_path.open("w", newline="") as manifest_handle:
        writer = csv.DictWriter(manifest_handle, fieldnames=manifest_fields)
        writer.writeheader()

        for index, row in enumerate(plan, 1):
            print()
            print("=" * 64)
            print(f"Experiment {index} / {len(plan)}")
            print(
                f"block={row.block}/{repetitions['blocks']} "
                f"rho={row.rho:.2f} tau={row.tau_us} us "
                f"rate={row.arrival_rate:.3f}/s"
            )
            print("=" * 64)

            log_path = logs / (
                f"block-{row.block:02d}-order-{row.order_in_block:02d}"
                f"-rho-{row.rho:.4f}-tau-{row.tau_us}.log"
            )
            command = [
                str(binary),
                "--workload",
                data["experiment"]["workload"],
                "--scheduler",
                data["experiment"]["scheduler"],
                "--workers",
                str(workload["workers"]),
                "--queue-capacity",
                str(workload["queue_capacity"]),
                "--threadance-spin-us",
                str(row.tau_us),
                "--work-units",
                str(workload["work_units"]),
                "--validators",
                str(workload["validators"]),
                "--signers-per-certificate",
                str(workload["signers_per_certificate"]),
                "--certificates-per-work-unit",
                str(workload["certificates_per_work_unit"]),
                "--fixed-arrivals",
                "--arrival-rate",
                f"{row.arrival_rate:.12f}",
                "--seed",
                str(workload["workload_seed"]),
                "--dataset-cache-dir",
                str(Path(artifacts["dataset_cache_dir"]).resolve()),
                "--warmup",
                str(repetitions["warmup_runs_per_block"]),
                "--runs",
                str(repetitions["measured_runs_per_block"]),
                "--profile",
                data["experiment"]["profile"],
                "--campaign-id",
                campaign_id,
                "--database",
                str(db_path.resolve()),
            ]

            experiment_id = run_logged(command, log_path)
            writer.writerow(
                {
                    "experiment_id": experiment_id,
                    "block": row.block,
                    "order_in_block": row.order_in_block,
                    "global_order": row.global_order,
                    "rho": f"{row.rho:.6f}",
                    "tau_ratio": f"{row.tau_ratio:.9f}",
                    "tau_us": row.tau_us,
                    "arrival_rate": f"{row.arrival_rate:.12f}",
                    "predicted_idle_us": f"{row.predicted_idle_us:.9f}",
                    "rho_star": f"{row.rho_star:.9f}",
                    "prediction": row.prediction,
                    "log": str(log_path),
                }
            )
            manifest_handle.flush()

    expected_experiments = len(plan)
    expected_runs = expected_experiments * int(repetitions["measured_runs_per_block"])
    verify_database(db_path, expected_experiments, expected_runs)

    if not args.skip_analysis:
        subprocess.run(
            [sys.executable, str(root / "analysis" / "fixed_wait.py"), str(out)],
            check=True,
        )

    print()
    print("=" * 64)
    print("CAMPAIGN COMPLETE")
    print("=" * 64)
    print(f"database         : {db_path}")
    print(f"plan             : {plan_path}")
    print(f"manifest         : {manifest_path}")
    print(f"dataset identity : {dataset_identity_path}")
    print(f"environment      : {environment_path}")
    if not args.skip_analysis:
        print(f"paired effects   : {out / 'paired_effects.csv'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; partial campaign artifacts were retained.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
