"""Replot archived paced BLS measurements using scheduler-specific batch capacities.

Inputs are existing, separately collected 48 paced and 4 saturation campaigns.
Never modifies archived measurements or runs benchmarks.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import tomllib
from collections import defaultdict
from pathlib import Path

from experiments.common.campaign import ROOT

CONFIGS = {
    "rayon": "rayon",
    "mpmc-sp0": "threadance__spin-then-park__q64__s0",
    "mpmc-ab": "threadance__agave-backoff__q64",
    "rtrb-sp0-b1": "threadance-hot-first-rtrb__spin-then-park__q64__b1__s0",
    "rtrb-sp0-b2": "threadance-hot-first-rtrb__spin-then-park__q64__b2__s0",
}
LABELS = ["Rayon", "MPMC Spin(0)", "MPMC Agave", "RTRB b1", "RTRB b2"]
COLORS = ["#1d4ed8", "#e4572e", "#16a34a", "#9333ea", "#c58a08"]
WORKERS = (2, 4, 8, 16)
MODES = ("fixed-arrivals", "bursty-arrivals")
METRICS = ("throughput", "task_clock_per_wu", "cycles_per_wu",
           "drain_delay_ms", "branch_misses_per_wu")


def assert_valid(ok, message):
    if not ok:
        raise ValueError(message)


def read_csv(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}; analyze the archived campaign first")
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def read_campaign(folder, mode, blocks):
    exp = tomllib.loads((folder / "experiment.toml").read_text())
    w = exp["workload"]
    expected = (2500, 2000, 1600, 1, 1)
    actual = tuple(w[k] for k in ("dataset_work_units", "validators",
                                  "signers_per_certificate",
                                  "certificates_per_work_unit", "workload_seed"))
    assert_valid(actual == expected and exp["delivery"]["mode"] == mode
                 and exp["experiment"]["profile"] == "standard"
                 and exp["repetitions"]["blocks"] == blocks,
                 f"Incompatible campaign: {folder}")
    rows = defaultdict(dict)
    for entry in read_csv(folder / "block_summaries.csv"):
        config, block = entry["configuration"], int(entry["block"])
        assert_valid(entry["perf_pass"] == "standard"
                     and 1 <= block <= blocks and block not in rows[config],
                     f"Invalid block summary: {folder}/{config}/{block}")
        rows[config][block] = entry
    assert_valid(all(set(records) == set(range(1, blocks + 1))
                     for records in rows.values()), f"Incomplete blocks in {folder}")
    return exp, rows


def capacity_data(root):
    folders = sorted(p.parent for p in root.glob("bls-saturation-recheck-w*/experiment.toml"))
    assert_valid(len(folders) == 4, f"Expected 4 saturation campaigns, got {len(folders)}")
    data = {}
    paths = []
    for folder in folders:
        exp, rows = read_campaign(folder, "all-at-once", 30)
        w = int(exp["workload"]["workers"])
        assert_valid(w in WORKERS and w not in {k[0] for k in data}
                     and exp["workload"]["work_units"] == 2500
                     and exp["repetitions"]["warmup_runs_per_block"] == 4
                     and exp["repetitions"]["measured_runs_per_block"] == 3,
                     f"Unexpected saturation protocol at {folder}")
        assert_valid(set(CONFIGS.values()).issubset(rows),
                     f"Missing saturation configurations: {folder}")
        for short, full in CONFIGS.items():
            data[w, short] = [float(rows[full][b]["throughput"])
                              for b in range(1, 31)]
        paths.append(str(folder))
    assert_valid(set(data) == {(w, s) for w in WORKERS for s in CONFIGS},
                 "Incomplete capacity matrix")
    return data, paths


def paced_data(root):
    folders = sorted(p.parent for p in root.glob("bls-normalized-*/experiment.toml"))
    assert_valid(len(folders) == 48, f"Expected 48 paced campaigns, got {len(folders)}")
    data = {}
    paths = []
    for folder in folders:
        raw = tomllib.loads((folder / "experiment.toml").read_text())
        mode = raw["delivery"]["mode"]
        assert_valid(mode in MODES, f"Unexpected paced mode at {folder}")
        exp, rows = read_campaign(folder, mode, 20)
        w = int(exp["workload"]["workers"])
        rates = exp["delivery"]["arrival_rates"]
        assert_valid(w in WORKERS and len(rates) == 1
                     and set(rows) == set(CONFIGS),
                     f"Unexpected paced design at {folder}")
        rate = float(rates[0])
        key = w, mode, rate
        assert_valid(key not in data and math.isfinite(rate) and rate > 0,
                     f"Duplicate or invalid rate: {key}")
        data[key] = rows
        paths.append(str(folder))
    for w in WORKERS:
        for mode in MODES:
            assert_valid(sum(key[:2] == (w, mode) for key in data) == 6,
                         f"Missing six load levels for {w}/{mode}")
    return data, paths


def bootstrap_mean(values, rng, replicates):
    import numpy as np
    samples = np.asarray(values, dtype=float)
    assert_valid(np.all(np.isfinite(samples)), "Invalid/non-finite metric")
    ids = rng.integers(0, len(samples), size=(replicates, len(samples)))
    return samples[ids].mean(axis=1)


def build(capacity, paced, replicates):
    import numpy as np
    rng = np.random.default_rng(20261101)
    cap_draws = {key: bootstrap_mean(value, rng, replicates)
                 for key, value in sorted(capacity.items())}
    cap_rows, points = [], []
    for (w, scheduler), blocks in sorted(capacity.items()):
        sims = cap_draws[w, scheduler]
        cap_rows.append(dict(
            workers=w, scheduler=scheduler,
            finite_batch_capacity_wu_s=statistics.mean(blocks),
            ci95_low=float(np.quantile(sims, .025)),
            ci95_high=float(np.quantile(sims, .975)),
            independent_blocks=30,
        ))
    for (w, mode, arrival_rate), table in sorted(paced.items()):
        for scheduler in CONFIGS:
            denominator = statistics.mean(capacity[w, scheduler])
            denominator_draws = cap_draws[w, scheduler]
            x = arrival_rate / denominator
            xdraw = arrival_rate / denominator_draws
            for metric in METRICS:
                values = [float(table[scheduler][b][metric]) for b in range(1, 21)]
                mean = statistics.mean(values)
                simulations = bootstrap_mean(values, rng, replicates)
                ylabel = "achieved_over_capacity" if metric == "throughput" else metric
                ydraw = simulations / denominator_draws if metric == "throughput" else simulations
                points.append(dict(
                    workers=w, mode=mode, scheduler=scheduler,
                    scheduled_arrival_rate=arrival_rate,
                    batch_capacity=denominator, relative_load=x,
                    x_ci95_low=float(np.quantile(xdraw, .025)),
                    x_ci95_high=float(np.quantile(xdraw, .975)),
                    metric=ylabel,
                    value=mean / denominator if metric == "throughput" else mean,
                    ci95_low=float(np.quantile(ydraw, .025)),
                    ci95_high=float(np.quantile(ydraw, .975)),
                    absolute_value=mean,
                    absolute_ci95_low=float(np.quantile(simulations, .025)),
                    absolute_ci95_high=float(np.quantile(simulations, .975)),
                    independent_blocks=20,
                ))
    return cap_rows, points


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def render(points, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    charts = (
        ("achieved_over_capacity", "Achieved throughput / batch capacity"),
        ("task_clock_per_wu", "Task-clock per WU (perf units)"),
        ("cycles_per_wu", "CPU cycles per WU"),
        ("drain_delay_ms", "Elapsed since last submission started (ms)"),
        ("branch_misses_per_wu", "Branch misses per WU"),
        ("absolute", "Achieved throughput (WU/s), absolute scheduled rate"),
    )
    for w in WORKERS:
        for mode in MODES:
            fig, axes = plt.subplots(2, 3, figsize=(19, 11), constrained_layout=True)
            for ax, (metric, title) in zip(axes.flat, charts):
                for i, scheduler in enumerate(CONFIGS):
                    selected_metric = "achieved_over_capacity" if metric == "absolute" else metric
                    selected = sorted(
                        (row for row in points
                         if row["workers"] == w and row["mode"] == mode
                         and row["scheduler"] == scheduler
                         and row["metric"] == selected_metric),
                        key=lambda row: row["scheduled_arrival_rate"],
                    )
                    assert_valid(len(selected) == 6, "Incomplete data for graph")
                    if metric == "absolute":
                        x = np.asarray([row["scheduled_arrival_rate"] for row in selected])
                        y = np.asarray([row["absolute_value"] for row in selected])
                        lo = np.asarray([row["absolute_ci95_low"] for row in selected])
                        hi = np.asarray([row["absolute_ci95_high"] for row in selected])
                    else:
                        x = np.asarray([row["relative_load"] for row in selected])
                        y = np.asarray([row["value"] for row in selected])
                        lo = np.asarray([row["ci95_low"] for row in selected])
                        hi = np.asarray([row["ci95_high"] for row in selected])
                    ax.plot(x, y, "-o", color=COLORS[i], markersize=3.4,
                            linewidth=1.4, label=LABELS[i])
                    ax.fill_between(x, lo, hi, color=COLORS[i], alpha=0.12)
                ax.set_title(title, fontsize=11)
                ax.set_xlabel("Scheduled arrivals (WU/s)" if metric == "absolute"
                              else "Scheduled arrival rate / scheduler batch capacity")
                ax.grid(alpha=.23)
                if metric != "absolute":
                    ax.axvline(1, color="gray", linewidth=1, linestyle="--")
                if metric == "achieved_over_capacity":
                    ax.axhline(1, color="gray", linewidth=1, linestyle=":")
                ax.ticklabel_format(axis="y", style="plain", useOffset=False)
            handles, labels = axes.flat[0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="outside upper center", ncol=5)
            fig.suptitle(f"BLS normalized load: {w} workers, {mode}\n"
                         "Archived paced results / new scheduler-specific finite-batch capacity",
                         fontsize=14, y=1.04)
            basename = f"w{w}-{mode}"
            for suffix in ("png", "svg"):
                fig.savefig(output / f"{basename}.{suffix}",
                            dpi=180, bbox_inches="tight")
            plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paced-root", type=Path,
                        default=ROOT / "results/studies/bls-normalized-load/campaigns")
    parser.add_argument("--capacity-root", type=Path,
                        default=ROOT / "results/studies/bls-saturation-recheck/campaigns")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results/studies/bls-capacity-normalized")
    parser.add_argument("--bootstrap", type=int, default=4000)
    args = parser.parse_args()
    assert_valid(args.bootstrap >= 1000, "bootstrap must be >=1000")
    capacities, cap_sources = capacity_data(args.capacity_root)
    paced, paced_sources = paced_data(args.paced_root)
    cap_rows, points = build(capacities, paced, args.bootstrap)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "capacity_estimates.csv", cap_rows)
    write_csv(args.output / "normalized_curves.csv", points)
    render(points, args.output)
    caveats = [
        "These graphs reuse historical paced data; no new paced measurements were performed.",
        "Paced load levels were originally based on preliminary Rayon-only batch throughput.",
        "Normalization uses each scheduler's new 2500-WU finite-batch completion throughput.",
        "Old paced runs used one warmup; independent new saturation runs used four warmups.",
        "This does not prove indefinitely sustainable service rate or actual CPU utilization.",
        "At overload, bounded queues may delay actual submission behind its schedule.",
        "Tasks per paced run vary by scheduled rate; arrival lateness requires separate review.",
        "Drain interval starts at the final task submission call, not confirmed final enqueue.",
        "Comparison at equal normalized x is not comparison at equal absolute arrival rate.",
        "Bootstrap intervals resample randomized blocks in each independent acquisition.",
        "Old context-switch counters are deliberately omitted (historical perf scope may be invalid).",
    ]
    (args.output / "provenance.json").write_text(json.dumps(dict(
        capacity_folders=cap_sources, paced_folders=paced_sources,
        capacity_units="finite-batch work units per second",
        x_axis="scheduled arrival rate / own finite-batch capacity",
        throughput_y_axis="N/elapsed / own finite-batch capacity",
        bootstrap_samples=args.bootstrap, caveats=caveats,
    ), indent=2) + "\n")
    report = ["# Scheduler-relative BLS offered-load curves", "",
              "Eight figures use 48 archived paced campaigns and four new batch-capacity campaigns.",
              "The horizontal axis is offered rate divided by scheduler-specific batch throughput.",
              "The main vertical axis is achieved batch throughput divided by the same denominator.",
              "Shading represents block-bootstrap 95% CIs, including denominator uncertainty.",
              "The final panel uses absolute arrivals (WU/s) for like-for-like comparisons.", ""]
    report += ["## Interpretation constraints", ""] + ["- " + c for c in caveats]
    report += ["", "## Charts", ""]
    for w in WORKERS:
        for mode in MODES:
            name = f"w{w}-{mode}.png"
            report += [f"### {w} workers, {mode}", "", f"![{name}]({name})", ""]
    (args.output / "report.md").write_text("\n".join(report))
    print(f"Created 8 PNG/SVG figures and {len(points)} normalized metric points at {args.output}")


if __name__ == "__main__":
    main()
