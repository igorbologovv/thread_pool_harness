"""Additional pinned STRESSED BLS offered-load sweep with >=200 ms arrival windows.

Extends the original 2500-WU pinned interference controls at different
offered loads. Never runs a clean arm or overwrites old campaigns.

    python3 -m experiments.studies.run_bls_pinned_interference_load_sweep --check
    python3 -m experiments.studies.run_bls_pinned_interference_load_sweep --run --workers 2 4
    python3 -m experiments.studies.run_bls_pinned_interference_load_sweep --run --workers 8 16
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

from experiments.common.cache_interference import campaign_signals, run_supervised
from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign

NAME = "bls-pinned-interference-load-sweep"
BASE = ROOT / "experiments/studies/bls_pinned_control_template.toml"
RESULTS = ROOT / "results/studies" / NAME / "campaigns"
GENERATED = ROOT / "experiments/generated" / NAME
WORKERS = (2, 4, 8, 16)
MODES = ("fixed-arrivals", "bursty-arrivals")
# Frozen mean Rayon throughput in the original PINNED INTERFERENCE all-at-once
# cpu-cache block summaries (20 blocks x 2 repetitions, 2500 WU; 2026-11-05).
# This is a capacity REFERENCE for x-axis design, not actual CPU utilization.
STRESSED_CAPACITY_WU_S = {
    2: 1079.6329437074166,
    4: 2141.9489212053763,
    8: 4012.266936881269,
    16: 4658.680879056548,
}
LOAD_PERCENT = {
    2: (25, 40, 45, 50, 65, 80),
    4: (25, 40, 50, 65, 80),
    8: (25, 40, 50, 65, 80),
    16: (25, 40, 50, 65, 80),
}
TARGET_WINDOW_SECONDS = 0.2
DATASET_WORK_UNITS = 2500
BLOCKS = 20
WARMUPS = 4
REPEATS = 2
SEED = 20261110
CONFIGURATIONS = {"rayon", "bevy", "mpmc-sp0", "mpmc-ab",
                  "rtrb-sp0-b1", "rtrb-sp0-b2", "mpmc-sp40"}


def points(workers: int) -> list[tuple[int, int, int, float]]:
    """(nominal load %, requested arrival rate, WU, scheduled window seconds)."""
    if workers not in WORKERS:
        raise ValueError(f"Unsupported worker count: {workers}")
    result = []
    for pct in LOAD_PERCENT[workers]:
        # Nearest integer rate; no cross-policy recalibration.
        rate = math.floor(STRESSED_CAPACITY_WU_S[workers] * pct / 100 + 0.5)
        # N arrivals = N-1 gaps; the harness uses T=(N-1)/rate.
        work_units = math.ceil(TARGET_WINDOW_SECONDS * rate) + 1
        window = (work_units - 1) / rate
        if not (2 <= work_units <= DATASET_WORK_UNITS
                and TARGET_WINDOW_SECONDS <= window < TARGET_WINDOW_SECONDS + 1 / rate):
            raise ValueError(f"Invalid 200-ms BLS window at w{workers}/{pct}%")
        result.append((pct, rate, work_units, window))
    if len({rate for _, rate, _, _ in result}) != len(result):
        raise ValueError("Rate rounding collapsed load levels")
    return result


def definition(workers: int, mode: str, percent: int) -> tuple[dict, Path, str]:
    if mode not in MODES:
        raise ValueError(f"Unexpected delivery mode: {mode}")
    point = next((p for p in points(workers) if p[0] == percent), None)
    if point is None:
        raise ValueError(f"Unexpected offered load: {percent}%")
    _, rate, work_units, _ = point
    data = copy.deepcopy(read_definition(BASE))
    if {x["id"] for x in data["configurations"]} != CONFIGURATIONS:
        raise ValueError("Pinned-control scheduler configurations have changed")
    passes = [p for p in data["perf"]["passes"] if p["name"] == "cpu-cache"]
    if len(passes) != 1:
        raise ValueError("Pinned-control cpu-cache pass missing or duplicated")
    name = f"{NAME}-w{workers}-load{percent:03d}-{mode}"
    data["experiment"].update(name=name, profile="deep")
    data["workload"].update(workers=workers, work_units=work_units,
                            dataset_work_units=DATASET_WORK_UNITS, queue_capacity=64)
    data["delivery"] = {"mode": mode, "arrival_rates": [float(rate)],
                        "arrival_seed_base": 42}
    data["repetitions"] = {"blocks": BLOCKS, "warmup_runs_per_block": WARMUPS,
                           "measured_runs_per_block": REPEATS}
    random_seed = SEED + workers * 100 + percent * 2 + MODES.index(mode)
    data["randomization"]["seed"] = random_seed
    data["analysis"]["bootstrap_seed"] = random_seed
    data["execution"]["settle_seconds"] = 0.0
    data["perf"] = {"passes": copy.deepcopy(passes)}
    data["artifacts"]["output_root"] = str(RESULTS.relative_to(ROOT))
    path = GENERATED / f"{name}.toml"
    serialized = render_toml(data)
    if tomllib.loads(serialized) != data:
        raise ValueError("Generated experiment TOML did not round-trip")
    if len(make_plan(data, None)) != BLOCKS * len(CONFIGURATIONS):
        raise ValueError("Unexpected randomized-block invocation count")
    return data, path, serialized


def entries(workers: tuple[int, ...], modes: tuple[str, ...]) -> list[tuple[dict, Path, str]]:
    result = [definition(w, mode, pct)
              for w in workers for mode in modes for pct in LOAD_PERCENT[w]]
    # Deterministic campaign order to avoid systematically collecting low loads first.
    random.Random(SEED).shuffle(result)
    return result


def preflight() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run benchmarks as the normal user, not root")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                                    cwd=ROOT, text=True).strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before collecting measurements")
    subprocess.run([sys.executable, "-m", "experiments.studies.check_perf_scope"],
                   cwd=ROOT, check=True)


def audit_pmu(folder: Path, expected_invocations: int) -> None:
    """Require the complete single cpu-cache PMU pass with no multiplexing."""
    data = read_definition(folder / "experiment.toml")
    required_events = {e.split(":")[0] for e in data["perf"]["passes"][0]["events"]}
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        rows = db.execute(
            "SELECT r.id, r.perf_pass, p.event_name, p.counter_value, "
            "p.percent_running, p.status, pc.perf_exit_code "
            "FROM run r JOIN perf_metric p ON p.run_id=r.id "
            "JOIN perf_capture pc ON pc.run_id=r.id"
        ).fetchall()
    grouped: dict[int, dict[str, tuple]] = {}
    for run_id, pass_name, event, value, running, status, exit_code in rows:
        base = event.split(":")[0]
        capture = grouped.setdefault(run_id, {})
        if pass_name != "cpu-cache" or base in capture:
            raise RuntimeError(f"Unexpected/duplicate PMU event in {folder}, run {run_id}")
        capture[base] = (value, running, status, exit_code)
    if len(grouped) != expected_invocations * REPEATS:
        raise RuntimeError(f"{folder}: incomplete PMU run count ({len(grouped)})")
    for run_id, capture in grouped.items():
        if set(capture) != required_events:
            raise RuntimeError(f"{folder}: incomplete events, run {run_id}")
        for value, running, status, exit_code in capture.values():
            if (value is None or status != "ok" or exit_code != 0
                    or (running is not None and not 99.5 <= running <= 100.01)):
                raise RuntimeError(f"{folder}: unavailable/multiplexed PMU run {run_id}")


def collect(plan: list[tuple[dict, Path, str]]) -> dict:
    preflight()
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, (data, path, serialized) in enumerate(plan, 1):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != serialized:
                raise RuntimeError(f"Conflicting generated definition: {path}")
            path.write_text(serialized)
            name = data["experiment"]["name"]
            expected = len(make_plan(data, None))
            prior = existing_campaign(name, path, RESULTS, cache_interference=True,
                                      pin_workers=True)
            if prior is None and any(RESULTS.glob(name + "-*")):
                raise RuntimeError(f"Conflicting existing campaign for {name}")
            print(f"[{index}/{len(plan)}] {name}", flush=True)
            if prior is None or not completed(prior, expected):
                subprocess.run(command("prepare", path, cache_interference=True,
                                       pin_workers=True), cwd=ROOT, check=True)
                argv = command("resume" if prior else "collect", path, prior,
                               cache_interference=True, pin_workers=True)
                with campaign_signals():
                    run_supervised(argv, cwd=ROOT, stop_timeout=15).check_returncode()
                prior = existing_campaign(name, path, RESULTS, cache_interference=True,
                                          pin_workers=True)
            if prior is None or not completed(prior, expected):
                raise RuntimeError(f"Incomplete stress campaign: {name}")
            verify_campaign_database(prior, data, expected)
            audit_pmu(prior, expected)
            if not (prior / "analysis_raw_runs.csv").is_file():
                subprocess.run(command("analyze", path, prior), cwd=ROOT, check=True)
    count = sum(len(make_plan(data, None)) for data, _, _ in plan)
    return {"campaigns": len(plan), "invocations": count,
            "physical_runs": count * REPEATS, "warmup_runs": count * WARMUPS,
            "output_root": str(RESULTS)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Show plan; no writes")
    action.add_argument("--run", action="store_true", help="Collect/resume only stressed runs")
    parser.add_argument("--workers", nargs="+", type=int, default=[2, 4, 8],
                        help="Worker counts to include, default 2 4 8; 16 optional")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    if (len(set(args.workers)) != len(args.workers) or
            len(set(args.modes)) != len(args.modes) or
            set(args.workers) - set(WORKERS)):
        parser.error("Unknown or repeated worker count or arrival mode")
    plan = entries(tuple(args.workers), tuple(args.modes))
    if args.check:
        count = sum(len(make_plan(data, None)) for data, _, _ in plan)
        result = {
            "study": NAME, "environment": "pinned-interference-only",
            "rates": {str(w): [{"nominal_load_pct": pct, "arrival_rate_wu_s": rate,
                               "work_units": n, "window_ms": round(window * 1000, 3)}
                              for pct, rate, n, window in points(w)]
                      for w in args.workers},
            "modes": args.modes, "blocks": BLOCKS,
            "warmups_per_invocation": WARMUPS, "measured_runs_per_invocation": REPEATS,
            "perf_passes": ["cpu-cache"], "campaigns": len(plan),
            "invocations": count, "physical_runs": count * REPEATS,
            "warmup_runs": count * WARMUPS,
            "note": "Archival 900/1500 WU/s runs contain 2500 WU; "
                    "do not pool with these ~200 ms campaigns without a length-sensitivity check.",
        }
    else:
        result = collect(plan)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
