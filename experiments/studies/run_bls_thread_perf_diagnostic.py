"""Strict per-run, per-thread BLS diagnostic using an opt-in harness PMU mode.

The harness synchronizes perf enable/disable with every measured run and
attaches to the pre-existing pacer + two worker TIDs. All raw per-thread
events remain in SQLite perf_capture.raw_json.
"""
from __future__ import annotations
import argparse
import copy
import fcntl
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.run_bls_normalized_deep_final import (
    PASS_COUNT, REPETITIONS_PER_PASS, audit_perf_captures,
)

STUDY = "bls-w2-thread-perf-diagnostic"
GENERATED = ROOT / "experiments/generated" / STUDY
OUTPUT = ROOT / "results/studies" / STUDY / "campaigns"
BLOCKS = 20
IDS = ("mpmc-sp0", "rtrb-sp0-b2")
EVENTS = [
    {"name": "cpu-cache", "events": [
        "cycles:uk", "instructions:uk", "cache-references:uk",
        "cache-misses:uk", "L1-dcache-load-misses:uk",
        "task-clock", "context-switches", "cpu-migrations", "page-faults"]},
    {"name": "l1-branches", "events": [
        "cycles:uk", "L1-dcache-loads:uk", "L1-dcache-load-misses:uk",
        "branches:uk", "branch-misses:uk",
        "task-clock", "context-switches", "cpu-migrations", "page-faults"]},
]


def spec():
    source = ROOT / "experiments/studies/bls_w2_per_thread_source.toml"
    data = tomllib.loads(source.read_text())
    data["experiment"]["name"] = STUDY + "-w2-load05000-fixed-arrivals"
    data["configurations"] = [
        config for config in data["configurations"] if config["id"] in IDS
    ]
    data["repetitions"]["blocks"] = BLOCKS
    data["perf"]["passes"] = copy.deepcopy(EVENTS)
    data["randomization"]["seed"] = 20261106
    data["analysis"]["bootstrap_seed"] = 20261106
    data["artifacts"]["output_root"] = str(OUTPUT.relative_to(ROOT))
    if len(data["configurations"]) != 2 or {x["id"] for x in data["configurations"]} != set(IDS):
        raise ValueError("Expected MPMC spin0 and RTRB backlog2")
    if data["workload"]["workers"] != 2 or data["workload"]["work_units"] != 256:
        raise ValueError("Unexpected workload")
    if data["delivery"]["mode"] != "fixed-arrivals":
        raise ValueError("Unexpected arrival mode")
    if len(EVENTS) != PASS_COUNT:
        raise ValueError("Unexpected perf pass count")
    rendered = render_toml(data)
    if tomllib.loads(rendered) != data:
        raise ValueError("TOML roundtrip failed")
    invocations = len(make_plan(data, None))
    if invocations != BLOCKS * 2:
        raise ValueError(f"Expected 40 invocations, found {invocations}")
    return {
        "study": STUDY, "experiment_name": data["experiment"]["name"],
        "invocations": invocations, "physical_runs": invocations * REPETITIONS_PER_PASS * PASS_COUNT,
        "definitions": str(GENERATED / (STUDY + ".toml")),
        "output_root": str(OUTPUT),
    }, data, rendered


def execute(check=False, smoke=False):
    summary, data, rendered = spec()
    generated = GENERATED
    destination = OUTPUT
    if smoke:
        data["experiment"]["name"] += "-smoke-counter-diagnostic"
        data["repetitions"]["blocks"] = 1
        generated = GENERATED / "smoke-counter-diagnostic"
        destination = OUTPUT.parent / "smoke-counter-diagnostic"
        data["artifacts"]["output_root"] = str(destination.relative_to(ROOT))
        rendered = render_toml(data)
        invocations = len(make_plan(data, None))
        summary = {**summary, "experiment_name": data["experiment"]["name"],
                   "invocations": invocations,
                   "physical_runs": invocations * REPETITIONS_PER_PASS * PASS_COUNT,
                   "output_root": str(destination)}
    if check:
        return summary
    if os.geteuid() == 0:
        raise RuntimeError("Do not run as root")
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                           cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before collecting")
    subprocess.run([sys.executable, "-m", "experiments.studies.check_perf_scope"],
                   cwd=ROOT, check=True)
    generated.mkdir(parents=True, exist_ok=True)
    definition = generated / (STUDY + ".toml")
    if definition.exists() and definition.read_text() != rendered:
        raise RuntimeError("Conflicting generated TOML")
    definition.write_text(rendered)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = existing_campaign(data["experiment"]["name"], definition, destination)
        if prior is None and any(destination.glob(data["experiment"]["name"] + "-*")):
            raise RuntimeError("Conflicting campaign")
        if prior is None or not completed(prior, summary["invocations"]):
            subprocess.run(command("prepare", definition), cwd=ROOT, check=True)
            env = os.environ.copy()
            env["THREADANCE_PER_THREAD_PERF"] = "1"
            subprocess.run(command("resume" if prior else "collect", definition, prior),
                           cwd=ROOT, env=env, check=True)
            prior = existing_campaign(data["experiment"]["name"], definition, destination)
        if prior is None or not completed(prior, summary["invocations"]):
            raise RuntimeError("Incomplete thread diagnostic")
        verify_campaign_database(prior, data, summary["invocations"])
        audit_perf_captures(prior, data, summary["invocations"])
        subprocess.run([sys.executable, "-m",
                        "experiments.studies.audit_bls_thread_perf",
                        str(prior), str(summary["physical_runs"])], cwd=ROOT, check=True)
        subprocess.run([sys.executable, "-m", "experiments.studies.analyze_bls_thread_perf",
                        str(prior)], cwd=ROOT, check=True)
    return {**summary, "completed": True, "campaign_dir": str(prior)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(check=args.check, smoke=args.smoke), indent=2))


if __name__ == "__main__":
    main()
