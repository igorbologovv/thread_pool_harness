"""Analyze randomized warmup counts without pooling measured-run positions."""
from __future__ import annotations
import argparse
import csv
import random
import statistics
from pathlib import Path

METRICS = ("throughput", "elapsed_ms", "cycles_per_wu",
           "instructions_per_wu", "task_clock_per_wu", "branch_misses_per_wu")
WARMUPS = (1, 2, 4, 8)
WORKERS = (4, 8, 16)


def read_rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def save_rows(path, records):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def ci(values, seed, replicates=10000):
    generator = random.Random(seed)
    samples = sorted(statistics.mean(generator.choices(values, k=len(values)))
                     for _ in range(replicates))
    return samples[int(replicates * .025)], samples[int(replicates * .975)]


def analyze(root: Path, replicates=10000):
    if replicates < 100:
        raise ValueError("bootstrap replicates must be >= 100")
    paths = sorted(root.glob("bls-warmup-sweep-w*/analysis_raw_runs.csv"))
    if len(paths) != 3:
        raise ValueError(f"Expected 3 complete campaigns, got {len(paths)}")
    summary, paired, comparisons = [], [], []
    for path in paths:
        workers = int(path.parent.name.split("-w", 1)[1].split("-", 1)[0])
        if workers not in WORKERS:
            raise ValueError("Unexpected worker count")
        data = {}
        for row in read_rows(path):
            config = row["configuration"]
            if config not in {f"rayon-w{w}" for w in WARMUPS} or row["perf_pass"] != "standard":
                raise ValueError("Unexpected configuration or perf pass")
            warmup = int(config.split("-w")[1])
            key = warmup, int(row["block"]), int(row["run_index"])
            if key in data:
                raise ValueError(f"Duplicate {key}")
            data[key] = row
        required = {(w, b, i) for w in WARMUPS for b in range(1, 21) for i in range(8)}
        if set(data) != required:
            raise ValueError(f"Missing warmup/position/block measurements, workers={workers}")
        for m, metric in enumerate(METRICS):
            values = {}
            for warmup in WARMUPS:
                for position in range(8):
                    vec = [float(data[(warmup, block, position)][metric]) for block in range(1, 21)]
                    values[warmup, position] = vec
                    lo, hi = ci(vec, 20261101 + workers*10000 + m*100 + warmup*10 + position, replicates)
                    summary.append(dict(workers=workers, warmups=warmup, position=position+1,
                                        metric=metric, n=20, mean=statistics.mean(vec),
                                        median=statistics.median(vec),
                                        sample_variance=statistics.variance(vec),
                                        ci95_low=lo, ci95_high=hi))
                for position in range(1, 8):
                    percentages = [100*(values[warmup,position][b] / values[warmup,0][b] - 1)
                                   for b in range(20)]
                    lo, hi = ci(percentages, 220000 + workers*10000 + m*100 + warmup*10 + position, replicates)
                    paired.append(dict(workers=workers, warmups=warmup, position=position+1,
                        baseline_position=1, metric=metric, n_pairs=20,
                        delta_pct=statistics.mean(percentages), ci95_low_pct=lo,
                        ci95_high_pct=hi, positive_pairs=sum(p>0 for p in percentages),
                        negative_pairs=sum(p<0 for p in percentages)))
            for warmup in (2, 4, 8):
                # Compare the same measured position, in the same randomized block,
                # against the 1-warmup condition (a different process).
                ratios = [100*(values[warmup,0][b]/values[1,0][b]-1) for b in range(20)]
                lo, hi = ci(ratios, 330000+workers*10000+m*100+warmup, replicates)
                comparisons.append(dict(workers=workers, warmups=warmup,
                    baseline_warmups=1, position=1, metric=metric, n_blocks=20,
                    delta_pct=statistics.mean(ratios), ci95_low_pct=lo,
                    ci95_high_pct=hi))
    save_rows(root/"warmup_position_summary.csv",summary)
    save_rows(root/"warmup_position_paired.csv",paired)
    save_rows(root/"warmup_first_position_comparison.csv",comparisons)
    text = ["# Warmup-count diagnostic", "",
            "20 randomized blocks per worker count, 8 perf-measured positions per invocation.",
            "Bootstrap resamples blocks; repeated positions within a process are paired.",
            "No stationary regime, cache mechanism, or sustained throughput is assumed.",
            "", "## First measured run (WU/s)", "",
            "| Workers | Warmups 1 | Warmups 2 | Warmups 4 | Warmups 8 |",
            "|---|---:|---:|---:|---:|"]
    for workers in WORKERS:
        vals = [next(r["mean"] for r in summary if r["workers"]==workers and r["warmups"]==w
                     and r["position"]==1 and r["metric"]=="throughput") for w in WARMUPS]
        text.append(f'| {workers} | '+" | ".join(f"{v:.2f}" for v in vals)+" |")
    text += ["", "## Measured position 2 vs 1, throughput, paired percentages", "",
             "| Workers | Warmups | Change % | 95% CI | Positive blocks |",
             "|---|---:|---:|---:|---:|"]
    for workers in WORKERS:
        for warmup in WARMUPS:
            r = next(r for r in paired if r["workers"]==workers and r["warmups"]==warmup
                     and r["position"]==2 and r["metric"]=="throughput")
            text.append(f'| {workers} | {warmup} | {r["delta_pct"]:+.3f} | [{r["ci95_low_pct"]:+.3f}, {r["ci95_high_pct"]:+.3f}] | {r["positive_pairs"]}/20 |')
    text += ["", "For every position and CPU counter see the accompanying CSVs.", ""]
    (root/"warmup_report.md").write_text("\n".join(text))
    return summary, paired, comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    a, b, c = analyze(args.root.resolve())
    print(f"Analyzed {len(a)} summaries, {len(b)} within-process contrasts and {len(c)} between-warmup contrasts")


if __name__ == "__main__":
    main()
