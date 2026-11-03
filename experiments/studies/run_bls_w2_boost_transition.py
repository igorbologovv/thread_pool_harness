"""Two-wave randomized load sweep for the w2 RTRB boost transition.

Measures MPMC spin0 and RTRB b2 using the existing strict per-TID perf
path. Each load is run in two opposing-order waves to mitigate linear
time/temperature drift. Each cell has its own resumable campaign and raw data.
No CPU or governor controls are changed by this module.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import random
import subprocess
import sys
import tomllib
from functools import lru_cache
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.run_bls_normalized_deep_final import (
    PASS_COUNT,
    REPETITIONS_PER_PASS,
    audit_perf_captures,
)
from experiments.studies.run_bls_thread_perf_diagnostic import (
    EVENTS,
    IDS,
    spec as thread_perf_spec,
)
from experiments.studies.run_bls_w2_boost_control import (
    BOOST_PATH,
    system_snapshot,
)

STUDY = "bls-w2-boost-transition"
BASE = ROOT / "results" / "studies" / STUDY
GENERATED = ROOT / "experiments" / "generated" / STUDY
LOADS = (0.35, 0.40, 0.45, 0.475, 0.50, 0.525, 0.55, 0.60, 0.65, 0.70, 0.75)
WAVES = (1, 2)
BLOCKS_PER_WAVE = 10
WORK_UNITS = 256
REFERENCE_RATE = 1294.0  # 647 WU/s at exactly 50% in the original w2 study
SEED = 20261115


def ordered_cells() -> list[tuple[int, float]]:
    first = list(LOADS)
    random.Random(SEED).shuffle(first)
    return [(1, load) for load in first] + [
        (2, load) for load in reversed(first)
    ]


@lru_cache(maxsize=1)
def original_definition() -> dict:
    _, data, _ = thread_perf_spec()
    if data["delivery"]["arrival_rates"] != [647.0]:
        raise ValueError("The historical 50% source rate is not 647 WU/s")
    return data


def definition(wave: int, load: float, smoke: bool = False):
    if wave not in WAVES or load not in LOADS:
        raise ValueError("Unsupported wave/load combination")
    data = copy.deepcopy(original_definition())
    load_id = round(load * 10000)
    tag = f"wave{wave:02d}-load{load_id:05d}"
    if smoke:
        tag += "-smoke"
    data["experiment"]["name"] = f"{STUDY}-{tag}-fixed-arrivals"
    data["delivery"]["arrival_rates"] = [round(REFERENCE_RATE * load, 8)]
    data["repetitions"]["blocks"] = 1 if smoke else BLOCKS_PER_WAVE
    data["randomization"]["seed"] = SEED + wave * 10000 + load_id
    data["analysis"]["bootstrap_seed"] = SEED + wave * 10000 + load_id
    data["analysis"].pop("reference_configuration", None)
    destination = BASE / ("smoke" if smoke else "campaigns") / tag
    data["artifacts"]["output_root"] = str(destination.relative_to(ROOT))
    definition_path = GENERATED / ("smoke" if smoke else "campaigns") / f"{tag}.toml"
    serialized = render_toml(data)
    if tomllib.loads(serialized) != data:
        raise RuntimeError("TOML roundtrip mismatch")
    invocations = len(make_plan(data, None))
    if invocations != data["repetitions"]["blocks"] * 2:
        raise RuntimeError("Unexpected number of randomized scheduler invocations")
    if (
        data["delivery"]["mode"] != "fixed-arrivals"
        or data["workload"]["workers"] != 2
        or data["workload"]["work_units"] != WORK_UNITS
        or {c["id"] for c in data["configurations"]} != set(IDS)
        or len(data["configurations"]) != 2
        or len(data["perf"]["passes"]) != PASS_COUNT
        or data["perf"]["passes"] != EVENTS
    ):
        raise RuntimeError("Study configuration drifted from validated per-TID protocol")
    return {
        "wave": wave,
        "load": load,
        "tag": tag,
        "arrival_rate": data["delivery"]["arrival_rates"][0],
        "interval_us": 1e6 / data["delivery"]["arrival_rates"][0],
        "arrival_window_ms": (WORK_UNITS - 1) * 1e3 / data["delivery"]["arrival_rates"][0],
        "blocks": data["repetitions"]["blocks"],
        "invocations": invocations,
        "physical_runs": invocations * REPETITIONS_PER_PASS * PASS_COUNT,
        "name": data["experiment"]["name"],
    }, data, serialized, definition_path, destination


def plan(smoke: bool = False) -> list[dict]:
    cells = [(1, 0.50)] if smoke else ordered_cells()
    rows = [definition(wave, load, smoke)[0] for wave, load in cells]
    if not smoke:
        assert len(rows) == 22
        assert sum(row["physical_runs"] for row in rows) == 880
        assert all(row["blocks"] == 10 and row["physical_runs"] == 40 for row in rows)
    else:
        assert rows[0]["physical_runs"] == 4
    return rows


def require_boost_on() -> None:
    if not BOOST_PATH.is_file():
        raise RuntimeError(f"Missing CPU boost switch: {BOOST_PATH}")
    if BOOST_PATH.read_text().strip() != "1":
        raise RuntimeError("CPU Boost must be enabled (boost=1); no settings changed")


def save_snapshot(path: Path, doc: dict) -> None:
    encoded = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        old = json.loads(path.read_text())
        if old["boost"] != doc["boost"] or old["policies"] != doc["policies"]:
            raise RuntimeError(f"Changed CPU frequency settings since {path}")
    else:
        path.write_text(encoded)


def acquire_cell(wave: int, load: float, smoke: bool = False) -> dict:
    row, data, rendered, path, destination = definition(wave, load, smoke)
    require_boost_on()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() != rendered:
        raise RuntimeError(f"Conflicting existing definition: {path}")
    path.write_text(rendered)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".cell.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = system_snapshot()
        save_snapshot(destination / "power_before.json", before)
        prior = existing_campaign(row["name"], path, destination)
        if prior is None and any(destination.glob(row["name"] + "-*")):
            raise RuntimeError(f"Conflicting archived campaign at {destination}")
        if prior is None or not completed(prior, row["invocations"]):
            subprocess.run(command("prepare", path), cwd=ROOT, check=True)
            env = os.environ.copy()
            env["THREADANCE_PER_THREAD_PERF"] = "1"
            subprocess.run(
                command("resume" if prior else "collect", path, prior),
                cwd=ROOT, env=env, check=True,
            )
            prior = existing_campaign(row["name"], path, destination)
        if prior is None or not completed(prior, row["invocations"]):
            raise RuntimeError(f"Incomplete campaign: {row['name']}")
        verify_campaign_database(prior, data, row["invocations"])
        audit_perf_captures(prior, data, row["invocations"])
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.audit_bls_thread_perf",
             str(prior), str(row["physical_runs"])],
            cwd=ROOT, check=True,
        )
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.analyze_bls_thread_perf",
             str(prior)],
            cwd=ROOT, check=True,
        )
        after = system_snapshot()
        save_snapshot(destination / "power_after.json", after)
        if before["boost"] != after["boost"] or before["policies"] != after["policies"]:
            raise RuntimeError("CPU frequency policy changed during benchmark")
        return {**row, "campaign_dir": str(prior), "complete": True}


def execute(*, check: bool = False, smoke: bool = False, wave: int | None = None) -> dict:
    if wave is not None and wave not in WAVES:
        raise ValueError("wave must be 1 or 2")
    rows = plan(smoke)
    if wave is not None:
        rows = [r for r in rows if r["wave"] == wave]
    summary = {
        "study": STUDY,
        "cells": rows,
        "campaigns": len(rows),
        "measured_runs": sum(r["physical_runs"] for r in rows),
        "waves": 1 if smoke else 2,
        "perf_passes": [p["name"] for p in EVENTS],
        "note": "Load is a fraction of 1294 WU/s, not CPU occupancy",
    }
    if check:
        return summary
    if os.geteuid() == 0:
        raise RuntimeError("Run the benchmark as a normal user, not root")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before measurement")
    require_boost_on()
    subprocess.run(
        [sys.executable, "-m", "experiments.studies.check_perf_scope"],
        cwd=ROOT, check=True,
    )
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / ".study.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, row in enumerate(rows, 1):
            print(
                f"[{index}/{len(rows)}] wave={row['wave']} load={row['load']:.3f} "
                f"rate={row['arrival_rate']:.3f} WU/s",
                flush=True,
            )
            acquire_cell(row["wave"], row["load"], smoke)
    if not smoke and wave is None:
        subprocess.run(
            [sys.executable, "-m", "experiments.studies.analyze_bls_w2_boost_transition",
             "--run"],
            cwd=ROOT, check=True,
        )
    return {**summary, "completed": len(rows), "output_root": str(BASE)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--smoke", action="store_true")
    parser.add_argument("--wave", type=int, choices=WAVES)
    args = parser.parse_args()
    print(json.dumps(execute(check=args.check, smoke=args.smoke, wave=args.wave),
                     indent=2), flush=True)


if __name__ == "__main__":
    main()
