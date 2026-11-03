"""Validate every raw per-TID counter and export exact per-run observations."""
from __future__ import annotations
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def audit(folder: Path, expected_runs: int) -> dict:
    output = folder / "per_thread_perf.csv"
    rows = []
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        captures = db.execute("""
            SELECT r.id, r.perf_pass, p.raw_json
            FROM run r JOIN perf_capture p ON p.run_id = r.id
            ORDER BY r.id
        """).fetchall()
    if len(captures) != expected_runs:
        raise RuntimeError(f"Expected {expected_runs} perf captures, got {len(captures)}")
    required = {"task-clock", "cycles", "instructions", "cache-misses",
                "context-switches"}  # Other events checked by the main PMU audit.
    for run_id, perf_pass, raw in captures:
        doc = json.loads(raw)
        tids = {int(t["tid"]): t["role"] for t in doc["thread_map"]}
        if len(tids) != 3 or sorted(tids.values()) != ["pacer", "worker", "worker"]:
            raise RuntimeError(f"Bad TID map for run {run_id}")
        observed = defaultdict(set)
        for event in doc["records"]:
            tid = int(event["tid"])
            name = event["event"].split(":")[0]
            if tid not in tids or event["role"] != tids[tid]:
                raise RuntimeError(f"Thread-map mismatch in run {run_id}")
            if name in observed[tid]:
                raise RuntimeError(f"Duplicate event {name} for TID {tid} in run {run_id}")
            observed[tid].add(name)
            state = event["measurement_state"]
            raw_value = event["counter-value"]
            if state == "inactive-no-scheduled-runtime":
                if raw_value != "<not counted>" and float(raw_value) != 0.0:
                    raise RuntimeError(f"Invalid inactive TID value {tid} {name}")
                val = 0.0
                if float(event["accounting_value"]) != 0.0:
                    raise RuntimeError(f"Invalid inactive accounting for {tid}")
            elif state == "counted":
                val = float(raw_value)
                if float(event["accounting_value"]) != val:
                    raise RuntimeError(f"Accounting mismatch {tid} {name}")
            else:
                raise RuntimeError(f"Unknown measurement state {state}")
            if not 0 <= val < float("inf"):
                raise RuntimeError(f"Invalid counter {name} for run {run_id}")
            rows.append((run_id, perf_pass, tid, tids[tid], name, val, state, raw_value))
        if set(observed) != set(tids):
            raise RuntimeError(f"Missing TID in run {run_id}")
        if perf_pass == "cpu-cache" and any(not required.issubset(seen) for seen in observed.values()):
            raise RuntimeError(f"Missing required events in run {run_id}")
    with output.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "perf_pass", "tid", "role", "event", "accounting_value", "measurement_state", "raw_value"])
        w.writerows(rows)
    return {"runs": len(captures), "events": len(rows), "csv": str(output)}


def main():
    folder = Path(sys.argv[1])
    expected = int(sys.argv[2])
    print(json.dumps(audit(folder, expected), indent=2))


if __name__ == "__main__":
    main()
