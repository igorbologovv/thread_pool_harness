#!/usr/bin/env bash
# One preflight shared by GitHub CI and the local pre-push hook.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

echo "[CI] Rust formatting"
cargo fmt --all -- --check

echo "[CI] Rust tests"
cargo test --workspace

echo "[CI] Rust clippy"
cargo clippy --workspace --all-targets -- -D warnings

echo "[CI] Python syntax"
python3 -m py_compile \
  experiments/common/calibrate_service_time.py \
  experiments/common/validate_calibration.py \
  experiments/common/bls_dataset_identity.py \
  experiments/common/cache_interference.py \
  experiments/common/study_run.py \
  experiments/fixed_wait/run.py \
  experiments/fixed_wait/analysis.py \
  experiments/fixed_wait/diagnostics.py \
  experiments/max_throughput/run.py \
  experiments/common/campaign.py \
  experiments/common/analysis.py \
  experiments/common/study.py \
  experiments/compute_heavy/run.py \
  experiments/compute_heavy/analysis.py \
  experiments/queue_topology/run.py \
  experiments/queue_topology/analysis.py

echo "[CI] Python campaign tests"
python3 -m unittest discover -s tests -p 'test_*.py'

echo "[CI] Real-harness campaign / resume smoke test"
python3 scripts/smoke_campaign.py

echo "[CI] Shell syntax"
bash -n scripts/calibrate_service_time.sh
bash -n scripts/run_wait_policy_probe.sh
bash -n scripts/init_benchmark_db.sh
bash -n scripts/ci-checks.sh
bash -n .githooks/pre-push

echo "[CI] SQLite schema smoke test"
DB="$(mktemp --suffix=.sqlite3)"
cleanup() {
  rm -f "$DB" "$DB-wal" "$DB-shm"
}
trap cleanup EXIT
rm -f "$DB"
./scripts/init_benchmark_db.sh "$DB"
test -z "$(sqlite3 "$DB" 'PRAGMA foreign_key_check;')"
test "$(sqlite3 "$DB" 'SELECT max(version) FROM schema_migrations;')" = "10"

echo "[CI] All checks passed"
