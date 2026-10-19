# Experiments

Every randomized-block campaign uses the common engine in
`experiments/common/campaign.py`, with a TOML file as the source of truth.
Workloads supported: BLS aggregate verify and compute-heavy.

## Workflow

From the repository root (do not use sudo for build/preparation):

```sh
python experiments/compute_heavy/run.py prepare
python experiments/compute_heavy/run.py plan
sudo python experiments/compute_heavy/run.py collect
python experiments/compute_heavy/analysis.py results/compute_heavy/<campaign-directory>
```

Replace `compute_heavy` with `fixed_wait` or `queue_topology` to use
their TOML definitions. To recover an interrupted campaign:

```sh
sudo python experiments/compute_heavy/run.py resume --campaign results/compute_heavy/<campaign-directory>
```

Resume validates the archived TOML, randomized plan, completed database records,
Git revision, and release binary hash. Unmanifested orphaned invocations within
that campaign are deleted and repeated; other completed results are preserved. The `plan` command for `rho`-based campaigns
requires `prepare` to have created/reused calibration first. For explicit
`arrival_rates`, plan needs no calibration.

The runner creates one SQLite database per campaign and archives the TOML,
randomized `plan.csv`, incremental `manifest.csv`, `environment.json`,
logs, and (on analysis) raw-run, block-summary, and paired-effect CSVs.

Each experimental condition is run once per block with one warm-up and two
measured runs by default. Conditions are shuffled with a reproducible seed
in every block. Measurements are grouped into block-level means, then
compared to the reference configuration within each block. Confidence
intervals are 95% percentile bootstrap intervals of the paired differences.
With deep profiling, each independent perf pass is summarized and compared
separately; metrics from different passes are never mixed.

## TOML

`[experiment]` selects a workload and profile;
`[workload]` defines task count, worker count, and BLS or matrix dimensions;
`[[configurations]]` declares scheduler, wait policy, spin duration and
queue topology parameters (a `spin_us` list expands into multiple conditions);
`[delivery]` declares fixed/steady/variable/bursty/all-at-once delivery
and either `rho` or explicit `arrival_rates`;
`[repetitions]` defines block/warmup/measurement counts;
`[randomization]` defines the seed;
`[analysis]` selects reference configuration and bootstrap settings;
`[artifacts]` sets output paths.

`rho` is converted to a per-second arrival rate using scheduler-free
single-thread work-unit service time: arrival_rate = rho * workers * 1e6 / service_time_us.
BLS calibrations are cached in results/calibrations; compute-heavy uses
the generic Rust service_time binary and stores raw CSV and JSON. Explicit
arrival rates require no calibration but should be justified as pilot values.

The settle interval is a fixed pause and is **not** a monitored stability
criterion. Python checks database row counts, completion status, and
task-clock presence for standard profiling, but cannot guarantee external
thermal/frequency stability. Check actual arrivals, perf quality, plots,
and distribution anomalies before drawing conclusions.

Archived older runner implementation remains in git history; ongoing
changes should target the common engine rather than experiment-specific
copies.
