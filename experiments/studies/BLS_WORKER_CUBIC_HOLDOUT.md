# BLS CPU time versus worker count: independent cubic-model holdouts

## Research question

Does a cubic curve fitted at worker counts **2, 4, 8 and 16** predict CPU time
per work unit at **6, 10 and 12** workers, for both **fixed** and **bursty**
arrivals at *nominal 50% load*? The three validation worker counts must **not**
participate in estimating the polynomial. There are six scheduler configurations.

The original variable BLS batch lengths were intentional: N = ceil(0.2 * arrival_rate) + 1
preserves at least 200 ms of scheduled arrivals as workers and offered rates increase.
**Do not force equal work-unit counts** for differently sized pools. This study
**reuses the original 2/4/8/16 training observations**, acquires new held-out
6/10/12 observations with the same sizing rule, and preserves the old data.

## Acquisition design

| Parameter | Value |
|---|---|
| Training worker counts | 2, 4, 8, 16 |
| Holdouts (never fit) | **6, 10, 12** |
| Arrival modes | fixed-arrivals, bursty-arrivals |
| Nominal offered load | 50% of Rayon finite-batch 2500-WU capacity at each worker count |
| Paced work units | **ceil(0.2 × arrival_rate) + 1**, per worker count; target ≥200 ms |
| Dataset | 2500 certificates; validators=2000, signers=1600, seed=1 |
| Schedulers | Rayon, Bevy, MPMC Spin(0), MPMC AgaveBackoff, RTRB b1/b2 |
| Randomized blocks per worker/mode | 40 |
| Warmups per process invocation | 4 (unprofiled) |
| Measured repetitions | 2 in each of **two separate PMU passes** |
| PMU passes | `cpu-cache`, `l1-branches` from `bls_advanced_perf.toml` |
| New paced campaigns | 3 counts × 2 modes = **6**; original 8 campaigns reused |
| New paced executions | **1440 process invocations, 5760 measured physical runs** |
| Capacity calibration | **3 new** Rayon-only campaigns for 6/10/12 (2500 WU, 30 blocks × 3); existing 2/4/8/16 rechecks reused |

New 6/10/12 capacity is measured on the benchmark host. For 2/4/8/16,
capacity is read from existing completed saturation-recheck campaigns. Nominal 50% **does not** mean 50% hardware utilization. It is defined
as `offered_rate = 0.5 × measured Rayon all-at-once finite-batch throughput` for
each worker count; all six schedulers at a given worker count receive the same
arrival rate. The paced workload length is chosen separately at each offered rate to preserve
at least a 200 ms scheduled arrival window. New calibration and paced runs are saved under separate output roots; archived
training and capacity campaigns remain unchanged. Note that the original
50%-load anchor rates were set using an earlier finite-batch capacity estimate;
check and report their exact ratios to the rechecked capacity before treating
training and validation points as identical nominal loads.

The worker counts include both the 8-physical-core region and SMT. Without CPU
pinning, this protocol cannot establish precisely which physical cores each
worker uses. The 10/12/16 results must not be interpreted as a universal SMT
scaling law. CPU frequency, host interference and separate campaign acquisition
remain potential confounders.

## How to run

From the **repository root** after checking out the study branch, keep the
benchmark machine idle and close browsers and other load-generating processes:

```bash
# Run local checks BEFORE pushing code or starting the experiment.
cargo fmt --all -- --check
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
python3 -m unittest tests.test_bls_worker_cubic_model tests.test_bls_worker_cubic_holdout

sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.check_perf_scope

# No writes, builds or measurements:
python3 -m experiments.studies.run_bls_worker_cubic_holdout --check

# Step 1: measure only missing capacities at 6/10/12; reuse archived 2/4/8/16.
set -o pipefail
systemd-inhibit --what=sleep:idle --why='BLS worker-count capacity reference' \
  python3 -m experiments.studies.run_bls_worker_cubic_holdout --capacity \
  2>&1 | tee -a bls-worker-cubic-capacity.log

# Step 2: collect fixed/bursty CPU-time holdouts at 6/10/12, two PMU passes.
systemd-inhibit --what=sleep:idle --why='BLS worker-count cubic holdout' \
  python3 -m experiments.studies.run_bls_worker_cubic_holdout --run \
  2>&1 | tee -a bls-worker-cubic-holdout.log

# Step 3: fit only 2/4/8/16, predict and score the held-out 6/10/12.
python3 -m experiments.studies.run_bls_worker_cubic_holdout --evaluate
```

Both collection stages are resumable using the *same commands*. Do not run the
harness with `sudo`; only the `sysctl` preflight needs privilege if access is
restricted. The runner rejects dirty tracked source, mismatched cached
experiment TOMLs, or an out-of-date capacity reference.

## Validation and reporting

The evaluation uses only the `cpu-cache` pass to obtain **task-clock/WU**, reads
old 20-block training observations for 2/4/8/16 and new 40-block held-out
observations for 6/10/12, and retains entire blocks as resampling units. It averages the two
measured repeats within each block, constructs a cubic polynomial using the
means at 2/4/8/16 workers, and tests it at 6/10/12. It reports the signed
prediction error, relative error, observed 95% bootstrap CI, and a bootstrap
95% interval for the *prediction error* that propagates training uncertainty.
The validation results are written to:

`results/studies/bls-worker-cubic-holdout/validation/held_out_predictions.csv`

The historical training curves are tested without refitting their means to
held-out worker counts. Document differences in acquisition time, run lengths,
and how 50% offered load was calibrated before interpreting errors. Interpret the cubic as a hardware- and
workload-specific empirical approximation if (and only if) its holdout errors
are convincingly small.

Raw SQLite and PMU measurements are retained for subsequent figures of cache
misses, context switches, cycles and branch misses. Do not merge observations
across the two distinct PMU passes as if they came from the same run.