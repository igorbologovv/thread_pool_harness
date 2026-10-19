"""Pass-aware paired block-level analysis of benchmark campaigns."""
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

# Only compare counters that are actually recorded in the same perf pass.
PASS_EVENTS = {
    "standard": ("task-clock", "cycles", "instructions", "context-switches",
                 "cpu-migrations", "branch-misses"),
    "cache": ("task-clock", "cache-references", "cache-misses"),
    "l1d": ("task-clock", "L1-dcache-loads", "L1-dcache-load-misses"),
    "dtlb": ("task-clock", "l1_dtlb_misses", "l2_dtlb_misses"),
    "itlb": ("task-clock", "bp_l1_tlb_miss_l2_tlb_hit", "l2_itlb_misses"),
}
DEEP_PASSES = ("cache", "l1d", "dtlb", "itlb")
EVENTS = tuple(dict.fromkeys(event for group in PASS_EVENTS.values() for event in group))
METRIC_COLUMNS = {name: name.replace("-", "_") + "_per_wu" for name in EVENTS}


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
        # Deep passes have different counter columns; use the union across rows.
        fields = list(dict.fromkeys(field for row in rows for field in row))
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def allowed_metrics(profile: str, perf_pass: str, delivery: str) -> tuple[str, ...]:
    basic = ("elapsed_ms", "throughput")
    if delivery != "all-at-once":
        basic += ("drain_delay_ms",)
    events = () if profile == "none" else PASS_EVENTS[perf_pass]
    return basic + tuple(METRIC_COLUMNS[event] for event in events)


def analyze(folder: Path) -> None:
    definition = tomllib.loads((folder / "experiment.toml").read_text())
    profile = definition["experiment"]["profile"]
    delivery = definition["delivery"]["mode"]
    expected_passes = DEEP_PASSES if profile == "deep" else (profile,)
    with (folder / "manifest.csv").open(newline="") as handle:
        manifest = list(csv.DictReader(handle))
    with (folder / "plan.csv").open(newline="") as handle:
        plan = list(csv.DictReader(handle))
    if len(plan) != len(manifest):
        raise RuntimeError("Incomplete manifest")
    ids = {int(row["experiment_id"]): row for row in manifest}
    if len(ids) != len(manifest):
        raise RuntimeError("Duplicate experiment IDs")

    counters = ",\n".join(
        "(SELECT p.counter_value FROM perf_metric p WHERE p.run_id=r.id "
        f"AND p.event_name='{event}') AS counter_{i}"
        for i, event in enumerate(EVENTS)
    )
    query = f"""
    SELECT e.id, e.work_units, r.id, r.run_index, r.perf_pass,
           r.pass_run_index, r.success, r.completed_work_units,
           r.elapsed_ns, r.work_units_per_second,
           (SELECT MAX(a.actual_submit_offset_ns) FROM run_arrival a
            WHERE a.run_id=r.id) AS last_arrival,
           {counters}
    FROM experiment e JOIN run r ON r.experiment_id=e.id
    ORDER BY e.id, r.run_index
    """
    with sqlite3.connect(folder / "benchmarks.sqlite3") as db:
        records = db.execute(query).fetchall()

    grouped: dict[tuple, list[dict]] = defaultdict(list)
    raw = []
    for row in records:
        (eid, work_units, run_id, index, perf_pass, pass_index, success,
         completed, elapsed_ns, throughput, last_arrival, *perf) = row
        if eid not in ids or success != 1 or completed != work_units:
            raise RuntimeError(f"Unexpected/failed run {run_id}")
        if perf_pass not in expected_passes:
            raise RuntimeError(f"Unexpected perf pass {perf_pass!r} for run {run_id}")
        m = ids[eid]
        entry = dict(
            block=int(m["block"]), order_in_block=int(m["order_in_block"]),
            configuration=m["configuration"], spin_us=int(m["spin_us"]),
            arrival_rate=m["arrival_rate"], rho=m["rho"],
            run_id=run_id, run_index=index, perf_pass=perf_pass, pass_run_index=pass_index,
            elapsed_ms=elapsed_ns / 1e6, throughput=throughput,
            drain_delay_ms=max(0, elapsed_ns - last_arrival) / 1e6
            if last_arrival is not None else None,
        )
        entry.update({
            METRIC_COLUMNS[event]: float(value) / work_units if value is not None else None
            for event, value in zip(EVENTS, perf)
        })
        raw.append(entry)
        grouped[(entry["block"], entry["configuration"], entry["spin_us"],
                 entry["arrival_rate"], perf_pass)].append(entry)

    expected_runs = int(definition["repetitions"]["measured_runs_per_block"])
    summaries = []
    for key, items in sorted(grouped.items()):
        if len(items) != expected_runs:
            raise RuntimeError(f"Missing measured runs for {key}")
        pass_indices = sorted(item["pass_run_index"] for item in items)
        if pass_indices != list(range(expected_runs)):
            raise RuntimeError(f"Missing/duplicate physical pass indices for {key}")
        block, configuration, spin_us, arrival_rate, perf_pass = key
        summary = dict(block=block, configuration=configuration, spin_us=spin_us,
                       arrival_rate=arrival_rate, rho=items[0]["rho"],
                       perf_pass=perf_pass, n_runs=len(items))
        for metric in allowed_metrics(profile, perf_pass, delivery):
            values = [item[metric] for item in items]
            summary[metric] = statistics.mean(values) if all(v is not None for v in values) else None
        summaries.append(summary)
    blocks = int(definition["repetitions"]["blocks"])
    if len(summaries) != len(plan) * len(expected_passes):
        raise RuntimeError("Incomplete pass-aware block summaries")
    by_key = {(row["block"], row["configuration"], row["spin_us"],
               row["arrival_rate"], row["perf_pass"]): row for row in summaries}
    reference = definition.get("analysis", {}).get(
        "reference_configuration", definition["configurations"][0]["id"])
    rng = random.Random(int(definition.get("analysis", {}).get(
        "bootstrap_seed", 20261019)))
    bootstrap = int(definition.get("analysis", {}).get("bootstrap_replicates", 10000))
    if bootstrap < 1:
        raise ValueError("bootstrap_replicates must be positive")
    effects = []
    conditions = sorted({
        (m["configuration"], int(m["spin_us"]), m["arrival_rate"], pass_name)
        for m in manifest for pass_name in expected_passes
    })
    for config, spin, rate, pass_name in conditions:
        if config == reference:
            continue
        for metric in allowed_metrics(profile, pass_name, delivery):
            differences, relative = [], []
            for block in range(1, blocks + 1):
                lhs = by_key[(block, config, spin, rate, pass_name)][metric]
                refs = [x for x in summaries if x["block"] == block and
                        x["configuration"] == reference and x["arrival_rate"] == rate and
                        x["perf_pass"] == pass_name]
                if len(refs) != 1:
                    raise RuntimeError("Reference must have exactly one condition per arrival rate/pass")
                rhs = refs[0][metric]
                if lhs is None or rhs is None:
                    raise RuntimeError(f"Unavailable {metric}: {config}, {pass_name}, block {block}")
                differences.append(lhs - rhs)
                if rhs:
                    relative.append((lhs - rhs) / rhs)
            if not all(math.isfinite(v) for v in differences + relative):
                raise RuntimeError(f"Non-finite block difference for {metric}")
            low, high = ci(differences, rng, bootstrap)
            rlow, rhigh = ci(relative, rng, bootstrap) if relative else (math.nan, math.nan)
            effects.append(dict(
                configuration=config, reference=reference, spin_us=spin,
                arrival_rate=rate, perf_pass=pass_name, metric=metric,
                n_blocks=len(differences), absolute_mean=statistics.mean(differences),
                absolute_ci95_low=low, absolute_ci95_high=high,
                relative_mean=statistics.mean(relative) if relative else math.nan,
                relative_ci95_low=rlow, relative_ci95_high=rhigh,
                positive_blocks=sum(x > 0 for x in differences),
                negative_blocks=sum(x < 0 for x in differences),
            ))
    csv_write(folder / "analysis_raw_runs.csv", raw)
    csv_write(folder / "block_summaries.csv", summaries)
    if effects:
        csv_write(folder / "paired_effects.csv", effects)
    print(f"Analyzed {len(raw)} physical runs in {blocks} randomized blocks: {folder}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    analyze(args.campaign.resolve())


if __name__ == "__main__":
    main()
