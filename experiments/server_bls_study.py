"""Resumable, logged BLS server study; invoked by run-server.sh.

Only existing benchmark engine/calibration/analysis modules perform measurements.
Never invokes sudo, changes host permissions or overwrites results.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import json
import logging
import math
import os
import subprocess
import sys
import tomllib
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from experiments.common import campaign
from experiments.common.analysis import analyze
from experiments.common.bls_dataset_identity import (
    config_from_experiment, expected_dataset_path, identify_dataset,
)
from experiments.common.calibrate_service_time import (
    CalibrationConfig, create_calibration, find_compatible_calibration,
)
from experiments.common.study import render_toml
from experiments.common.study_report import generate as generate_report
from experiments.common.study_run import completed, existing_campaign

ROOT = campaign.ROOT
WORKERS = (2, 4, 8, 12, 16, 20, 24)
MODES = ("all-at-once", "fixed-arrivals", "bursty-arrivals")
LOADS = tuple(i / 100 for i in range(25, 96, 5))
CALIBRATION_N = 200
WINDOW_S = 0.2
HEADROOM = 1.20
MIN_DATASET_N = 6000
MAX_DATASET_N = 100000
NAME = "bls-server-24w"
STUDY = ROOT / "results" / "studies" / NAME
DEFINITIONS = STUDY / "definitions"
CAMPAIGNS = STUDY / "campaigns"
CALIBRATIONS = ROOT / "results" / "calibrations"
DATASETS = ROOT / "results" / "datasets"
LOG = logging.getLogger("server_bls")


def log_setup():
    STUDY.mkdir(parents=True, exist_ok=True)
    path = STUDY / "server-run.log"
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    for stream in (sys.stdout, path.open("a", buffering=1, encoding="utf-8")):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        LOG.addHandler(handler)
    return path


def run(args):
    LOG.info("COMMAND: %s", " ".join(map(str, args)))
    with subprocess.Popen(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=1) as process:
        for line in process.stdout:
            LOG.info("%s", line.rstrip("\n"))
        code = process.wait()
    if code:
        raise RuntimeError(f"Command exited with status {code}: {args}")


def preflight():
    if os.geteuid() == 0:
        raise RuntimeError("Run as a regular user with perf permission, not root")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, text=True).strip()
    if dirty:
        raise RuntimeError("Tracked source tree must be clean:\n" + dirty)
    if not (ROOT.parent / "threadance" / "Cargo.toml").exists():
        raise RuntimeError("Clone threadance beside thread_pool_harness first")
    for tool in ("rustc", "cargo", "perf", "lscpu"):
        if subprocess.run(["bash", "-lc", f"command -v {tool}"],
                          stdout=subprocess.DEVNULL).returncode:
            raise RuntimeError(f"Missing required command: {tool}")
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < max(WORKERS):
        raise RuntimeError(f"Only {len(cpus)} CPUs available, need 24")
    LOG.info("Available CPU affinity set: %s", cpus)
    for line in subprocess.check_output(["lscpu"], text=True).splitlines():
        if line.startswith(("Model name:", "CPU(s):", "Core(s) per socket:",
                            "Socket(s):", "Thread(s) per core:", "NUMA node(s):")):
            LOG.info("HOST: %s", line)
    check = subprocess.run(["perf", "stat", "-e", "task-clock", "--", "true"],
                           capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError("perf task-clock cannot be read by this user: " +
                           check.stderr.strip())
    LOG.info("perf verified; worker pinning is off; reserve server for testing")


def calibrated():
    sample = CalibrationConfig(
        work_units=CALIBRATION_N, validators=2000,
        signers_per_certificate=1600, certificates_per_work_unit=1,
        workload_seed=1, dataset_cache_dir=DATASETS)
    workload = dict(work_units=CALIBRATION_N,
                    calibration_work_units=CALIBRATION_N, validators=2000,
                    signers_per_certificate=1600,
                    certificates_per_work_unit=1, workload_seed=1)
    candidate = find_compatible_calibration(ROOT, CALIBRATIONS, workload)
    if candidate is None:
        LOG.info("STAGE 1/5: Calibrating BLS with %d work units; auto-generating small calibration dataset if absent", CALIBRATION_N)
        candidate = create_calibration(
            ROOT, sample, CALIBRATIONS, allow_dirty=False, settle_seconds=10)
    else:
        LOG.info("STAGE 1/5: Reusing validated calibration: %s", candidate)
    service_us = float(json.loads(candidate.read_text())["service_time"]["mean_us"])
    if not math.isfinite(service_us) or service_us <= 0:
        raise RuntimeError("Calibration returned invalid service time")
    LOG.info("BLS mean service time: %.4f microseconds per WU", service_us)
    return candidate, service_us


def work_units_for(workers, service_us):
    """Nominal single-thread-derived capacity, not measured saturated throughput."""
    maximum_rate = max(LOADS) * workers * 1e6 / service_us
    units = max(1000, math.ceil(WINDOW_S * maximum_rate * HEADROOM) + 1)
    return units, maximum_rate


def definitions(service_us):
    template = tomllib.loads(
        (ROOT / "experiments" / "queue_topology" / "experiment.toml").read_text())
    template["experiment"]["profile"] = "standard"
    template["workload"].update(
        validators=2000, signers_per_certificate=1600,
        certificates_per_work_unit=1, workload_seed=1, queue_capacity=64,
        calibration_work_units=CALIBRATION_N)
    template["repetitions"] = dict(
        blocks=20, warmup_runs_per_block=4, measured_runs_per_block=2)
    template["execution"] = dict(settle_seconds=3.0)
    template["analysis"]["reference_configuration"] = "rayon"
    template["artifacts"].update(
        dataset_cache_dir="results/datasets",
        calibration_cache_dir="results/calibrations",
        output_root=str(CAMPAIGNS.relative_to(ROOT)))
    template["configurations"] = [
        dict(id="rayon", scheduler="rayon"),
        dict(id="bevy", scheduler="bevy"),
        dict(id="mpmc-spin0", scheduler="threadance",
             wait_policy="spin-then-park", spin_us=0, queue_capacity=64),
        dict(id="mpmc-agave", scheduler="threadance",
             wait_policy="agave-backoff", queue_capacity=64),
        dict(id="rtrb-b1", scheduler="threadance-hot-first-rtrb",
             wait_policy="spin-then-park", spin_us=0,
             hot_first_backlog=1, queue_capacity=64),
        dict(id="rtrb-b2", scheduler="threadance-hot-first-rtrb",
             wait_policy="spin-then-park", spin_us=0,
             hot_first_backlog=2, queue_capacity=64),
    ]
    sizes = {str(w): work_units_for(w, service_us) for w in WORKERS}
    needed = max(MIN_DATASET_N, *(n for n, _ in sizes.values()))
    dataset_n = ((needed + 999) // 1000) * 1000
    if dataset_n > MAX_DATASET_N:
        raise RuntimeError(f"Required dataset {dataset_n} exceeds safe limit {MAX_DATASET_N}; review calibration and resource budget")
    files = []
    for workers in WORKERS:
        for mode in MODES:
            data = deepcopy(template)
            data["experiment"]["name"] = f"{NAME}-w{workers}-{mode}"
            data["workload"].update(
                workers=workers, work_units=sizes[str(workers)][0],
                dataset_work_units=dataset_n)
            data["delivery"] = (dict(mode=mode) if mode == "all-at-once" else
                                dict(mode=mode, rho=list(LOADS), arrival_seed_base=42))
            data["randomization"] = dict(
                seed=20261123 + workers * 100 + MODES.index(mode))
            files.append((DEFINITIONS / f"w{workers}-{mode}.toml", data))
    return files, dict(
        calibrated_mean_us=service_us, worker_sizing=sizes,
        dataset_work_units=dataset_n, loads=list(LOADS),
        workers=list(WORKERS), modes=list(MODES),
        window_seconds=WINDOW_S, headroom=HEADROOM,
        campaigns=len(files), conditions=7 * 6 * 31,
        invocations=20 * 7 * 6 * 31, measured_runs=2 * 20 * 7 * 6 * 31)


def save_plan(files, summary, calibration):
    DEFINITIONS.mkdir(parents=True, exist_ok=True)
    generated = {path: render_toml(data) for path, data in files}
    metadata = {**summary, "calibration_path": str(calibration),
                "git_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "generated_at": datetime.now().astimezone().isoformat()}
    old_path = DEFINITIONS / "server_plan.json"
    if old_path.exists():
        old = json.loads(old_path.read_text())
        for field in ("git_commit", "worker_sizing", "dataset_work_units",
                      "loads", "workers", "modes"):
            if old[field] != metadata[field]:
                raise RuntimeError(f"Frozen server plan changed at {field}; refusing unsafe resume")
    for path, content in generated.items():
        if path.exists() and path.read_text() != content:
            raise RuntimeError(f"Existing generated definition differs: {path}")
    for path, content in generated.items():
        if not path.exists():
            path.write_text(content)
    if not old_path.exists():
        old_path.write_text(json.dumps(metadata, indent=2) + "\n")
    LOG.info("STAGE 2/5: planned %d campaigns, %d conditions, %d invocations, %d measured runs",
             summary["campaigns"], summary["conditions"],
             summary["invocations"], summary["measured_runs"])
    for workers, (units, rate) in summary["worker_sizing"].items():
        LOG.info("workers=%s: %d distinct units/run, nominal max λ=%.1f WU/s",
                 workers, units, rate)
    LOG.info("Shared cached BLS dataset: %d distinct certificates",
             summary["dataset_work_units"])


def verify_dataset(first):
    definition, data = first
    cache_dir, cfg = config_from_experiment(definition)
    expected = expected_dataset_path(ROOT / cache_dir, cfg)
    if not expected.is_file():
        LOG.info("STAGE 3/5: BLS dataset absent; generating it before any measurement")
        run([sys.executable, "-u", "-m", "experiments.common.campaign", "prepare",
             "--experiment", str(definition)])
    else:
        LOG.info("STAGE 3/5: Dataset exists; validating header and computing SHA-256")
    identity = identify_dataset(expected, cfg)
    LOG.info("Dataset OK: %s; size=%d bytes; sha256=%s",
             identity.path, identity.size_bytes, identity.sha256)


def collect_all(files, *, report=False):
    CAMPAIGNS.mkdir(parents=True, exist_ok=True)
    for index, (definition, data) in enumerate(files, 1):
        name = data["experiment"]["name"]
        LOG.info("STAGE 4/5: Campaign %d/%d: %s", index, len(files), name)
        previous = existing_campaign(name, definition, CAMPAIGNS)
        expected = len(campaign.make_plan(
            data, campaign.calibration_service_us(data, definition, create=False)))
        if previous is not None and completed(previous, expected):
            LOG.info("Already complete; retaining measurements: %s", previous)
            folder = previous
        else:
            LOG.info("Preparing binary and cache, then %d randomized invocations", expected)
            run([sys.executable, "-u", "-m", "experiments.common.campaign",
                 "prepare", "--experiment", str(definition)])
            args = [sys.executable, "-u", "-m", "experiments.common.campaign",
                    "resume" if previous else "collect",
                    "--experiment", str(definition)]
            if previous is not None:
                LOG.info("Resuming incomplete campaign without repeating completed invocations")
                args += ["--campaign", str(previous)]
            run(args)
            folder = existing_campaign(name, definition, CAMPAIGNS)
        if folder is None or not completed(folder, expected):
            raise RuntimeError(f"Campaign incomplete: {name}")
        LOG.info("Validated complete campaign: %s", folder)
    if report:
        LOG.info("STAGE 5/5: Validate SQLite measurements and generate statistical reports")
        LOG.info("Report: %s", generate_report(CAMPAIGNS))



def saturated_references(saturated_files):
    """Measure common C_w, averaging each scheduler's mean saturated throughput."""
    references = {}
    details = {}
    for definition, data in saturated_files:
        workers = data["workload"]["workers"]
        folder = existing_campaign(data["experiment"]["name"], definition, CAMPAIGNS)
        if folder is None:
            raise RuntimeError(f"Cannot find completed saturated campaign for w={workers}")
        analyze(folder)
        values = {}
        with (folder / "analysis_raw_runs.csv").open(newline="") as stream:
            for row in csv.DictReader(stream):
                values.setdefault(row["configuration"], []).append(float(row["throughput"]))
        if len(values) != 6 or any(len(runs) != 40 for runs in values.values()):
            raise RuntimeError(f"Expected six schedulers x 40 saturated runs for w={workers}")
        means = {config: statistics.mean(runs) for config, runs in values.items()}
        reference = statistics.mean(means.values())
        if not math.isfinite(reference) or reference <= 0:
            raise RuntimeError(f"Invalid measured saturated capacity for w={workers}")
        references[str(workers)] = reference
        details[str(workers)] = {"reference_throughput_wu_s": reference,
                                 "mean_by_scheduler": means, "campaign": str(folder)}
        spread = 100 * (max(means.values()) - min(means.values())) / reference
        LOG.info("SATURATED w=%d: measured Cw = %.2f WU/s; scheduler spread = %.2f%%",
                 workers, reference, spread)
        if spread > 5:
            LOG.warning("Large cross-scheduler saturation spread at w=%d; common Cw may hide different scheduler headroom", workers)
    path = DEFINITIONS / "saturation_references.json"
    content = json.dumps(details, sort_keys=True, indent=2) + "\n"
    if path.exists() and path.read_text() != content:
        raise RuntimeError("Saturated references differ from frozen prior results")
    if not path.exists():
        path.write_text(content)
    return references


def paced_definitions(files, references):
    """Use measured Cw for rho. Validate arrival window, grow cache if needed."""
    paced = [(path, deepcopy(data)) for path, data in files
             if data["delivery"]["mode"] != "all-at-once"]
    highest = max(MIN_DATASET_N, *(
        max(data["workload"]["work_units"],
            math.ceil(WINDOW_S * HEADROOM * max(LOADS) *
                      references[str(data["workload"]["workers"])]) + 1)
        for _, data in paced
    ))
    dataset_n = ((highest + 999) // 1000) * 1000
    if dataset_n > MAX_DATASET_N:
        raise RuntimeError(f"Measured saturation requires {dataset_n} certificates; safety limit {MAX_DATASET_N}")
    for _, data in paced:
        workers = data["workload"]["workers"]
        capacity = references[str(workers)]
        required = math.ceil(WINDOW_S * HEADROOM * max(LOADS) * capacity) + 1
        data["workload"]["work_units"] = max(data["workload"]["work_units"], required)
        data["workload"]["dataset_work_units"] = dataset_n
        data["delivery"]["saturated_reference_wu_s"] = capacity
        actual_window = (data["workload"]["work_units"] - 1) / (max(LOADS) * capacity)
        if actual_window < WINDOW_S:
            raise AssertionError("Scheduled window too short")
        LOG.info("PACE w=%d: measured Cw=%.2f; %d distinct WU/run; minimum scheduled window=%.3fs",
                 workers, capacity, data["workload"]["work_units"], actual_window)
    LOG.info("Paced dataset size: %d; old cache remains intact if a larger dataset is necessary", dataset_n)
    return paced

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true",
                        help="Calibrate, size and freeze the study without collecting benchmark runs")
    args = parser.parse_args()
    LOG.info("Logging to %s", log_setup())
    try:
        preflight()
        os.environ["RUSTFLAGS"] = "-C target-cpu=native"
        calibration, service_us = calibrated()
        files, summary = definitions(service_us)
        saturated = [(path, data) for path, data in files
                     if data["delivery"]["mode"] == "all-at-once"]
        save_plan(saturated, summary, calibration)
        if args.plan_only:
            LOG.info("Plan-only: no main dataset generation or benchmark runs")
            return
        saturated = [(path, data) for path, data in files
                     if data["delivery"]["mode"] == "all-at-once"]
        LOG.info("Seven saturated-throughput campaigns run first; paced rates are based on measured Cw")
        verify_dataset(saturated[0])
        collect_all(saturated)
        references = saturated_references(saturated)
        paced = paced_definitions(files, references)
        save_plan(paced, summary, calibration)
        verify_dataset(paced[0])
        collect_all(paced, report=True)
        LOG.info("SUCCESS: completed the server study")
    except BaseException:
        LOG.exception("STOPPED. Results preserved. Rerun same command to resume.")
        raise


if __name__ == "__main__":
    main()
