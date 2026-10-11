#!/usr/bin/env bash
set -euo pipefail

# Compare Threadance fixed spin durations against Agave's fixed backoff policy
# under the same one-worker BLS workload and deterministic arrival schedule.
# The service time and all pacing parameters are derived from an existing
# scheduler-free calibration artifact; no arrival interval is entered by hand.

CALIBRATION_FILE=""
RHO="0.80"
WORKERS=1
QUEUE_CAPACITY=1024
BLOCKS=10
RUNS_PER_BLOCK=5
WARMUP_FIRST_BLOCK=5
WARMUP_OTHER_BLOCKS=1
RANDOMIZATION_SEED=20261011

usage() {
    cat <<'EOF'
Usage:
  ./scripts/run_wait_policy_probe.sh [--calibration PATH] [--rho VALUE]

Defaults:
  --calibration  newest results/calibrations/*/calibration.json
  --rho          0.80

The script derives the fixed-arrival rate and the three Threadance spin
points from the calibration, then compares:
  Threadance tau = 0.25 * predicted idle gap
  Threadance tau = 1.00 * predicted idle gap
  Threadance tau = 2.00 * predicted idle gap
  Agave waker    = fixed Agave backoff policy, no tau
EOF
}

while (( $# > 0 )); do
    case "$1" in
        --calibration)
            if (( $# < 2 )); then
                echo "ERROR: --calibration requires a file path." >&2
                exit 2
            fi
            CALIBRATION_FILE="$2"
            shift 2
            ;;
        --rho)
            if (( $# < 2 )); then
                echo "ERROR: --rho requires a value." >&2
                exit 2
            fi
            RHO="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo >&2
            usage >&2
            exit 2
            ;;
    esac
done

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

python3 - "$RHO" <<'PY'
import math
import sys

try:
    rho = float(sys.argv[1])
except ValueError:
    raise SystemExit("ERROR: --rho must be a number")

if not math.isfinite(rho) or not (0.0 < rho < 1.0):
    raise SystemExit("ERROR: --rho must be finite and strictly between 0 and 1")
PY

if [[ -z "$CALIBRATION_FILE" ]]; then
    CALIBRATION_FILE="$(python3 - <<'PY'
from pathlib import Path

files = list(Path("results/calibrations").glob("*/calibration.json"))
if not files:
    raise SystemExit(
        "ERROR: no calibration found under results/calibrations/. "
        "Run ./scripts/calibrate_service_time.sh first."
    )

print(max(files, key=lambda path: path.stat().st_mtime))
PY
)"
fi

if [[ ! -f "$CALIBRATION_FILE" ]]; then
    echo "ERROR: calibration file does not exist: $CALIBRATION_FILE" >&2
    exit 2
fi

CALIBRATION_FILE="$(realpath "$CALIBRATION_FILE")"

# Validate and read the existing scheduler-free calibration. Only changes that
# can affect the measured BLS execute path invalidate the artifact. Test-only
# edits and unrelated scheduler dependencies must not force recalibration.
mapfile -t CALIBRATION_VALUES < <(
    python3 - \
        "$CALIBRATION_FILE" \
        "$(git rev-parse HEAD)" \
        "$(uname -m)" \
        "$(lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n1)" \
        <<'PY'
import json
import math
import subprocess
import sys
import tomllib
from pathlib import Path

path, current_commit, current_arch, current_cpu = sys.argv[1:]

try:
    data = json.loads(Path(path).read_text())
except Exception as exc:
    raise SystemExit(f"ERROR: could not read calibration JSON: {exc}")

errors = []

if data.get("schema_version") != 1:
    errors.append(f"unsupported schema_version {data.get('schema_version')!r}")

if data.get("workload") != "bls-aggregate-verify":
    errors.append(
        "workload mismatch: expected 'bls-aggregate-verify', got "
        f"{data.get('workload')!r}"
    )

config = data.get("workload_config", {})
required_config = [
    "calibration_work_units",
    "validators",
    "signers_per_certificate",
    "certificates_per_work_unit",
    "workload_seed",
]
for key in required_config:
    if key not in config:
        errors.append(f"workload_config.{key} is missing")

service = data.get("service_time", {})
try:
    s_us = float(service["mean_us"])
except Exception:
    s_us = math.nan
    errors.append("service_time.mean_us is missing or invalid")

if not math.isfinite(s_us) or s_us <= 0:
    errors.append(f"service_time.mean_us must be finite and > 0, got {s_us!r}")

provenance = data.get("provenance", {})
cal_commit = provenance.get("git_commit")


def git_show(commit, file_path):
    result = subprocess.run(
        ["git", "show", f"{commit}:{file_path}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def strip_tests(source):
    # Rust workload tests are not part of the benchmark binary hot path.
    marker = "\n#[cfg(test)]"
    return source.split(marker, 1)[0]


def lock_package_signature(lock_text, package_names):
    data = tomllib.loads(lock_text)
    signatures = []
    for package in data.get("package", []):
        if package.get("name") in package_names:
            signatures.append(
                (
                    package.get("name"),
                    package.get("version"),
                    package.get("source"),
                    package.get("checksum"),
                    tuple(package.get("dependencies", [])),
                )
            )
    return sorted(signatures)


if not cal_commit:
    errors.append("calibration provenance.git_commit is missing")
else:
    try:
        # These files directly define the scheduler-free calibration and the
        # non-test BLS workload code. Compare them exactly, except for the
        # #[cfg(test)] section of the workload implementation.
        exact_paths = [
            "Cargo.toml",
            "harness/Cargo.toml",
            "harness/src/bin/service_time.rs",
            "harness/src/workload/mod.rs",
        ]
        for file_path in exact_paths:
            old = git_show(cal_commit, file_path)
            new = git_show(current_commit, file_path)
            if old != new:
                errors.append(
                    f"calibration-relevant source changed since calibration: {file_path}"
                )

        workload_path = "harness/src/workload/bls_aggregate_verify.rs"
        old_workload = strip_tests(git_show(cal_commit, workload_path))
        new_workload = strip_tests(git_show(current_commit, workload_path))
        if old_workload != new_workload:
            errors.append(
                "calibration-relevant source changed since calibration: "
                f"{workload_path} (non-test code)"
            )

        # Cargo.lock may change because scheduler-only dependencies are added.
        # Only reject when the BLS verification dependency records themselves
        # changed. This avoids invalidating S for unrelated Threadance changes.
        bls_packages = {
            "solana-bls-signatures",
            "blst",
            "blstrs",
            "ff",
            "group",
            "pairing",
            "subtle",
            "zeroize",
        }
        old_lock = git_show(cal_commit, "Cargo.lock")
        new_lock = git_show(current_commit, "Cargo.lock")
        if lock_package_signature(old_lock, bls_packages) != lock_package_signature(
            new_lock, bls_packages
        ):
            errors.append(
                "BLS dependency versions in Cargo.lock changed since calibration"
            )
    except Exception as exc:
        errors.append(f"could not validate calibration source: {exc}")

if provenance.get("architecture") != current_arch:
    errors.append(
        "architecture mismatch: "
        f"calibration={provenance.get('architecture')!r}, current={current_arch!r}"
    )

if provenance.get("cpu_model") != current_cpu:
    errors.append(
        "CPU model mismatch: "
        f"calibration={provenance.get('cpu_model')!r}, current={current_cpu!r}"
    )

if errors:
    print("ERROR: calibration is not valid for this probe.", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"{s_us:.9f}")
print(data.get("calibration_id", "unknown"))
print(service.get("samples", "unknown"))
print(service.get("median_us", "nan"))
print(config["calibration_work_units"])
print(config["validators"])
print(config["signers_per_certificate"])
print(config["certificates_per_work_unit"])
print(config["workload_seed"])
PY
)

if (( ${#CALIBRATION_VALUES[@]} != 9 )); then
    echo "ERROR: calibration validation returned unexpected data." >&2
    exit 1
fi

S_US="${CALIBRATION_VALUES[0]}"
CALIBRATION_ID="${CALIBRATION_VALUES[1]}"
CALIBRATION_SAMPLES="${CALIBRATION_VALUES[2]}"
CALIBRATION_MEDIAN_US="${CALIBRATION_VALUES[3]}"
WORK_UNITS="${CALIBRATION_VALUES[4]}"
VALIDATORS="${CALIBRATION_VALUES[5]}"
SIGNERS_PER_CERTIFICATE="${CALIBRATION_VALUES[6]}"
CERTIFICATES_PER_WORK_UNIT="${CALIBRATION_VALUES[7]}"
WORKLOAD_SEED="${CALIBRATION_VALUES[8]}"

mapfile -t DERIVED_VALUES < <(
    python3 - "$S_US" "$RHO" "$WORKERS" <<'PY'
import sys

s_us = float(sys.argv[1])
rho = float(sys.argv[2])
workers = int(sys.argv[3])

arrival_rate = rho * workers * 1_000_000.0 / s_us
cycle_us = 1_000_000.0 / arrival_rate
idle_gap_us = s_us * (1.0 / rho - 1.0)

print(f"{arrival_rate:.12f}")
print(f"{cycle_us:.9f}")
print(f"{idle_gap_us:.9f}")
print(round(idle_gap_us * 0.25))
print(round(idle_gap_us))
print(round(idle_gap_us * 2.0))
PY
)

ARRIVAL_RATE="${DERIVED_VALUES[0]}"
CYCLE_US="${DERIVED_VALUES[1]}"
IDLE_GAP_US="${DERIVED_VALUES[2]}"
TAU_LOW="${DERIVED_VALUES[3]}"
TAU_BOUNDARY="${DERIVED_VALUES[4]}"
TAU_HIGH="${DERIVED_VALUES[5]}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "ERROR: tracked files are dirty. Commit or restore them before benchmarking." >&2
    git status --short >&2
    exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
CAMPAIGN_ID="wait-policy-probe-${STAMP}"
OUT="results/sweeps/${CAMPAIGN_ID}"
DB="$OUT/benchmarks.sqlite3"
PLAN="$OUT/plan.tsv"
MANIFEST="$OUT/manifest.csv"
ENVIRONMENT="$OUT/environment.txt"
PARAMETERS="$OUT/parameters.txt"
RAW="$OUT/raw_runs.csv"
SUMMARY="$OUT/summary.csv"

mkdir -p "$OUT/logs"

cat > "$PARAMETERS" <<EOF
campaign_id=$CAMPAIGN_ID
calibration_file=$CALIBRATION_FILE
calibration_id=$CALIBRATION_ID
calibration_samples=$CALIBRATION_SAMPLES
service_time_mean_us=$S_US
service_time_median_us=$CALIBRATION_MEDIAN_US
rho=$RHO
workers=$WORKERS
work_units=$WORK_UNITS
arrival_rate=$ARRIVAL_RATE
arrival_cycle_us=$CYCLE_US
predicted_idle_gap_us=$IDLE_GAP_US
tau_low_us=$TAU_LOW
tau_boundary_us=$TAU_BOUNDARY
tau_high_us=$TAU_HIGH
EOF

printf '%s\n' \
    "Service-time calibration:" \
    "  file       = $CALIBRATION_FILE" \
    "  id         = $CALIBRATION_ID" \
    "  mean S     = $S_US us/work unit" \
    "  median S   = $CALIBRATION_MEDIAN_US us/work unit" \
    "  samples    = $CALIBRATION_SAMPLES" \
    "" \
    "Derived one-worker fixed-arrival probe:" \
    "  rho        = $RHO" \
    "  cycle      = $CYCLE_US us" \
    "  idle gap   = $IDLE_GAP_US us" \
    "  rate       = $ARRIVAL_RATE work units/s" \
    "  LOW tau    = $TAU_LOW us" \
    "  BOUNDARY   = $TAU_BOUNDARY us" \
    "  HIGH tau   = $TAU_HIGH us" \
    "" \
    "Campaign:" \
    "  $CAMPAIGN_ID" \
    "  $OUT" \
    ""

cargo build --release \
    -p threadance-harness \
    --bin threadance-harness

BIN="$ROOT/target/release/threadance-harness"

{
    echo "timestamp=$(date -Iseconds)"
    cat "$PARAMETERS"
    echo "git_commit=$(git rev-parse HEAD)"
    echo "git_branch=$(git branch --show-current)"
    echo "kernel=$(uname -r)"
    echo "architecture=$(uname -m)"
    echo "rustc=$(rustc --version)"
    echo "cargo=$(cargo --version)"
    echo "perf=$(perf --version 2>/dev/null || true)"
    echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true)"
    echo
    echo "=== CPU ==="
    lscpu
    echo
    echo "=== CPU governors ==="
    grep -h . /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor \
        2>/dev/null | sort -u || true
    echo
    echo "=== SMT ==="
    cat /sys/devices/system/cpu/smt/control 2>/dev/null || true
    echo
    echo "=== Memory ==="
    free -h
} > "$ENVIRONMENT"

if pgrep -af 'firefox|chromium|google-chrome|chrome' >/dev/null 2>&1; then
    echo "WARNING: browser process detected. Close it now for a cleaner measurement."
    echo "Continuing in 10 seconds..."
    sleep 10
else
    echo "No common browser process detected. Waiting 3 seconds for the machine to settle..."
    sleep 3
fi

python3 - "$PLAN" "$BLOCKS" "$RANDOMIZATION_SEED" "$TAU_LOW" "$TAU_BOUNDARY" "$TAU_HIGH" <<'PY'
import csv
import random
import sys

path = sys.argv[1]
blocks = int(sys.argv[2])
seed = int(sys.argv[3])
low = int(sys.argv[4])
boundary = int(sys.argv[5])
high = int(sys.argv[6])

configs = [
    ("threadance-low", "threadance", str(low)),
    ("threadance-boundary", "threadance", str(boundary)),
    ("threadance-high", "threadance", str(high)),
    ("agave-waker", "agave-waker", ""),
]

with open(path, "w", newline="") as handle:
    writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
    writer.writerow(["block", "order_in_block", "label", "scheduler", "tau_us"])

    for block in range(1, blocks + 1):
        rows = configs.copy()
        random.Random(seed + block).shuffle(rows)
        for order, row in enumerate(rows, 1):
            writer.writerow([block, order, *row])
PY

printf '%s\n' \
'experiment_id,block,order_in_block,label,scheduler,tau_us,rho,arrival_rate,predicted_idle_us,log' \
> "$MANIFEST"

TOTAL="$((BLOCKS * 4))"
CURRENT=0

while IFS=$'\t' read -r BLOCK ORDER LABEL SCHEDULER TAU_US; do
    CURRENT=$((CURRENT + 1))

    if (( BLOCK == 1 )); then
        WARMUP_THIS="$WARMUP_FIRST_BLOCK"
    else
        WARMUP_THIS="$WARMUP_OTHER_BLOCKS"
    fi

    echo
    echo "============================================================"
    echo "Probe $CURRENT / $TOTAL"
    echo "block          = $BLOCK / $BLOCKS"
    echo "configuration  = $LABEL"
    echo "scheduler      = $SCHEDULER"
    if [[ -n "$TAU_US" ]]; then
        echo "tau            = $TAU_US us"
    else
        echo "tau            = n/a (Agave fixed backoff)"
    fi
    echo "arrival rate   = $ARRIVAL_RATE work units/s"
    echo "predicted idle = $IDLE_GAP_US us"
    echo "============================================================"

    LOG="$OUT/logs/block-$(printf '%02d' "$BLOCK")-order-$(printf '%02d' "$ORDER")-${LABEL}.log"

    CMD=(
        "$BIN"
        --workload bls-aggregate-verify
        --scheduler "$SCHEDULER"
        --workers "$WORKERS"
        --queue-capacity "$QUEUE_CAPACITY"
        --work-units "$WORK_UNITS"
        --validators "$VALIDATORS"
        --signers-per-certificate "$SIGNERS_PER_CERTIFICATE"
        --certificates-per-work-unit "$CERTIFICATES_PER_WORK_UNIT"
        --fixed-arrivals
        --arrival-rate "$ARRIVAL_RATE"
        --seed "$WORKLOAD_SEED"
        --warmup "$WARMUP_THIS"
        --runs "$RUNS_PER_BLOCK"
        --profile standard
        --campaign-id "$CAMPAIGN_ID"
        --database "$DB"
    )

    if [[ "$SCHEDULER" == "threadance" ]]; then
        CMD+=(--threadance-spin-us "$TAU_US")
    fi

    "${CMD[@]}" 2>&1 | tee "$LOG"

    EXPERIMENT_ID="$(
        sed -n 's/.*Stored benchmark: session=[0-9][0-9]* experiment=\([0-9][0-9]*\).*/\1/p' \
            "$LOG" | tail -n1
    )"

    if [[ -z "$EXPERIMENT_ID" ]]; then
        echo "ERROR: could not determine experiment id from $LOG" >&2
        exit 1
    fi

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$EXPERIMENT_ID" \
        "$BLOCK" \
        "$ORDER" \
        "$LABEL" \
        "$SCHEDULER" \
        "$TAU_US" \
        "$RHO" \
        "$ARRIVAL_RATE" \
        "$IDLE_GAP_US" \
        "$LOG" \
        >> "$MANIFEST"
done < <(tail -n +2 "$PLAN")

python3 - "$DB" "$MANIFEST" "$RAW" "$SUMMARY" <<'PY'
import csv
import random
import sqlite3
import statistics
import sys
from collections import defaultdict

DB, MANIFEST, RAW, SUMMARY = sys.argv[1:]

meta = {}
with open(MANIFEST, newline="") as handle:
    for row in csv.DictReader(handle):
        row["experiment_id"] = int(row["experiment_id"])
        meta[row["experiment_id"]] = row

conn = sqlite3.connect(DB)

runs = {}
query = """
SELECT
    e.id,
    r.id,
    r.run_index,
    r.elapsed_ns,
    r.work_units_per_second,
    pm.event_name,
    pm.counter_value
FROM experiment e
JOIN run r ON r.experiment_id = e.id
LEFT JOIN perf_metric pm ON pm.run_id = r.id
ORDER BY e.id, r.run_index
"""

for experiment_id, run_id, run_index, elapsed_ns, throughput, event, value in conn.execute(query):
    if experiment_id not in meta:
        continue

    key = (experiment_id, run_id)
    if key not in runs:
        runs[key] = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "run_index": run_index,
            "label": meta[experiment_id]["label"],
            "block": int(meta[experiment_id]["block"]),
            "tau_us": meta[experiment_id]["tau_us"],
            "elapsed_ms": elapsed_ns / 1e6,
            "throughput": throughput,
            "task-clock": None,
            "cycles": None,
            "instructions": None,
            "context-switches": None,
            "cpu-migrations": None,
        }

    if event in {
        "task-clock",
        "cycles",
        "instructions",
        "context-switches",
        "cpu-migrations",
    }:
        runs[key][event] = value

fields = [
    "experiment_id",
    "run_id",
    "run_index",
    "label",
    "block",
    "tau_us",
    "elapsed_ms",
    "throughput",
    "task-clock",
    "cycles",
    "instructions",
    "context-switches",
    "cpu-migrations",
]

with open(RAW, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for row in runs.values():
        writer.writerow(row)

by_label = defaultdict(list)
for row in runs.values():
    by_label[row["label"]].append(row)

order = [
    "threadance-low",
    "threadance-boundary",
    "threadance-high",
    "agave-waker",
]


def values(rows, key):
    return [float(row[key]) for row in rows if row[key] is not None]


def bootstrap_median_ci(xs, seed):
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(xs)
    medians = []
    for _ in range(5000):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        medians.append(statistics.median(sample))
    medians.sort()
    return (medians[int(0.025 * len(medians))], medians[int(0.975 * len(medians))])

summary_fields = [
    "label",
    "n",
    "elapsed_mean_ms",
    "elapsed_median_ms",
    "elapsed_sd_ms",
    "elapsed_median_ci95_low_ms",
    "elapsed_median_ci95_high_ms",
    "throughput_mean",
    "throughput_median",
    "task_clock_mean",
    "task_clock_median",
    "cycles_mean",
    "cycles_median",
    "instructions_mean",
    "instructions_median",
    "context_switches_mean",
    "context_switches_median",
    "cpu_migrations_mean",
    "cpu_migrations_median",
]

summary_rows = []
for index, label in enumerate(order):
    rows = by_label[label]
    elapsed = values(rows, "elapsed_ms")
    throughput = values(rows, "throughput")
    task_clock = values(rows, "task-clock")
    cycles = values(rows, "cycles")
    instructions = values(rows, "instructions")
    context_switches = values(rows, "context-switches")
    cpu_migrations = values(rows, "cpu-migrations")

    ci_low, ci_high = bootstrap_median_ci(elapsed, 20261011 + index)

    def mean(xs):
        return statistics.mean(xs) if xs else float("nan")

    def median(xs):
        return statistics.median(xs) if xs else float("nan")

    def sd(xs):
        return statistics.stdev(xs) if len(xs) > 1 else float("nan")

    summary_rows.append({
        "label": label,
        "n": len(rows),
        "elapsed_mean_ms": mean(elapsed),
        "elapsed_median_ms": median(elapsed),
        "elapsed_sd_ms": sd(elapsed),
        "elapsed_median_ci95_low_ms": ci_low,
        "elapsed_median_ci95_high_ms": ci_high,
        "throughput_mean": mean(throughput),
        "throughput_median": median(throughput),
        "task_clock_mean": mean(task_clock),
        "task_clock_median": median(task_clock),
        "cycles_mean": mean(cycles),
        "cycles_median": median(cycles),
        "instructions_mean": mean(instructions),
        "instructions_median": median(instructions),
        "context_switches_mean": mean(context_switches),
        "context_switches_median": median(context_switches),
        "cpu_migrations_mean": mean(cpu_migrations),
        "cpu_migrations_median": median(cpu_migrations),
    })

with open(SUMMARY, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=summary_fields)
    writer.writeheader()
    writer.writerows(summary_rows)

print()
print("Final aggregate medians (50 measured runs/config):")
print(
    f"{'configuration':23} {'elapsed ms':>11} {'task-clock':>11} "
    f"{'cycles':>15} {'ctx sw':>9}"
)
for row in summary_rows:
    print(
        f"{row['label']:23} "
        f"{row['elapsed_median_ms']:11.3f} "
        f"{row['task_clock_median']:11.3f} "
        f"{row['cycles_median']:15.1f} "
        f"{row['context_switches_median']:9.1f}"
    )
PY

echo
echo "Done."
echo "  database : $DB"
echo "  raw runs : $RAW"
echo "  summary  : $SUMMARY"
echo "  manifest : $MANIFEST"
