"""Create a reproducible, offline report across completed benchmark campaigns."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
import tomllib
from collections import defaultdict
from pathlib import Path

from experiments.common.analysis import analyze, percentile


def rows(path: Path) -> list[dict]:
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def summarize(raw: list[dict]) -> list[dict]:
    series = defaultdict(list)
    for run in raw:
        for metric in ("elapsed_ms", "throughput", "drain_delay_ms",
                       "task_clock_per_wu"):
            value = run.get(metric)
            if value not in (None, "") and math.isfinite(float(value)):
                key = (run["configuration"], run["arrival_rate"],
                       run["perf_pass"], metric)
                series[key].append(float(value))
    result = []
    for (config, rate, perf_pass, metric), values in sorted(series.items()):
        q1, q3 = percentile(values, .25), percentile(values, .75)
        iqr = q3 - q1
        result.append({
            "configuration": config, "rate": rate, "perf_pass": perf_pass,
            "metric": metric, "n": len(values),
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "sample_variance": statistics.variance(values)
            if len(values) > 1 else None,
            "iqr_fence_count": sum(
                x < q1 - 1.5 * iqr or x > q3 + 1.5 * iqr for x in values
            ) if iqr else 0,
        })
    return result


def plot(effects: list[dict], metric: str) -> str:
    good = [x for x in effects if x["metric"] == metric and all(
        math.isfinite(float(x[k])) for k in (
            "relative_mean", "relative_ci95_low", "relative_ci95_high"
        )
    )]
    if not good:
        return ""
    good.sort(key=lambda x: abs(float(x["relative_mean"])), reverse=True)
    good = good[:12]
    low = min([0.0] + [float(x["relative_ci95_low"]) for x in good])
    high = max([0.0] + [float(x["relative_ci95_high"]) for x in good])
    span = high - low or 1.0
    pos = lambda v: 410 + (float(v) - low) / span * 510
    size = 42 + len(good) * 24
    lines = [f'<svg viewBox="0 0 950 {size}">',
             f'<line x1="{pos(0):.1f}" x2="{pos(0):.1f}" y1="0" '
             f'y2="{size}" stroke="#888" stroke-dasharray="4,4"/>']
    for index, item in enumerate(good):
        y = 25 + index * 24
        label = html.escape(
            (item["configuration"] + " / " + item["arrival_rate"])[:56])
        lines.extend([
            f'<text x="0" y="{y+4}" font-size="11">{label}</text>',
            f'<line x1="{pos(item["relative_ci95_low"]):.1f}" '
            f'x2="{pos(item["relative_ci95_high"]):.1f}" y1="{y}" y2="{y}" '
            'stroke="#555" stroke-width="2"/>',
            f'<circle cx="{pos(item["relative_mean"]):.1f}" '
            f'cy="{y}" r="4" fill="#2463a6"/>',
        ])
    return "\n".join(lines + ["</svg>"])


def generate(root: Path) -> dict:
    root = root.resolve()
    campaigns = []
    for manifest in sorted(root.rglob("manifest.csv")):
        folder = manifest.parent
        analyze(folder)  # enforces complete runs and paired block alignment
        definition = tomllib.loads((folder / "experiment.toml").read_text())
        raw = rows(folder / "analysis_raw_runs.csv")
        effects = rows(folder / "paired_effects.csv")
        blocks = int(definition["repetitions"]["blocks"])
        if not raw or not effects or any(
            int(row["n_blocks"]) != blocks for row in effects
        ):
            raise ValueError(f"Invalid effects in campaign {folder}")
        campaigns.append({
            "name": definition["experiment"]["name"],
            "folder": str(folder),
            "workload": definition["experiment"]["workload"],
            "workers": definition["workload"]["workers"],
            "arrival_mode": definition["delivery"]["mode"],
            "blocks": blocks,
            "raw_runs": len(raw),
            "summaries": summarize(raw),
            "paired_effects": effects,
        })
    if not campaigns:
        raise ValueError(f"No collected campaigns found under {root}")
    root.mkdir(parents=True, exist_ok=True)
    payload = {"campaigns": campaigns}
    (root / "study_report.json").write_text(
        json.dumps(payload, indent=2) + "\n"
    )
    page = ['<!doctype html><html lang="en"><meta charset="utf-8">',
            '<title>Threadance research report</title>',
            '<style>body{max-width:1100px;margin:30px auto;padding:0 20px;'
            'font:15px/1.5 system-ui}svg{width:100%;height:auto}'
            'table{border-collapse:collapse;font-size:13px}'
            'td,th{padding:5px;border-bottom:1px solid #ddd}</style>',
            '<h1>Threadance research report</h1>',
            '<p>Effects and 95% confidence intervals use the existing '
            'paired-block bootstrap. Run distributions are not assumed Gaussian. '
            'Inspect raw runs for drift and multimodality.</p>']
    for campaign in campaigns:
        page += [f'<h2>{html.escape(campaign["name"])}</h2>',
                 f'<p>{campaign["workers"]} workers, '
                 f'{html.escape(campaign["arrival_mode"])}, '
                 f'{campaign["blocks"]} blocks</p>']
        if campaign["blocks"] < 10:
            page.append('<p>Exploratory only: very few paired blocks.</p>')
        for metric in ("task_clock_per_wu", "drain_delay_ms", "elapsed_ms"):
            svg = plot(campaign["paired_effects"], metric)
            if svg:
                page.extend([f'<h3>{html.escape(metric)}: relative difference '
                             'vs reference (95% CI)</h3>', svg])
        page.append('<details><summary>Descriptive statistics for all conditions</summary>'
                    '<table><tr><th>Configuration</th><th>Rate</th><th>Metric</th>'
                    '<th>n</th><th>Mean</th><th>Variance</th><th>IQR flags</th></tr>')
        for item in campaign["summaries"]:
            variance = item["sample_variance"]
            page.append(
                f'<tr><td>{html.escape(item["configuration"])}</td>'
                f'<td>{html.escape(item["rate"])}</td>'
                f'<td>{html.escape(item["metric"])}</td>'
                f'<td>{item["n"]}</td><td>{item["mean"]:.5g}</td>'
                f'<td>{variance:.5g}</td>' if variance is not None else
                f'<tr><td>{html.escape(item["configuration"])}</td>'
                f'<td>{html.escape(item["rate"])}</td>'
                f'<td>{html.escape(item["metric"])}</td>'
                f'<td>{item["n"]}</td><td>{item["mean"]:.5g}</td><td>—</td>'
            )
            page.append(f'<td>{item["iqr_fence_count"]}</td></tr>')
        page.append('</table></details>')
    page.append('</html>')
    (root / "study_report.html").write_text("\n".join(page))
    return {"campaigns": len(campaigns),
            "html": str(root / "study_report.html"),
            "json": str(root / "study_report.json")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.root), indent=2))


if __name__ == "__main__":
    main()
