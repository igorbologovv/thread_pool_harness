# BLS CPU time versus worker count: independent cubic-model holdouts

## Research question

Does a cubic curve fitted at worker counts **2, 4, 8 and 16** predict CPU time
per work unit at **6, 10 and 12** workers, for both **fixed** and **bursty**
arrivals at *nominal 50% load*? The three validation worker counts must **not**
participate in estimating the polynomial. There are six scheduler configurations.

The earlier 50%-load data used different BLS batch lengths at different worker
counts, so their cubic curves are not a controlled predictive model. This new
study measures **all seven worker counts again**, always with the **same 1024
BLS work units**, same 2500-certificate cached dataset, workload seed, queue
parameters, warmup protocol and PMU passes. Old data are not overwritten.

## Acquisition design

| Parameter | Value |
|---|---|
| Training worker counts | 2, 4, 8, 16 |
| Holdouts (never fit) | **6, 10, 12** |
| Arrival modes | fixed-arrivals, bursty-arrivals |
| Nominal offered load | 50% of Rayon finite-batch 2500-WU capacity at each worker count |
| Paced work units | **1024 for every count and mode** |
| Dataset | 2500 certificates; validators=2000, signers=1600, seed=1 |
| Schedulers | Rayon, Bevy, MPMC Spin(0), MPMC AgaveBackoff, RTRB b1/b2 |
| Randomized blocks per worker/mode | 40 |
| Warmups per process invocation | 4 (unprofiled) |
| Measured repetitions | 2 in each of **two separate PMU passes** |
| PMU passes | `cpu-cache`, `l1-branches` from `bls_advanced_perf.toml` |
| Paced campaigns | 7 counts × 2 modes = **14** |
| Paced executions | 3360 process invocations, 13440 measured physical runs |
| Preliminary capacity calibration | 7 Rayon-only all-at-once 2500-WU campaigns, 30 blocks × 3 measured |

Capacity is freshly measured on the benchmark host using the same source
revision. Nominal 50% **does not** mean 50% hardware utilization. It is defined
as `offered_rate = 0.5 × measured Rayon all-at-once finite-batch throughput` for
each worker count; all six schedulers at a given worker count receive the same
arrival rate. The paced workload uses 1024 WU to preserve at least a 200 ms
scheduled arrival window at the calibrated rates. The reference calibration
and all paced runs are saved under separate output roots.

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

# Step 1: independently measure capacities at all seven worker counts.
set -o pipefail
systemd-inhibit --what=sleep:idle --why='BLS worker-count capacity reference' \
  python3 -m experiments.studies.run_bls_worker_cubic_holdout --capacity \
  2>&1 | tee -a bls-worker-cubic-capacity.log

# Step 2: collect all fixed/bursty CPU-time points with two PMU passes.
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

The evaluation uses only the `cpu-cache` pass to obtain **task-clock/WU** and
retains randomized blocks as independent resampling units. It averages the two
measured repeats within each block, constructs a cubic polynomial using the
means at 2/4/8/16 workers, and tests it at 6/10/12. It reports the signed
prediction error, relative error, observed 95% bootstrap CI, and a bootstrap
95% interval for the *prediction error* that propagates training uncertainty.
The validation results are written to:

`results/studies/bls-worker-cubic-holdout/validation/held_out_predictions.csv`

The extrapolation quality of the previously published curves is **not** proved
by this new within-study fit: the old curves came from different task-prefix
lengths and acquisition campaigns. Compare the old frozen curves separately,
and disclose their protocol mismatch. Interpret the cubic as a hardware- and
workload-specific empirical approximation if (and only if) its holdout errors
are convincingly small.

Raw SQLite and PMU measurements are retained for subsequent figures of cache
misses, context switches, cycles and branch misses. Do not merge observations
across the two distinct PMU passes as if they came from the same run.