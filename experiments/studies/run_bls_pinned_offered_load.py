"""Matched pinned BLS offered-load sweep (clean versus background interference).

No new capacity calibration. Absolute arrival rates are frozen so both arms
receive identical demand. This deliberately does NOT measure a pinning effect.
Only one CPU/cache PMU pass is used to keep the acquisition bounded.

Examples:
    python3 -m experiments.studies.run_bls_pinned_offered_load --check
    python3 -m experiments.studies.run_bls_pinned_offered_load --run --workers 2 4
    python3 -m experiments.studies.run_bls_pinned_offered_load --run --workers 8
    python3 -m experiments.studies.run_bls_pinned_offered_load --run --workers 16
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

from experiments.common.cache_interference import campaign_signals, run_supervised
from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign

STUDY = "bls-pinned-offered-load"
BASE = ROOT / "experiments/studies/bls_pinned_control_template.toml"
RESULTS = ROOT / "results/studies" / STUDY
GENERATED = ROOT / "experiments/generated" / STUDY
WORKERS = (2, 4, 8, 16)
MODES = ("fixed-arrivals", "bursty-arrivals")
ARMS = ("clean", "interference")
# Rates are common to both environments at each worker count. Existing
# 900/1500 anchors are retained where they are within stressed capacity.
RATES = {
    2: (270.0, 430.0, 540.0, 720.0, 900.0),
    4: (600.0, 900.0, 1200.0, 1500.0, 1850.0),
    8: (900.0, 1500.0, 2000.0, 2600.0, 3400.0),
    16: (900.0, 1500.0, 2200.0, 3000.0, 3900.0),
}
BLOCKS = 20
WARMUPS = 4
REPEATS = 2
WORK_UNITS = 1024
SEED = 20261110
CONFIGS = {"rayon", "bevy", "mpmc-sp0", "mpmc-ab",
           "rtrb-sp0-b1", "rtrb-sp0-b2", "mpmc-sp40"}


def build_definition(workers: int, mode: str, arm: str) -> tuple[dict, Path, str]:
    if workers not in WORKERS or mode not in MODES or arm not in ARMS:
        raise ValueError("Unsupported workers, mode or experimental arm")
    data = copy.deepcopy(read_definition(BASE))
    configs = {c["id"] for c in data["configurations"]}
    if configs != CONFIGS:
        raise ValueError(f"Scheduler set changed in template: {configs}")
    passes = [p for p in data["perf"]["passes"] if p["name"] == "cpu-cache"]
    if len(passes) != 1:
        raise ValueError("Exactly one cpu-cache PMU pass is required")
    name = f"{STUDY}-{arm}-w{workers}-{mode}"
    data["experiment"].update(name=name, profile="deep")
    data["workload"].update(workers=workers, work_units=WORK_UNITS,
                            dataset_work_units=2500, queue_capacity=64)
    data["delivery"] = {
        "mode": mode, "arrival_rates": list(RATES[workers]), "arrival_seed_base": 42,
    }
    data["repetitions"] = {
        "blocks": BLOCKS, "warmup_runs_per_block": WARMUPS,
        "measured_runs_per_block": REPEATS,
    }
    # The two arms use the SAME condition order and arrival seeds.
    # This is not a randomized cross-arm allocation.
    mode_seed = SEED + workers * 10 + MODES.index(mode)
    data["randomization"]["seed"] = mode_seed
    data["analysis"]["bootstrap_seed"] = mode_seed
    data["execution"]["settle_seconds"] = 0.0
    data["perf"] = {"passes": copy.deepcopy(passes)}
    data["artifacts"]["output_root"] = str((RESULTS / arm / "campaigns").relative_to(ROOT))
    path = GENERATED / arm / f"{name}.toml"
    serialized = render_toml(data)
    if tomllib.loads(serialized) != data:
        raise ValueError("Generated TOML does not roundtrip")
    if len(make_plan(data, None)) != BLOCKS * len(CONFIGS) * len(RATES[workers]):
        raise ValueError("Unexpected number of randomized invocations")
    # Ensure a >=200 ms scheduled arrival window even at the highest rate.
    if (WORK_UNITS - 1) / max(RATES[workers]) < 0.2:
        raise ValueError("Paced batch duration is below 200 ms")
    return data, path, serialized


def selected(workers: tuple[int, ...], modes: tuple[str, ...],
             arms: tuple[str, ...]) -> list[tuple[dict, Path, str]]:
    entries = []
    for workers_count in workers:
        for mode in modes:
            # Alternate the clean/stressed order between worker/mode pairs.
            arm_order = list(ARMS)
            if (WORKERS.index(workers_count) + MODES.index(mode)) % 2:
                arm_order.reverse()
            for arm in arm_order:
                if arm in arms:
                    entries.append(build_definition(workers_count, mode, arm))
    return entries


def preflight() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Collect as an ordinary user, not root")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, text=True,
    ).strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before collecting")
    subprocess.run(
        [sys.executable, "-m", "experiments.studies.check_perf_scope"],
        cwd=ROOT, check=True,
    )


def audit_pmu(folder: Path, expected_invocations: int) -> None:
    """Check the complete, unmultiplexed single-pass PMU capture."""
    definition = read_definition(folder / "experiment.toml")
    expected_events = {event.split(":")[0] for event in
                       definition["perf"]["passes"][0]["events"]}
    expected_runs = expected_invocations * REPEATS
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        measurements = db.execute(
            "SELECT r.id, r.perf_pass, p.event_name, p.counter_value, "
            "p.percent_running, p.status, pc.perf_exit_code "
            "FROM run r JOIN perf_metric p ON p.run_id=r.id "
            "JOIN perf_capture pc ON pc.run_id=r.id"
        ).fetchall()
    by_run: dict[int, dict[str, tuple]] = {}
    for run_id, perf_pass, name, value, running, status, exit_code in measurements:
        if perf_pass != "cpu-cache":
            raise RuntimeError(f"Unexpected PMU pass {perf_pass} in {folder}")
        event = name.split(":")[0]
        events = by_run.setdefault(run_id, {})
        if event in events:
            raise RuntimeError(f"Duplicate PMU event {event} in run {run_id}")
        events[event] = (value, running, status, exit_code)
    if len(by_run) != expected_runs:
        raise RuntimeError(f"{folder}: expected {expected_runs} PMU runs, got {len(by_run)}")
    for run_id, events in by_run.items():
        if set(events) != expected_events:
            raise RuntimeError(f"Missing PMU events in run {run_id}: {set(events)}")
        for value, running, status, exit_code in events.values():
            if (value is None or status != "ok" or exit_code != 0
                    or (running is not None and not 99.5 <= running <= 100.01)):
                raise RuntimeError(f"Missing/invalid/multiplexed PMU capture: run {run_id}")


def run(entries: list[tuple[dict, Path, str]]) -> dict:
    preflight()
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, (data, path, serialized) in enumerate(entries, 1):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != serialized:
                raise RuntimeError(f"Conflicting generated definition: {path}")
            path.write_text(serialized)
            arm = data["experiment"]["name"].split(f"{STUDY}-", 1)[1].split("-", 1)[0]
            interfered = arm == "interference"
            output = ROOT / data["artifacts"]["output_root"]
            output.mkdir(parents=True, exist_ok=True)
            name = data["experiment"]["name"]
            expected = len(make_plan(data, None))
            previous = existing_campaign(
                name, path, output, cache_interference=interfered, pin_workers=True,
            )
            if previous is None and any(output.glob(name + "-*")):
                raise RuntimeError(f"Conflicting campaign for {name}")
            print(f"[{index}/{len(entries)}] {name}", flush=True)
            if previous is None or not completed(previous, expected):
                subprocess.run(
                    command("prepare", path, cache_interference=interfered,
                            pin_workers=True), cwd=ROOT, check=True,
                )
                argv = command(
                    "resume" if previous else "collect", path, previous,
                    cache_interference=interfered, pin_workers=True,
                )
                if interfered:
                    with campaign_signals():
                        run_supervised(argv, cwd=ROOT, stop_timeout=15).check_returncode()
                else:
                    subprocess.run(argv, cwd=ROOT, check=True)
                previous = existing_campaign(
                    name, path, output, cache_interference=interfered,
                    pin_workers=True,
                )
            if previous is None or not completed(previous, expected):
                raise RuntimeError(f"Campaign incomplete: {name}")
            verify_campaign_database(previous, data, expected)
            audit_pmu(previous, expected)
            if not (previous / "analysis_raw_runs.csv").is_file():
                subprocess.run(command("analyze", path, previous), cwd=ROOT, check=True)
    invocations = sum(len(make_plan(data, None)) for data, _, _ in entries)
    return {
        "campaigns": len(entries),
        "invocations": invocations,
        "measured_physical_runs": invocations * REPEATS,
        "warmup_runs": invocations * WARMUPS,
        "output_root": str(RESULTS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="Dry-run plan; no writes")
    actions.add_argument("--run", action="store_true", help="Collect/resume selected arms")
    parser.add_argument("--workers", type=int, nargs="+", default=[2, 4, 8],
                        help="2 4 8 by default; 16 optional")
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS))
    args = parser.parse_args()
    if (len(set(args.workers)) != len(args.workers)
            or set(args.workers) - set(WORKERS)
            or len(set(args.modes)) != len(args.modes)
            or len(set(args.arms)) != len(args.arms)):
        parser.error("Unknown or repeated workers/modes/arms")
    entries = selected(tuple(args.workers), tuple(args.modes), tuple(args.arms))
    invocations = sum(len(make_plan(data, None)) for data, _, _ in entries)
    if args.check:
        result = {
            "study": STUDY,
            "workers": args.workers,
            "modes": args.modes,
            "arms": args.arms,
            "rates_wu_per_second": {str(w): RATES[w] for w in args.workers},
            "work_units": WORK_UNITS,
            "blocks": BLOCKS,
            "warmups_per_invocation": WARMUPS,
            "measured_repetitions_per_invocation": REPEATS,
            "perf_passes": ["cpu-cache"],
            "campaigns": len(entries),
            "invocations": invocations,
            "measured_physical_runs": invocations * REPEATS,
            "warmup_runs": invocations * WARMUPS,
            "min_scheduled_window_seconds": round(
                (WORK_UNITS - 1) / max(max(RATES[w]) for w in args.workers), 4
            ),
            "baseline_note": "Fresh 1024-WU pinned clean arm is the matched control; "
                             "older 2500-WU pinned controls must not be pooled.",
        }
    else:
        result = run(entries)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
