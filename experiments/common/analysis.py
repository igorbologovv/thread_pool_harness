"""Paired block-level analysis for TOML-defined benchmark campaigns."""
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

METRICS = {
    "task-clock": "task_clock_per_wu",
    "cycles": "cycles_per_wu",
    "instructions": "instructions_per_wu",
    "context-switches": "context_switches_per_wu",
    "cpu-migrations": "cpu_migrations_per_wu",
    "branch-misses": "branch_misses_per_wu",
}
OUTPUT_METRICS = tuple(METRICS.values()) + ("elapsed_ms", "throughput", "drain_delay_ms")


def percentile(items: list[float], p: float) -> float:
    values = sorted(items)
    position = p * (len(values) - 1)
    left = math.floor(position)
    right = math.ceil(position)
    return values[left] + (values[right] - values[left]) * (position - left)


def ci(samples: list[float], rng: random.Random, count: int) -> tuple[float, float]:
    estimates = [statistics.mean(rng.choices(samples, k=len(samples))) for _ in range(count)]
    return percentile(estimates, .025), percentile(estimates, .975)


def csv_write(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("Empty analysis result")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze(folder: Path) -> None:
    definition = tomllib.loads((folder / "experiment.toml").read_text())
    with (folder / "manifest.csv").open(newline="") as handle:
        manifest = list(csv.DictReader(handle))
    with (folder / "plan.csv").open(newline="") as handle:
        plan = list(csv.DictReader(handle))
    if len(plan) != len(manifest):
        raise RuntimeError("Incomplete manifest")
    ids = {int(row["experiment_id"]): row for row in manifest}
    if len(ids) != len(manifest):
        raise RuntimeError("Duplicate experiment IDs")
    sql = """
    SELECT e.id, e.work_units, r.id, r.run_index, r.success,
           r.completed_work_units, r.elapsed_ns, r.work_units_per_second,
           (SELECT MAX(a.actual_submit_offset_ns) FROM run_arrival a
            WHERE a.run_id=r.id),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='task-clock'),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='cycles'),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='instructions'),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='context-switches'),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='cpu-migrations'),
           (SELECT MAX(p.counter_value) FROM perf_metric p
            WHERE p.run_id=r.id AND p.event_name='branch-misses')
    FROM experiment e JOIN run r ON r.experiment_id=e.id
    ORDER BY e.id,r.run_index
    """
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        records = db.execute(sql).fetchall()
    grouped = defaultdict(list)
    raw = []
    for row in records:
        eid, work_units, run_id, index, success, completed, elapsed_ns, throughput, last_arrival, *perf = row
        if eid not in ids or success != 1 or completed != work_units:
            raise RuntimeError(f"Unexpected/failed run {run_id}")
        m = ids[eid]
        entry = dict(block=int(m["block"]), order_in_block=int(m["order_in_block"]),
                     configuration=m["configuration"], spin_us=int(m["spin_us"]),
                     arrival_rate=m["arrival_rate"], rho=m["rho"],
                     run_id=run_id, run_index=index,
                     elapsed_ms=elapsed_ns / 1e6, throughput=throughput,
                     drain_delay_ms=max(0, elapsed_ns-last_arrival) / 1e6 if last_arrival is not None else None)
        entry.update({name: (float(value) / work_units if value is not None else None)
                      for name, value in zip(METRICS.values(), perf)})
        raw.append(entry)
        grouped[(entry["block"], entry["configuration"], entry["spin_us"],
                 entry["arrival_rate"])].append(entry)
    expected = int(definition["repetitions"]["measured_runs_per_block"])
    summaries = []
    for key, items in sorted(grouped.items()):
        if len(items) != expected:
            raise RuntimeError(f"Missing measured runs for {key}")
        summary = dict(block=key[0], configuration=key[1], spin_us=key[2],
                       arrival_rate=key[3], rho=items[0]["rho"], n_runs=len(items))
        for metric in OUTPUT_METRICS:
            values = [item[metric] for item in items]
            summary[metric] = statistics.mean(values) if all(v is not None for v in values) else None
        summaries.append(summary)
    expected_summaries = int(definition["repetitions"]["blocks"]) * (
        len(plan) // int(definition["repetitions"]["blocks"]))
    if len(summaries) != expected_summaries:
        raise RuntimeError("Incomplete block summaries")
    by_key = {(row["block"], row["configuration"], row["spin_us"], row["arrival_rate"]): row
              for row in summaries}
    reference = definition.get("analysis", {}).get("reference_configuration",
                                                   definition["configurations"][0]["id"])
    blocks = int(definition["repetitions"]["blocks"])
    bootstrap = int(definition.get("analysis", {}).get("bootstrap_replicates", 10000))
    rng = random.Random(int(definition.get("analysis", {}).get("bootstrap_seed", 20261019)))
    effects = []
    for config, spin, rate in sorted({(m["configuration"], int(m["spin_us"]), m["arrival_rate"])
                                      for m in manifest}):
        if config == reference:
            continue
        for metric in OUTPUT_METRICS:
            differences, relative = [], []
            for block in range(1, blocks + 1):
                lhs = by_key[(block, config, spin, rate)][metric]
                refs = [x for x in summaries if x["block"] == block and
                        x["configuration"] == reference and x["arrival_rate"] == rate]
                if len(refs) != 1:
                    raise RuntimeError("Reference must have exactly one condition per arrival rate")
                rhs = refs[0][metric]
                if lhs is None or rhs is None:
                    if metric == "drain_delay_ms" and lhs is None and rhs is None:
                        differences = []
                        break
                    raise RuntimeError(f"Missing {metric} for {config} in block {block}")
                differences.append(lhs - rhs)
                relative.append((lhs-rhs)/rhs if rhs else math.nan)
            if not differences:
                continue
            if not all(math.isfinite(v) for v in differences):
                raise RuntimeError("Non-finite block difference")
            low, high = ci(differences, rng, bootstrap)
            relative = [v for v in relative if math.isfinite(v)]
            rlow, rhigh = ci(relative, rng, bootstrap) if relative else (math.nan, math.nan)
            effects.append(dict(configuration=config, reference=reference, spin_us=spin,
                                arrival_rate=rate, metric=metric, n_blocks=len(differences),
                                absolute_mean=statistics.mean(differences),
                                absolute_ci95_low=low, absolute_ci95_high=high,
                                relative_mean=statistics.mean(relative) if relative else math.nan,
                                relative_ci95_low=rlow, relative_ci95_high=rhigh,
                                positive_blocks=sum(x > 0 for x in differences),
                                negative_blocks=sum(x < 0 for x in differences)))
    csv_write(folder / "analysis_raw_runs.csv", raw)
    csv_write(folder / "block_summaries.csv", summaries)
    if effects:
        csv_write(folder / "paired_effects.csv", effects)
    print(f"Analyzed {len(raw)} runs in {blocks} randomized blocks: {folder}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    analyze(args.campaign.resolve())


if __name__ == "__main__":
    main()
