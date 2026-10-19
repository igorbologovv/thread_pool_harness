#!/usr/bin/env python3
"""Randomized complete-block comparison of Hot-first, Threadance and Rayon.

Preparation is unprivileged; the measurement phase can be run with sudo when
perf_event_paranoid requires it. The TOML is the sole experiment definition.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Allow direct invocation as python experiments/queue_topology/run.py.
sys.path.insert(0, str(ROOT))
EXPERIMENT = Path(__file__).with_name("experiment.toml")
BENCHMARK_ID = re.compile(r"Stored benchmark: session=\d+ experiment=(\d+) database=")
SCHEDULERS = ("threadance-hot-first", "threadance", "rayon")


def run_output(argv: list[str], *, check: bool = True) -> str:
    p = subprocess.run(argv, text=True, capture_output=True, cwd=ROOT)
    if check and p.returncode:
        raise RuntimeError(f"{argv!r} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def load_definition() -> dict:
    data = tomllib.loads(EXPERIMENT.read_text())
    assert data["experiment"]["workload"] == "bls-aggregate-verify"
    assert data["experiment"]["profile"] == "standard"
    assert data["delivery"]["mode"] == "bursty-arrivals"
    assert sorted(data["threadance"]["schedulers"]) == sorted(SCHEDULERS)
    assert data["threadance"]["wait_policy"] == "spin-then-park"
    w = data["workload"]
    assert all(int(w[k]) > 0 for k in ("workers", "work_units", "queue_capacity", "validators", "signers_per_certificate", "certificates_per_work_unit"))
    assert int(data["threadance"]["hot_first_backlog"]) >= 1
    # Hot-first allocates exactly workers * backlog physical slots.
    assert int(w["queue_capacity"]) == int(w["workers"]) * int(data["threadance"]["hot_first_backlog"]), "capacity must equal Hot-first physical slots"
    assert len(data["delivery"]["arrival_rates"]) == 2
    assert all(float(x) > 0 for x in data["delivery"]["arrival_rates"])
    r = data["repetitions"]
    assert int(r["blocks"]) > 1 and int(r["measured_runs_per_block"]) > 0 and int(r["warmup_runs_per_block"]) >= 0
    return data


def plan(data: dict) -> list[dict]:
    rates = [float(x) for x in data["delivery"]["arrival_rates"]]
    rows = []
    for block in range(1, int(data["repetitions"]["blocks"]) + 1):
        conditions = [(rate, scheduler) for rate in rates for scheduler in data["threadance"]["schedulers"]]
        random.Random(int(data["randomization"]["seed"]) + block).shuffle(conditions)
        # Same arrival pattern across schedulers at a given rate within block.
        arrival_seed = int(data["delivery"]["arrival_seed_base"]) + block - 1
        for order, (rate, scheduler) in enumerate(conditions, 1):
            rows.append(dict(block=block, order_in_block=order, global_order=len(rows)+1,
                             arrival_rate=rate, scheduler=scheduler, arrival_seed=arrival_seed))
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def dataset_identity(data: dict) -> dict:
    from experiments.common.bls_dataset_identity import (
        config_from_experiment, expected_dataset_path, identify_dataset,
    )
    cache, cfg = config_from_experiment(EXPERIMENT)
    return asdict(identify_dataset(expected_dataset_path(ROOT / cache, cfg), cfg))


def benchmark_command(data: dict, row: dict, database: Path, campaign: str) -> list[str]:
    w = data["workload"]
    t = data["threadance"]
    r = data["repetitions"]
    return [
        str(ROOT / "target/release/threadance-harness"),
        "--workload", data["experiment"]["workload"],
        "--scheduler", row["scheduler"],
        "--workers", str(w["workers"]),
        "--queue-capacity", str(w["queue_capacity"]),
        "--hot-first-backlog", str(t["hot_first_backlog"]),
        "--threadance-wait-policy", t["wait_policy"],
        "--threadance-spin-us", str(t["spin_us"]),
        "--work-units", str(w["work_units"]),
        "--validators", str(w["validators"]),
        "--signers-per-certificate", str(w["signers_per_certificate"]),
        "--certificates-per-work-unit", str(w["certificates_per_work_unit"]),
        "--seed", str(w["workload_seed"]),
        "--dataset-cache-dir", str((ROOT / data["artifacts"]["dataset_cache_dir"]).resolve()),
        "--bursty-arrivals", "--arrival-rate", str(row["arrival_rate"]),
        "--arrival-seed", str(row["arrival_seed"]),
        "--warmup", str(r["warmup_runs_per_block"]),
        "--runs", str(r["measured_runs_per_block"]),
        "--profile", data["experiment"]["profile"],
        "--campaign-id", campaign, "--database", str(database),
    ]


def prepare(data: dict) -> None:
    if os.geteuid() == 0:
        raise RuntimeError("prepare as normal user; sudo is only needed for measurement")
    print("Building release harness...")
    subprocess.run(["cargo", "build", "--release", "-p", "threadance-harness", "--bin", "threadance-harness"], cwd=ROOT, check=True)
    # A single 1000-WU invocation prepares the cached BLS dataset, outside measurements.
    w = data["workload"]
    print("Preparing cached 1000-WU BLS dataset (may take some time on first run)...")
    subprocess.run([
        str(ROOT / "target/release/threadance-harness"),
        "--workload", "bls-aggregate-verify", "--scheduler", "rayon",
        "--workers", str(w["workers"]), "--work-units", str(w["work_units"]),
        "--validators", str(w["validators"]), "--signers-per-certificate", str(w["signers_per_certificate"]),
        "--certificates-per-work-unit", str(w["certificates_per_work_unit"]),
        "--seed", str(w["workload_seed"]), "--all-at-once",
        "--warmup", "0", "--runs", "1", "--profile", "none",
        "--database", str(ROOT / "results/queue_topology/preparation.sqlite3"),
    ], cwd=ROOT, check=True)
    print("Dataset identity:", dataset_identity(data)["sha256"])


def collect(data: dict, *, dry_run: bool) -> None:
    if os.geteuid() != 0:
        print("NOTE: standard perf profiling may require sudo on this machine.")
    os.chdir(ROOT)
    rows = plan(data)
    if dry_run:
        for row in rows:
            print(row)
        return
    if run_output(["git", "status", "--porcelain", "--untracked-files=no"]):
        raise RuntimeError("Tracked tree is dirty; commit changes before the campaign")
    if not (ROOT / "target/release/threadance-harness").is_file():
        raise RuntimeError("Release harness not found; run prepare first")
    identity = dataset_identity(data)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    campaign = f"queue-topology-{stamp}"
    out = ROOT / data["artifacts"]["output_root"] / campaign
    out.mkdir(parents=True, exist_ok=False)
    (out / "logs").mkdir()
    db = (out / "benchmarks.sqlite3").resolve()
    shutil.copy2(EXPERIMENT, out / "experiment.toml")
    (out / "dataset_identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    write_csv(out / "plan.csv", rows)
    meta = dict(campaign_id=campaign, git_commit=run_output(["git", "rev-parse", "HEAD"]),
                git_branch=run_output(["git", "branch", "--show-current"]),
                threadance_commit=run_output(["git", "-C", str(ROOT.parent / "threadance"), "rev-parse", "HEAD"], check=False),
                kernel=run_output(["uname", "-r"]), cpu=run_output(["lscpu"], check=False),
                perf=run_output(["perf", "--version"], check=False),
                binary_sha256=hashlib.sha256((ROOT / "target/release/threadance-harness").read_bytes()).hexdigest(),
                dataset_sha256=identity["sha256"], timestamp=datetime.now().astimezone().isoformat())
    (out / "environment.json").write_text(json.dumps(meta, indent=2) + "\n")
    manifest = []
    settle = float(data["execution"]["settle_seconds"])
    try:
        for row in rows:
            if settle:
                time.sleep(settle)  # Fixed pause only, not an active stability gate.
            print(f"Block {row['block']}/{data['repetitions']['blocks']}, order {row['order_in_block']}: {row['scheduler']} @ {row['arrival_rate']} WU/s", flush=True)
            cmd = benchmark_command(data, row, db, campaign)
            log = out / "logs" / f"block-{row['block']:02d}-order-{row['order_in_block']:02d}.log"
            with log.open("w") as f:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT)
                assert proc.stdout
                experiment_id = None
                for line in proc.stdout:
                    f.write(line)
                    if line.startswith("Stored benchmark:"):
                        m = BENCHMARK_ID.search(line)
                        if m:
                            experiment_id = int(m.group(1))
                if proc.wait() != 0 or experiment_id is None:
                    raise RuntimeError(f"Benchmark failed, inspect {log}")
            manifest.append(dict(**row, experiment_id=experiment_id, log=str(log.relative_to(out))))
            write_csv(out / "manifest.csv", manifest)
    except BaseException:
        print(f"Partial campaign retained at {out}", file=sys.stderr)
        raise
    with sqlite3.connect(db) as conn:
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"Foreign-key violations: {fk_errors[:5]}")
        experiments = conn.execute("SELECT COUNT(*) FROM experiment").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM run").fetchone()[0]
        missing = conn.execute("SELECT COUNT(*) FROM run r LEFT JOIN perf_metric p ON p.run_id=r.id AND p.event_name='task-clock' WHERE p.counter_value IS NULL").fetchone()[0]
        invalid = conn.execute("SELECT COUNT(*) FROM run WHERE success<>1 OR completed_work_units<>?", (int(data["workload"]["work_units"]),)).fetchone()[0]
    expected = len(rows) * int(data["repetitions"]["measured_runs_per_block"])
    if experiments != len(rows) or runs != expected or missing or invalid:
        raise RuntimeError(f"Campaign integrity failed: experiments={experiments}/{len(rows)}, runs={runs}/{expected}, missing task-clock={missing}, invalid={invalid}")
    print(f"Complete: {out}\nAnalyze with: python experiments/queue_topology/analysis.py {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "plan", "collect"))
    args = parser.parse_args()
    data = load_definition()
    if args.action == "prepare":
        prepare(data)
    elif args.action == "plan":
        collect(data, dry_run=True)
    else:
        collect(data, dry_run=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
