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


def compare_to_rayon(paced, capacity, repetitions):
    """Paired original-load comparisons, with Rayon batch capacity as common X."""
    import numpy as np
    rng = np.random.default_rng(20261102)
    rows = []
    for (workers, mode, arrival_rate), table in sorted(paced.items()):
        rayon_capacity = statistics.mean(capacity[workers, "rayon"])
        for scheduler in CONFIGS:
            if scheduler == "rayon":
                continue
            for metric in ("throughput", "task_clock_per_wu"):
                paired = []
                for block in range(1, 21):
                    ref = float(table["rayon"][block][metric])
                    actual = float(table[scheduler][block][metric])
                    assert_valid(math.isfinite(actual) and math.isfinite(ref) and ref > 0,
                                 f"Invalid paired comparison: {scheduler}/{metric}")
                    paired.append(100 * (actual / ref - 1))
                bs = bootstrap_mean(paired, rng, repetitions)
                rows.append(dict(
                    workers=workers, mode=mode, scheduler=scheduler,
                    metric=metric,
                    scheduled_arrival_rate=arrival_rate,
                    common_load_percent=100 * arrival_rate / rayon_capacity,
                    change_percent=statistics.mean(paired),
                    ci95_low=float(np.quantile(bs, .025)),
                    ci95_high=float(np.quantile(bs, .975)),
                    independent_paired_blocks=20,
                ))
    return rows


def render(points, comparisons, output):
    """Focused, readable charts, one question per figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    style = {"font.size": 11, "axes.titlesize": 15, "axes.labelsize": 11,
             "legend.fontsize": 10, "figure.titlesize": 15}
    plt.rcParams.update(style)

    def matching(workers, mode, scheduler, metric):
        selected = sorted(
            (row for row in points if row["workers"] == workers
             and row["mode"] == mode and row["scheduler"] == scheduler
             and row["metric"] == metric),
            key=lambda row: row["scheduled_arrival_rate"],
        )
        assert_valid(len(selected) == 6, f"Missing data: {workers}/{mode}/{scheduler}/{metric}")
        return selected

    def finish(fig, filename):
        for extension in ("png", "svg"):
            fig.savefig(output / f"{filename}.{extension}",
                        dpi=170, bbox_inches="tight")
        plt.close(fig)

    def add_legend(fig, ax, ncol=3):
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center",
                   bbox_to_anchor=(.5, .025), ncol=ncol,
                   frameon=False, columnspacing=1.8)

    for workers in WORKERS:
        for mode in MODES:
            mode_title = "Periodic arrivals" if mode == "fixed-arrivals" else "Bursty arrivals"
            base = f"w{workers}-{mode}"

            # Figure A: direct CPU-cost view in the original perf task-clock units.
            fig, ax = plt.subplots(figsize=(11.5, 6.6))
            fig.subplots_adjust(left=.10, right=.97, bottom=.23, top=.83)
            for i, scheduler in enumerate(CONFIGS):
                rows = matching(workers, mode, scheduler, "task_clock_per_wu")
                x = np.array([row["relative_load"] * 100 for row in rows])
                y = np.array([row["value"] for row in rows])
                low = np.array([row["ci95_low"] for row in rows])
                high = np.array([row["ci95_high"] for row in rows])
                ax.plot(x, y, "-o", lw=2, markersize=5.5,
                        label=LABELS[i], color=COLORS[i])
                ax.fill_between(x, low, high, alpha=.13, color=COLORS[i])
            ax.axvline(100, ls="--", color="gray", lw=1)
            ax.set(xlabel="Offered load relative to each scheduler's measured batch capacity (%)",
                   ylabel="CPU time per task (perf task-clock / WU)",
                   title="CPU cost by normalized offered load — lower is better")
            ax.grid(alpha=.23)
            fig.suptitle(f"{workers} workers  |  {mode_title}", y=.98)
            add_legend(fig, ax)
            finish(fig, base + "-cpu")

            # Figure B: a simple throughput-vs-demand curve, including uncertainty
            # in both the measured numerator and the independent capacity baseline.
            fig, ax = plt.subplots(figsize=(11.5, 6.6))
            fig.subplots_adjust(left=.10, right=.97, bottom=.23, top=.83)
            ax.plot([0, 100, 220], [0, 100, 100],
                    "--", lw=1.3, color="gray", label="Idealized limit (guide)")
            for i, scheduler in enumerate(CONFIGS):
                rows = matching(workers, mode, scheduler, "achieved_over_capacity")
                x = np.array([row["relative_load"] * 100 for row in rows])
                y = np.array([row["value"] * 100 for row in rows])
                low = np.array([row["ci95_low"] * 100 for row in rows])
                high = np.array([row["ci95_high"] * 100 for row in rows])
                ax.plot(x, y, "-o", lw=2, markersize=5.5,
                        label=LABELS[i], color=COLORS[i])
                ax.fill_between(x, low, high, alpha=.13, color=COLORS[i])
            ax.axvline(100, ls=":", color="gray", lw=1)
            ax.set(xlabel="Offered load relative to each scheduler's measured batch capacity (%)",
                   ylabel="Achieved throughput / batch capacity (%)",
                   title="How much of the batch capacity is achieved?")
            ax.grid(alpha=.23)
            fig.suptitle(f"{workers} workers  |  {mode_title}", y=.98)
            add_legend(fig, ax, ncol=3)
            finish(fig, base + "-throughput")

            # Figure C: properly paired direct comparisons. Unlike own-capacity X,
            # the common Rayon-normalized X maps to the SAME physical arrival rate.
            fig, (ax_cpu, ax_tp) = plt.subplots(2, 1, figsize=(11.5, 9.0),
                                                 sharex=True)
            fig.subplots_adjust(left=.11, right=.97, bottom=.19, top=.88, hspace=.24)
            for ax, metric in ((ax_cpu, "task_clock_per_wu"),
                               (ax_tp, "throughput")):
                for i, scheduler in enumerate(CONFIGS):
                    if scheduler == "rayon":
                        continue
                    rows = sorted(
                        (r for r in comparisons if r["workers"] == workers
                         and r["mode"] == mode and r["scheduler"] == scheduler
                         and r["metric"] == metric),
                        key=lambda row: row["scheduled_arrival_rate"],
                    )
                    assert_valid(len(rows) == 6, "Incomplete paired comparison")
                    xs = np.array([r["common_load_percent"] for r in rows])
                    ys = np.array([r["change_percent"] for r in rows])
                    lo = np.array([r["ci95_low"] for r in rows])
                    hi = np.array([r["ci95_high"] for r in rows])
                    ax.plot(xs, ys, "-o", lw=2, markersize=5,
                            label=LABELS[i], color=COLORS[i])
                    ax.fill_between(xs, lo, hi, alpha=.13, color=COLORS[i])
                ax.axhline(0, ls="--", lw=1, color="gray")
                ax.grid(alpha=.23)
            ax_cpu.set(title="CPU time relative to Rayon — negative = less CPU time",
                       ylabel="Difference vs Rayon (%)")
            ax_tp.set(title="Achieved throughput relative to Rayon — positive = faster",
                      ylabel="Difference vs Rayon (%)",
                      xlabel="Offered load / Rayon batch capacity (%) — same arrival rate for all")
            fig.suptitle(f"{workers} workers  |  {mode_title}  |  Paired comparison at matched arrival rates",
                         y=.97)
            add_legend(fig, ax_cpu, ncol=4)
            finish(fig, base + "-vs-rayon")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paced-root", type=Path,
                        default=ROOT / "results/studies/bls-normalized-load/campaigns")
    parser.add_argument("--capacity-root", type=Path,
                        default=ROOT / "results/studies/bls-saturation-recheck/campaigns")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results/studies/bls-capacity-normalized/clear-plots")
    parser.add_argument("--bootstrap", type=int, default=4000)
    args = parser.parse_args()
    assert_valid(args.bootstrap >= 1000, "bootstrap must be >=1000")
    capacities, cap_sources = capacity_data(args.capacity_root)
    paced, paced_sources = paced_data(args.paced_root)
    cap_rows, points = build(capacities, paced, args.bootstrap)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "capacity_estimates.csv", cap_rows)
    write_csv(args.output / "normalized_curves.csv", points)
    comparisons = compare_to_rayon(paced, capacities, args.bootstrap)
    write_csv(args.output / "paired_vs_rayon.csv", comparisons)
    render(points, comparisons, args.output)
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
    report += ["", "## Charts", "",
               "For each worker/mode combination: CPU cost, relative throughput, and",
               "paired comparison against Rayon at a **matched absolute arrival rate**.",
               "The self-normalized curves use a different capacity denominator for",
               "each scheduler. The paired chart uses the same Rayon-normalized",
               "horizontal axis for all schedulers to preserve fair comparisons.",
               "The former six-panel overview has been replaced; old images remain",
               "in the previous output directory.", ""]
    for w in WORKERS:
        for mode in MODES:
            report += [f"### {w} workers, {mode}", ""]
            for kind in ("cpu", "throughput", "vs-rayon"):
                name = f"w{w}-{mode}-{kind}.png"
                report += [f"![{name}]({name})", ""]
    (args.output / "report.md").write_text("\n".join(report))
    print(f"Created 24 focused PNG/SVG charts, {len(points)} normalized metric points, "
          f"and {len(comparisons)} paired differences at {args.output}")


if __name__ == "__main__":
    main()
