"""Reproducible 2-worker, 50%-load cross-over: MPMC vs RTRB, boost ON/OFF.

Eight ordered phases (ON, OFF, OFF, ON, ON, OFF, OFF, ON), five
randomized blocks per phase. Never changes system power settings itself:
the external launcher changes + restores boost explicitly. Every physical
run keeps its original per-TID perf records and independent PMU pass identity.
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
from datetime import datetime, timezone
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.run_bls_thread_perf_diagnostic import (
    EVENTS, IDS, spec as base_spec,
)
from experiments.studies.run_bls_normalized_deep_final import (
    PASS_COUNT, REPETITIONS_PER_PASS, audit_perf_captures,
)

NAME = "bls-w2-boost-coherence-control"
BASE = ROOT / "results/studies" / NAME
GENERATED = ROOT / "experiments/generated" / NAME
PHASE_BOOST = ("on", "off", "off", "on", "on", "off", "off", "on")
BLOCKS_PER_PHASE = 5
BOOST_PATH = Path("/sys/devices/system/cpu/cpufreq/boost")


def mode(phase: int) -> str:
    if not 1 <= phase <= len(PHASE_BOOST):
        raise ValueError("phase must be in 1..8")
    return PHASE_BOOST[phase - 1]


def definition(phase: int, smoke: bool = False):
    _, original, _ = base_spec()
    data = copy.deepcopy(original)
    tag = f"phase-{phase:02d}-{mode(phase)}"
    if smoke:
        tag += "-smoke"
    data["experiment"]["name"] = f"{NAME}-{tag}"
    data["repetitions"]["blocks"] = 1 if smoke else BLOCKS_PER_PHASE
    data["randomization"]["seed"] = 20261110 + phase
    data["analysis"]["bootstrap_seed"] = 20261110 + phase
    # The inherited reference points at Rayon, which is not one of the
    # two conditions. The independent analysis needs no reference at all.
    data["analysis"].pop("reference_configuration", None)
    destination = BASE / ("smoke" if smoke else "campaigns") / tag
    data["artifacts"]["output_root"] = str(destination.relative_to(ROOT))
    output_definition = GENERATED / ("smoke" if smoke else "campaigns") / f"{tag}.toml"
    serialized = render_toml(data)
    if tomllib.loads(serialized) != data:
        raise RuntimeError("TOML roundtrip mismatch")
    expected = data["repetitions"]["blocks"] * 2
    if len(make_plan(data, None)) != expected:
        raise RuntimeError("Not exactly two configurations in every block")
    if len(data["configurations"]) != 2 or {item["id"] for item in data["configurations"]} != set(IDS):
        raise RuntimeError("Unexpected scheduler configurations")
    if data["delivery"]["mode"] != "fixed-arrivals" or data["workload"]["workers"] != 2:
        raise RuntimeError("Unexpected workload/delivery")
    if data["workload"]["work_units"] != 256 or len(EVENTS) != PASS_COUNT:
        raise RuntimeError("Unexpected work-unit count or perf passes")
    if abs(data["delivery"]["arrival_rates"][0] - 647.0) > 0.0001:
        raise RuntimeError("Unexpected arrival rate")
    return data, serialized, output_definition, destination, expected


def system_snapshot() -> dict:
    policies = sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy[0-9]*"))
    frequencies = {}
    for policy in policies:
        frequencies[policy.name] = {}
        for field in ("scaling_driver", "scaling_governor", "scaling_min_freq",
                      "scaling_max_freq", "cpuinfo_min_freq", "cpuinfo_max_freq",
                      "base_frequency", "affected_cpus"):
            path = policy / field
            if path.is_file():
                frequencies[policy.name][field] = path.read_text().strip()
    model = next(
        (line.split(":", 1)[1].strip()
         for line in Path("/proc/cpuinfo").read_text().splitlines()
         if line.startswith("model name")), "unknown"
    )
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "git_commit": git,
        "boost": BOOST_PATH.read_text().strip(),
        "policies": frequencies,
    }


def run_phase(phase: int, smoke: bool = False, check: bool = False) -> dict:
    data, serialized, definition_file, destination, invocations = definition(phase, smoke)
    result = {
        "phase": phase, "boost": mode(phase), "smoke": smoke,
        "blocks": data["repetitions"]["blocks"], "invocations": invocations,
        "physical_runs": invocations * REPETITIONS_PER_PASS * PASS_COUNT,
        "output_root": str(destination),
    }
    if check:
        return result
    if os.geteuid() == 0:
        raise RuntimeError("Benchmark collection must run as a normal user")
    if not BOOST_PATH.is_file():
        raise RuntimeError(f"Missing kernel-wide boost control: {BOOST_PATH}")
    expected_boost = "1" if mode(phase) == "on" else "0"
    if BOOST_PATH.read_text().strip() != expected_boost:
        raise RuntimeError(
            f"Phase {phase} requires boost={expected_boost}; "
            f"observed {BOOST_PATH.read_text().strip()}"
        )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before benchmark collection")
    subprocess.run([sys.executable, "-m", "experiments.studies.check_perf_scope"],
                   cwd=ROOT, check=True)
    definition_file.parent.mkdir(parents=True, exist_ok=True)
    if definition_file.exists() and definition_file.read_text() != serialized:
        raise RuntimeError("A different definition already exists at phase path")
    definition_file.write_text(serialized)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".phase.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = existing_campaign(data["experiment"]["name"], definition_file, destination)
        if previous is None and any(destination.glob(data["experiment"]["name"] + "-*")):
            raise RuntimeError("Conflicting archived campaign. Do not overwrite it.")
        pre_path = destination / "power_before.json"
        if not pre_path.exists():
            pre_path.write_text(json.dumps(system_snapshot(), indent=2) + "\n")
        pre = json.loads(pre_path.read_text())
        if pre["boost"] != expected_boost:
            raise RuntimeError("Saved power mode conflicts with current phase")

        if previous is None or not completed(previous, invocations):
            subprocess.run(command("prepare", definition_file), cwd=ROOT, check=True)
            env = os.environ.copy()
            env["THREADANCE_PER_THREAD_PERF"] = "1"
            subprocess.run(
                command("resume" if previous else "collect", definition_file, previous),
                cwd=ROOT, env=env, check=True,
            )
            previous = existing_campaign(data["experiment"]["name"], definition_file, destination)
        if previous is None or not completed(previous, invocations):
            raise RuntimeError("Incomplete campaign")
        verify_campaign_database(previous, data, invocations)
        audit_perf_captures(previous, data, invocations)
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.audit_bls_thread_perf",
             str(previous), str(result["physical_runs"])], cwd=ROOT, check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.analyze_bls_thread_perf",
             str(previous)], cwd=ROOT, check=True,
        )
        post = system_snapshot()
        (destination / "power_after.json").write_text(json.dumps(post, indent=2) + "\n")
        if post["boost"] != expected_boost:
            raise RuntimeError("Boost changed during collection; results invalid")
        if pre["policies"] != post["policies"]:
            raise RuntimeError("cpufreq policy changed during collection; results invalid")
        result["campaign_dir"] = str(previous)
        result["complete"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", type=int)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check and args.phase is None:
        result = [run_phase(i, check=True) for i in range(1, 9)]
    else:
        if args.phase is None:
            parser.error("--phase is required unless checking all phases")
        result = run_phase(args.phase, smoke=args.smoke, check=args.check)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
