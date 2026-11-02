#!/usr/bin/env bash
# Run eight strictly ordered power-mode phases and restore initial boost on exit.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."

BOOST=/sys/devices/system/cpu/cpufreq/boost
if [[ ! -f "$BOOST" ]]; then
  echo "ERROR: kernel-wide cpufreq boost control unavailable: $BOOST" >&2
  exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
  echo "ERROR: Run as normal user; sudo is only used for writing boost sysfs." >&2
  exit 1
fi
original=$(cat "$BOOST")
if [[ "$original" != 0 && "$original" != 1 ]]; then
  echo "ERROR: unexpected original boost value: $original" >&2
  exit 1
fi
restore() {
  local current
  current=$(cat "$BOOST" || true)
  if [[ "$current" != "$original" ]]; then
    echo "$original" | sudo tee "$BOOST" >/dev/null || echo "CRITICAL: restore boost=$original manually: sudo sh -c 'echo $original > $BOOST'" >&2
  fi
}
trap restore EXIT INT TERM HUP

# Password should be cached before the sequence; do not run via sudo.
sudo -v
python3 -m experiments.studies.run_bls_w2_boost_control --check
mkdir -p results/studies/bls-w2-boost-coherence-control
log="results/studies/bls-w2-boost-coherence-control/run-$(date -u +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$log") 2>&1

for phase in 1 2 3 4 5 6 7 8; do
  case "$phase" in
    1|4|5|8) target=1 ;;
    *) target=0 ;;
  esac
  echo "PHASE $phase: setting boost=$target"
  if [[ $(cat "$BOOST") != "$target" ]]; then
    echo "$target" | sudo tee "$BOOST" >/dev/null
  fi
  [[ $(cat "$BOOST") == "$target" ]] || { echo "ERROR: boost did not change" >&2; exit 1; }
  sleep 20
  systemd-inhibit --what=sleep:idle --why="BLS W2 boost and coherence control" \
    python3 -m experiments.studies.run_bls_w2_boost_control --phase "$phase"
done
echo "All 8 phases completed. Restoring original boost=$original."
