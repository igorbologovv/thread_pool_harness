"""Independent stressed/pinned BLS blocks at selected offered-load conditions.

Original campaigns are never overwritten. Run:

    python3 -m experiments.studies.run_bls_pinned_interference_ci_extension --check
    python3 -m experiments.studies.run_bls_pinned_interference_ci_extension --run

The follow-up is a separate acquisition batch. Inspect between-batch drift
before pooling its blocks with the original 20 blocks.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import random
import subprocess
import tomllib
from pathlib import Path

from experiments.common.cache_interference import campaign_signals, run_supervised
from experiments.common.campaign import ROOT, make_plan, verify_campaign_database
from experiments.common.study import render_toml
from experiments.common.study_run import command, completed, existing_campaign
from experiments.studies import run_bls_pinned_interference_load_sweep as original

NAME = "bls-pinned-interference-ci-extension"
RESULTS = ROOT / "results/studies" / NAME / "campaigns"
GENERATED = ROOT / "experiments/generated" / NAME
DEFAULT_TARGETS = (
    (2, "fixed-arrivals", 45),
    (2, "fixed-arrivals", 50),
    (4, "bursty-arrivals", 25),
    (8, "bursty-arrivals", 25),
    (8, "bursty-arrivals", 40),
    (8, "bursty-arrivals", 50),
    (16, "bursty-arrivals", 25),
    (16, "bursty-arrivals", 40),
    (16, "bursty-arrivals", 50),
    (16, "bursty-arrivals", 65),
)


def parse_target(text: str) -> tuple[int, str, int]:
    try:
        worker_str, mode_str, load_str = text.split(":")
        workers, pct = int(worker_str), int(load_str)
    except ValueError as exc:
        raise ValueError(f"Invalid target {text!r}; expected W:fixed|bursty:PCT") from exc
    mode = {"fixed": "fixed-arrivals", "bursty": "bursty-arrivals"}.get(mode_str)
    if mode is None or workers not in original.WORKERS or pct not in original.LOAD_PERCENT[workers]:
        raise ValueError(f"Unsupported target {text!r}")
    return workers, mode, pct


def build_definition(target: tuple[int, str, int], batch: int, blocks: int):
    """Keep original CPU, stressor, BLS, PMU and scheduler settings identical."""
    if batch < 1 or blocks < 2:
        raise ValueError("batch must be >=1 and blocks must be >=2")
    workers, mode, pct = target
    data, _, _ = original.definition(workers, mode, pct)
    name = f"{NAME}-batch{batch:03d}-w{workers}-load{pct:03d}-{mode}"
    data["experiment"]["name"] = name
    data["repetitions"]["blocks"] = blocks
    # Distinct scheduler permutations and bursty-arrival realisations from the old batch.
    seed = 202611120 + batch * 100000 + workers * 1000 + pct * 2 + original.MODES.index(mode)
    data["randomization"]["seed"] = seed
    data["analysis"]["bootstrap_seed"] = seed
    data["delivery"]["arrival_seed_base"] = 424242 + batch * 100000
    data["artifacts"]["output_root"] = str(RESULTS.relative_to(ROOT))
    path = GENERATED / f"{name}.toml"
    serialized = render_toml(data)
    if tomllib.loads(serialized) != data:
        raise ValueError("Generated TOML did not round-trip")
    if len(make_plan(data, None)) != blocks * len(original.CONFIGURATIONS):
        raise ValueError("Unexpected randomized-block plan")
    return data, path, serialized


def make_entries(targets: tuple[tuple[int, str, int], ...], batch: int, blocks: int):
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("At least one distinct target is required")
    entries = [build_definition(target, batch, blocks) for target in targets]
    random.Random(202611120 + batch).shuffle(entries)
    return entries


def collect(entries):
    original.preflight()
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / ".study-run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, (data, path, serialized) in enumerate(entries, 1):
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != serialized:
                raise RuntimeError(f"Conflicting generated definition: {path}")
            path.write_text(serialized)
            name = data["experiment"]["name"]
            expected = len(make_plan(data, None))
            prior = existing_campaign(name, path, RESULTS,
                                      cache_interference=True, pin_workers=True)
            if prior is None and any(RESULTS.glob(name + "-*")):
                raise RuntimeError(f"Conflicting existing campaign: {name}")
            print(f"[{index}/{len(entries)}] {name}", flush=True)
            if prior is None or not completed(prior, expected):
                subprocess.run(command("prepare", path, cache_interference=True,
                                       pin_workers=True), cwd=ROOT, check=True)
                argv = command("resume" if prior else "collect", path, prior,
                               cache_interference=True, pin_workers=True)
                with campaign_signals():
                    run_supervised(argv, cwd=ROOT, stop_timeout=15).check_returncode()
                prior = existing_campaign(name, path, RESULTS,
                                          cache_interference=True, pin_workers=True)
            if prior is None or not completed(prior, expected):
                raise RuntimeError(f"Incomplete stress campaign: {name}")
            verify_campaign_database(prior, data, expected)
            original.audit_pmu(prior, expected)
            if not (prior / "analysis_raw_runs.csv").is_file():
                subprocess.run(command("analyze", path, prior), cwd=ROOT, check=True)
    invocations = sum(len(make_plan(data, None)) for data, _, _ in entries)
    return {
        "campaigns": len(entries), "invocations": invocations,
        "measured_runs": invocations * original.REPEATS,
        "warmup_runs": invocations * original.WARMUPS,
        "output_root": str(RESULTS)
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true", help="Display plan without writes")
    actions.add_argument("--run", action="store_true", help="Collect/resume stressed batches")
    parser.add_argument("--batch", type=int, default=1, help="Independent batch ID")
    parser.add_argument("--blocks", type=int, default=40, help="New blocks per condition")
    parser.add_argument("--targets", nargs="+", metavar="W:fixed|bursty:PCT",
                        help="Selected cells; defaults to ten high-uncertainty cells")
    args = parser.parse_args()
    if args.batch < 1 or not 2 <= args.blocks <= 1000:
        parser.error("batch must be >=1; blocks must be 2..1000")
    try:
        targets = tuple(parse_target(item) for item in args.targets) if args.targets else DEFAULT_TARGETS
        entries = make_entries(targets, args.batch, args.blocks)
    except ValueError as exc:
        parser.error(str(exc))
    if args.check:
        result = {
            "study": NAME, "batch": args.batch,
            "new_blocks_per_condition": args.blocks,
            "warmups_per_block": original.WARMUPS,
            "measured_runs_per_block": original.REPEATS,
            "stressor": True, "pin_workers": True,
            "targets": [
                dict(workers=w, mode=mode, offered_load_pct=pct,
                     arrival_rate_wu_s=next(rate for p, rate, _, _ in original.points(w) if p == pct))
                for w, mode, pct in targets
            ],
            "campaigns": len(entries),
            "invocations": sum(len(make_plan(data, None)) for data, _, _ in entries),
            "output_root": str(RESULTS),
            "note": "Independent additional blocks; verify batch drift before pooling with prior campaigns."
        }
    else:
        result = collect(entries)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
