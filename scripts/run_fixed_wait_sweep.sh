#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Fixed-arrival Threadance wait-policy sweep
#
# Research variables:
#
#   rho   = lambda * S / W
#   alpha = tau / S
#
# Prediction boundary:
#
#   rho* = 1 / (1 + tau/S)
#
# Fixed:
#   workload = BLS aggregate verify
#   S        = measured by required service-time calibration
#   W        = 8 workers
#   CV       = 0 (deterministic periodic arrivals)
# ============================================================

# ------------------------------------------------------------
# Required service-time calibration.
# ------------------------------------------------------------

CALIBRATION_FILE=""

while (( $# > 0 )); do
    case "$1" in
        --calibration)
            if (( $# < 2 )); then
                echo "ERROR: --calibration requires a file path."
                exit 2
            fi

            CALIBRATION_FILE="$2"
            shift 2
            ;;

        -h|--help)
            cat <<'EOF'
Usage:
  ./scripts/run_fixed_wait_sweep.sh \
      --calibration PATH/TO/calibration.json

A scheduler-free service-time calibration is mandatory because
rho and tau/S depend on the measured service time S.

Generate one with:
  ./scripts/calibrate_service_time.sh
EOF
            exit 0
            ;;

        *)
            echo "ERROR: unknown argument: $1"
            echo
            echo "Use --help for usage."
            exit 2
            ;;
    esac
done

if [[ -z "$CALIBRATION_FILE" ]]; then
    echo "ERROR: service-time calibration is required."
    echo
    echo "Run:"
    echo "  ./scripts/calibrate_service_time.sh"
    echo
    echo "Then start this sweep with:"
    echo "  ./scripts/run_fixed_wait_sweep.sh \\"
    echo "      --calibration results/calibrations/.../calibration.json"
    exit 2
fi

if [[ ! -f "$CALIBRATION_FILE" ]]; then
    echo "ERROR: calibration file does not exist:"
    echo "  $CALIBRATION_FILE"
    exit 2
fi


WORKERS=8
WORK_UNITS=200
QUEUE_CAPACITY=1000

# Final measurements use a randomized blocked design.
#
# Every configuration still receives 50 measured repetitions total:
#
#   10 blocks * 5 measured runs = 50 runs/configuration
#
# The configuration order is independently randomized inside every block.
# This prevents slow thermal, frequency, or background-system drift from
# being systematically associated with one configuration.
BLOCKS=10
RUNS_PER_BLOCK=5

# The first encounter with each configuration receives the full warm-up.
# Later blocks still warm the newly created scheduler briefly, but avoid
# repeating ten warm-up executions before every five measured runs.
WARMUP_FIRST_BLOCK=10
WARMUP_OTHER_BLOCKS=2

WORKLOAD_SEED=1

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# Resolve calibration path before changing/using it further.
CALIBRATION_FILE="$(
    realpath "$CALIBRATION_FILE"
)"

# ------------------------------------------------------------
# Validate service-time calibration.
#
# A calibration is valid only for the matching workload configuration,
# host, and calibration-relevant source.
#
# The entire Git HEAD does not need to match. Documentation, analysis
# scripts and sweep-only changes do not alter scheduler-free service time.
# ------------------------------------------------------------

mapfile -t CALIBRATION_VALUES < <(
    python3 - \
        "$CALIBRATION_FILE" \
        "$(git rev-parse HEAD)" \
        "$(uname -m)" \
        "$(lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n1)" \
        "$WORKLOAD_SEED" \
        "$WORK_UNITS" \
        <<'__VALIDATE_CALIBRATION__'
import json
import math
import subprocess
import sys
from pathlib import Path

(
    path,
    current_commit,
    current_arch,
    current_cpu,
    expected_seed,
    expected_work_units,
) = sys.argv[1:]

try:
    data = json.loads(Path(path).read_text())
except Exception as exc:
    raise SystemExit(
        f"ERROR: could not read calibration JSON: {exc}"
    )

errors = []

if data.get("schema_version") != 1:
    errors.append(
        "unsupported calibration schema_version "
        f"{data.get('schema_version')!r}"
    )

if data.get("workload") != "bls-aggregate-verify":
    errors.append(
        "workload mismatch: expected "
        "'bls-aggregate-verify', got "
        f"{data.get('workload')!r}"
    )

config = data.get("workload_config", {})

expected = {
    "calibration_work_units": int(expected_work_units),
    "validators": 2000,
    "signers_per_certificate": 1600,
    "certificates_per_work_unit": 1,
    "workload_seed": int(expected_seed),
}

for key, expected_value in expected.items():
    actual = config.get(key)

    if actual != expected_value:
        errors.append(
            f"{key} mismatch: calibration={actual!r}, "
            f"experiment={expected_value!r}"
        )

service = data.get("service_time", {})

try:
    s_us = float(service["mean_us"])
except Exception:
    errors.append("service_time.mean_us is missing or invalid")
    s_us = math.nan

if not math.isfinite(s_us) or s_us <= 0:
    errors.append(
        f"service_time.mean_us must be finite and > 0, "
        f"got {s_us!r}"
    )

provenance = data.get("provenance", {})

cal_commit = provenance.get("git_commit")

if not cal_commit:
    errors.append("calibration provenance.git_commit is missing")
else:
    # Calibration validity depends only on source that can change the
    # scheduler-free BLS service-time measurement.
    #
    # Documentation, analysis scripts, sweep orchestration and unrelated
    # scheduler code do not invalidate S.
    calibration_relevant_paths = [
        "Cargo.toml",
        "Cargo.lock",
        "harness/Cargo.toml",
        "harness/src/bin/service_time.rs",
        "harness/src/workload",
    ]

    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            f"{cal_commit}..{current_commit}",
            "--",
            *calibration_relevant_paths,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        errors.append(
            "could not compare calibration-relevant source between "
            f"{cal_commit!r} and {current_commit!r}: "
            f"{result.stderr.strip()}"
        )
    else:
        changed = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

        if changed:
            errors.append(
                "calibration-relevant source changed since calibration "
                f"commit {cal_commit}: "
                + ", ".join(changed)
            )

cal_arch = provenance.get("architecture")

if cal_arch != current_arch:
    errors.append(
        "architecture mismatch: "
        f"calibration={cal_arch!r}, current={current_arch!r}"
    )

cal_cpu = provenance.get("cpu_model")

if cal_cpu != current_cpu:
    errors.append(
        "CPU model mismatch: "
        f"calibration={cal_cpu!r}, current={current_cpu!r}"
    )

if errors:
    print("ERROR: calibration is not valid for this sweep.",
          file=sys.stderr)
    print(file=sys.stderr)

    for error in errors:
        print(f"  - {error}", file=sys.stderr)

    print(file=sys.stderr)
    print(
        "Generate a fresh calibration with:",
        file=sys.stderr,
    )
    print(
        "  ./scripts/calibrate_service_time.sh",
        file=sys.stderr,
    )

    raise SystemExit(1)

print(f"{s_us:.6f}")
print(data.get("calibration_id", "unknown"))
print(service.get("samples", "unknown"))
print(service.get("median_us", "nan"))
__VALIDATE_CALIBRATION__
)

if (( ${#CALIBRATION_VALUES[@]} != 4 )); then
    echo "ERROR: calibration validation returned unexpected data."
    exit 1
fi

S_US="${CALIBRATION_VALUES[0]}"
CALIBRATION_ID="${CALIBRATION_VALUES[1]}"
CALIBRATION_SAMPLES="${CALIBRATION_VALUES[2]}"
CALIBRATION_MEDIAN_US="${CALIBRATION_VALUES[3]}"

echo "Service-time calibration:"
echo "  file      = $CALIBRATION_FILE"
echo "  id        = $CALIBRATION_ID"
echo "  mean S    = $S_US us/work unit"
echo "  median S  = $CALIBRATION_MEDIAN_US us/work unit"
echo "  samples   = $CALIBRATION_SAMPLES"
echo

# ------------------------------------------------------------
# Reproducibility checks
# ------------------------------------------------------------

if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: git working tree is dirty."
    echo "Commit the experiment code before running the sweep."
    exit 1
fi

TEMPLATE_DB="results/benchmarks.sqlite3"

if [[ ! -f "$TEMPLATE_DB" ]]; then
    echo "ERROR: $TEMPLATE_DB does not exist."
    exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
CAMPAIGN_ID="fixed-wait-${STAMP}"
OUT="results/sweeps/${CAMPAIGN_ID}"

mkdir -p "$OUT/logs"

DB="$OUT/benchmarks.sqlite3"
PLAN="$OUT/plan.tsv"
MANIFEST="$OUT/manifest.csv"
ENVIRONMENT="$OUT/environment.txt"

echo "Campaign:"
echo "  $CAMPAIGN_ID"
echo
echo "Experiment output:"
echo "  $OUT"
echo

# ------------------------------------------------------------
# Build ONCE before measurements
# ------------------------------------------------------------

cargo build --release \
    -p threadance-harness \
    --bin threadance-harness

BIN="$ROOT/target/release/threadance-harness"

# ------------------------------------------------------------
# Make an empty campaign DB using the already-tested schema.
# Keep migration history, remove previous experimental data.
# ------------------------------------------------------------

cp "$TEMPLATE_DB" "$DB"

sqlite3 "$DB" <<'SQL'
PRAGMA foreign_keys = ON;

DELETE FROM benchmark_session;

DELETE FROM sqlite_sequence
WHERE name IN (
    'benchmark_session',
    'experiment',
    'run'
);

VACUUM;
SQL

COUNTS="$(
    sqlite3 "$DB" "
        SELECT
            (SELECT COUNT(*) FROM benchmark_session) || '|' ||
            (SELECT COUNT(*) FROM experiment) || '|' ||
            (SELECT COUNT(*) FROM run);
    "
)"

if [[ "$COUNTS" != "0|0|0" ]]; then
    echo "ERROR: campaign database is not empty: $COUNTS"
    exit 1
fi

# ------------------------------------------------------------
# Record machine/environment state
# ------------------------------------------------------------

{
    echo "timestamp=$(date -Iseconds)"
    echo "campaign_id=$CAMPAIGN_ID"
    echo "calibration_file=$CALIBRATION_FILE"
    echo "calibration_id=$CALIBRATION_ID"
    echo "service_time_mean_us=$S_US"
    echo "service_time_median_us=$CALIBRATION_MEDIAN_US"
    echo "calibration_samples=$CALIBRATION_SAMPLES"
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

# Warn about obvious background browsers.
if pgrep -af 'firefox|chromium|google-chrome|chrome' >/dev/null 2>&1; then
    echo
    echo "WARNING: a browser process appears to be running:"
    pgrep -af 'firefox|chromium|google-chrome|chrome' || true
    echo
    echo "Close it before continuing if you want a clean measurement."
    echo "Press Ctrl+C now, or wait 15 seconds to continue."
    sleep 15
else
    echo "No common browser process detected."
    echo "Waiting 10 seconds for the machine to settle..."
    sleep 10
fi

# ------------------------------------------------------------
# Generate experiment matrix.
#
# tau/S:
#   0
#   0.10
#   0.25
#   0.50
#   1.00
#
# rho values deliberately include the theoretical crossover
# regions:
#
#   tau/S = 1.00 -> rho* = 0.500
#   tau/S = 0.50 -> rho* = 0.667
#   tau/S = 0.25 -> rho* = 0.800
#   tau/S = 0.10 -> rho* = 0.909
#
# plus low-load and high-load endpoints.
#
# Execution order is randomized with a FIXED seed so thermal /
# frequency drift is not systematically aligned with rho or tau.
# ------------------------------------------------------------

python3 - "$PLAN" "$S_US" "$BLOCKS" "$WORKERS" <<'PY'
import csv
import random
import sys

S_US = float(sys.argv[2])
BLOCKS = int(sys.argv[3])
W = int(sys.argv[4])

rhos = [
    0.25,
    0.50,
    0.67,
    0.80,
    0.91,
    0.95,
]

tau_ratios = [
    0.00,
    0.10,
    0.25,
    0.50,
    1.00,
]

rows = []

for rho in rhos:
    arrival_rate = rho * W * 1_000_000.0 / S_US

    # Simplified ideal fixed-arrival model.
    predicted_idle_us = S_US * (1.0 / rho - 1.0)

    for tau_ratio in tau_ratios:
        tau_us = round(S_US * tau_ratio)

        actual_tau_ratio = tau_us / S_US
        rho_star = 1.0 / (1.0 + actual_tau_ratio)

        if tau_us == 0:
            prediction = "immediate-block"
        elif tau_us < predicted_idle_us:
            prediction = "spin-then-block-likely"
        else:
            prediction = "next-work-during-spin-likely"

        rows.append({
            "rho": rho,
            "tau_ratio": actual_tau_ratio,
            "tau_us": tau_us,
            "arrival_rate": arrival_rate,
            "predicted_idle_us": predicted_idle_us,
            "rho_star": rho_star,
            "prediction": prediction,
        })

RANDOMIZATION_SEED = 20261007

with open(sys.argv[1], "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t", lineterminator="\n")

    writer.writerow([
        "block",
        "order_in_block",
        "global_order",
        "rho",
        "tau_ratio",
        "tau_us",
        "arrival_rate",
        "predicted_idle_us",
        "rho_star",
        "prediction",
    ])

    global_order = 0

    for block in range(1, BLOCKS + 1):
        # Start every block from the same complete configuration set,
        # then randomize independently with a deterministic seed.
        block_rows = [row.copy() for row in rows]

        rng = random.Random(RANDOMIZATION_SEED + block)
        rng.shuffle(block_rows)

        for order_in_block, row in enumerate(block_rows, 1):
            global_order += 1

            writer.writerow([
                block,
                order_in_block,
                global_order,
                f"{row['rho']:.4f}",
                f"{row['tau_ratio']:.6f}",
                row["tau_us"],
                f"{row['arrival_rate']:.6f}",
                f"{row['predicted_idle_us']:.3f}",
                f"{row['rho_star']:.6f}",
                row["prediction"],
            ])
PY

echo
echo "Experimental matrix:"
column -t -s $'\t' "$PLAN"
echo

printf '%s\n' \
'experiment_id,block,order_in_block,global_order,rho,tau_ratio,tau_us,arrival_rate,predicted_idle_us,rho_star,prediction,log' \
> "$MANIFEST"

# ------------------------------------------------------------
# Run sweep
# ------------------------------------------------------------

TOTAL="$(($(wc -l < "$PLAN") - 1))"
CURRENT=0

while IFS=$'\t' read -r \
    BLOCK \
    ORDER_IN_BLOCK \
    GLOBAL_ORDER \
    RHO \
    TAU_RATIO \
    TAU_US \
    ARRIVAL_RATE \
    PREDICTED_IDLE_US \
    RHO_STAR \
    PREDICTION
do
    CURRENT=$((CURRENT + 1))

    if (( BLOCK == 1 )); then
        WARMUP_THIS="$WARMUP_FIRST_BLOCK"
    else
        WARMUP_THIS="$WARMUP_OTHER_BLOCKS"
    fi

    echo
    echo "============================================================"
    echo "Experiment $CURRENT / $TOTAL"
    echo "block            = $BLOCK / $BLOCKS"
    echo "order in block   = $ORDER_IN_BLOCK"
    echo "rho              = $RHO"
    echo "tau/S            = $TAU_RATIO"
    echo "tau              = $TAU_US us"
    echo "arrival rate     = $ARRIVAL_RATE work units/s"
    echo "predicted idle   = $PREDICTED_IDLE_US us"
    echo "predicted rho*   = $RHO_STAR"
    echo "prediction       = $PREDICTION"
    echo "============================================================"
    echo

    LOG="$OUT/logs/block-$(printf '%02d' "$BLOCK")-order-$(printf '%02d' "$ORDER_IN_BLOCK")-rho-${RHO}-tau-${TAU_US}.log"

    BEFORE_ID="$(
        sqlite3 "$DB" 'SELECT COALESCE(MAX(id), 0) FROM experiment;'
    )"

    "$BIN" \
        --workload bls-aggregate-verify \
        --scheduler threadance \
        --workers "$WORKERS" \
        --queue-capacity "$QUEUE_CAPACITY" \
        --threadance-spin-us "$TAU_US" \
        --work-units "$WORK_UNITS" \
        --validators 2000 \
        --signers-per-certificate 1600 \
        --certificates-per-work-unit 1 \
        --fixed-arrivals \
        --arrival-rate "$ARRIVAL_RATE" \
        --seed "$WORKLOAD_SEED" \
        --warmup "$WARMUP_THIS" \
        --runs "$RUNS_PER_BLOCK" \
        --profile standard \
        --campaign-id "$CAMPAIGN_ID" \
        --database "$DB" \
        2>&1 | tee "$LOG"

    EXPERIMENT_ID="$(
        sqlite3 "$DB" 'SELECT COALESCE(MAX(id), 0) FROM experiment;'
    )"

    if (( EXPERIMENT_ID <= BEFORE_ID )); then
        echo "ERROR: benchmark did not create an experiment row."
        exit 1
    fi

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$EXPERIMENT_ID" \
        "$BLOCK" \
        "$ORDER_IN_BLOCK" \
        "$GLOBAL_ORDER" \
        "$RHO" \
        "$TAU_RATIO" \
        "$TAU_US" \
        "$ARRIVAL_RATE" \
        "$PREDICTED_IDLE_US" \
        "$RHO_STAR" \
        "$PREDICTION" \
        "$LOG" \
        >> "$MANIFEST"

    # Small cooldown between independently configured experiments.
    sleep 3

done < <(tail -n +2 "$PLAN")

# ------------------------------------------------------------
# Integrity check
# ------------------------------------------------------------

FK_ERRORS="$(sqlite3 "$DB" 'PRAGMA foreign_key_check;')"

if [[ -n "$FK_ERRORS" ]]; then
    echo "ERROR: foreign-key check failed:"
    echo "$FK_ERRORS"
    exit 1
fi

# ------------------------------------------------------------
# Produce raw and aggregate CSVs for later analysis.
# ------------------------------------------------------------

python3 - "$DB" "$MANIFEST" "$OUT" "$S_US" "$WORK_UNITS" <<'PY'
import csv
import math
import random
import sqlite3
import statistics
import sys
from collections import defaultdict

db_path, manifest_path, out_dir, s_us, work_units = sys.argv[1:]
s_us = float(s_us)
work_units = int(work_units)

manifest = {}

with open(manifest_path, newline="") as f:
    for row in csv.DictReader(f):
        row["experiment_id"] = int(row["experiment_id"])
        manifest[row["experiment_id"]] = row

conn = sqlite3.connect(db_path)

query = """
SELECT
    e.id,
    e.arrival_rate,
    e.threadance_spin_us,
    r.id,
    r.run_index,
    r.elapsed_ns,
    r.work_units_per_second,
    pm.event_name,
    pm.counter_value
FROM experiment e
JOIN run r
    ON r.experiment_id = e.id
LEFT JOIN perf_metric pm
    ON pm.run_id = r.id
ORDER BY e.id, r.run_index
"""

runs = {}

for (
    experiment_id,
    arrival_rate,
    spin_us,
    run_id,
    run_index,
    elapsed_ns,
    throughput,
    event_name,
    counter_value,
) in conn.execute(query):

    if experiment_id not in manifest:
        continue

    key = (experiment_id, run_id)

    if key not in runs:
        meta = manifest[experiment_id]

        runs[key] = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "run_index": run_index,
            "block": int(meta["block"]),
            "order_in_block": int(meta["order_in_block"]),
            "global_order": int(meta["global_order"]),
            "rho": float(meta["rho"]),
            "tau_ratio": float(meta["tau_ratio"]),
            "tau_us": int(meta["tau_us"]),
            "arrival_rate": float(meta["arrival_rate"]),
            "predicted_idle_us": float(meta["predicted_idle_us"]),
            "rho_star": float(meta["rho_star"]),
            "prediction": meta["prediction"],
            "elapsed_ms": elapsed_ns / 1_000_000.0,
            "throughput": throughput,
        }

    if event_name is not None:
        runs[key][event_name] = counter_value

raw_columns = [
    "experiment_id",
    "run_id",
    "run_index",
    "block",
    "order_in_block",
    "global_order",
    "rho",
    "tau_ratio",
    "tau_us",
    "arrival_rate",
    "predicted_idle_us",
    "rho_star",
    "prediction",
    "elapsed_ms",
    "throughput",
    "task-clock",
    "cycles",
    "instructions",
    "branches",
    "branch-misses",
    "context-switches",
    "cpu-migrations",
    "page-faults",
]

raw_path = f"{out_dir}/raw_runs.csv"

with open(raw_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=raw_columns)
    writer.writeheader()

    for row in runs.values():
        writer.writerow({name: row.get(name) for name in raw_columns})


BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20261011


def percentile(sorted_values, probability):
    if not sorted_values:
        return math.nan

    if len(sorted_values) == 1:
        return sorted_values[0]

    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return sorted_values[lower]

    weight = position - lower

    return (
        sorted_values[lower] * (1.0 - weight)
        + sorted_values[upper] * weight
    )


def bootstrap_mean_ci(values, seed):
    if len(values) == 1:
        return (values[0], values[0])

    rng = random.Random(seed)
    n = len(values)

    bootstrap_means = []

    for _ in range(BOOTSTRAP_REPLICATES):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        bootstrap_means.append(statistics.mean(sample))

    bootstrap_means.sort()

    return (
        percentile(bootstrap_means, 0.025),
        percentile(bootstrap_means, 0.975),
    )


def descriptive_statistics(values, seed):
    values = [float(v) for v in values if v is not None]

    if not values:
        return {
            "n": 0,
            "mean": math.nan,
            "sd": math.nan,
            "variance": math.nan,
            "median": math.nan,
            "q1": math.nan,
            "q3": math.nan,
            "iqr": math.nan,
            "ci95_low": math.nan,
            "ci95_high": math.nan,
        }

    ordered = sorted(values)

    mean = statistics.mean(values)
    median = statistics.median(values)

    if len(values) > 1:
        sd = statistics.stdev(values)
        variance = statistics.variance(values)
    else:
        sd = math.nan
        variance = math.nan

    q1 = percentile(ordered, 0.25)
    q3 = percentile(ordered, 0.75)
    iqr = q3 - q1

    ci95_low, ci95_high = bootstrap_mean_ci(values, seed)

    return {
        "n": len(values),
        "mean": mean,
        "sd": sd,
        "variance": variance,
        "median": median,
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "ci95_low": ci95_low,
        "ci95_high": ci95_high,
    }


groups = defaultdict(list)

for row in runs.values():
    key = (
        row["rho"],
        row["tau_ratio"],
        row["tau_us"],
        row["arrival_rate"],
        row["predicted_idle_us"],
        row["rho_star"],
        row["prediction"],
    )

    groups[key].append(row)

summary_columns = [
    "rho",
    "tau_ratio",
    "tau_us",
    "arrival_rate",
    "predicted_idle_us",
    "rho_star",
    "prediction",
    "n",

    "elapsed_ms_mean",
    "elapsed_ms_sd",
    "elapsed_ms_variance",
    "elapsed_ms_median",
    "elapsed_ms_q1",
    "elapsed_ms_q3",
    "elapsed_ms_iqr",
    "elapsed_ms_ci95_low",
    "elapsed_ms_ci95_high",

    "task_clock_mean",
    "task_clock_sd",
    "task_clock_variance",
    "task_clock_median",
    "task_clock_q1",
    "task_clock_q3",
    "task_clock_iqr",
    "task_clock_ci95_low",
    "task_clock_ci95_high",
    "task_clock_per_work_unit",

    "cycles_mean",
    "cycles_sd",
    "cycles_variance",
    "cycles_median",
    "cycles_q1",
    "cycles_q3",
    "cycles_iqr",
    "cycles_ci95_low",
    "cycles_ci95_high",
    "cycles_per_work_unit",

    "context_switches_mean",
    "context_switches_sd",
    "context_switches_variance",
    "context_switches_median",
    "context_switches_q1",
    "context_switches_q3",
    "context_switches_iqr",
    "context_switches_ci95_low",
    "context_switches_ci95_high",
    "context_switches_per_work_unit",

    "cpu_migrations_mean",
    "cpu_migrations_sd",
    "cpu_migrations_median",
]

summary_path = f"{out_dir}/summary.csv"

with open(summary_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=summary_columns)
    writer.writeheader()

    for key in sorted(groups):
        (
            rho,
            tau_ratio,
            tau_us,
            arrival_rate,
            predicted_idle_us,
            rho_star,
            prediction,
        ) = key

        rows = groups[key]

        # Use deterministic, non-parametric bootstrap confidence intervals
        # instead of assuming a Gaussian run distribution.
        #
        # The seed is varied by experiment and metric so rerunning analysis
        # remains reproducible while bootstrap samples are independent across
        # the reported quantities.
        seed_base = BOOTSTRAP_SEED + int(rho * 10_000) + tau_us * 101

        elapsed = descriptive_statistics(
            [row.get("elapsed_ms") for row in rows],
            seed_base + 1,
        )

        task_clock = descriptive_statistics(
            [row.get("task-clock") for row in rows],
            seed_base + 2,
        )

        cycles = descriptive_statistics(
            [row.get("cycles") for row in rows],
            seed_base + 3,
        )

        ctx = descriptive_statistics(
            [row.get("context-switches") for row in rows],
            seed_base + 4,
        )

        migrations = descriptive_statistics(
            [row.get("cpu-migrations") for row in rows],
            seed_base + 5,
        )

        writer.writerow({
            "rho": rho,
            "tau_ratio": tau_ratio,
            "tau_us": tau_us,
            "arrival_rate": arrival_rate,
            "predicted_idle_us": predicted_idle_us,
            "rho_star": rho_star,
            "prediction": prediction,
            "n": len(rows),

            "elapsed_ms_mean": elapsed["mean"],
            "elapsed_ms_sd": elapsed["sd"],
            "elapsed_ms_variance": elapsed["variance"],
            "elapsed_ms_median": elapsed["median"],
            "elapsed_ms_q1": elapsed["q1"],
            "elapsed_ms_q3": elapsed["q3"],
            "elapsed_ms_iqr": elapsed["iqr"],
            "elapsed_ms_ci95_low": elapsed["ci95_low"],
            "elapsed_ms_ci95_high": elapsed["ci95_high"],

            "task_clock_mean": task_clock["mean"],
            "task_clock_sd": task_clock["sd"],
            "task_clock_variance": task_clock["variance"],
            "task_clock_median": task_clock["median"],
            "task_clock_q1": task_clock["q1"],
            "task_clock_q3": task_clock["q3"],
            "task_clock_iqr": task_clock["iqr"],
            "task_clock_ci95_low": task_clock["ci95_low"],
            "task_clock_ci95_high": task_clock["ci95_high"],
            "task_clock_per_work_unit": (
                task_clock["mean"] / work_units
            ),

            "cycles_mean": cycles["mean"],
            "cycles_sd": cycles["sd"],
            "cycles_variance": cycles["variance"],
            "cycles_median": cycles["median"],
            "cycles_q1": cycles["q1"],
            "cycles_q3": cycles["q3"],
            "cycles_iqr": cycles["iqr"],
            "cycles_ci95_low": cycles["ci95_low"],
            "cycles_ci95_high": cycles["ci95_high"],
            "cycles_per_work_unit": (
                cycles["mean"] / work_units
            ),

            "context_switches_mean": ctx["mean"],
            "context_switches_sd": ctx["sd"],
            "context_switches_variance": ctx["variance"],
            "context_switches_median": ctx["median"],
            "context_switches_q1": ctx["q1"],
            "context_switches_q3": ctx["q3"],
            "context_switches_iqr": ctx["iqr"],
            "context_switches_ci95_low": ctx["ci95_low"],
            "context_switches_ci95_high": ctx["ci95_high"],
            "context_switches_per_work_unit": (
                ctx["mean"] / work_units
            ),

            "cpu_migrations_mean": migrations["mean"],
            "cpu_migrations_sd": migrations["sd"],
            "cpu_migrations_median": migrations["median"],
        })

conn.close()

print(f"wrote {raw_path}")
print(f"wrote {summary_path}")
PY

# ------------------------------------------------------------
# Save methodology/predictions alongside the measurements.
# ------------------------------------------------------------

cat > "$OUT/README.txt" <<EOF2
Fixed-arrival Threadance wait-policy sweep

Campaign:
    ${CAMPAIGN_ID}

Service-time calibration:
    calibration id   = ${CALIBRATION_ID}
    calibration file = ${CALIBRATION_FILE}
    mean S           = ${S_US} us/work unit
    median S         = ${CALIBRATION_MEDIAN_US} us/work unit
    samples          = ${CALIBRATION_SAMPLES}

The mean service time is used for rho and tau/S calculations.

Workers:
    W = ${WORKERS}

Work units/run:
    N = ${WORK_UNITS}

Repetition design:
    randomized blocked design

Blocks:
    ${BLOCKS}

Measured runs/block/configuration:
    ${RUNS_PER_BLOCK}

Total measured runs/configuration:
    $((BLOCKS * RUNS_PER_BLOCK))

Warmups/configuration in first block:
    ${WARMUP_FIRST_BLOCK}

Warmups/configuration in later blocks:
    ${WARMUP_OTHER_BLOCKS}

Arrival process:
    deterministic periodic (CV approximately zero)

Nominal offered load:
    rho = lambda * S / W

Normalized spin threshold:
    alpha = tau / S

Idealized worker-idle prediction:
    D ~= S * (1/rho - 1)

Predicted spin/block boundary:
    rho* ~= 1 / (1 + tau/S)

Primary performance quantities:
    task-clock / work unit
    cycles / work unit

Supporting quantities:
    context switches / work unit
    CPU migrations
    elapsed time / throughput

Important interpretation:
    elapsed time is strongly constrained by the fixed arrival window
    below saturation. CPU cost is therefore the primary metric for
    wait-policy efficiency in those regimes.

Execution order:
    randomized independently within every block.

Randomization:
    deterministic and reproducible from base seed 20261007;
    block b uses seed 20261007 + b.

Rationale:
    blocking distributes slow thermal, CPU-frequency, and background-system
    drift across configurations instead of measuring all 50 repetitions of
    one configuration consecutively.
EOF2

echo
echo "============================================================"
echo "SWEEP COMPLETE"
echo "============================================================"
echo
echo "Database:"
echo "  $DB"
echo
echo "Plan:"
echo "  $PLAN"
echo
echo "Manifest:"
echo "  $MANIFEST"
echo
echo "Raw run data:"
echo "  $OUT/raw_runs.csv"
echo
echo "Aggregate summary:"
echo "  $OUT/summary.csv"
echo
echo "Environment:"
echo "  $ENVIRONMENT"
echo
echo "Methodology:"
echo "  $OUT/README.txt"
echo
