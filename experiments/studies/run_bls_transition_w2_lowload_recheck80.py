"""Densify 25–50% fixed-arrival BLS load at two workers.

Companion to run_bls_transition_w2_sweep.py (50–75%). Uses the same
archived 50% source configuration, the same 256-task prefix, six
schedulers, 20 randomized blocks and two separate PMU passes.
"""
from __future__ import annotations

import argparse
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
    FINAL_CONFIGS,
    PASS_COUNT,
    PERF_SPEC,
    REPETITIONS_PER_PASS,
    WARMUPS,
    adjusted,
    archived_campaigns,
    audit_perf_captures,
)

STUDY = "bls-transition-w2-lowload-recheck80"
OUTPUT = ROOT / "results/studies" / STUDY / "campaigns"
GENERATED = ROOT / "experiments/generated" / STUDY
LOADS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
MODE = "fixed-arrivals"
WORKERS = 2
WORK_UNITS = 256
BLOCKS = 80
BASE_LOAD = 0.50
SEED = 20261106


def spec() -> tuple[list[dict], dict[Path, str]]:
    originals = archived_campaigns()
    perf = tomllib.loads(PERF_SPEC.read_text())["perf"]["passes"]
    if len(perf) != PASS_COUNT or [p["name"] for p in perf] != [
        "cpu-cache", "l1-branches"
    ]:
        raise ValueError("Expected the two original PMU passes")

    sources = [
        data for _, data in originals
        if data["workload"]["workers"] == WORKERS
        and data["delivery"]["mode"] == MODE
        and "load05000" in data["experiment"]["name"]
    ]
    if len(sources) != 1:
        raise ValueError(f"Expected one archived {WORKERS}-worker 50% {MODE} source")
    source = sources[0]
    baseline_rate = float(source["delivery"]["arrival_rates"][0]) / BASE_LOAD

    entries: list[dict] = []
    definitions: dict[Path, str] = {}
    for load in LOADS:
        data = adjusted(source, perf)
        name = f"{STUDY}-load{round(load * 10000):05d}-{MODE}"
        data["experiment"]["name"] = name
        data["workload"]["work_units"] = WORK_UNITS
        data["delivery"]["arrival_rates"] = [round(baseline_rate * load, 8)]
        data["repetitions"]["blocks"] = BLOCKS
        data["randomization"]["seed"] = SEED
        data["analysis"]["bootstrap_seed"] = SEED
        data["artifacts"]["output_root"] = str(OUTPUT.relative_to(ROOT))

        if {c["id"] for c in data["configurations"]} != FINAL_CONFIGS:
            raise ValueError(f"Incomplete scheduler set in {name}")
        if data["repetitions"]["warmup_runs_per_block"] != WARMUPS:
            raise ValueError(f"Unexpected warmup count in {name}")
        if (WORK_UNITS - 1) / data["delivery"]["arrival_rates"][0] < 0.2:
            raise ValueError(f"Arrival window below 200 ms in {name}")

        serialized = render_toml(data)
        if tomllib.loads(serialized) != data:
            raise ValueError(f"TOML roundtrip failure in {name}")
        path = GENERATED / (name + ".toml")
        invocations = len(make_plan(data, None))
        if invocations != BLOCKS * len(FINAL_CONFIGS):
            raise ValueError(f"Unexpected invocation count in {name}: {invocations}")
        definitions[path] = serialized
        entries.append(dict(
            name=name,
            file=path.name,
            mode=MODE,
            load_fraction=load,
            arrival_rate=data["delivery"]["arrival_rates"][0],
            work_units=WORK_UNITS,
            invocations=invocations,
            measured_runs=invocations * REPETITIONS_PER_PASS * PASS_COUNT,
        ))

    expected = len(LOADS) * BLOCKS * len(FINAL_CONFIGS) * REPETITIONS_PER_PASS * PASS_COUNT
    if len(entries) != len(LOADS) or sum(e["measured_runs"] for e in entries) != expected:
        raise ValueError("Unexpected low-load sweep size")
    return entries, definitions


def execute(*, check: bool = False) -> dict:
    entries, definitions = spec()
    summary = dict(
        study=STUDY,
        campaign_count=len(entries),
        process_invocations=sum(e["invocations"] for e in entries),
        measured_physical_runs=sum(e["measured_runs"] for e in entries),
        discarded_warmup_runs=sum(e["invocations"] for e in entries) * WARMUPS,
        cells=entries,
    )
    if check:
        return summary
    if os.geteuid() == 0:
        raise RuntimeError("Run as a normal user, not root")
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
