"""Acquire matching BLS worker-count data and test frozen cubic holdouts.

Stages: --check (no writes), --capacity (only missing 6/10/12 Rayon rates),
--run (holdout counts x fixed/bursty, six schedulers), --evaluate.
Reuse archived 2/4/8/16 data without new collection. Each paced batch has
at least a 200 ms scheduled arrival window, matching the original design.
"""
from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import json
import math
import os
import random
import statistics
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import design, render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.bls_worker_cubic_model import evaluate_holdouts
from experiments.studies.run_bls_normalized_deep_final import audit_perf_captures

STUDY = "bls-worker-cubic-holdout"
ROOT_DIR = ROOT / "results/studies" / STUDY
CAPACITY_SPEC = ROOT / "experiments/studies/bls_worker_cubic_capacity.toml"
CAPACITY_OUTPUT = ROOT_DIR / "capacity/campaigns"
CAPACITY_FILE = ROOT_DIR / "capacity_reference.json"
OUTPUT = ROOT_DIR / "campaigns"
GENERATED = ROOT / "experiments/generated" / STUDY
BASE = ROOT / "experiments/studies/bls_saturation_recheck_template.toml"
PERF_SPEC = ROOT / "experiments/studies/bls_advanced_perf.toml"
WORKERS = (2, 4, 6, 8, 10, 12, 16)
OLD_WORKERS = (2, 4, 8, 16)
NEW_WORKERS = (6, 10, 12)
SATURATION_ARCHIVE = ROOT / "results/studies/bls-saturation-recheck/campaigns"
CPU_ARCHIVE = ROOT / "results/studies/bls-normalized-deep-final/campaigns"
MIN_WINDOW_SECONDS = 0.2
MODES = ("fixed-arrivals", "bursty-arrivals")
SCHEDULERS = {"rayon", "bevy", "mpmc-sp0", "mpmc-ab", "rtrb-sp0-b1", "rtrb-sp0-b2"}
BLOCKS = 40
SEED = 20261108


def commit_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def preflight() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Run as the normal user, not root")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                                    cwd=ROOT, text=True).strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before collecting")
    subprocess.run([sys.executable, "-m", "experiments.studies.check_perf_scope"],
                   cwd=ROOT, check=True)


def load_capacity() -> dict[int, float]:
    record = json.loads(CAPACITY_FILE.read_text())
    if record["git_commit"] != commit_sha():
        raise ValueError("Capacity reference came from a different Git commit")
    values = {int(k): float(v) for k, v in record["rayon_wu_per_second"].items()}
    if set(values) != set(WORKERS) or any(not math.isfinite(v) or v <= 0 for v in values.values()):
        raise ValueError("Capacity reference needs seven positive finite rates")
    return values



def _read_ray_saturation(root: Path, workers: int, study_prefix: str) -> float:
    name = f"{study_prefix}-w{workers}-n2500-g1-all-at-once"
    folders = sorted(
        path for path in root.glob(name + "-*")
        if path.is_dir() and (path / "analysis_raw_runs.csv").is_file()
    )
    if len(folders) != 1:
        raise RuntimeError(
            f"Need one completed 2500-WU saturation recheck: {name} under {root}; "
            f"found {len(folders)}. Do not substitute provisional throughput."
        )
    blocks = defaultdict(list)
    with (folders[0] / "analysis_raw_runs.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["configuration"] == "rayon":
                blocks[int(row["block"])].append(float(row["throughput"]))
    if set(blocks) != set(range(1, 31)) or any(len(x) != 3 for x in blocks.values()):
        raise RuntimeError(f"Incomplete 30-block x 3 Rayon reference: {folders[0]}")
    return statistics.mean(statistics.mean(samples) for samples in blocks.values())


def calibrate_capacity() -> dict:
    preflight()
    report = design(CAPACITY_SPEC, write=True)
    if len(report["campaigns"]) != len(NEW_WORKERS):
        raise RuntimeError("Expected three missing-worker saturation campaigns")
    generated = ROOT / "experiments/generated/bls-worker-cubic-capacity"
    CAPACITY_OUTPUT.mkdir(parents=True, exist_ok=True)
    with (CAPACITY_OUTPUT / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, entry in enumerate(report["campaigns"], 1):
            path = generated / entry["file"]
            definition = read_definition(path)
            if definition["workload"]["workers"] not in NEW_WORKERS:
                raise RuntimeError("Refusing to recalibrate an existing worker count")
            prior = existing_campaign(entry["name"], path, CAPACITY_OUTPUT)
            if prior is None and any(CAPACITY_OUTPUT.glob(entry["name"] + "-*")):
                raise RuntimeError(f"Conflicting saturation campaign: {entry['name']}")
            print(f"[capacity {index}/3] {entry['name']}", flush=True)
            if prior is None or not completed(prior, entry["invocations"]):
                subprocess.run(command("prepare", path), cwd=ROOT, check=True)
                subprocess.run(command("resume" if prior else "collect", path, prior),
                               cwd=ROOT, check=True)
                prior = existing_campaign(entry["name"], path, CAPACITY_OUTPUT)
            if prior is None or not completed(prior, entry["invocations"]):
                raise RuntimeError(f"Incomplete saturation campaign: {entry['name']}")
            verify_campaign_database(prior, definition, entry["invocations"])
            if not (prior / "analysis_raw_runs.csv").exists():
                subprocess.run(command("analyze", path, prior), cwd=ROOT, check=True)
    rates = {}
    for workers in OLD_WORKERS:
        rates[str(workers)] = _read_ray_saturation(
            SATURATION_ARCHIVE, workers, "bls-saturation-recheck"
        )
    for workers in NEW_WORKERS:
        rates[str(workers)] = _read_ray_saturation(
            CAPACITY_OUTPUT, workers, "bls-worker-cubic-capacity"
        )
    record = {
        "git_commit": commit_sha(),
        "reference": "Rayon, all-at-once, 2500 WU, 30 blocks x 3",
        "archived_worker_counts": list(OLD_WORKERS),
        "new_worker_counts": list(NEW_WORKERS),
        "rayon_wu_per_second": rates,
        "note": "Finite-batch capacity, not indefinite sustained throughput",
    }
    if CAPACITY_FILE.exists() and json.loads(CAPACITY_FILE.read_text()) != record:
        raise RuntimeError("Capacity reference already differs; not overwriting")
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    CAPACITY_FILE.write_text(json.dumps(record, indent=2) + "\n")
    return record


def definitions(reference: dict[int, float]) -> list[tuple[dict, Path, str]]:
    base = read_definition(BASE)
    passes = tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    if [p["name"] for p in passes] != ["cpu-cache", "l1-branches"]:
        raise ValueError("Expected two separate verified PMU passes")
    if {c["id"] for c in base["configurations"]} != SCHEDULERS:
        raise ValueError("Scheduler configurations differ from archived protocol")
    result = []
    for workers in NEW_WORKERS:
        if workers not in reference or not math.isfinite(reference[workers]) or reference[workers] <= 0:
            raise ValueError(f"Missing positive saturation rate for {workers} workers")
        offered_rate = 0.5 * reference[workers]
        work_units = math.ceil(MIN_WINDOW_SECONDS * offered_rate) + 1
        if work_units > 2500:
            raise ValueError(f"Dataset cannot support 200-ms window: w{workers}")
        for mode in MODES:
            data = copy.deepcopy(base)
            name = f"{STUDY}-w{workers}-{mode}"
            data["experiment"].update(name=name, profile="deep")
            data["workload"].update(workers=workers, work_units=work_units,
                                    dataset_work_units=2500, queue_capacity=64)
            data["delivery"] = {"mode": mode, "arrival_rates": [round(offered_rate, 8)],
                                "arrival_seed_base": 42}
            data["repetitions"] = {"blocks": BLOCKS, "warmup_runs_per_block": 4,
                                    "measured_runs_per_block": 2}
            data["randomization"]["seed"] = SEED + workers * 2 + MODES.index(mode)
            data["analysis"]["bootstrap_seed"] = SEED + workers * 2 + MODES.index(mode)
            data["execution"]["settle_seconds"] = 0.0
            data["artifacts"]["output_root"] = str(OUTPUT.relative_to(ROOT))
            data["perf"] = {"passes": copy.deepcopy(passes)}
            path = GENERATED / (name + ".toml")
            plan = make_plan(data, None)
            if len(plan) != BLOCKS * len(SCHEDULERS):
                raise ValueError("Wrong number of randomized invocations")
            serialized = render_toml(data)
            if tomllib.loads(serialized) != data:
                raise ValueError("TOML roundtrip failed")
            result.append(({
                "name": name, "workers": workers, "mode": mode,
                "offered_rate": offered_rate, "work_units": work_units,
                "invocations": len(plan)
            }, path, serialized))
    random.Random(SEED).shuffle(result)
    return result


def collect() -> dict:
    preflight()
    entries = definitions(load_capacity())
    GENERATED.mkdir(parents=True, exist_ok=True)
    for _, path, content in entries:
        if path.exists() and path.read_text() != content:
            raise RuntimeError(f"Conflicting generated definition: {path}")
        path.write_text(content)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, (entry, path, _) in enumerate(entries, 1):
            data = read_definition(path)
            previous = existing_campaign(entry["name"], path, OUTPUT)
            if previous is None and any(OUTPUT.glob(entry["name"] + "-*")):
                raise RuntimeError(f"Conflicting campaign name: {entry['name']}")
            print(f"[{index}/{len(entries)}] {entry['name']}", flush=True)
            if previous is None or not completed(previous, entry["invocations"]):
                subprocess.run(command("prepare", path), cwd=ROOT, check=True)
                subprocess.run(command("resume" if previous else "collect", path, previous),
                               cwd=ROOT, check=True)
                previous = existing_campaign(entry["name"], path, OUTPUT)
            if previous is None or not completed(previous, entry["invocations"]):
                raise RuntimeError(f"Incomplete campaign: {entry['name']}")
            verify_campaign_database(previous, data, entry["invocations"])
            audit_perf_captures(previous, data, entry["invocations"])
            if not (previous / "analysis_raw_runs.csv").exists():
                subprocess.run(command("analyze", path, previous), cwd=ROOT, check=True)
    return {"campaigns": len(entries), "invocations": len(entries)*6*BLOCKS,
            "measured_runs": len(entries)*6*BLOCKS*2*2,
            "output_root": str(OUTPUT)}


def _read_paced_blocks(folder: Path, expected_blocks: int) -> dict[str, list[float]]:
    by_scheduler = defaultdict(lambda: defaultdict(list))
    with (folder / "analysis_raw_runs.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["perf_pass"] == "cpu-cache":
                by_scheduler[row["configuration"]][int(row["block"])].append(
                    float(row["task_clock_per_wu"]))
    if set(by_scheduler) != SCHEDULERS:
        raise RuntimeError(f"Incomplete scheduler set: {folder}")
    out = {}
    for scheduler, block_values in by_scheduler.items():
        if set(block_values) != set(range(1, expected_blocks + 1)):
            raise RuntimeError(f"Missing randomized blocks: {folder}/{scheduler}")
        if any(len(values) != 2 for values in block_values.values()):
            raise RuntimeError(f"Expected two cpu-cache repeats: {folder}/{scheduler}")
        out[scheduler] = [
            statistics.mean(block_values[block]) for block in range(1, expected_blocks + 1)
        ]
    return out


def evaluate() -> dict:
    reference = load_capacity()
    entries = definitions(reference)
    grouped = {}
    for entry, path, _ in entries:
        folder = existing_campaign(entry["name"], path, OUTPUT)
        if folder is None or not completed(folder, entry["invocations"]):
            raise RuntimeError(f"Missing complete holdout campaign: {entry['name']}")
        per_scheduler = _read_paced_blocks(folder, BLOCKS)
        for scheduler, samples in per_scheduler.items():
            grouped[(entry["mode"], scheduler, entry["workers"])] = samples
    # TRAINING IS FROZEN TO THE ORIGINAL FOUR 50%-load experiments.
    # The 6/10/12 holdout CSVs never enter a fit.
    historical_specs = {}
    for workers in OLD_WORKERS:
        for mode in MODES:
            prefix = f"bls-normalized-deep-final-w{workers}-load05000-{mode}"
            folders = sorted(
                path for path in CPU_ARCHIVE.glob(prefix + "-*")
                if path.is_dir() and (path / "experiment.toml").is_file()
            )
            if len(folders) != 1:
                raise RuntimeError(f"Expected original 50%-load campaign {prefix}, found {len(folders)}")
            folder = folders[0]
            archived = read_definition(folder / "experiment.toml")
            if archived["workload"]["workers"] != workers or archived["delivery"]["mode"] != mode:
                raise RuntimeError(f"Archived condition identity mismatch: {folder}")
            if (archived["repetitions"]["blocks"] != 20
                or archived["repetitions"]["warmup_runs_per_block"] != 4
                or archived["repetitions"]["measured_runs_per_block"] != 2
                or [p["name"] for p in archived["perf"]["passes"]] != ["cpu-cache", "l1-branches"]):
                raise RuntimeError(f"Archived acquisition protocol mismatch: {folder}")
            if not (folder / "analysis_raw_runs.csv").is_file():
                raise RuntimeError(f"Archived raw-run CSV missing: {folder}")
            per_scheduler = _read_paced_blocks(folder, 20)
            for scheduler, samples in per_scheduler.items():
                grouped[(mode, scheduler, workers)] = samples
            historical_specs[(mode, workers)] = {
                "work_units": archived["workload"]["work_units"],
                "arrival_rate": archived["delivery"]["arrival_rates"][0],
                "nominal_fraction_of_recheck_capacity":
                    archived["delivery"]["arrival_rates"][0] / reference[workers],
            }
    results = []
    for mode in MODES:
        for scheduler in sorted(SCHEDULERS):
            series = {workers: grouped[(mode, scheduler, workers)] for workers in WORKERS}
            report = evaluate_holdouts(series, seed=SEED, replicates=4000)
            for row in report["holdouts"]:
                results.append({
                    "mode": mode, "scheduler": scheduler,
                    "training_workers": "2,4,8,16",
                    "training_source": "archived bls-normalized-deep-final",
                    **row, "coefficients_z": json.dumps(report["coefficients"])
                })
    destination = ROOT_DIR / "validation"
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "held_out_predictions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    (destination / "historical_training_conditions.json").write_text(
        json.dumps({f"{mode}-w{w}": info for (mode,w),info in historical_specs.items()}, indent=2)
        + "\n"
    )
    return {
        "predictions": len(results), "holdout_workers": list(NEW_WORKERS),
        "historical_training_workers": list(OLD_WORKERS),
        "report": str(destination / "held_out_predictions.csv")
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("check", "capacity", "run", "evaluate"):
        actions.add_argument("--" + action, action="store_true")
    args = parser.parse_args()
    if args.check:
        report = {"study": STUDY, "workers": WORKERS, "training": list(OLD_WORKERS),
                  "holdouts": list(NEW_WORKERS), "modes": MODES,
                  "scheduled_window_seconds": MIN_WINDOW_SECONDS,
                  "blocks": BLOCKS, "passes": ["cpu-cache", "l1-branches"],
                  "new_campaigns": len(NEW_WORKERS)*len(MODES),
                  "new_measured_runs": len(NEW_WORKERS)*len(MODES)*6*BLOCKS*2*2,
                  "capacity_ready": CAPACITY_FILE.exists()}
        if CAPACITY_FILE.exists():
            report["cells"] = [entry for entry, _, _ in definitions(load_capacity())]
    elif args.capacity:
        report = calibrate_capacity()
    elif args.run:
        report = collect()
    else:
        report = evaluate()
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()