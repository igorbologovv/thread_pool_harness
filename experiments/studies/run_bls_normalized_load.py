"""Run the normalized-load BLS campaign serially, with verified resume.

Never starts concurrent benchmark processes. Matching completed campaigns are
verified and skipped; matching interrupted campaigns are resumed by the shared
campaign engine. A mismatched older campaign fails closed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from pathlib import Path

from experiments.common.campaign import (
    ROOT,
    make_plan,
    read_definition,
    verify_campaign_database,
)
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies.bls_normalized_load import BLOCKS, build

GENERATED = ROOT / "experiments/generated/bls-normalized-load"


def run(*, check: bool, reference: dict[int, float] | None, blocks: int) -> dict:
    # `--check` performs no writes, builds, benchmarks or perf acquisition.
    report = build(write=not check, reference=reference, blocks=blocks)
    if check:
        return report
    if os.geteuid() == 0:
        raise RuntimeError("Run as a normal user, not through sudo")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit or restore tracked source changes before collecting")

    destination = ROOT / "results/studies/bls-normalized-load/campaigns"
    destination.mkdir(parents=True, exist_ok=True)
    finished = 0
    # Prevent two independent runs of this study from writing into the same root.
    with (destination / ".study-run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another normalized-load study runner is active") from exc

        for index, entry in enumerate(report["campaigns"], 1):
            definition = GENERATED / entry["file"]
            data = read_definition(definition)
            name = data["experiment"]["name"]
            expected = len(make_plan(data, None))
            if expected != entry["invocations"]:
                raise RuntimeError(f"Unexpected invocation count for {name}")
            prior = existing_campaign(name, definition, destination)
            # Existing files with the same experiment name but a different
            # definition cannot be silently mixed with this acquisition series.
            if prior is None and any(destination.glob(name + "-*")):
                raise RuntimeError(f"Conflicting archived campaign for {name}")
            print(
                f"[{index}/{len(report['campaigns'])}] {name}: "
                f"{'resume' if prior else 'new'}",
                flush=True,
            )
            if prior is not None and completed(prior, expected):
                verify_campaign_database(prior, data, expected)
                folder = prior
                print(f"  Verified completed campaign: {folder}", flush=True)
            else:
                subprocess.run(command("prepare", definition), cwd=ROOT, check=True)
                subprocess.run(
                    command("resume" if prior else "collect", definition, prior),
                    cwd=ROOT, check=True,
                )
                folder = existing_campaign(name, definition, destination)
                if folder is None or not completed(folder, expected):
                    raise RuntimeError(f"Campaign did not finish: {name}")
                verify_campaign_database(folder, data, expected)

            if not (folder / "paired_effects.csv").is_file():
                subprocess.run(
                    command("analyze", definition, folder), cwd=ROOT, check=True,
                )
            finished += 1

    return dict(study="bls-normalized-load", campaigns=finished, total=len(report["campaigns"]),
                measured_runs=report["total_measured_runs"], output_root=str(destination))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true",
                        help="Preview the matrix without creating files or running benchmarks")
    action.add_argument("--run", action="store_true",
                        help="Generate and sequentially collect all campaigns, resuming safely")
    parser.add_argument("--reference-json", type=Path,
                        help="JSON mapping workers 2, 4, 8, 16 to measured baseline WU/s")
    parser.add_argument("--blocks", type=int, default=BLOCKS)
    args = parser.parse_args()
    reference = None
    if args.reference_json is not None:
        reference = {
            int(k): float(v)
            for k, v in json.loads(args.reference_json.read_text()).items()
        }
    print(json.dumps(run(check=args.check, reference=reference, blocks=args.blocks),
                     indent=2), flush=True)


if __name__ == "__main__":
    main()
