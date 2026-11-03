"""Analyze the two-wave boost-on transition without treating physical runs as independent blocks.

Raw SQLite runs, TID records and submission lateness are retained per campaign.
Outputs per-run, per-block and wave-stratified uncertainty summaries. A nonzero
worker task-clock indicates activity, NOT a measured task completion or wakeup.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

from experiments.studies.run_bls_w2_boost_transition import (
    BASE,
    IDS,
    LOADS,
    STUDY,
    WAVES,
    definition,
    ordered_cells,
)

BOOTSTRAPS = 10000
ACTIVE_THRESHOLDS_MS = (0.1, 1.0, 5.0)
METRICS = (
    "cpu_ms_per_wu",
    "cycles_per_wu",
    "effective_ghz",
    "worker0_cpu_ms_per_wu",
    "worker1_cpu_ms_per_wu",
    "worker0_ghz",
    "worker1_active_gt_1ms",
    "cache_misses_per_wu",
    "branch_misses_per_wu",
    "context_switches_per_wu",
    "throughput",
    "mean_lateness_us",
    "max_lateness_us",
)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No observations for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(x: list[float], seed: int, *, strata: list[int] | None = None):
    if not x:
        return None, None, None, None
    mean = statistics.mean(x)
    variance = statistics.variance(x) if len(x) > 1 else None
    if len(x) < 2:
        return mean, variance, None, None
    rng = random.Random(seed)
    if strata is None:
        groups = {0: x}
    else:
        groups = defaultdict(list)
        for value, group in zip(x, strata, strict=True):
            groups[group].append(value)
        if any(len(a) < 2 for a in groups.values()):
            return mean, variance, None, None
    estimates = []
    for _ in range(BOOTSTRAPS):
        sampled = []
        for group in sorted(groups):
            population = groups[group]
            sampled.extend(rng.choices(population, k=len(population)))
        estimates.append(statistics.mean(sampled))
    estimates.sort()
    n = len(estimates)
    return mean, variance, estimates[max(0, int(0.025 * n) - 1)], estimates[min(n - 1, int(0.975 * n) - 1)]


def inspect_campaign(folder: Path, wave: int, load: float) -> list[dict]:
    _, data, _, _, _ = definition(wave, load)
    expected = data["repetitions"]["blocks"] * len(IDS) * 4
    with (folder / "manifest.csv").open(newline="") as f:
        manifests = {int(r["experiment_id"]): r for r in csv.DictReader(f)}
    if len(manifests) != data["repetitions"]["blocks"] * len(IDS):
        raise ValueError(f"Incomplete manifest: {folder}")

    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        db.row_factory = sqlite3.Row
        runs = db.execute("""
            SELECT r.id, r.experiment_id, r.perf_pass, r.elapsed_ns,
                   r.work_units_per_second, r.completed_work_units,
                   p.raw_json
            FROM run AS r JOIN perf_capture AS p ON p.run_id = r.id
            ORDER BY r.id
        """).fetchall()
        lateness = {}
        for r in db.execute("""
            SELECT run_id, AVG(lateness_ns)/1000.0 AS mean_us,
                   MAX(lateness_ns)/1000.0 AS max_us,
                   COUNT(*) AS n
            FROM run_arrival GROUP BY run_id
        """):
            lateness[int(r["run_id"])] = r
    if len(runs) != expected:
        raise ValueError(f"{folder}: expected {expected} runs, got {len(runs)}")

    out = []
    for run in runs:
        rid = int(run["id"])
        eid = int(run["experiment_id"])
        if eid not in manifests:
            raise ValueError(f"Missing experiment ID {eid}")
        manifest = manifests[eid]
        if int(run["completed_work_units"]) != 256:
            raise ValueError(f"Unexpected work units in run {rid}")
        if rid not in lateness or int(lateness[rid]["n"]) != 256:
            raise ValueError(f"Missing scheduled/actual arrivals in run {rid}")
        doc = json.loads(run["raw_json"])
        names = {int(x["tid"]): x["comm"] for x in doc["thread_map"]}
        if set(names.values()) != {"threadance-harn", "threadance-00", "threadance-01"}:
            raise ValueError(f"Unexpected thread names in run {rid}: {names}")
        by_comm = defaultdict(dict)
        for record in doc["records"]:
            tid = int(record["tid"])
            key = record["event"].split(":")[0]
            value = float(record["accounting_value"])
            if not math.isfinite(value) or value < 0 or key in by_comm[names[tid]]:
                raise ValueError(f"Invalid/duplicate counter {rid}/{names[tid]}/{key}")
            by_comm[names[tid]][key] = value
        required = ("task-clock", "cycles", "context-switches")
        if any(not all(k in by_comm[name] for k in required)
               for name in ("threadance-harn", "threadance-00", "threadance-01")):
            raise ValueError(f"Missing core counters in run {rid}")
        pacer, w0, w1 = (
            by_comm["threadance-harn"], by_comm["threadance-00"],
            by_comm["threadance-01"],
        )
        clock_ms = sum(t["task-clock"] for t in (pacer, w0, w1))
        cycles = sum(t["cycles"] for t in (pacer, w0, w1))
        if clock_ms <= 0:
            raise ValueError(f"Zero total task clock: {rid}")

        def freq(thread):
            if thread["task-clock"] <= 0:
                return None
            return thread["cycles"] / (thread["task-clock"] * 1e6)

        raw = {
            "wave": wave,
            "load": load,
            "rate_wu_s": data["delivery"]["arrival_rates"][0],
            "block": int(manifest["block"]),
            "configuration": manifest["configuration"],
            "pass": run["perf_pass"],
            "run_id": rid,
            "elapsed_ms": run["elapsed_ns"] / 1e6,
            "throughput": float(run["work_units_per_second"]),
            "cpu_ms_per_wu": clock_ms / 256,
            "cycles_per_wu": cycles / 256,
            "effective_ghz": cycles / (clock_ms * 1e6),
            "pacer_cpu_ms_per_wu": pacer["task-clock"] / 256,
            "worker0_cpu_ms_per_wu": w0["task-clock"] / 256,
            "worker1_cpu_ms_per_wu": w1["task-clock"] / 256,
            "worker0_ghz": freq(w0),
            "worker1_ghz": freq(w1),
            "worker1_cpu_ms": w1["task-clock"],
            "worker1_active_gt_0p1ms": float(w1["task-clock"] > 0.1),
            "worker1_active_gt_1ms": float(w1["task-clock"] > 1.0),
            "worker1_active_gt_5ms": float(w1["task-clock"] > 5.0),
            "context_switches_per_wu": sum(t["context-switches"]
                                           for t in (pacer, w0, w1)) / 256,
            "mean_lateness_us": float(lateness[rid]["mean_us"]),
            "max_lateness_us": float(lateness[rid]["max_us"]),
            "cache_misses_per_wu": None,
            "branch_misses_per_wu": None,
        }
        for event, label in (("cache-misses", "cache_misses_per_wu"),
                             ("branch-misses", "branch_misses_per_wu")):
            if all(event in t for t in (pacer, w0, w1)):
                raw[label] = sum(t[event] for t in (pacer, w0, w1)) / 256
        if raw["configuration"] not in IDS:
            raise ValueError(f"Wrong configuration {raw['configuration']}")
        out.append(raw)
    return out


def load_runs(root: Path = BASE) -> list[dict]:
    all_runs = []
    for wave, load in ordered_cells():
        row, _, _, _, destination = definition(wave, load)
        if root != BASE:
            destination = root / "campaigns" / row["tag"]
        folders = sorted(destination.glob(row["name"] + "-*"))
        if len(folders) != 1:
            raise RuntimeError(f"Expected one campaign for {row['tag']}: {folders}")
        all_runs.extend(inspect_campaign(folders[0], wave, load))
    return all_runs


def aggregate(runs: list[dict]):
    """Block means, paired contrasts, and wave-stratified 95% CIs."""
    grouped = defaultdict(list)
    for run in runs:
        for metric in METRICS:
            value = run.get(metric)
            if value is not None:
                grouped[(run["wave"], run["load"], run["block"],
                         run["configuration"], run["pass"], metric)].append(value)
    blocks = []
    for (wave, load, block, config, perf_pass, metric), vals in sorted(grouped.items()):
        if len(vals) != 2:
            raise ValueError(f"Expected exactly two measured repeats: {wave}/{load}/{block}/{config}/{perf_pass}/{metric}")
        blocks.append({"wave": wave, "load": load, "block": block,
                       "configuration": config, "pass": perf_pass,
                       "metric": metric, "mean": statistics.mean(vals)})
    by_key = {(b["wave"], b["load"], b["block"], b["configuration"],
               b["pass"], b["metric"]): b["mean"] for b in blocks}
    summaries, contrasts = [], []
    for load in LOADS:
        for perf_pass in ("cpu-cache", "l1-branches"):
            for metric in METRICS:
                available = [
                    b for b in blocks if b["load"] == load
                    and b["pass"] == perf_pass and b["metric"] == metric
                ]
                if not available:
                    continue
                for config in IDS:
                    rows = [b for b in available if b["configuration"] == config]
                    if len(rows) != 20:
                        raise ValueError(f"Missing block means {load}/{perf_pass}/{metric}/{config}")
                    vals = [b["mean"] for b in rows]
                    m, v, lo, hi = summarize(vals, 20261115 + round(load * 10000),
                                             strata=[b["wave"] for b in rows])
                    summaries.append({"load": load, "pass": perf_pass,
                                      "configuration": config, "metric": metric,
                                      "n_blocks": len(rows), "mean": m,
                                      "variance": v, "ci95_low": lo, "ci95_high": hi})
                diffs = []
                strata = []
                for wave in WAVES:
                    for block in range(1, 11):
                        ka = (wave, load, block, IDS[0], perf_pass, metric)
                        kb = (wave, load, block, IDS[1], perf_pass, metric)
                        if ka not in by_key or kb not in by_key:
                            raise ValueError(f"Missing paired block {ka}/{kb}")
                        diffs.append(by_key[kb] - by_key[ka])
                        strata.append(wave)
                if len(diffs) == 20:
                    m, v, lo, hi = summarize(diffs, 20261115 + round(load * 10000) + 991,
                                             strata=strata)
                    contrasts.append({"load": load, "pass": perf_pass, "metric": metric,
                                      "rtrb_minus_mpmc": m, "variance": v,
                                      "ci95_low": lo, "ci95_high": hi,
                                      "n_paired_blocks": len(diffs)})
    return blocks, summaries, contrasts


def plots(summaries: list[dict], output: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    keys = (
        ("cpu_ms_per_wu", "CPU time per work unit (ms)"),
        ("effective_ghz", "Effective frequency (GHz)"),
        ("worker1_cpu_ms_per_wu", "Second worker CPU time per work unit (ms)"),
        ("worker1_active_gt_1ms", "Worker 1 active fraction (CPU time > 1 ms)"),
        ("cycles_per_wu", "Cycles per work unit"),
    )
    for metric, ylabel in keys:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for config in IDS:
            data = sorted((r for r in summaries
                           if r["pass"] == "cpu-cache"
                           and r["configuration"] == config
                           and r["metric"] == metric),
                          key=lambda r: r["load"])
            if not data:
                continue
            x = [r["load"] * 100 for r in data]
            y = [r["mean"] for r in data]
            ax.plot(x, y, marker="o", label=config)
            lo = [r["ci95_low"] for r in data]
            hi = [r["ci95_high"] for r in data]
            if all(a is not None and b is not None for a, b in zip(lo, hi)):
                ax.fill_between(x, lo, hi, alpha=0.15)
        ax.set_xlabel("Offered load (% of two-worker reference throughput)")
        ax.set_ylabel(ylabel)
        ax.legend()
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(output / f"{metric}.png", dpi=170)
        plt.close(fig)
    return True


def analyze(root: Path = BASE) -> dict:
    runs = load_runs(root)
    if len(runs) != 880:
        raise ValueError(f"Expected 880 measured physical runs, got {len(runs)}")
    output = root / "analysis"
    output.mkdir(parents=True, exist_ok=True)
    blocks, summaries, contrasts = aggregate(runs)
    write_csv(output / "raw_runs.csv", runs)
    write_csv(output / "block_means.csv", blocks)
    write_csv(output / "summaries.csv", summaries)
    write_csv(output / "paired_differences.csv", contrasts)
    charted = plots(summaries, output)
    report = {
        "study": STUDY, "physical_runs": len(runs),
        "paired_blocks_per_load": 20,
        "confidence_interval": "wave-stratified block bootstrap, 10000 resamples",
        "worker_identity": "threadance-00 and threadance-01 from per-run TID map",
        "activation_definition": "second worker task-clock > 1 ms per measured run; not per-task routing",
        "charts_available": charted,
        "output": str(output),
    }
    (output / "analysis_manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    print(json.dumps(analyze(), indent=2))


if __name__ == "__main__":
    main()
