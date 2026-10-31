"""Generate a bounded BLS load study, normalized to a common reference per worker count.

No measurements are made by this script. Run generated TOMLs with the existing
experiments.common.campaign prepare/collect/analyze workflow.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from experiments.common.campaign import ROOT, make_plan, read_definition
from experiments.common.study import render_toml

# Preliminary all-at-once Rayon throughput from the archived BLS chapter.
# Measured reference capacity, not physical CPU utilization or thread occupancy.
REFERENCE_WU_PER_S = {2: 1294.0, 4: 2583.0, 8: 4792.0, 16: 5224.0}
LOAD_FRACTIONS = (0.25, 0.5, 0.75, 0.9375, 1.10, 2.0)
MODES = ("fixed-arrivals", "bursty-arrivals")
DATASET_WORK_UNITS = 2500
MIN_WINDOW_SECONDS = 0.200
BLOCKS = 20


def plan(reference: dict[int, float] | None = None) -> list[dict]:
    reference = REFERENCE_WU_PER_S if reference is None else reference
    if set(reference) != {2, 4, 8, 16} or any(
        not math.isfinite(float(v)) or v <= 0 for v in reference.values()
    ):
        raise ValueError("reference must contain positive finite throughput for 2/4/8/16 workers")
    rows = []
    for workers, capacity in sorted(reference.items()):
        for fraction in LOAD_FRACTIONS:
            rate = capacity * fraction
            # ceil protects a >= 200-ms scheduled arrival window, even after
            # whole-work-unit rounding. The final task is scheduled at (N-1)/rate.
            units = math.ceil(MIN_WINDOW_SECONDS * rate) + 1
            if units > DATASET_WORK_UNITS:
                raise ValueError(
                    f"2500-cert dataset too small: workers={workers} load={fraction}"
                    f" requires {units} work units"
                )
            for mode in MODES:
                rows.append(dict(workers=workers, load_fraction=fraction,
                                 arrival_rate=rate, work_units=units,
                                 scheduled_window_seconds=(units - 1) / rate,
                                 delivery_mode=mode))
    return rows


def configurations() -> list[dict]:
    # Five informative anchors instead of the entire factorial configuration grid.
    return [
        {"id": "rayon", "scheduler": "rayon"},
        {"id": "mpmc-sp0", "scheduler": "threadance",
         "wait_policy": "spin-then-park", "spin_us": 0, "queue_capacity": 64},
        {"id": "mpmc-ab", "scheduler": "threadance",
         "wait_policy": "agave-backoff", "spin_us": 0, "queue_capacity": 64},
        {"id": "rtrb-sp0-b1", "scheduler": "threadance-hot-first-rtrb",
         "wait_policy": "spin-then-park", "spin_us": 0,
         "hot_first_backlog": 1, "queue_capacity": 64},
        {"id": "rtrb-sp0-b2", "scheduler": "threadance-hot-first-rtrb",
         "wait_policy": "spin-then-park", "spin_us": 0,
         "hot_first_backlog": 2, "queue_capacity": 64},
    ]


def build(write: bool, reference: dict[int, float] | None = None,
          blocks: int = BLOCKS) -> dict:
    if blocks < 1:
        raise ValueError("blocks must be >= 1")
    import copy
    base = read_definition(ROOT / "experiments/queue_topology/experiment.toml")
    root = ROOT / "experiments/generated/bls-normalized-load"
    records = []
    targets = {}
    for row in plan(reference):
        fraction = row["load_fraction"]
        label = str(round(fraction * 10000)).zfill(5)
        filename = f"w{row['workers']}-load{label}-{row['delivery_mode']}.toml"
        data = copy.deepcopy(base)
        data["experiment"]["name"] = "bls-normalized-" + filename.removesuffix(".toml")
        data["experiment"]["profile"] = "standard"
        data["workload"].update(workers=row["workers"], work_units=row["work_units"],
                                dataset_work_units=DATASET_WORK_UNITS,
                                queue_capacity=64, validators=2000,
                                signers_per_certificate=1600,
                                certificates_per_work_unit=1, workload_seed=1)
        data["delivery"] = dict(mode=row["delivery_mode"],
                                arrival_rates=[row["arrival_rate"]],
                                arrival_seed_base=42)
        data["repetitions"] = dict(blocks=blocks, warmup_runs_per_block=1,
                                   measured_runs_per_block=2)
        data["randomization"] = dict(seed=20261019)
        data["execution"] = dict(settle_seconds=0.0)
        data["artifacts"]["output_root"] = "results/studies/bls-normalized-load/campaigns"
        data["artifacts"]["dataset_cache_dir"] = "results/datasets"
        data["analysis"]["reference_configuration"] = "rayon"
        data["configurations"] = configurations()
        text = render_toml(data)
        targets[root / filename] = text
        entry = dict(row, file=filename, conditions_per_block=len(data["configurations"]),
                     invocations=len(make_plan(data, None)),
                     warmup_runs=blocks * len(data["configurations"]),
                     measured_runs=blocks * len(data["configurations"]) * 2)
        records.append(entry)
    summary = dict(
        load_definition="offered_rate / archived Rayon saturated capacity at same worker count",
        reference_wu_per_second=REFERENCE_WU_PER_S if reference is None else reference,
        not_cpu_core_utilization=True,
        dataset_work_units=DATASET_WORK_UNITS,
        min_scheduled_window_seconds=MIN_WINDOW_SECONDS,
        blocks=blocks, campaigns=records,
        total_invocations=sum(x["invocations"] for x in records),
        total_measured_runs=sum(x["measured_runs"] for x in records),
        total_warmup_runs=sum(x["warmup_runs"] for x in records),
        caveat="Producer backpressure may delay actual submission beyond scheduled offsets.",
    )
    if write:
        targets[root / "plan.json"] = json.dumps(summary, indent=2) + "\n"
        # Fail before any output on pre-existing mismatched generated data.
        for path, value in targets.items():
            if path.exists() and path.read_text() != value:
                raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")
        root.mkdir(parents=True, exist_ok=True)
        for path, value in targets.items():
            path.write_text(value)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", action="store_true")
    parser.add_argument("--reference-json", type=Path,
                        help="JSON object mapping worker counts to measured WU/s")
    parser.add_argument("--blocks", type=int, default=BLOCKS)
    args = parser.parse_args()
    reference = None
    if args.reference_json:
        reference = {int(k): float(v) for k, v in
                     json.loads(args.reference_json.read_text()).items()}
    print(json.dumps(build(args.write, reference, args.blocks), indent=2))


if __name__ == "__main__":
    main()
