#!/usr/bin/env python3
"""Run the fixed-wait experiment."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.common.calibrate_service_time import ensure_calibration

EXPERIMENT = ROOT / "experiments" / "fixed_wait" / "experiment.toml"
CAMPAIGN = ROOT / "experiments" / "fixed_wait" / "campaign.py"


def parse_preflight_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--settle-seconds", type=float, default=10.0)
    parser.add_argument("--allow-dirty", action="store_true")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    os.chdir(ROOT)
    original_args = sys.argv[1:]

    if "-h" in original_args or "--help" in original_args:
        os.execv(
            sys.executable,
            [sys.executable, str(CAMPAIGN), "--experiment", str(EXPERIMENT), *original_args],
        )

    args = parse_preflight_args()
    forwarded = ["--experiment", str(EXPERIMENT), *original_args]

    if args.calibration is None:
        calibration = ensure_calibration(
            ROOT,
            EXPERIMENT,
            allow_dirty=args.allow_dirty,
            settle_seconds=args.settle_seconds,
        )
        forwarded = ["--calibration", str(calibration), *forwarded]

    os.execv(sys.executable, [sys.executable, str(CAMPAIGN), *forwarded])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
