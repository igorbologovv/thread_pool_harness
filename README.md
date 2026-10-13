# Threadance Benchmark Harness

Research harness for evaluating configurable Rust thread pools under controlled CPU-bound workloads.

The project separates three layers:

- `threadance/` — configurable Rust thread-pool implementation;
- `harness/` — workloads, delivery schedules, scheduler adapters, perf collection, and SQLite storage;
- `experiments/` + `scripts/` + `analysis/` — reproducible experiment definitions, campaign runners, and analysis.

## Current primary experiment

The current fixed-arrival wait-policy experiment uses:

- BLS aggregate verification;
- 4 Threadance workers;
- deterministic fixed arrivals;
- nominal offered load `rho = lambda * E[S] / W`;
- spin-before-block durations from 0 to 96 microseconds;
- 20 randomized complete blocks;
- 2 measured runs per configuration per block;
- 1 identical warm-up run per scheduler instance.

The experiment definition is the source of truth:

```text
experiments/fixed_wait.toml
```

The detailed analysis protocol is documented in:

```text
docs/analysis-plan.md
```

## Measurement workflow

### 1. Create or reuse the deterministic BLS dataset

The harness stores reusable datasets under `results/datasets/` by default. Dataset generation and loading occur outside measured intervals.

Campaigns record the exact dataset path, parameters, byte size, format version, and SHA-256 checksum.

### 2. Calibrate scheduler-free service time

```bash
./scripts/calibrate_service_time.sh
```

This measures single-thread scheduler-free service time `S` for the exact BLS workload and stores raw calibration samples and provenance under `results/calibrations/`.

Calibration compatibility is checked by:

```bash
python3 scripts/validate_calibration.py --calibration PATH/TO/calibration.json
```

### 3. Measure empirical four-worker saturated capacity

```bash
python3 scripts/run_capacity_reference.py
```

This produces a separate saturated all-at-once reference for 4 workers. It is used to compare the mechanistic nominal-load parameter with observed multicore service capacity.

### 4. Run the fixed-arrival wait-policy campaign

```bash
python3 scripts/run_fixed_wait.py \
  --calibration results/calibrations/.../calibration.json
```

The legacy shell entry point remains as a thin compatibility wrapper:

```bash
./scripts/run_fixed_wait_sweep.sh \
  --calibration results/calibrations/.../calibration.json
```

A campaign stores its SQLite database, randomized plan, manifest, calibration copy, dataset identity, environment metadata, logs, and analysis outputs under `results/sweeps/`.

### 5. Re-run analysis without re-running benchmarks

```bash
python3 analysis/fixed_wait.py results/sweeps/FIXED-WAIT-CAMPAIGN
python3 analysis/diagnostics.py results/sweeps/FIXED-WAIT-CAMPAIGN
```

The main fixed-wait comparison is paired within randomized blocks. Repeated runs inside one block/configuration are summarized first, and uncertainty for configuration effects is estimated by resampling whole blocks rather than treating all run-level observations as independent.

## Main outcomes

For the fixed-arrival wait-policy experiment:

- primary CPU-efficiency outcome: `task-clock / work unit`;
- supporting CPU-cost outcome: `cycles / work unit`;
- responsiveness outcome: drain delay after the final actual submission;
- secondary end-to-end outcomes: elapsed time and throughput;
- explanatory counters: instructions, branches, branch misses, context switches, CPU migrations, page faults, and selected deep-profile counters.

Raw runs are retained. The analysis does not automatically remove statistical outliers or assume Gaussian sampling distributions.

## Database

The harness stores benchmark data in SQLite. A fresh database can be initialized with:

```bash
./scripts/init_benchmark_db.sh results/benchmarks.sqlite3
```

The harness also upgrades older compatible databases through migrations.

## Development checks

```bash
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

Python analysis and orchestration scripts use only the standard library unless otherwise documented.

GitHub Actions runs Rust formatting/tests/clippy, Python syntax checks, and a fresh SQLite schema smoke test.

## Reproducibility

Final campaign artifacts should retain at least:

- raw SQLite database;
- raw run-level data;
- experiment definition;
- randomized execution order;
- calibration artifact and raw samples;
- dataset identity and SHA-256 checksum;
- environment and CPU metadata;
- git commit;
- analysis scripts and generated tables/figures.

Measurement code should be frozen before final data collection and should not be changed during a campaign.
