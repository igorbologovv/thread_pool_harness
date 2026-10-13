#!/usr/bin/env python3
"""Block-aware analysis for the fixed-arrival wait-policy campaign.

The benchmark campaign uses randomized complete blocks. Repeated runs inside one
block/configuration are first summarized locally; configuration comparisons are
then paired within block. Bootstrap uncertainty resamples whole blocks rather
than individual run-level observations.

Only the Python standard library is required so the retained campaign database
and manifest are sufficient to reproduce the main numerical tables.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20261013
REFERENCE_TAU_US = 0

PRIMARY_METRIC = "task_clock_per_work_unit"
PAIRWISE_METRICS = (
    "task_clock_per_work_unit",
    "cycles_per_work_unit",
    "drain_delay_ms",
    "elapsed_ms",
)


@dataclass(frozen=True)
class RunObservation:
    experiment_id: int
    run_id: int
    run_index: int
    block: int
    order_in_block: int
    global_order: int
    rho: float
    tau_us: int
    elapsed_ms: float
    throughput: float
    drain_delay_ms: float | None
    task_clock_per_work_unit: float | None
    cycles_per_work_unit: float | None
    instructions_per_work_unit: float | None
    context_switches_per_work_unit: float | None
    cpu_migrations_per_work_unit: float | None


@dataclass(frozen=True)
class BlockSummary:
    block: int
    rho: float
    tau_us: int
    n: int
    elapsed_ms: float
    throughput: float
    drain_delay_ms: float | None
    task_clock_per_work_unit: float | None
    cycles_per_work_unit: float | None
    instructions_per_work_unit: float | None
    context_switches_per_work_unit: float | None
    cpu_migrations_per_work_unit: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a fixed-arrival wait-policy campaign by randomized block"
    )
    parser.add_argument(
        "campaign",
        type=Path,
        help="campaign directory containing benchmarks.sqlite3 and manifest.csv",
    )
    parser.add_argument(
        "--bootstrap-replicates",
        type=int,
        default=BOOTSTRAP_REPLICATES,
    )
    return parser.parse_args()


def mean_optional(values: Iterable[float | None]) -> float | None:
    xs = [value for value in values if value is not None and math.isfinite(value)]
    return statistics.mean(xs) if xs else None


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return math.nan
    values = sorted(values)
    if len(values) == 1:
        return values[0]

    position = probability * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]

    weight = position - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def bootstrap_mean_ci(
    values: list[float], replicates: int, seed: int
) -> tuple[float, float]:
    if not values:
        return math.nan, math.nan
    if len(values) == 1:
        return values[0], values[0]

    rng = random.Random(seed)
    n = len(values)
    estimates = []
    for _ in range(replicates):
        estimates.append(statistics.mean(values[rng.randrange(n)] for _ in range(n)))

    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def load_manifest(path: Path) -> dict[int, dict[str, str]]:
    rows: dict[int, dict[str, str]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            experiment_id = int(row["experiment_id"])
            if experiment_id in rows:
                raise ValueError(f"duplicate experiment_id in manifest: {experiment_id}")
            rows[experiment_id] = row
    if not rows:
        raise ValueError("manifest contains no experiment rows")
    return rows


def load_runs(
    db_path: Path, manifest: dict[int, dict[str, str]]
) -> list[RunObservation]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row

    query = """
    SELECT
        e.id AS experiment_id,
        e.work_units,
        r.id AS run_id,
        r.run_index,
        r.elapsed_ns,
        r.work_units_per_second,
        MAX(ra.actual_submit_offset_ns) AS last_submit_offset_ns,
        MAX(CASE WHEN pm.event_name = 'task-clock' THEN pm.counter_value END)
            AS task_clock,
        MAX(CASE WHEN pm.event_name = 'cycles' THEN pm.counter_value END)
            AS cycles,
        MAX(CASE WHEN pm.event_name = 'instructions' THEN pm.counter_value END)
            AS instructions,
        MAX(CASE WHEN pm.event_name = 'context-switches' THEN pm.counter_value END)
            AS context_switches,
        MAX(CASE WHEN pm.event_name = 'cpu-migrations' THEN pm.counter_value END)
            AS cpu_migrations
    FROM experiment e
    JOIN run r
        ON r.experiment_id = e.id
    LEFT JOIN run_arrival ra
        ON ra.run_id = r.id
    LEFT JOIN perf_metric pm
        ON pm.run_id = r.id
    WHERE r.success = 1
    GROUP BY e.id, e.work_units, r.id, r.run_index, r.elapsed_ns,
             r.work_units_per_second
    ORDER BY e.id, r.run_index
    """

    observations: list[RunObservation] = []

    try:
        for row in connection.execute(query):
            experiment_id = int(row["experiment_id"])
            if experiment_id not in manifest:
                continue

            meta = manifest[experiment_id]
            work_units = int(row["work_units"])
            elapsed_ns = int(row["elapsed_ns"])
            last_submit_offset_ns = row["last_submit_offset_ns"]

            drain_delay_ms = None
            if last_submit_offset_ns is not None:
                drain_ns = max(0, elapsed_ns - int(last_submit_offset_ns))
                drain_delay_ms = drain_ns / 1_000_000.0

            def per_work_unit(column: str) -> float | None:
                value = row[column]
                if value is None:
                    return None
                return float(value) / work_units

            observations.append(
                RunObservation(
                    experiment_id=experiment_id,
                    run_id=int(row["run_id"]),
                    run_index=int(row["run_index"]),
                    block=int(meta["block"]),
                    order_in_block=int(meta["order_in_block"]),
                    global_order=int(meta["global_order"]),
                    rho=float(meta["rho"]),
                    tau_us=int(meta["tau_us"]),
                    elapsed_ms=elapsed_ns / 1_000_000.0,
                    throughput=float(row["work_units_per_second"]),
                    drain_delay_ms=drain_delay_ms,
                    task_clock_per_work_unit=per_work_unit("task_clock"),
                    cycles_per_work_unit=per_work_unit("cycles"),
                    instructions_per_work_unit=per_work_unit("instructions"),
                    context_switches_per_work_unit=per_work_unit("context_switches"),
                    cpu_migrations_per_work_unit=per_work_unit("cpu_migrations"),
                )
            )
    finally:
        connection.close()

    if not observations:
        raise ValueError("campaign database contains no matching successful runs")
    return observations


def build_block_summaries(runs: list[RunObservation]) -> list[BlockSummary]:
    groups: dict[tuple[int, float, int], list[RunObservation]] = defaultdict(list)
    for run in runs:
        groups[(run.block, run.rho, run.tau_us)].append(run)

    summaries: list[BlockSummary] = []
    for (block, rho, tau_us), rows in sorted(groups.items()):
        summaries.append(
            BlockSummary(
                block=block,
                rho=rho,
                tau_us=tau_us,
                n=len(rows),
                elapsed_ms=statistics.mean(row.elapsed_ms for row in rows),
                throughput=statistics.mean(row.throughput for row in rows),
                drain_delay_ms=mean_optional(row.drain_delay_ms for row in rows),
                task_clock_per_work_unit=mean_optional(
                    row.task_clock_per_work_unit for row in rows
                ),
                cycles_per_work_unit=mean_optional(
                    row.cycles_per_work_unit for row in rows
                ),
                instructions_per_work_unit=mean_optional(
                    row.instructions_per_work_unit for row in rows
                ),
                context_switches_per_work_unit=mean_optional(
                    row.context_switches_per_work_unit for row in rows
                ),
                cpu_migrations_per_work_unit=mean_optional(
                    row.cpu_migrations_per_work_unit for row in rows
                ),
            )
        )
    return summaries


def write_raw_runs(path: Path, runs: list[RunObservation]) -> None:
    fields = list(RunObservation.__dataclass_fields__)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            writer.writerow(run.__dict__)


def write_block_summaries(path: Path, summaries: list[BlockSummary]) -> None:
    fields = list(BlockSummary.__dataclass_fields__)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            writer.writerow(summary.__dict__)


def metric_value(summary: BlockSummary, metric: str) -> float | None:
    return getattr(summary, metric)


def write_paired_effects(
    path: Path,
    summaries: list[BlockSummary],
    bootstrap_replicates: int,
) -> None:
    by_key = {
        (summary.block, summary.rho, summary.tau_us): summary
        for summary in summaries
    }

    rhos = sorted({summary.rho for summary in summaries})
    taus = sorted({summary.tau_us for summary in summaries})
    blocks = sorted({summary.block for summary in summaries})

    fields = [
        "rho",
        "tau_us",
        "reference_tau_us",
        "metric",
        "n_blocks",
        "absolute_effect_mean",
        "absolute_effect_median",
        "absolute_effect_ci95_low",
        "absolute_effect_ci95_high",
        "relative_effect_mean",
        "relative_effect_median",
        "relative_effect_ci95_low",
        "relative_effect_ci95_high",
    ]

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()

        for rho in rhos:
            for tau_us in taus:
                if tau_us == REFERENCE_TAU_US:
                    continue

                for metric_index, metric in enumerate(PAIRWISE_METRICS):
                    absolute_effects: list[float] = []
                    relative_effects: list[float] = []

                    for block in blocks:
                        reference = by_key.get((block, rho, REFERENCE_TAU_US))
                        treatment = by_key.get((block, rho, tau_us))
                        if reference is None or treatment is None:
                            continue

                        reference_value = metric_value(reference, metric)
                        treatment_value = metric_value(treatment, metric)
                        if reference_value is None or treatment_value is None:
                            continue
                        if not math.isfinite(reference_value) or not math.isfinite(
                            treatment_value
                        ):
                            continue

                        absolute_effects.append(treatment_value - reference_value)
                        if reference_value != 0:
                            relative_effects.append(
                                (treatment_value - reference_value) / reference_value
                            )

                    seed_base = (
                        BOOTSTRAP_SEED
                        + int(round(rho * 10_000)) * 101
                        + tau_us * 1009
                        + metric_index * 10_007
                    )
                    absolute_low, absolute_high = bootstrap_mean_ci(
                        absolute_effects, bootstrap_replicates, seed_base
                    )
                    relative_low, relative_high = bootstrap_mean_ci(
                        relative_effects, bootstrap_replicates, seed_base + 1
                    )

                    writer.writerow(
                        {
                            "rho": rho,
                            "tau_us": tau_us,
                            "reference_tau_us": REFERENCE_TAU_US,
                            "metric": metric,
                            "n_blocks": len(absolute_effects),
                            "absolute_effect_mean": statistics.mean(absolute_effects)
                            if absolute_effects
                            else math.nan,
                            "absolute_effect_median": statistics.median(absolute_effects)
                            if absolute_effects
                            else math.nan,
                            "absolute_effect_ci95_low": absolute_low,
                            "absolute_effect_ci95_high": absolute_high,
                            "relative_effect_mean": statistics.mean(relative_effects)
                            if relative_effects
                            else math.nan,
                            "relative_effect_median": statistics.median(relative_effects)
                            if relative_effects
                            else math.nan,
                            "relative_effect_ci95_low": relative_low,
                            "relative_effect_ci95_high": relative_high,
                        }
                    )


def validate_design(summaries: list[BlockSummary]) -> None:
    blocks = sorted({summary.block for summary in summaries})
    configurations = sorted({(summary.rho, summary.tau_us) for summary in summaries})

    missing = []
    keys = {(summary.block, summary.rho, summary.tau_us) for summary in summaries}
    for block in blocks:
        for rho, tau_us in configurations:
            if (block, rho, tau_us) not in keys:
                missing.append((block, rho, tau_us))

    if missing:
        preview = ", ".join(
            f"block={block},rho={rho},tau={tau}"
            for block, rho, tau in missing[:10]
        )
        raise ValueError(
            "randomized-block campaign is incomplete; missing configuration "
            f"cells: {preview}"
        )

    if not any(summary.tau_us == REFERENCE_TAU_US for summary in summaries):
        raise ValueError("zero-spin reference configuration is missing")


def main() -> int:
    args = parse_args()
    if args.bootstrap_replicates <= 0:
        raise SystemExit("--bootstrap-replicates must be greater than zero")

    campaign = args.campaign.resolve()
    db_path = campaign / "benchmarks.sqlite3"
    manifest_path = campaign / "manifest.csv"

    if not db_path.is_file():
        raise SystemExit(f"missing campaign database: {db_path}")
    if not manifest_path.is_file():
        raise SystemExit(f"missing campaign manifest: {manifest_path}")

    manifest = load_manifest(manifest_path)
    runs = load_runs(db_path, manifest)
    summaries = build_block_summaries(runs)
    validate_design(summaries)

    raw_path = campaign / "analysis_raw_runs.csv"
    block_path = campaign / "block_summaries.csv"
    effects_path = campaign / "paired_effects.csv"

    write_raw_runs(raw_path, runs)
    write_block_summaries(block_path, summaries)
    write_paired_effects(effects_path, summaries, args.bootstrap_replicates)

    print("Block-aware fixed-wait analysis complete")
    print(f"  runs             = {len(runs)}")
    print(f"  block summaries  = {len(summaries)}")
    print(f"  raw runs         = {raw_path}")
    print(f"  block summaries  = {block_path}")
    print(f"  paired effects   = {effects_path}")
    print()
    print(
        "Primary comparison: paired block-level relative effect in "
        f"{PRIMARY_METRIC} against tau={REFERENCE_TAU_US} us."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
