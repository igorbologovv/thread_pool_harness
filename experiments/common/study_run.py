"""One-command sequential study orchestration with crash-safe campaign resume.

Run as a normal user; collection requires perf access for that user. This
program never silently invokes sudo, never executes multiple campaigns in
parallel and never changes existing benchmark measurements.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from experiments.common.campaign import ROOT
from experiments.common.study import design, read_study, root_path
from experiments.common.study_report import generate


def command(action: str, definition: Path, folder: Path | None = None) -> list[str]:
    argv = [
        sys.executable, "-m", "experiments.common.campaign", action,
        "--experiment", str(definition),
    ]
    if folder is not None:
        argv.extend(["--campaign", str(folder)])
    return argv


def existing_campaign(name: str, definition: Path, destination: Path) -> Path | None:
    """Only select a campaign with a byte-for-byte matching archived TOML."""
    folders = sorted(
        folder for folder in destination.glob(name + "-*")
        if folder.is_dir() and (folder / "experiment.toml").is_file()
        and (folder / "experiment.toml").read_bytes() == definition.read_bytes()
    )
    if len(folders) > 1:
        raise RuntimeError(
            f"Multiple matching campaigns for {name}; refusing ambiguous resume: "
            + ", ".join(str(folder) for folder in folders)
        )
    return folders[0] if folders else None


def completed(folder: Path, expected: int) -> bool:
    path = folder / "manifest.csv"
    if not path.is_file():
        return False
    with path.open(newline="") as file:
        return len(list(csv.DictReader(file))) == expected


def run_study(spec_path: Path, *, check: bool = False) -> dict:
    if os.geteuid() == 0:
        raise RuntimeError("Run the study runner as a normal user, not with sudo")
    specification = read_study(spec_path)
    report = design(spec_path, write=not check)
    if check:
        return report
    # Collection has a clean-git precondition; reject before any preparation.
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError("Commit tracked changes before benchmarking")

    generated = root_path(specification["study"]["output_dir"])
    output = root_path(specification["study"].get(
        "output_root", f"results/studies/{specification['study']['name']}/campaigns"
    ))
    output.mkdir(parents=True, exist_ok=True)
    folders = []
    for index, campaign in enumerate(report["campaigns"], 1):
        definition = generated / campaign["file"]
        if not definition.is_file():
            raise FileNotFoundError(definition)
        name = campaign["name"]
        print(f"[{index}/{len(report['campaigns'])}] {name}", flush=True)
        prior = existing_campaign(name, definition, output)
        if prior is not None and completed(prior, campaign["invocations"]):
            print(f"  Reusing completed campaign: {prior}", flush=True)
            folder = prior
        else:
            # The existing implementation builds the release binary and prepares
            # datasets/calibrations. Repeated preparations are valid but cached.
            subprocess.run(command("prepare", definition), cwd=ROOT, check=True)
            subprocess.run(
                command("resume" if prior else "collect", definition, prior),
                cwd=ROOT, check=True,
            )
            folder = existing_campaign(name, definition, output)
            if folder is None:
                raise RuntimeError(f"Campaign did not produce result folder: {name}")
        if not completed(folder, campaign["invocations"]):
            raise RuntimeError(f"Incomplete campaign after collection: {folder}")
        # Validate existing SQLite measurements and produce paired bootstrap CSV.
        if not (folder / "paired_effects.csv").is_file():
            subprocess.run(command("analyze", definition, folder), cwd=ROOT, check=True)
        folders.append(folder)

    # Avoid accidentally mixing old campaigns with the current study.
    # The report reader scans a folder: verify that its only complete campaigns
    # are precisely those selected by the study.
    all_manifests = {item.parent.resolve() for item in output.rglob("manifest.csv")}
    if all_manifests != set(folders):
        raise RuntimeError(
            "Output root contains other campaigns; use a unique study.output_root "
            "to avoid mixing unrelated results"
        )
    result = generate(output)
    return {"study": report["study"], "campaigns": len(folders), "report": result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Only show planned workload")
    action.add_argument("--run", action="store_true",
                        help="Prepare and collect every campaign serially, then report")
    args = parser.parse_args()
    print(json.dumps(run_study(args.spec, check=args.check), indent=2))


if __name__ == "__main__":
    main()
