#!/usr/bin/env python3
"""Run the compute-heavy randomized-block campaign."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.common.campaign import main

if __name__ == "__main__":
    sys.argv.extend(["--experiment", str(Path(__file__).with_name("experiment.toml"))])
    main()
