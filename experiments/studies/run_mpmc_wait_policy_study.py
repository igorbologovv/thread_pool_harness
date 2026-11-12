"""Resumable clean Threadance MPMC wait/wake-policy experiment.

Calibrate one saturated SpinThenPark(0) reference per workload/worker count,
then compare five MPMC wait policies under fixed and Weibull-bursty arrivals.
Only one task-delivery mechanism (CrossbeamMpmc) is used.

Run from the repository root:
  python3 -m experiments.studies.run_mpmc_wait_policy_study --calibrate
  python3 -m experiments.studies.run_mpmc_wait_policy_study --check
  python3 -m experiments.studies.run_mpmc_wait_policy_study --run

An interrupted campaign is resumed using its archived TOML/manifest/SQLite.
Do not change Git revision, release binary, or study settings mid-campaign.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from experiments.common.campaign import (
    ROOT, RUST, make_plan, read_definition, verify_campaign_database,
)
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign

NAME = "mpmc-wait-policy-study"
BASE = ROOT / "experiments/studies/bls_pinned_control_template.toml"
ROOT_OUT = ROOT / "results/studies" / NAME
RESULTS = ROOT_OUT / "campaigns"
CALIBRATIONS = ROOT_OUT / "calibrations"
GENERATED = ROOT / "experiments/generated" / NAME

WORKERS = (2, 4, 6, 8, 10, 12, 14, 16)
DEFAULT_WORKERS = WORKERS
WORKLOADS = ("bls-aggregate-verify", "compute-heavy")
MODES = ("fixed-arrivals", "bursty-arrivals")
LOADS = (25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 95)
SPINS_US = (0, 10, 40, 100)
BLOCKS = 40
WARMUPS = 4
REPEATS = 2
CALIBRATION_WU = 2500
COMPUTE_CALIBRATION_WU = 512
CALIBRATION_RUNS = 12
SEED = 20261112
COMPUTE_INPUT_PAIRS = 4096
MAX_PACED_RATE_WU_S = 10000.0

# Each compute-heavy operation stores two 8x8 f64 matrices (2*8*8*8 bytes).
# Every work unit owns distinct operations in the current workload implementation.
BYTES_PER_OPERATION = 1024


def configurations() -> list[dict]:
    return [
        {"id": f"mpmc-sp{spin}", "scheduler": "threadance",
         "queue_capacity": 64, "wait_policy": "spin-then-park", "spin_us": spin}
        for spin in SPINS_US
    ] + [
        {"id": "mpmc-agave", "scheduler": "threadance",
         "queue_capacity": 64, "wait_policy": "agave-backoff", "spin_us": 0},
    ]


def label(workload: str, ops: int) -> str:
    if workload == "bls-aggregate-verify":
        return "bls"
    if workload == "compute-heavy":
        return f"compute-ops{ops}-pairs{COMPUTE_INPUT_PAIRS}"
    raise ValueError(f"Unknown workload {workload}")


def calibration_file(workload: str, workers: int, ops: int) -> Path:
    return CALIBRATIONS / git_revision()[:12] / f"{label(workload, ops)}-w{workers}.json"


def git_revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def release_hash() -> str:
    return hashlib.sha256(RUST.read_bytes()).hexdigest()


def preflight(*, perf: bool) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Benchmarks must run as a normal user, not root")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, text=True,
    ).strip()
    if dirty:
        raise RuntimeError("Commit or discard tracked changes before benchmarking")
    if perf:
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.check_perf_scope"],
            cwd=ROOT, check=True,
        )


def build_harness() -> None:
    env = os.environ.copy()
    env["RUSTFLAGS"] = "-C target-cpu=native"
    subprocess.run(
        ["cargo", "build", "--release", "-p", "threadance-harness",
         "--bin", "threadance-harness"],
        cwd=ROOT, env=env, check=True,
    )


def load_reference(workload: str, workers: int, ops: int) -> dict:
    file = calibration_file(workload, workers, ops)
    record = json.loads(file.read_text())
    required = {
        "workload": workload, "workers": workers,
        "operations_per_work_unit": ops if workload == "compute-heavy" else None,
        "compute_input_pairs": COMPUTE_INPUT_PAIRS if workload == "compute-heavy" else None,
        "git_commit": git_revision(), "binary_sha256": release_hash(),
    }
    if any(record.get(key) != value for key, value in required.items()):
        raise RuntimeError(f"Calibration identity mismatch; do not mix revisions: {file}")
    if not math.isfinite(float(record["reference_wu_s"])) or record["reference_wu_s"] <= 0:
        raise RuntimeError(f"Invalid saturated reference: {file}")
    return record


def calibrate_one(workload: str, workers: int, ops: int) -> dict:
    path = calibration_file(workload, workers, ops)
    if path.exists():
        return load_reference(workload, workers, ops)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = path.with_suffix(".sqlite3")
    if db.exists():
        raise RuntimeError(f"Orphaned calibration DB {db}; inspect before retrying")

    calibration_wu = (COMPUTE_CALIBRATION_WU
                      if workload == "compute-heavy" else CALIBRATION_WU)
    args = [
        str(RUST), "--workload", workload, "--scheduler", "threadance",
        "--threadance-wait-policy", "spin-then-park", "--threadance-spin-us", "0",
        "--workers", str(workers), "--queue-capacity", "64",
        "--work-units", str(calibration_wu),
        "--seed", "1", "--warmup", str(WARMUPS), "--runs", str(CALIBRATION_RUNS),
        "--all-at-once", "--profile", "none", "--database", str(db),
        "--campaign-id", f"{NAME}-reference-{label(workload, ops)}-w{workers}",
    ]
    if workload == "compute-heavy":
        args += ["--operations-per-work-unit", str(ops),
                 "--compute-input-pairs", str(COMPUTE_INPUT_PAIRS)]
    else:
        args += [
            "--dataset-work-units", str(CALIBRATION_WU),
            "--validators", "2000", "--signers-per-certificate", "1600",
            "--certificates-per-work-unit", "1",
            "--dataset-cache-dir", str((ROOT / "results/datasets").resolve()),
        ]
    subprocess.run(args, cwd=ROOT, check=True)
    with sqlite3.connect(db) as conn:
        rates = [float(row[0]) for row in conn.execute(
            "SELECT work_units_per_second FROM run ORDER BY id"
        )]
    if len(rates) != CALIBRATION_RUNS or any(
        not math.isfinite(rate) or rate <= 0 for rate in rates
    ):
        raise RuntimeError(f"Invalid saturation calibration runs in {db}")
    record = {
        "workload": workload, "workers": workers,
        "operations_per_work_unit": ops if workload == "compute-heavy" else None,
        "compute_input_pairs": COMPUTE_INPUT_PAIRS if workload == "compute-heavy" else None,
        "calibration_work_units": calibration_wu,
        "calibration_policy": "CrossbeamMpmc / SpinThenPark(0)",
        "calibration_warmups": WARMUPS, "calibration_measured_runs": CALIBRATION_RUNS,
        "reference_wu_s": statistics.median(rates),
        "reference_kind": "median all-at-once throughput; per workload and worker count",
        "git_commit": git_revision(), "binary_sha256": release_hash(),
        "database": str(db.relative_to(ROOT)),
    }
    path.write_text(json.dumps(record, indent=2) + "\n")
    return record


def window_seconds(reference: float, ops: int, max_data_mib: int,
                   target_window_ms: int) -> float:
    """Fixed window with explicit bounded-input memory and pacing guards."""
    del ops
    footprint = COMPUTE_INPUT_PAIRS * BYTES_PER_OPERATION
    if footprint > max_data_mib * 1024 * 1024:
        raise ValueError(
            f"Compute-heavy input pool requires {footprint / 1048576:.1f} MiB; "
            f"configured cap is {max_data_mib} MiB"
        )
    if reference * max(LOADS) / 100 > MAX_PACED_RATE_WU_S:
        raise ValueError(
            f"Compute-heavy would emit >{MAX_PACED_RATE_WU_S:.0f} WU/s; "
            "increase --ops-per-wu and recalibrate to avoid unreliable short sleeps"
        )
    return target_window_ms / 1000.0


def points(workload: str, workers: int, ops: int,
           target_window_ms: int, max_data_mib: int) -> list[tuple[int, int, float, float]]:
    reference = float(load_reference(workload, workers, ops)["reference_wu_s"])
    duration = (window_seconds(reference, ops, max_data_mib, target_window_ms)
                if workload == "compute-heavy" else target_window_ms / 1000)
    out = []
    for pct in LOADS:
        rate = reference * pct / 100
        units = math.ceil(duration * rate) + 1
        if workload == "bls-aggregate-verify" and units > CALIBRATION_WU:
            raise ValueError(f"BLS dataset too small at {workers} workers / {pct}%")
        if units < 2 or (units - 1) / rate < duration - 1e-9:
            raise ValueError("Invalid paced schedule")
        out.append((pct, units, rate, (units - 1) / rate))
    return out

def definition(workload: str, workers: int, mode: str, pct: int,
               ops: int, target_window_ms: int, max_data_mib: int):
    if mode not in MODES or workers not in WORKERS:
        raise ValueError("Unsupported arrival mode or worker count")
    point = next(p for p in points(workload, workers, ops,
                                   target_window_ms, max_data_mib) if p[0] == pct)
    _, units, rate, _ = point
    data = copy.deepcopy(read_definition(BASE))
    exp_name = f"{NAME}-{label(workload, ops)}-w{workers}-load{pct:03d}-{mode}"
    data["experiment"] = {
        "name": exp_name, "workload": workload, "profile": "deep",
    }
    data["workload"] = {
        "workers": workers, "work_units": units,
        "workload_seed": 1, "queue_capacity": 64,
    }
    if workload == "compute-heavy":
        data["workload"]["operations_per_work_unit"] = ops
        data["workload"]["compute_input_pairs"] = COMPUTE_INPUT_PAIRS
    else:
        data["workload"].update(
            dataset_work_units=CALIBRATION_WU, validators=2000,
            signers_per_certificate=1600, certificates_per_work_unit=1,
        )
    data["delivery"] = {
        "mode": mode, "arrival_rates": [rate], "arrival_seed_base": 202611120,
    }
    data["configurations"] = configurations()
    data["repetitions"] = {
        "blocks": BLOCKS, "warmup_runs_per_block": WARMUPS,
        "measured_runs_per_block": REPEATS,
    }
    seed = SEED * 1000 + workers * 100 + pct * 2 + MODES.index(mode)
    if workload == "compute-heavy":
        seed += 100000
    data["randomization"] = {"seed": seed}
    data["analysis"].update(
        reference_configuration="mpmc-sp0", bootstrap_seed=seed,
    )
    data["execution"] = {"settle_seconds": 0.0}
    data["perf"] = {"passes": [p for p in data["perf"]["passes"]
                               if p["name"] == "cpu-cache"]}
    if len(data["perf"]["passes"]) != 1:
        raise ValueError("Missing cpu-cache PMU pass in the base definition")
    data["artifacts"]["output_root"] = str(RESULTS.relative_to(ROOT))
    path = GENERATED / (exp_name + ".toml")
    rendered = render_toml(data)
    if tomllib.loads(rendered) != data:
        raise ValueError("Generated TOML failed round-trip")
    if len(make_plan(data, None)) != BLOCKS * len(configurations()):
        raise ValueError("Incorrect randomized-block plan")
    return data, path, rendered


def entries(workers: tuple[int, ...], workloads: tuple[str, ...],
            modes: tuple[str, ...], ops: int, target_window_ms: int,
            max_data_mib: int):
    items = [
        definition(workload, workers_count, mode, pct,
                   ops, target_window_ms, max_data_mib)
        for workload in workloads for workers_count in workers
        for mode in modes for pct in LOADS
    ]
    random.Random(SEED).shuffle(items)
    return items



def audit_pmu(folder: Path, expected_invocations: int) -> None:
    """Reject missing/multiplexed cpu-cache perf captures before accepting a campaign."""
    data = read_definition(folder / "experiment.toml")
    required = {event.split(":")[0] for event in data["perf"]["passes"][0]["events"]}
    grouped: dict[int, dict[str, tuple]] = {}
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        rows = db.execute(
            "SELECT r.id, r.perf_pass, p.event_name, p.counter_value, "
            "p.percent_running, p.status, pc.perf_exit_code "
            "FROM run r JOIN perf_metric p ON p.run_id=r.id "
            "JOIN perf_capture pc ON pc.run_id=r.id"
        ).fetchall()
    for run_id, pass_name, event, value, running, status, exit_code in rows:
        base = event.split(":")[0]
        capture = grouped.setdefault(run_id, {})
        if pass_name != "cpu-cache" or base in capture:
            raise RuntimeError(f"Duplicate or unexpected perf event: {folder}, {run_id}")
        capture[base] = (value, running, status, exit_code)
    if len(grouped) != expected_invocations * REPEATS:
        raise RuntimeError(f"Missing profiled runs in {folder}: {len(grouped)}")
    for run_id, capture in grouped.items():
        if set(capture) != required:
            raise RuntimeError(f"Missing perf events in {folder}, run {run_id}")
        for value, running, status, exit_code in capture.values():
            if (value is None or status != "ok" or exit_code != 0
                    or (running is not None and not 99.5 <= running <= 100.01)):
                raise RuntimeError(f"Unavailable/multiplexed perf data: {folder}, run {run_id}")


def collect(items: list, budget_hours: float) -> dict:
    preflight(perf=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    finished = 0
    started = time.monotonic()
    with (RESULTS / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, (data, path, rendered) in enumerate(items, 1):
            if budget_hours and time.monotonic() - started >= budget_hours * 3600:
                break
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != rendered:
                raise RuntimeError(f"Generated definition differs: {path}")
            path.write_text(rendered)
            name = data["experiment"]["name"]
            expected = BLOCKS * len(configurations())
            old = existing_campaign(name, path, RESULTS, cache_interference=False,
                                    pin_workers=False)
            if old is None and any(RESULTS.glob(name + "-*")):
                raise RuntimeError(f"Conflicting archived campaign: {name}")
            print(f"[{index}/{len(items)}] {name}", flush=True)
            if old is None or not completed(old, expected):
                subprocess.run(command("prepare", path), cwd=ROOT, check=True)
                cmd = command("resume" if old else "collect", path, old)
                subprocess.run(cmd, cwd=ROOT, check=True)
                old = existing_campaign(name, path, RESULTS,
                                        cache_interference=False, pin_workers=False)
            if old is None or not completed(old, expected):
                raise RuntimeError(f"Incomplete campaign: {name}")
            verify_campaign_database(old, data, expected)
            audit_pmu(old, expected)
            if not (old / "analysis_raw_runs.csv").is_file():
                subprocess.run(command("analyze", path, old), cwd=ROOT, check=True)
            finished += 1
    return {
        "completed_in_selected_plan": finished, "selected_campaigns": len(items),
        "all_selected_complete": finished == len(items),
        "output_root": str(RESULTS), "budget_hours": budget_hours,
        "note": "Use the same --run command to resume; keep Git HEAD unchanged.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--calibrate", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, nargs="+", default=list(DEFAULT_WORKERS))
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS, default=list(WORKLOADS))
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--ops-per-wu", type=int, default=16384)
    parser.add_argument("--target-window-ms", type=int, default=200)
    parser.add_argument("--max-compute-mib", type=int, default=1024)
    parser.add_argument("--budget-hours", type=float, default=0,
                        help="Optional campaign-start time cap; 0 disables it")
    args = parser.parse_args()
    if (not args.workers or len(set(args.workers)) != len(args.workers)
            or set(args.workers) - set(WORKERS)
            or len(set(args.workloads)) != len(args.workloads)
            or len(set(args.modes)) != len(args.modes)
            or args.ops_per_wu <= 0
            or not 100 <= args.target_window_ms <= 2000
            or not 128 <= args.max_compute_mib <= 65536
            or args.budget_hours < 0):
        parser.error("Invalid study axis or numeric setting")

    selected_workers = tuple(args.workers)
    workloads = tuple(args.workloads)
    modes = tuple(args.modes)
    if args.calibrate:
        preflight(perf=False)
        build_harness()
        results = {}
        for workload in workloads:
            for w in selected_workers:
                record = calibrate_one(workload, w, args.ops_per_wu)
                results[f"{label(workload, args.ops_per_wu)}-w{w}"] = record["reference_wu_s"]
        print(json.dumps({"reference_wu_s": results}, indent=2))
        return

    missing = [
        str(calibration_file(workload, w, args.ops_per_wu))
        for workload in workloads for w in selected_workers
        if not calibration_file(workload, w, args.ops_per_wu).is_file()
    ]
    if missing:
        if args.run:
            raise SystemExit("Missing calibrations: " + ", ".join(missing)
                             + "; run --calibrate first")
        print(json.dumps({
            "study": NAME, "calibration_missing": missing,
            "planned_campaigns": len(selected_workers)*len(workloads)*len(modes)*len(LOADS),
            "planned_invocations": len(selected_workers)*len(workloads)*len(modes)*len(LOADS)
                                   *BLOCKS*len(configurations()),
            "blocks": BLOCKS, "warmups": WARMUPS, "measured_repetitions": REPEATS,
            "policies": [item["id"] for item in configurations()],
            "offered_load_pct": LOADS,
        }, indent=2))
        return

    items = entries(selected_workers, workloads, modes, args.ops_per_wu,
                    args.target_window_ms, args.max_compute_mib)
    if args.check:
        rate_info = {}
        for workload in workloads:
            for workers in selected_workers:
                cells = points(workload, workers, args.ops_per_wu,
                               args.target_window_ms, args.max_compute_mib)
                rate_info[f"{label(workload,args.ops_per_wu)}-w{workers}"] = {
                    "reference_wu_s": load_reference(workload, workers, args.ops_per_wu)["reference_wu_s"],
                    "max_work_units": max(p[1] for p in cells),
                    "nominal_window_ms": round(cells[0][3]*1000, 2),
                }
        print(json.dumps({
            "study": NAME, "campaigns": len(items),
            "invocations": len(items)*BLOCKS*len(configurations()),
            "warmup_runs": len(items)*BLOCKS*len(configurations())*WARMUPS,
            "measured_runs": len(items)*BLOCKS*len(configurations())*REPEATS,
            "arrival_modes": modes, "loads": LOADS, "capacity_references": rate_info,
            "pin_workers": False, "background_interference": False,
            "profile": "deep/cpu-cache only",
        }, indent=2))
        return
    print(json.dumps(collect(items, args.budget_hours), indent=2))


if __name__ == "__main__":
    main()
