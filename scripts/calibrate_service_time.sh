#!/usr/bin/env bash
set -euo pipefail

# Scheduler-free service-time calibration for the current BLS workload.
#
# The primary output is E[S], the mean single-thread service time of
# one work unit. This value is later used to parameterize offered load:
#
#     rho = lambda * E[S] / W

WORK_UNITS=100
VALIDATORS=2000
SIGNERS_PER_CERTIFICATE=1600
CERTIFICATES_PER_WORK_UNIT=1
WORKLOAD_SEED=1

WARMUP=10
RUNS=50

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: git working tree is dirty."
    echo "Commit the experiment code before running calibration."
    exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
CALIBRATION_ID="bls-service-time-${STAMP}"
OUT="results/calibrations/${CALIBRATION_ID}"

mkdir -p "$OUT"

RAW="$OUT/raw_runs.csv"
SUMMARY="$OUT/summary.txt"
ENVIRONMENT="$OUT/environment.txt"
S_FILE="$OUT/service_time_us.txt"
CALIBRATION_JSON="$OUT/calibration.json"
LOG="$OUT/calibration.log"

echo "Calibration:"
echo "  $CALIBRATION_ID"
echo
echo "Output:"
echo "  $OUT"
echo

cargo build --release \
    -p threadance-harness \
    --bin service_time

BIN="$ROOT/target/release/service_time"

{
    echo "timestamp=$(date -Iseconds)"
    echo "calibration_id=$CALIBRATION_ID"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "git_branch=$(git branch --show-current)"
    echo "kernel=$(uname -r)"
    echo "architecture=$(uname -m)"
    echo "rustc=$(rustc --version)"
    echo "cargo=$(cargo --version)"

    echo
    echo "=== CPU ==="
    lscpu

    echo
    echo "=== CPU governors ==="
    grep -h . \
        /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor \
        2>/dev/null | sort -u || true

    echo
    echo "=== SMT ==="
    cat /sys/devices/system/cpu/smt/control \
        2>/dev/null || true

    echo
    echo "=== Memory ==="
    free -h
} > "$ENVIRONMENT"

if pgrep -af 'firefox|chromium|google-chrome|chrome' \
    >/dev/null 2>&1
then
    echo
    echo "WARNING: browser process detected:"
    pgrep -af 'firefox|chromium|google-chrome|chrome' || true
    echo
    echo "Press Ctrl+C now or calibration continues in 15 seconds."
    sleep 15
else
    echo "No common browser process detected."
    echo "Waiting 10 seconds for the machine to settle..."
    sleep 10
fi

"$BIN" \
    --work-units "$WORK_UNITS" \
    --validators "$VALIDATORS" \
    --signers-per-certificate "$SIGNERS_PER_CERTIFICATE" \
    --certificates-per-work-unit "$CERTIFICATES_PER_WORK_UNIT" \
    --seed "$WORKLOAD_SEED" \
    --warmup "$WARMUP" \
    --runs "$RUNS" \
    --samples-csv "$RAW" \
    2>&1 | tee "$LOG"

python3 - "$RAW" "$SUMMARY" "$S_FILE" <<'__CALIBRATION_STATS__'
import csv
import math
import statistics
import sys

raw_path, summary_path, s_path = sys.argv[1:]

with open(raw_path, newline="") as f:
    rows = list(csv.DictReader(f))

values_ns = [
    float(row["service_time_ns"])
    for row in rows
]

if not values_ns:
    raise SystemExit("ERROR: calibration produced no samples")

ordered = sorted(values_ns)


def percentile(values, probability):
    if len(values) == 1:
        return values[0]

    position = probability * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return values[lower]

    weight = position - lower

    return (
        values[lower] * (1.0 - weight)
        + values[upper] * weight
    )


mean_ns = statistics.mean(values_ns)
median_ns = statistics.median(values_ns)

sd_ns = (
    statistics.stdev(values_ns)
    if len(values_ns) > 1
    else math.nan
)

variance_ns2 = (
    statistics.variance(values_ns)
    if len(values_ns) > 1
    else math.nan
)

q1_ns = percentile(ordered, 0.25)
q3_ns = percentile(ordered, 0.75)
iqr_ns = q3_ns - q1_ns

mean_us = mean_ns / 1_000.0
median_us = median_ns / 1_000.0

with open(s_path, "w") as f:
    f.write(f"{mean_us:.6f}\n")

with open(summary_path, "w") as f:
    f.write("Scheduler-free service-time calibration\n\n")
    f.write(f"n={len(values_ns)}\n")
    f.write(f"mean_service_time_us={mean_us:.6f}\n")
    f.write(f"median_service_time_us={median_us:.6f}\n")
    f.write(f"sd_service_time_us={sd_ns / 1_000.0:.6f}\n")
    f.write(
        "variance_service_time_us2="
        f"{variance_ns2 / 1_000_000.0:.6f}\n"
    )
    f.write(f"q1_service_time_us={q1_ns / 1_000.0:.6f}\n")
    f.write(f"q3_service_time_us={q3_ns / 1_000.0:.6f}\n")
    f.write(f"iqr_service_time_us={iqr_ns / 1_000.0:.6f}\n")

print()
print("Calibration statistics:")
print(f"  n        = {len(values_ns)}")
print(f"  mean S   = {mean_us:.3f} us")
print(f"  median S = {median_us:.3f} us")
print(f"  SD       = {sd_ns / 1_000.0:.3f} us")
print(f"  IQR      = {iqr_ns / 1_000.0:.3f} us")
__CALIBRATION_STATS__

# ------------------------------------------------------------
# Create the machine-readable calibration artifact consumed by
# rho/tau-based experiment scripts.
# ------------------------------------------------------------

python3 - \
    "$S_FILE" \
    "$SUMMARY" \
    "$CALIBRATION_JSON" \
    "$CALIBRATION_ID" \
    "$WORK_UNITS" \
    "$VALIDATORS" \
    "$SIGNERS_PER_CERTIFICATE" \
    "$CERTIFICATES_PER_WORK_UNIT" \
    "$WORKLOAD_SEED" \
    "$(git rev-parse HEAD)" \
    "$(uname -m)" \
    "$(lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n1)" \
    "$RUNS" \
    <<'__CALIBRATION_JSON__'
import json
import sys
from pathlib import Path

(
    s_file,
    summary_file,
    output_file,
    calibration_id,
    work_units,
    validators,
    signers,
    certificates_per_work_unit,
    workload_seed,
    git_commit,
    architecture,
    cpu_model,
    samples,
) = sys.argv[1:]

mean_service_time_us = float(
    Path(s_file).read_text().strip()
)

summary = {}

for line in Path(summary_file).read_text().splitlines():
    if "=" not in line:
        continue

    key, value = line.split("=", 1)
    summary[key] = value

artifact = {
    "schema_version": 1,
    "calibration_id": calibration_id,

    "workload": "bls-aggregate-verify",

    "workload_config": {
        "calibration_work_units": int(work_units),
        "validators": int(validators),
        "signers_per_certificate": int(signers),
        "certificates_per_work_unit": int(
            certificates_per_work_unit
        ),
        "workload_seed": int(workload_seed),
    },

    "service_time": {
        "definition": (
            "scheduler-free mean single-thread "
            "service time per work unit"
        ),
        "mean_us": mean_service_time_us,
        "median_us": float(
            summary["median_service_time_us"]
        ),
        "sd_us": float(
            summary["sd_service_time_us"]
        ),
        "iqr_us": float(
            summary["iqr_service_time_us"]
        ),
        "samples": int(samples),
    },

    "provenance": {
        "git_commit": git_commit,
        "architecture": architecture,
        "cpu_model": cpu_model,
    },
}

Path(output_file).write_text(
    json.dumps(artifact, indent=2, sort_keys=True) + "\n"
)
__CALIBRATION_JSON__

echo
echo "============================================================"
echo "CALIBRATION COMPLETE"
echo "============================================================"
echo
echo "Service time used for offered-load calculations:"
printf '  S = '
cat "$S_FILE"
echo
echo "Raw runs:"
echo "  $RAW"
echo
echo "Summary:"
echo "  $SUMMARY"
echo
echo "Machine-readable S:"
echo "  $S_FILE"
echo
echo "Calibration artifact:"
echo "  $CALIBRATION_JSON"
echo
echo "Environment:"
echo "  $ENVIRONMENT"
