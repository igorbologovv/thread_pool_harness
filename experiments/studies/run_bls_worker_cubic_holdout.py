"""Acquire matching BLS worker-count data and test frozen cubic holdouts.

Stages: --check (no writes), --capacity (Rayon saturation reference),
--run (seven worker counts x two modes, all six schedulers), --evaluate.
Use --capacity before --run. Only 2/4/8/16 are used to fit cubic models;
6/10/12 are withheld until evaluation. All paced runs use 1024 WU.
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
MODES = ("fixed-arrivals", "bursty-arrivals")
SCHEDULERS = {"rayon", "bevy", "mpmc-sp0", "mpmc-ab", "rtrb-sp0-b1", "rtrb-sp0-b2"}
BLOCKS = 40
WORK_UNITS = 1024
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


def calibrate_capacity() -> dict:
    preflight()
    report = design(CAPACITY_SPEC, write=True)
    spec_output = ROOT / "experiments/generated/bls-worker-cubic-capacity"
    CAPACITY_OUTPUT.mkdir(parents=True, exist_ok=True)
    with (CAPACITY_OUTPUT / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, entry in enumerate(report["campaigns"], 1):
            path = spec_output / entry["file"]
            data = read_definition(path)
            prior = existing_campaign(entry["name"], path, CAPACITY_OUTPUT)
            if prior is None and any(CAPACITY_OUTPUT.glob(entry["name"] + "-*")):
                raise RuntimeError(f"Conflicting capacity campaign: {entry['name']}")
            print(f"[capacity {index}/{len(report['campaigns'])}] {entry['name']}", flush=True)
            if prior is None or not completed(prior, entry["invocations"]):
                subprocess.run(command("prepare", path), cwd=ROOT, check=True)
                subprocess.run(command("resume" if prior else "collect", path, prior),
                               cwd=ROOT, check=True)
                prior = existing_campaign(entry["name"], path, CAPACITY_OUTPUT)
            if prior is None or not completed(prior, entry["invocations"]):
                raise RuntimeError(f"Incomplete capacity campaign: {entry['name']}")
            verify_campaign_database(prior, data, entry["invocations"])
            if not (prior / "analysis_raw_runs.csv").exists():
                subprocess.run(command("analyze", path, prior), cwd=ROOT, check=True)
    rates = {}
    for workers in WORKERS:
        name = f"bls-worker-cubic-capacity-w{workers}-n2500-g1-all-at-once"
        folders = sorted(p for p in CAPACITY_OUTPUT.glob(name + "-*")
                         if p.is_dir() and (p / "analysis_raw_runs.csv").is_file())
        if len(folders) != 1:
            raise RuntimeError(f"Expected exactly one reference campaign for {workers}")
        per_block = defaultdict(list)
        with (folders[0] / "analysis_raw_runs.csv").open(newline="") as stream:
            for row in csv.DictReader(stream):
                if row["configuration"] == "rayon":
                    per_block[int(row["block"])].append(float(row["throughput"]))
        if set(per_block) != set(range(1, 31)) or any(len(v) != 3 for v in per_block.values()):
            raise RuntimeError(f"Invalid capacity repetitions for {workers}")
        rates[str(workers)] = statistics.mean(statistics.mean(v) for v in per_block.values())
    record = {"git_commit": commit_sha(), "reference": "Rayon, 2500 WU all-at-once",
              "blocks": 30, "measured_runs_per_block": 3,
              "rayon_wu_per_second": rates,
              "note": "Finite-batch reference, not universal sustained capacity"}
    if CAPACITY_FILE.exists() and json.loads(CAPACITY_FILE.read_text()) != record:
        raise RuntimeError("Existing capacity reference differs; not overwriting")
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    CAPACITY_FILE.write_text(json.dumps(record, indent=2) + "\n")
    return record


def definitions(reference: dict[int, float]) -> list[tuple[dict, Path, str]]:
    base = read_definition(BASE)
    passes = tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    if [p["name"] for p in passes] != ["cpu-cache", "l1-branches"]:
        raise ValueError("Expected the two verified PMU profiles")
    if {c["id"] for c in base["configurations"]} != SCHEDULERS:
        raise ValueError("Scheduler configurations differ from saturation template")
    result = []
    for workers in WORKERS:
        if workers not in reference or not math.isfinite(reference[workers]) or reference[workers] <= 0:
            raise ValueError(f"Missing positive saturation rate for {workers} workers")
        offered_rate = 0.5 * reference[workers]
        if (WORK_UNITS-1) / offered_rate < 0.2:
            raise ValueError(f"Scheduled window below 200 ms for {workers} workers")
        for mode in MODES:
            data = copy.deepcopy(base)
            name = f"{STUDY}-w{workers}-{mode}"
            data["experiment"].update(name=name, profile="deep")
            data["workload"].update(workers=workers, work_units=WORK_UNITS,
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
            text = render_toml(data)
            if tomllib.loads(text) != data:
                raise ValueError("TOML roundtrip failed")
            result.append(({"name": name, "workers": workers, "mode": mode,
                            "offered_rate": offered_rate, "invocations": len(plan)}, path, text))
    # Reproducible between-campaign order, distinct from within-block scheduling.
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
    return {"campaigns": len(entries), "invocations": 14*6*BLOCKS,
            "measured_runs": 14*6*BLOCKS*2*2, "output_root": str(OUTPUT)}


def evaluate() -> dict:
    entries = definitions(load_capacity())
    grouped = {}
    for entry, path, _ in entries:
        folder = existing_campaign(entry["name"], path, OUTPUT)
        if folder is None or not completed(folder, entry["invocations"]):
            raise RuntimeError(f"Missing complete campaign: {entry['name']}")
        per_scheduler = defaultdict(lambda: defaultdict(list))
        with (folder / "analysis_raw_runs.csv").open(newline="") as stream:
            for row in csv.DictReader(stream):
                if row["perf_pass"] == "cpu-cache":
                    per_scheduler[row["configuration"]][int(row["block"])].append(
                        float(row["task_clock_per_wu"]))
        if set(per_scheduler) != SCHEDULERS:
            raise RuntimeError(f"Incomplete cpu-cache scheduler set in {folder}")
        for scheduler, blocks in per_scheduler.items():
            if set(blocks) != set(range(1, BLOCKS+1)) or any(len(v) != 2 for v in blocks.values()):
                raise RuntimeError(f"Wrong two-repeat block structure: {folder}/{scheduler}")
            grouped[(entry["mode"], scheduler, entry["workers"])] = [
                statistics.mean(blocks[i]) for i in range(1, BLOCKS+1)
            ]
    results = []
    for mode in MODES:
        for scheduler in sorted(SCHEDULERS):
            by_workers = {w: grouped[(mode, scheduler, w)] for w in WORKERS}
            report = evaluate_holdouts(by_workers, seed=SEED, replicates=4000)
            for row in report["holdouts"]:
                results.append({"mode": mode, "scheduler": scheduler,
                                "training_workers": "2,4,8,16", **row,
                                "coefficients_z": json.dumps(report["coefficients"])})
    output = ROOT_DIR / "validation"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "held_out_predictions.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    return {"predictions": len(results), "holdout_workers": [6, 10, 12],
            "report": str(output / "held_out_predictions.csv")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    for action in ("check", "capacity", "run", "evaluate"):
        actions.add_argument("--" + action, action="store_true")
    args = parser.parse_args()
    if args.check:
        report = {"study": STUDY, "workers": WORKERS, "training": [2,4,8,16],
                  "holdouts": [6,10,12], "modes": MODES, "work_units": WORK_UNITS,
                  "blocks": BLOCKS, "passes": ["cpu-cache", "l1-branches"],
                  "campaigns": len(WORKERS)*len(MODES),
                  "measured_runs": len(WORKERS)*len(MODES)*6*BLOCKS*2*2,
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