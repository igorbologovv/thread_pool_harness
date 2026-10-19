#!/usr/bin/env python3
"""Compatibility entrypoint for common paired-block analysis."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.common.analysis import main

if __name__ == "__main__":
    main()
