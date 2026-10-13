#!/usr/bin/env python3
"""Run the fixed-wait campaign with automatic calibration resolution."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from calibrate_service_time import ensure_calibration, repository_root


def parse_preflight_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument(
        "--experiment",
        type=Path,
        default=Path("experiments/fixed_wait.toml"),
    )
    parser.add_argument("--settle-seconds", type=float, default=10.0)
    parser.add_argument("--allow-dirty", action="store_true")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    root = repository_root()
    os.chdir(root)

    core = root / "scripts" / "fixed_wait_campaign.py"
    original_args = sys.argv[1:]

    if "-h" in original_args or "--help" in original_args:
        os.execv(sys.executable, [sys.executable, str(core), *original_args])

    args = parse_preflight_args()
    if args.calibration is not None:
        os.execv(sys.executable, [sys.executable, str(core), *original_args])

    experiment_path = args.experiment.resolve()
    if not experiment_path.is_file():
        raise RuntimeError(f"missing experiment config: {experiment_path}")

    calibration = ensure_calibration(
        root,
        experiment_path,
        allow_dirty=args.allow_dirty,
        settle_seconds=args.settle_seconds,
    )

    os.execv(
        sys.executable,
        [
            sys.executable,
            str(core),
            "--calibration",
            str(calibration),
            *original_args,
        ],
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
