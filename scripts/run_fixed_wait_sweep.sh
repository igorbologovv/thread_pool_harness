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
#   S        = 1547 us/work unit (previous calibration)
#   W        = 8 workers
#   CV       = 0 (deterministic periodic arrivals)
# ============================================================

S_US=1547
WORKERS=8
WORK_UNITS=200
QUEUE_CAPACITY=1000

WARMUP=10
RUNS=50

WORKLOAD_SEED=1

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

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

python3 - "$PLAN" <<'PY'
import csv
import random
import sys

S_US = 1547.0
W = 8

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

rng = random.Random(20261007)
rng.shuffle(rows)

with open(sys.argv[1], "w", newline="") as f:
    writer = csv.writer(f, delimiter="\t", lineterminator="\n")

    writer.writerow([
        "order",
        "rho",
        "tau_ratio",
        "tau_us",
        "arrival_rate",
        "predicted_idle_us",
        "rho_star",
        "prediction",
    ])

    for order, row in enumerate(rows, 1):
        writer.writerow([
            order,
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
'experiment_id,order,rho,tau_ratio,tau_us,arrival_rate,predicted_idle_us,rho_star,prediction,log' \
> "$MANIFEST"

# ------------------------------------------------------------
# Run sweep
# ------------------------------------------------------------

TOTAL="$(($(wc -l < "$PLAN") - 1))"
CURRENT=0

while IFS=$'\t' read -r \
    ORDER \
    RHO \
    TAU_RATIO \
    TAU_US \
    ARRIVAL_RATE \
    PREDICTED_IDLE_US \
    RHO_STAR \
    PREDICTION
do
    CURRENT=$((CURRENT + 1))

    echo
    echo "============================================================"
    echo "Experiment $CURRENT / $TOTAL"
    echo "rho              = $RHO"
    echo "tau/S            = $TAU_RATIO"
    echo "tau              = $TAU_US us"
    echo "arrival rate     = $ARRIVAL_RATE work units/s"
    echo "predicted idle   = $PREDICTED_IDLE_US us"
    echo "predicted rho*   = $RHO_STAR"
    echo "prediction       = $PREDICTION"
    echo "============================================================"
    echo

    LOG="$OUT/logs/$(printf '%02d' "$ORDER")-rho-${RHO}-tau-${TAU_US}.log"

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
        --warmup "$WARMUP" \
        --runs "$RUNS" \
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

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$EXPERIMENT_ID" \
        "$ORDER" \
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


def mean_sd_ci(values):
    values = [float(v) for v in values if v is not None]

    if not values:
        return (math.nan, math.nan, math.nan)

    mean = statistics.mean(values)

    if len(values) == 1:
        return (mean, math.nan, math.nan)

    sd = statistics.stdev(values)

    # Approximate 95% CI; n=50 here, so normal approximation is fine.
    ci = 1.96 * sd / math.sqrt(len(values))

    return (mean, sd, ci)


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
    "elapsed_ms_ci95",
    "task_clock_mean",
    "task_clock_ci95",
    "task_clock_per_work_unit",
    "cycles_mean",
    "cycles_ci95",
    "cycles_per_work_unit",
    "context_switches_mean",
    "context_switches_ci95",
    "context_switches_per_work_unit",
    "cpu_migrations_mean",
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

        elapsed, _, elapsed_ci = mean_sd_ci(
            [row.get("elapsed_ms") for row in rows]
        )

        task_clock, _, task_clock_ci = mean_sd_ci(
            [row.get("task-clock") for row in rows]
        )

        cycles, _, cycles_ci = mean_sd_ci(
            [row.get("cycles") for row in rows]
        )

        ctx, _, ctx_ci = mean_sd_ci(
            [row.get("context-switches") for row in rows]
        )

        migrations, _, _ = mean_sd_ci(
            [row.get("cpu-migrations") for row in rows]
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
            "elapsed_ms_mean": elapsed,
            "elapsed_ms_ci95": elapsed_ci,
            "task_clock_mean": task_clock,
            "task_clock_ci95": task_clock_ci,
            "task_clock_per_work_unit": task_clock / work_units,
            "cycles_mean": cycles,
            "cycles_ci95": cycles_ci,
            "cycles_per_work_unit": cycles / work_units,
            "context_switches_mean": ctx,
            "context_switches_ci95": ctx_ci,
            "context_switches_per_work_unit": ctx / work_units,
            "cpu_migrations_mean": migrations,
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
    S = ${S_US} us/work unit

Workers:
    W = ${WORKERS}

Work units/run:
    N = ${WORK_UNITS}

Warmups/configuration:
    ${WARMUP}

Measured runs/configuration:
    ${RUNS}

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
    deterministically randomized with seed 20261007.
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
