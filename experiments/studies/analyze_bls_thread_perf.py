"""Independent symmetric summary of per-TID BLS diagnostic, no mandatory reference.

Reports each configuration separately, and block-paired differences between
the two only when >=2 complete randomized blocks exist. Does not combine
different perf passes as one physical run.
"""
from __future__ import annotations
import csv
import json
import math
import random
import sqlite3
import statistics
import sys
import tomllib
from collections import defaultdict
from pathlib import Path


def save_csv(path, rows):
    if not rows:
        raise RuntimeError(f"No rows for {path}")
    with path.open("w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(dict.fromkeys(key for row in rows for key in row)))
        writer.writeheader()
        writer.writerows(rows)


def mean_ci(values, seed=20261106):
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, None, None
    rng = random.Random(seed)
    estimates = sorted(statistics.mean(rng.choices(values, k=len(values)))
                       for _ in range(10000))
    return mean, estimates[249], estimates[9749]


def analyze(folder):
    definition = tomllib.loads((folder / "experiment.toml").read_text())
    with (folder / "manifest.csv").open(newline="") as fp:
        manifest = list(csv.DictReader(fp))
    ids = {int(row["experiment_id"]): row for row in manifest}
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        rows = db.execute("""
            SELECT r.id, r.experiment_id, r.perf_pass, r.elapsed_ns,
                   r.work_units_per_second, e.work_units, p.event_name, p.counter_value
            FROM run r
            JOIN experiment e ON e.id = r.experiment_id
            JOIN perf_metric p ON p.run_id = r.id
            ORDER BY r.id, p.event_name
        """).fetchall()
    runs = {}
    for run_id, exp_id, perf_pass, elapsed, throughput, wu, event, value in rows:
        if exp_id not in ids:
            raise RuntimeError(f"Unknown experiment {exp_id}")
        row = runs.setdefault(run_id, {
            "block": int(ids[exp_id]["block"]),
            "configuration": ids[exp_id]["configuration"],
            "perf_pass": perf_pass,
            "elapsed_ms": elapsed / 1e6,
            "throughput": throughput,
        })
        key = event.split(":")[0].replace("-", "_") + "_per_wu"
        if key in row:
            raise RuntimeError(f"Duplicate event {event} in run {run_id}")
        row[key] = value / wu
    if not runs:
        raise RuntimeError("No measured runs")
    save_csv(folder / "thread_diagnostic_raw_runs.csv",
             [{"run_id": run_id, **run} for run_id, run in runs.items()])

    groups = defaultdict(list)
    for run in runs.values():
        for metric, value in run.items():
            if metric in ("block", "configuration", "perf_pass"):
                continue
            groups[(run["block"], run["configuration"], run["perf_pass"], metric)].append(value)

    block_rows = []
    for (block, config, perf_pass, metric), values in sorted(groups.items()):
        block_rows.append({"block": block, "configuration": config,
                           "perf_pass": perf_pass, "metric": metric,
                           "n": len(values), "mean": statistics.mean(values)})
    save_csv(folder / "thread_diagnostic_block_means.csv", block_rows)

    sets = defaultdict(list)
    for row in block_rows:
        sets[(row["configuration"], row["perf_pass"], row["metric"])].append(row["mean"])
    summary = []
    for (config, perf_pass, metric), values in sorted(sets.items()):
        mean, low, high = mean_ci(values)
        summary.append({"configuration": config, "perf_pass": perf_pass,
                        "metric": metric, "n_blocks": len(values),
                        "mean": mean, "ci95_low": low, "ci95_high": high})
    save_csv(folder / "thread_diagnostic_summary.csv", summary)

    configs = sorted({k[0] for k in sets})
    if len(configs) != 2:
        raise RuntimeError(f"Expected exactly two configurations: {configs}")
    by_block = {(r["block"], r["configuration"], r["perf_pass"], r["metric"]): r["mean"]
                for r in block_rows}
    contrasts = []
    for perf_pass, metric in sorted({(r["perf_pass"], r["metric"]) for r in block_rows}):
        block_ids = sorted({r["block"] for r in block_rows})
        paired = []
        for block in block_ids:
            keys = [(block, config, perf_pass, metric) for config in configs]
            if any(key not in by_block for key in keys):
                raise RuntimeError(f"Incomplete paired block {block}, {perf_pass}, {metric}")
            paired.append(by_block[keys[1]] - by_block[keys[0]])
        mean, low, high = mean_ci(paired)
        contrasts.append({"configuration_a": configs[0], "configuration_b": configs[1],
                          "difference": "b_minus_a", "perf_pass": perf_pass,
                          "metric": metric, "n_blocks": len(paired),
                          "mean_difference": mean, "ci95_low": low, "ci95_high": high})
    save_csv(folder / "thread_diagnostic_paired_differences.csv", contrasts)
    return {"runs": len(runs), "blocks": len({r["block"] for r in block_rows}),
            "summary": str(folder / "thread_diagnostic_summary.csv"),
            "paired_differences": str(folder / "thread_diagnostic_paired_differences.csv"),
            "ci_available": len({r["block"] for r in block_rows}) >= 2}


def main():
    print(json.dumps(analyze(Path(sys.argv[1])), indent=2))


if __name__ == "__main__":
    main()
