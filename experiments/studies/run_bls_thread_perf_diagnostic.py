"""Exploratory per-thread perf experiment. No harness source modifications.

Attach perf to already-running main/pacer and 2 worker TIDs. Since attachment is
not synchronized with individual runs, this diagnoses a multi-run window rather
than providing exact per-run counters. Raw measurements are never silently
treated as conventional process-wide perf results.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
import random
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / "target/release/threadance-harness"
RESULTS = ROOT / "results/studies/bls-thread-perf-diagnostic"
CONFIGS = {
    "mpmc-sp0": ["--scheduler", "threadance", "--threadance-wait-policy", "spin-then-park",
                 "--threadance-spin-us", "0", "--queue-capacity", "64"],
    "rtrb-b2": ["--scheduler", "threadance-hot-first-rtrb", "--threadance-wait-policy",
                 "spin-then-park", "--threadance-spin-us", "0", "--queue-capacity", "64",
                 "--hot-first-backlog", "2"],
}
EVENTS = "task-clock,cycles:uk,instructions:uk,context-switches,cache-misses:uk,branch-misses:uk"


def threads(pid: int):
    root = Path(f"/proc/{pid}/task")
    result = []
    for p in sorted(root.iterdir(), key=lambda p: int(p.name)):
        try:
            result.append({"tid": int(p.name), "comm": (p / "comm").read_text().strip()})
        except FileNotFoundError:
            continue
    return result


def command(config: str, runs: int, database: Path):
    return [
        str(BINARY), "--workload", "bls-aggregate-verify",
        "--workers", "2", "--work-units", "256",
        "--dataset-work-units", "2500", "--validators", "2000",
        "--signers-per-certificate", "1600", "--seed", "1",
        "--fixed-arrivals", "--arrival-rate", "647.0",
        "--warmup", "4", "--runs", str(runs), "--profile", "none",
        "--database", str(database), *CONFIGS[config],
    ]


def collect(config: str, block: int, runs: int, out: Path):
    out.mkdir(parents=True, exist_ok=False)
    with (out / "harness.log").open("w") as log:
        child = subprocess.Popen(command(config, runs, out / "measure.sqlite3"),
                                 cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        perf = None
        try:
            deadline = time.monotonic() + 15
            snapshot = []
            while child.poll() is None and time.monotonic() < deadline:
                snapshot = threads(child.pid)
                if len(snapshot) == 3 and any(t["tid"] == child.pid for t in snapshot):
                    break
                time.sleep(0.005)
            if len(snapshot) != 3 or not any(t["tid"] == child.pid for t in snapshot):
                raise RuntimeError(f"Expected pacer + 2 workers; got {snapshot}. Log: {out}")
            (out / "threads.json").write_text(json.dumps(snapshot, indent=2))
            perf_cmd = ["perf", "stat", "--per-thread", "-t",
                        ",".join(str(t["tid"]) for t in snapshot),
                        "-e", EVENTS, "-x", ",", "-o", str(out / "perf.csv")]
            (out / "perf_command.json").write_text(json.dumps(perf_cmd, indent=2))
            perf = subprocess.Popen(perf_cmd, cwd=ROOT, stderr=subprocess.PIPE)
            # The perf attachment is not gate-synchronized with a warmup or run.
            child.wait()
            perf.send_signal(__import__("signal").SIGINT)
            _, err = perf.communicate(timeout=20)
            (out / "perf.stderr").write_bytes(err)
            if child.returncode != 0 or perf.returncode not in (0, 130, -2):
                raise RuntimeError(
                    f"Harness exit={child.returncode}, perf exit={perf.returncode}: {out}"
                )
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait()
            if perf is not None and perf.poll() is None:
                perf.terminate()
                perf.wait()
    return {"block": block, "config": config, "pid": child.pid, "threads": snapshot}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--blocks", type=int, default=12)
    p.add_argument("--runs", type=int, default=40)
    p.add_argument("--output", type=Path, default=RESULTS)
    args = p.parse_args()
    if args.blocks < 1 or args.runs < 4:
        p.error("Need positive blocks and at least four runs")
    cells = []
    for block in range(1, args.blocks + 1):
        names = list(CONFIGS)
        random.Random(20261105 + block).shuffle(names)
        cells.extend((block, name) for name in names)
    print(json.dumps({"plan": cells, "runs_per_invocation": args.runs,
                      "events": EVENTS, "important": "external attach, not per-run synchronized"},
                     indent=2), flush=True)
    if args.check:
        return
    if not args.run:
        p.error("Pass --check or --run")
    if os.geteuid() == 0:
        p.error("Run as normal user")
    if not BINARY.is_file():
        p.error(f"Release binary missing: {BINARY}; build it first")
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = args.output / "manifest.jsonl"
    if manifest.exists():
        p.error(f"Output already exists: {manifest}; use a new --output")
    with manifest.open("x") as file:
        for block, name in cells:
            dest = args.output / f"block-{block:02d}-{name}"
            row = collect(name, block, args.runs, dest)
            file.write(json.dumps(row) + "\n")
            file.flush()
            print(f"Completed block={block} config={name}", flush=True)


if __name__ == "__main__":
    main()
