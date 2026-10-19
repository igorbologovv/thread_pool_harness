#!/usr/bin/env python3
"""Paired block-level analysis of the queue topology replication campaign."""
from __future__ import annotations

import argparse
import csv
import math
import random
import sqlite3
import statistics
import tomllib
from collections import defaultdict
from pathlib import Path

METRICS = ("task_clock_per_wu", "cycles_per_wu", "instructions_per_wu",
           "context_switches_per_wu", "cpu_migrations_per_wu", "elapsed_ms", "throughput")


def percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    pos = p * (len(xs) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def ci(values: list[float], seed: int, nboot: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    draws = [statistics.mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(nboot)]
    return percentile(draws, 0.025), percentile(draws, 0.975)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    folder = args.campaign.resolve()
    data = tomllib.loads((folder / "experiment.toml").read_text())
    expected_blocks = int(data["repetitions"]["blocks"])
    expected_runs = int(data["repetitions"]["measured_runs_per_block"])
    nboot = int(data["analysis"]["bootstrap_replicates"])
    seed = int(data["analysis"]["bootstrap_seed"])
    ref = data["analysis"]["reference_scheduler"]
    with (folder / "manifest.csv").open(newline="") as f:
        manifest = list(csv.DictReader(f))
    expected_conditions = len(data["threadance"]["schedulers"]) * len(data["delivery"]["arrival_rates"])
    if len(manifest) != expected_blocks * expected_conditions:
        raise RuntimeError("Incomplete manifest")
    by_id = {int(row["experiment_id"]): row for row in manifest}
    if len(by_id) != len(manifest):
        raise RuntimeError("Duplicate experiment ID")
    sql = """
        SELECT e.id experiment_id, e.work_units, r.id run_id, r.run_index,
               r.elapsed_ns, r.work_units_per_second, r.completed_work_units,
               r.success,
               MAX(CASE WHEN p.event_name='task-clock' THEN p.counter_value END) task_clock,
               MAX(CASE WHEN p.event_name='cycles' THEN p.counter_value END) cycles,
               MAX(CASE WHEN p.event_name='instructions' THEN p.counter_value END) instructions,
               MAX(CASE WHEN p.event_name='context-switches' THEN p.counter_value END) context_switches,
               MAX(CASE WHEN p.event_name='cpu-migrations' THEN p.counter_value END) cpu_migrations
        FROM experiment e JOIN run r ON e.id=r.experiment_id
        LEFT JOIN perf_metric p ON p.run_id=r.id
        GROUP BY e.id,r.id
        ORDER BY e.id,r.run_index
    """
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        raw = db.execute(sql).fetchall()
    names = ("experiment_id", "work_units", "run_id", "run_index", "elapsed_ns",
             "work_units_per_second", "completed_work_units", "success",
             "task_clock", "cycles", "instructions", "context_switches", "cpu_migrations")
    run_rows = []
    groups = defaultdict(list)
    for record in raw:
        row = dict(zip(names, record))
        eid = int(row["experiment_id"])
        if eid not in by_id:
            raise RuntimeError(f"Unexpected experiment {eid}")
        meta = by_id[eid]
        wu = int(row["work_units"])
        if row["success"] != 1 or row["completed_work_units"] != wu:
            raise RuntimeError(f"Invalid run {row['run_id']}")
        vals = dict(
            task_clock_per_wu=(row["task_clock"] / wu if row["task_clock"] is not None else None),
            cycles_per_wu=(row["cycles"] / wu if row["cycles"] is not None else None),
            instructions_per_wu=(row["instructions"] / wu if row["instructions"] is not None else None),
            context_switches_per_wu=(row["context_switches"] / wu if row["context_switches"] is not None else None),
            cpu_migrations_per_wu=(row["cpu_migrations"] / wu if row["cpu_migrations"] is not None else None),
            elapsed_ms=row["elapsed_ns"] / 1e6,
            throughput=row["work_units_per_second"],
        )
        result = dict(block=int(meta["block"]), order_in_block=int(meta["order_in_block"]),
                      global_order=int(meta["global_order"]), scheduler=meta["scheduler"],
                      arrival_rate=float(meta["arrival_rate"]), arrival_seed=int(meta["arrival_seed"]),
                      run_index=row["run_index"], run_id=row["run_id"], **vals)
        run_rows.append(result)
        groups[(result["block"], result["arrival_rate"], result["scheduler"])].append(result)
    write_csv(folder / "analysis_raw_runs.csv", run_rows)
    summaries = []
    for (block, rate, scheduler), observations in sorted(groups.items()):
        if len(observations) != expected_runs:
            raise RuntimeError(f"Expected {expected_runs} runs for {block}/{rate}/{scheduler}, got {len(observations)}")
        vals = {metric: (statistics.mean([r[metric] for r in observations])
                         if all(r[metric] is not None for r in observations) else None)
                for metric in METRICS}
        summaries.append(dict(block=block, arrival_rate=rate, scheduler=scheduler,
                              order_in_block=observations[0]["order_in_block"],
                              n_runs=len(observations), **vals))
    if len(summaries) != expected_blocks * expected_conditions:
        raise RuntimeError("Incomplete block summaries")
    write_csv(folder / "block_summaries.csv", summaries)
    by_key = {(x["block"], x["arrival_rate"], x["scheduler"]): x for x in summaries}
    effects = []
    for rate in sorted({x["arrival_rate"] for x in summaries}):
        for scheduler in data["threadance"]["schedulers"]:
            if scheduler == ref:
                continue
            for mi, metric in enumerate(METRICS):
                differences, relatives = [], []
                for block in range(1, expected_blocks + 1):
                    lhs = by_key[(block, rate, scheduler)][metric]
                    rhs = by_key[(block, rate, ref)][metric]
                    if lhs is None or rhs is None:
                        raise RuntimeError(f"Missing metric {metric} in block {block} rate {rate}")
                    differences.append(lhs-rhs)
                    relatives.append((lhs-rhs)/rhs if rhs else math.nan)
                if not all(math.isfinite(v) for v in differences+relatives):
                    raise RuntimeError(f"Non-finite effect for {scheduler}/{rate}/{metric}")
                low, high = ci(differences, seed + mi * 1000 + int(rate), nboot)
                rlow, rhigh = ci(relatives, seed + mi * 1000 + int(rate) + 1, nboot)
                effects.append(dict(arrival_rate=rate, scheduler=scheduler, reference=ref,
                                    metric=metric, n_blocks=expected_blocks,
                                    absolute_mean=statistics.mean(differences),
                                    absolute_ci95_low=low, absolute_ci95_high=high,
                                    relative_mean=statistics.mean(relatives),
                                    relative_ci95_low=rlow, relative_ci95_high=rhigh,
                                    positive_blocks=sum(d>0 for d in differences),
                                    negative_blocks=sum(d<0 for d in differences)))
    write_csv(folder / "paired_effects.csv", effects)
    print("Block-aware analysis complete:", folder)
    print("  raw runs:", len(run_rows), "block summaries:", len(summaries))
    for row in effects:
        if row["metric"] == "task_clock_per_wu":
            print(f"  {row['scheduler']} vs {ref} @ {row['arrival_rate']}: "
                  f"{row['relative_mean']*100:+.2f}% "
                  f"(95% block-bootstrap CI [{row['relative_ci95_low']*100:+.2f}%, "
                  f"{row['relative_ci95_high']*100:+.2f}%]), "
                  f"negative blocks={row['negative_blocks']}/{expected_blocks}")


if __name__ == "__main__":
    main()
