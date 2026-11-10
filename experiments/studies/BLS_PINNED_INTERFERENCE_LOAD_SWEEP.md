# BLS pinned interference: offered-load sweep with 200 ms arrival windows

## Why this branch

This branch derives from `feature/bls-pinned-interference-controls`. The
original pinned stressed experiment is unchanged. We only collect **additional
fixed/bursty offered-load points with background interference**. There is **no
new clean run**, no new all-at-once run, and no duplicate 900/1500 WU/s point.
These additional measurements can extend the stressed chapter's CPU-time/WU
versus offered-load curves.

### Why work_units varies

The Rust harness computes the scheduled arrival window as

`T = (work_units - 1) / arrival_rate`.

Each new campaign therefore sets

`work_units = ceil(0.200 * arrival_rate_wu_s) + 1`

to guarantee a scheduled window of at least 200 ms. The bursty generator
normalizes Weibull (k=0.5) gaps to the same exact scheduled endpoint; actual
producer lateness is a separate data-quality diagnostic. The dataset retains
the same 2500 cached BLS certificates, with 2000 validators, 1600 signers,
one certificate per work unit and seed 1.

### Offered-load reference

All rates use **frozen measured Rayon 2500-WU all-at-once capacities under
the existing pinned interference setup**, taken from the `cpu-cache`
block summaries of the previous 2026-11-05 campaigns:

| Workers | Measured reference capacity (WU/s) |
| ---: | ---: |
| 2 | 1079.632944 |
| 4 | 2141.948921 |
| 8 | 4012.266937 |
| 16 | 4658.680879 |

`arrival_rate = round_nearest_integer(reference_capacity * target_load_pct / 100)`.
This defines **nominal offered load relative to an observed reference**,
not measured utilization and not an exact per-scheduler capacity fraction.
Rates are identical across the seven schedulers at each worker count.

| Workers | Target load (%) | Rates (WU/s) | Work units |
| ---: | --- | --- | --- |
| 2 | 25, 40, 45, 50, 65, 80 | 270, 432, 486, 540, 702, 864 | 55, 88, 99, 109, 142, 174 |
| 4 | 25, 40, 50, 65, 80 | 535, 857, 1071, 1392, 1714 | 108, 173, 216, 280, 344 |
| 8 | 25, 40, 50, 65, 80 | 1003, 1605, 2006, 2608, 3210 | 202, 322, 403, 523, 643 |
| 16 | 25, 40, 50, 65, 80 | 1165, 1863, 2329, 3028, 3727 | 234, 374, 467, 607, 747 |

The new series only reaches 80% nominal stressed capacity to avoid
unstable overload at the investigated points. It is a deliberate bounded
sweep, not a saturated-throughput measurement.

### Acquisition

- `fixed-arrivals` and `bursty-arrivals` at each rate.
- Seven *unchanged* scheduler configurations from
  `bls_pinned_control_template.toml`, including MPMC 0/40 µs.
- Pinning from `experiments/studies/cpu_affinity.toml` and the exact
  original background workload from `cache_interference.toml`.
- 20 randomized blocks × 4 warmups × 2 measured repetitions.
- `cpu-cache` PMU pass only; keeps task-clock, cycles, instructions,
  cache misses and context switches. The earlier 900/1500 WU/s anchors
  also contain the identical `cpu-cache` pass.
- Each rate and arrival mode has its **own campaign** because work units
  are necessarily rate-specific. Scheduler conditions are randomized
  within blocks, and campaign order is shuffled deterministically.
- The new collection is isolated under
  `results/studies/bls-pinned-interference-load-sweep/campaigns`.
  Existing pinned clean/stressed datasets and worker-cubic experiments
  remain untouched.

### Run

From repository root, with the new branch checked out:

```bash
git switch experiment/bls-pinned-interference-load-sweep
git pull
python3 -m unittest tests.test_bls_pinned_interference_load_sweep
cargo fmt --all -- --check
cargo test --workspace
sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.check_perf_scope

# Dry run. Print exact rates, WU and windows; no benchmarks or file writes.
python3 -m experiments.studies.run_bls_pinned_interference_load_sweep --check

# Small starting slice: two and four workers, stressed only.
set -o pipefail
systemd-inhibit --what=sleep:idle --why='BLS pinned stressed load sweep' \
  python3 -m experiments.studies.run_bls_pinned_interference_load_sweep \
  --run --workers 2 4 2>&1 | tee -a bls-pinned-stressed-load.log

# Continue with eight and optionally sixteen workers.
systemd-inhibit --what=sleep:idle --why='BLS pinned stressed load sweep' \
  python3 -m experiments.studies.run_bls_pinned_interference_load_sweep \
  --run --workers 8 16 2>&1 | tee -a bls-pinned-stressed-load.log
```

Run as the normal user (not sudo); the stressor is enabled automatically.
The runner checks a clean tracked Git tree, perf scope and PMU completeness,
supports resuming exact campaign definitions, and performs the standard
per-campaign analysis. Do not alter the source or experiment definitions
while an acquisition is in progress.

| Workers | Campaigns | Invocations | Measured runs | Warmup runs |
| --- | ---: | ---: | ---: | ---: |
| 2 + 4 | 22 | 3,080 | 6,160 | 12,320 |
| 2 + 4 + 8 | 32 | 4,480 | 8,960 | 17,920 |
| 2 + 4 + 8 + 16 | 42 | 5,880 | 11,760 | 23,520 |

## How to interpret the combined dataset

The old stressed campaigns at 900/1500 WU/s used **2500 work units** and
much longer arrival windows. The new points have **~200 ms windows**.
The stressor, pinning, BLS task definition and `cpu-cache` counters match,
but run length differs. Consequently, the old points are useful contextual
anchors, **not automatically exchangeable observations on a single fitted
curve**. Inspect window length and start-up/drain overhead; consider
showing the old anchors with a distinct marker rather than fitting all
points together unconditionally. This experiment adds no matched clean
offered-load curve, so clean-versus-stressed comparisons remain limited
to previously measured common conditions.
