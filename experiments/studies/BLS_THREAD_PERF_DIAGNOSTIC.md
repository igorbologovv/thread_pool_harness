# Exploratory BLS per-thread perf diagnostic

This is a *separate*, opt-in diagnostic, not a replacement for the existing
block-paired benchmark. No Threadance or harness Rust code is modified.

The focus is the CPU-time/cycle discrepancy at 2 workers, 50% fixed-arrival
load. It compares MPMC Spin(0) and RTRB backlog 2 at 647 WU/s with the
existing BLS dataset (2500 prepared certificates; 256 submitted per run).
Each invocation uses 4 warmups and 40 measured runs. There are 12 blocks
and the two configurations run in reproducibly randomized order.

## Important limitation

This Python wrapper starts the harness, waits until the main thread and two
worker threads exist, then attaches `perf stat --per-thread -t TID1,TID2,TID3`.
It therefore captures a **multi-run interval** rather than exact counters for
each individual invocation of `run_once()`. Initial warmups can be partially
captured, and the very start of the session is not synchronized with perf.
It cannot produce valid per-run paired confidence intervals. Treat results as
**exploratory totals** and keep ordinary benchmarks as the statistical
evidence. Do not claim a precise pacer vs worker cost partition from the
results without modifying the harness to gate the measured region.

The main-thread TID is labeled *pacer* (it also executes harness coordination).
The other two TIDs are workers, but native worker IDs may swap ordering between
processes; compare summed worker values before interpreting individuals.

## Run

From repo root in the newly added branch, after verifying the original dataset
is available:

```bash
git switch experiment/bls-w2-thread-perf-diagnostic
python3 -m unittest tests.test_bls_thread_perf_diagnostic
python3 -m experiments.studies.run_bls_thread_perf_diagnostic --check
cargo build --release -p threadance-harness --bin threadance-harness
sudo sysctl -w kernel.perf_event_paranoid=1
systemd-inhibit --what=sleep:idle --why="BLS per-thread diagnostic" \
  python3 -m experiments.studies.run_bls_thread_perf_diagnostic --run \
  2>&1 | tee bls-thread-perf-diagnostic.log
```

The script does not use sudo to collect, and it fails if it cannot observe
exactly three threads. Attach failures and unsupported events must be treated
as failures, not imputed. Raw comma-separated perf outputs, perf stderr, a
thread-to-TID map and the harness SQLite measurement are stored per block.

Results live in `results/studies/bls-thread-perf-diagnostic/`. To archive:

```bash
tar -czf bls-thread-perf-diagnostic.tar.gz \
  -C results/studies bls-thread-perf-diagnostic
```

Before publication-quality claims, implement gate-synchronized per-thread
perf event enable/disable in Rust or use an equivalent verified capture
method; verify the per-thread totals against independent process-wide
measurements. Separate scheduler overhead from BLS compute via explicit
instrumentation rather than inferring it from identical retired cycles.
