#!/usr/bin/env bash

set -euo pipefail

TOTAL_OPERATIONS=65536
OPS_VALUES=(1 2 4 8 16 32 64 128)

mkdir -p results/granularity

echo "Total matrix operations: $TOTAL_OPERATIONS"
echo

for OPS in "${OPS_VALUES[@]}"; do
    WORK_UNITS=$((TOTAL_OPERATIONS / OPS))

    echo "============================================================"
    echo "operations/work unit : $OPS"
    echo "work units           : $WORK_UNITS"
    echo "total operations     : $((WORK_UNITS * OPS))"
    echo "============================================================"

    target/release/adaptive-threadpool-thesis \
        --workload compute-heavy \
        --workers 8 \
        --work-units "$WORK_UNITS" \
        --operations-per-work-unit "$OPS" \
        --warmup 5 \
        --runs 50 \
        | tee "results/granularity/ops_${OPS}.txt"

    echo
done