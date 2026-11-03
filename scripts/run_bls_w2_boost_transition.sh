#!/usr/bin/env bash
# Reproducible w2 fixed-arrival boost-on transition benchmark.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [[ $(id -u) -eq 0 ]]; then
  echo "ERROR: Run as a normal user, not sudo." >&2
  exit 1
fi
BOOST=/sys/devices/system/cpu/cpufreq/boost
if [[ ! -f "$BOOST" || $(cat "$BOOST") != 1 ]]; then
  echo "ERROR: CPU Boost must already be enabled (sysfs boost=1)." >&2
  echo "No power settings were changed. Do not run until verified." >&2
  exit 1
fi

python3 -m experiments.studies.run_bls_w2_boost_transition --check
mkdir -p results/studies/bls-w2-boost-transition
log="results/studies/bls-w2-boost-transition/run-$(date -u +%Y%m%d-%H%M%S).log"
echo "Logging to: $log"
# No sysfs writes. Study runner refuses changes to boost/governor/policy.
systemd-inhibit --what=sleep:idle --why="BLS w2 boost-on activation transition" \
  python3 -m experiments.studies.run_bls_w2_boost_transition --run 2>&1 | tee -a "$log"
