# Threadance Benchmark Harness

Research harness for evaluating configurable Rust thread pools under controlled CPU-bound workloads.

The project separates three layers:

- `threadance/` — configurable Rust thread-pool implementation;
- `harness/` — workloads, delivery schedules, scheduler adapters, perf collection, and SQLite storage;
- `experiments/` — experiment definitions, runners, analysis, and shared experiment infrastructure.

## Experiment layout

```text
experiments/
├── common/
│   ├── calibrate_service_time.py
│   ├── validate_calibration.py
│   └── bls_dataset_identity.py
├── fixed_wait/
│   ├── experiment.toml
│   ├── run.py
│   ├── campaign.py
│   ├── analysis.py
│   ├── diagnostics.py
│   └── analysis-plan.md
└── max_throughput/
    ├── experiment.toml
    └── run.py
```

Experiment-specific code stays with its experiment. Shared calibration and dataset infrastructure lives under `experiments/common/` rather than a generic `utils/` bucket.

## Fixed-wait experiment

Run:

```bash
python3 experiments/fixed_wait/run.py prepare
python3 experiments/fixed_wait/run.py plan
sudo python3 experiments/fixed_wait/run.py collect
```

Scientific parameters live in:

```text
experiments/fixed_wait/experiment.toml
```

The `prepare` step builds the release binary and creates or reuses compatible service-time calibration under `results/calibrations/`. Run `collect` only after preparation (standard profiling requires Linux perf permissions).

Fixed-wait campaign artifacts are stored under:

```text
results/fixed_wait/
```

Each campaign directory contains the SQLite database, randomized plan, manifest, copied experiment configuration, calibration artifact, dataset identity, environment metadata, logs, and analysis outputs.

Resume an interrupted campaign (the original source commit and release binary must still match):

```bash
sudo python3 experiments/fixed_wait/run.py resume --campaign results/fixed_wait/FIXED-WAIT-CAMPAIGN
```

The runner validates completed conditions and retries any invocation committed to SQLite but missing from the manifest.

Re-run analysis without re-running measurements:

```bash
python3 experiments/fixed_wait/analysis.py results/fixed_wait/FIXED-WAIT-CAMPAIGN
python3 experiments/fixed_wait/diagnostics.py results/fixed_wait/FIXED-WAIT-CAMPAIGN
```

## Maximum-throughput reference

Run:

```bash
python3 experiments/max_throughput/run.py prepare
python3 experiments/max_throughput/run.py collect
```

Its parameters live in:

```text
experiments/max_throughput/experiment.toml
```

Results are stored under `results/max_throughput/`.

## Manual calibration

Normal fixed-wait runs calibrate automatically. A standalone calibration can still be created with:

```bash
./scripts/calibrate_service_time.sh
```

Calibration compatibility can be inspected directly with:

```bash
python3 experiments/common/validate_calibration.py --calibration PATH/TO/calibration.json
```

## Development checks

```bash
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

Python orchestration and analysis scripts use only the standard library unless otherwise documented.

Raw run-level observations are retained. Analysis does not automatically remove statistical outliers or assume Gaussian sampling distributions. Deep-profile runs are analyzed separately by perf pass rather than pooled together.
