"""Postprocess Rayon run-order diagnostic; preserves individual run positions."""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path


METRICS = ("throughput", "elapsed_ms", "task_clock_per_wu",
           "cycles_per_wu", "instructions_per_wu", "branch_misses_per_wu")


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("No rows to write")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def bootstrap_mean(values: list[float], *, seed: int, replicates: int = 10000) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    estimates = sorted(statistics.mean(rng.choices(values, k=n)) for _ in range(replicates))
    return estimates[int(replicates * .025)], estimates[int(replicates * .975)]


def analyze(root: Path, *, replicates: int = 10000) -> tuple[list[dict], list[dict]]:
    if replicates < 100:
        raise ValueError("At least 100 bootstrap replicates required")
    files = sorted(root.glob("bls-run-order-diagnostic-w*/analysis_raw_runs.csv"))
    if len(files) != 3:
        raise ValueError(f"Expected 3 worker campaigns; found {len(files)}")
    summary, deltas = [], []
    for file in files:
        workers = int(file.parent.name.split("-w", 1)[1].split("-", 1)[0])
        indexed: dict[tuple[int, int], dict] = {}
        for row in read_csv(file):
            if row["configuration"] != "rayon" or row["perf_pass"] != "standard":
                raise ValueError("Expected only Rayon standard profiling")
            block, index = int(row["block"]), int(row["run_index"])
            key = (block, index)
            if key in indexed:
                raise ValueError(f"Duplicate run {workers}: {key}")
            indexed[key] = row
        if set(indexed) != {(b, i) for b in range(1, 21) for i in range(8)}:
            raise ValueError(f"Missing/incomplete runs for {workers} workers")
        for metric_number, metric in enumerate(METRICS):
            if any(row.get(metric, "") in ("", None) for row in indexed.values()):
                raise ValueError(f"Missing {metric} for workers={workers}")
            for index in range(8):
                values = [float(indexed[(block, index)][metric]) for block in range(1, 21)]
                if not all(math.isfinite(v) for v in values):
                    raise ValueError("Nonfinite metric")
                lo, hi = bootstrap_mean(values, seed=20261031 + workers * 1000 + metric_number * 10 + index, replicates=replicates)
                summary.append(dict(workers=workers, position=index+1, metric=metric, n=20,
                                    mean=statistics.mean(values), median=statistics.median(values),
                                    sample_variance=statistics.variance(values), ci95_low=lo, ci95_high=hi))
                if index:
                    ratios = []
                    for block in range(1, 21):
                        base = float(indexed[(block, 0)][metric])
                        current = float(indexed[(block, index)][metric])
                        if base == 0:
                            raise ValueError(f"Zero baseline: {metric}")
                        ratios.append(100.0 * (current/base - 1.0))
                    lo, hi = bootstrap_mean(ratios, seed=20261031 + workers * 1000 + metric_number * 10 + index + 100000, replicates=replicates)
                    deltas.append(dict(workers=workers, position=index+1, reference_position=1,
                                       metric=metric, n_pairs=20, relative_mean_pct=statistics.mean(ratios),
                                       relative_median_pct=statistics.median(ratios),
                                       ci95_low_pct=lo, ci95_high_pct=hi,
                                       positive_pairs=sum(r > 0 for r in ratios),
                                       negative_pairs=sum(r < 0 for r in ratios)))
    write_csv(root / "run_position_summary.csv", summary)
    write_csv(root / "run_position_paired_deltas.csv", deltas)
    render_svg(root, summary)
    render_markdown(root, summary, deltas)
    return summary, deltas


def render_svg(root: Path, rows: list[dict]) -> None:
    selected = {(r["workers"], r["position"]): r for r in rows if r["metric"] == "throughput"}
    low = min(float(r["ci95_low"]) for r in selected.values())
    high = max(float(r["ci95_high"]) for r in selected.values())
    span = high-low or 1.0
    def x(i: int) -> float:
        return 80 + (i-1)*94
    def y(v: float) -> float:
        return 410 - 330*(v-low)/span
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 850 475">',
             '<rect width="850" height="475" fill="white"/>',
             '<text x="80" y="28" font-size="19">Rayon throughput vs measured run position</text>',
             '<line x1="80" y1="80" x2="80" y2="410" stroke="#555"/>',
             '<line x1="80" y1="410" x2="738" y2="410" stroke="#555"/>']
    palette = {4:"#2171b5", 8:"#2ca25f", 16:"#de2d26"}
    for w in (4,8,16):
        coords = [(x(i), y(float(selected[(w,i)]["mean"]))) for i in range(1,9)]
        lines.append('<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>' % (
            palette[w], " ".join(f"{a:.2f},{b:.2f}" for a,b in coords)))
        for i in range(1,9):
            r = selected[(w,i)]
            a = x(i)
            yl = y(float(r["ci95_low"]))
            yh = y(float(r["ci95_high"]))
            ym = y(float(r["mean"]))
            lines.extend([
                f'<line x1="{a}" y1="{yl:.2f}" x2="{a}" y2="{yh:.2f}" stroke="{palette[w]}"/>',
                f'<circle cx="{a}" cy="{ym:.2f}" r="3.5" fill="{palette[w]}"/>'])
        lines.append(f'<text x="{560 + (w//4)*35}" y="{36 + w}" font-size="13" fill="{palette[w]}">{w} workers</text>')
    for i in range(1,9):
        lines.append(f'<text x="{x(i)-4}" y="430" font-size="12">{i}</text>')
    for j in range(5):
        val = low + j*span/4
        lines.append(f'<text x="4" y="{y(val)+4:.1f}" font-size="12">{val:.0f}</text>')
    lines.append('<text x="280" y="459" font-size="13">Measured position (after 1 unprofiled warmup)</text>')
    lines.append('</svg>')
    (root / "run_position_throughput.svg").write_text("\n".join(lines)+"\n")


def render_markdown(root: Path, rows: list[dict], deltas: list[dict]) -> None:
    lines = ["# BLS run-order diagnostic (Rayon)", "",
             "Each cell represents 20 independent process invocations at one measured",
             "position after one unprofiled warmup; every measured position uses standard perf.",
             "Confidence intervals are block-resampled descriptive intervals, not proof of",
             "independent identically distributed or stationary observations.", "",
             "## Throughput by run position (WU/s)", "",
             "| Workers | " + " | ".join(f"Run {i}" for i in range(1,9)) + " |",
             "|---|"+"---:|"*8]
    for w in (4,8,16):
        mapping = {int(r["position"]): float(r["mean"]) for r in rows if r["workers"]==w and r["metric"]=="throughput"}
        lines.append(f"| {w} | "+" | ".join(f"{mapping[i]:.2f}" for i in range(1,9))+" |")
    lines += ["", "## Changes from first measured position (mean paired %, 95% CI)", ""]
    for metric in ("throughput", "cycles_per_wu", "task_clock_per_wu", "instructions_per_wu", "branch_misses_per_wu"):
        lines += [f"### {metric}", "", "| Workers | Run | Delta % | 95% CI | Pos / Neg blocks |",
                  "|---|---:|---:|---:|---:|"]
        for r in deltas:
            if r["metric"]==metric:
                lines.append(f'| {r["workers"]} | {r["position"]} | {r["relative_mean_pct"]:+.3f} | [{r["ci95_low_pct"]:+.3f}, {r["ci95_high_pct"]:+.3f}] | {r["positive_pairs"]} / {r["negative_pairs"]} |')
        lines.append("")
    lines += ["## Interpretation constraints", "",
              "The report intentionally does not declare a stable plateau, infer a cache or",
              "frequency cause, or use user-only context-switch/migration zero counters.",
              "Review per-position distributions and acquisition-order drift before",
              "inferring stationary performance. The measured quantity remains finite-batch",
              "completion throughput, not validated sustained capacity.", ""]
    (root/"run_position_report.md").write_text("\n".join(lines))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args=parser.parse_args()
    summary,deltas=analyze(args.root.resolve())
    print(f"Analyzed {len(summary)} position summaries and {len(deltas)} paired position comparisons: {args.root}")


if __name__=="__main__":
    main()
