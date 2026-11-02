"""Focused, paired 2-worker BLS load sweep across the 50–75% transition, including both endpoints.

Reuses the archived 50% BLS configuration and final two-pass perf protocol.
All twelve new load/mode cells use the *same 256 tasks*, avoiding the old
rate-dependent task-count confound. No archived campaign is modified.
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
    WARMUPS, REPETITIONS_PER_PASS, PASS_COUNT, PERF_SPEC,
    adjusted, archived_campaigns, audit_perf_captures,
)

STUDY = "bls-transition-w2-sweep"
OUTPUT = ROOT / "results/studies" / STUDY / "campaigns"
GENERATED = ROOT / "experiments/generated" / STUDY
LOADS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75)
MODES = ("fixed-arrivals", "bursty-arrivals")
WORK_UNITS = 256
BLOCKS = 20
# The original 2-worker study used one common provisional Rayon reference.
# Read this from the archived 50% study rather than supplying a new rate.
BASE_LOAD = 0.50


def spec() -> tuple[list[dict], dict[Path, str]]:
    originals = archived_campaigns()
    perf = tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    if len(perf) != PASS_COUNT:
        raise ValueError("Expected exactly two perf passes")
    definitions = {}
    entries = []
    for mode in MODES:
        source = next(
            data for _, data in originals
            if data["workload"]["workers"] == 2
            and data["delivery"]["mode"] == mode
            and "load05000" in data["experiment"]["name"]
        )
        original_rate = float(source["delivery"]["arrival_rates"][0])
        baseline = original_rate / BASE_LOAD
        for load in LOADS:
            data = adjusted(source, perf)
            data["experiment"]["name"] = (
                f"{STUDY}-load{round(load * 10000):05d}-{mode}"
            )
            data["workload"]["work_units"] = WORK_UNITS
            data["delivery"]["arrival_rates"] = [round(baseline * load, 8)]
            data["repetitions"]["blocks"] = BLOCKS
            data["randomization"]["seed"] = 20261102
            data["analysis"]["bootstrap_seed"] = 20261102
            data["artifacts"]["output_root"] = str(OUTPUT.relative_to(ROOT))
            # The 256-task prefix, dataset identity, wake strategies and PMU
            # definitions are identical across the 12 new conditions.
            if (WORK_UNITS - 1) / data["delivery"]["arrival_rates"][0] < 0.2:
                raise ValueError("Arrival window below 200ms")
            serialized = render_toml(data)
            if tomllib.loads(serialized) != data:
                raise ValueError("TOML roundtrip failure")
            name = data["experiment"]["name"]
            path = GENERATED / (name + ".toml")
            invocations = len(make_plan(data, None))
            if invocations != BLOCKS * 6:
                raise ValueError(f"Expected 120 invocations, got {invocations}")
            definitions[path] = serialized
            entries.append(dict(
                name=name, file=path.name, mode=mode, load_fraction=load,
                arrival_rate=data["delivery"]["arrival_rates"][0],
                work_units=WORK_UNITS, invocations=invocations,
                measured_runs=invocations * REPETITIONS_PER_PASS * PASS_COUNT,
            ))
    entries.sort(key=lambda row: (row["mode"], row["load_fraction"]))
    if len(entries) != 12 or sum(r["measured_runs"] for r in entries) != 5760:
        raise ValueError("Expected 12 campaigns and 5760 measured executions")
    return entries, definitions


def execute(*, check: bool = False) -> dict:
    entries, definitions = spec()
    summary = dict(
        study=STUDY, campaign_count=len(entries),
        process_invocations=sum(r["invocations"] for r in entries),
        measured_physical_runs=sum(r["measured_runs"] for r in entries),
        discarded_warmup_runs=sum(r["invocations"] for r in entries) * WARMUPS,
        cells=entries,
    )
    if check:
        return summary
    if os.geteuid() == 0:
        raise RuntimeError("Run as normal user, not root")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before collecting")
    subprocess.run(
        [sys.executable, "-m", "experiments.studies.check_perf_scope"],
        cwd=ROOT, check=True,
    )
    GENERATED.mkdir(parents=True, exist_ok=True)
    for path, content in definitions.items():
        if path.is_file() and path.read_text() != content:
            raise RuntimeError(f"Conflicting generated definition: {path}")
        path.write_text(content)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, entry in enumerate(entries, 1):
            definition = GENERATED / entry["file"]
            data = read_definition(definition)
            name = entry["name"]
            prior = existing_campaign(name, definition, OUTPUT)
            if prior is None and any(OUTPUT.glob(name + "-*")):
                raise RuntimeError(f"Conflicting experiment name: {name}")
            print(f"[{index}/{len(entries)}] {name}", flush=True)
            if prior is None or not completed(prior, entry["invocations"]):
                subprocess.run(command("prepare", definition), cwd=ROOT, check=True)
                subprocess.run(
                    command("resume" if prior else "collect", definition, prior),
                    cwd=ROOT, check=True,
                )
                prior = existing_campaign(name, definition, OUTPUT)
            if prior is None or not completed(prior, entry["invocations"]):
                raise RuntimeError(f"Incomplete {name}")
            verify_campaign_database(prior, data, entry["invocations"])
            audit_perf_captures(prior, data, entry["invocations"])
            if not (prior / "paired_effects.csv").is_file():
                subprocess.run(command("analyze", definition, prior), cwd=ROOT, check=True)
    return {**summary, "completed": len(entries), "output_root": str(OUTPUT)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(execute(check=args.check), indent=2))


if __name__ == "__main__":
    main()
