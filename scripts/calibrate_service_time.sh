#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

exec python3 scripts/calibrate_service_time.py create "$@"
