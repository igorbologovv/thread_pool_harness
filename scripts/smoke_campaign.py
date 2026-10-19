#!/usr/bin/env python3
"""Small real harness campaign and interrupted-campaign recovery check."""
from pathlib import Path
import csv
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
experiment = Path("experiments/smoke/experiment.toml")


def run(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "experiments.common.campaign",
                    *args, "--experiment", str(experiment)], cwd=ROOT, check=True)


def main() -> None:
    run("prepare")
    run("collect")
    campaign = max((ROOT / "results/smoke").glob("smoke-*"))
    manifest = campaign / "manifest.csv"
    with manifest.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if len(rows) != 4:
        raise AssertionError("Expected four planned invocations")
    # Simulate process death after Rust commits the last run to SQLite,
    # before Python publishes the matching manifest row.
    with manifest.open("w", newline="") as dest:
        writer = csv.DictWriter(dest, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows[:-1])
    run("resume", "--campaign", str(campaign))
    with manifest.open(newline="") as source:
        recovered = list(csv.DictReader(source))
    if len(recovered) != 4:
        raise AssertionError("Resume did not recover the missing condition")
    run("analyze", "--campaign", str(campaign))
    if not (campaign / "paired_effects.csv").is_file():
        raise AssertionError("Paired analysis was not produced")
    print("Full campaign and resume smoke check passed")
    shutil.rmtree(campaign)


if __name__ == "__main__":
    main()
