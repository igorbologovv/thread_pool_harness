# MPMC wait/wake-policy study — overnight collection

## Scientific purpose

Isolate the wait/wake strategy while holding the task-delivery mechanism constant.
All five policies instantiate Threadance with `CrossbeamMpmc` (queue capacity 64):

- `mpmc-sp0`: `SpinThenPark(0 µs)`
- `mpmc-sp10`: `SpinThenPark(10 µs)`
- `mpmc-sp40`: `SpinThenPark(40 µs)`
- `mpmc-sp100`: `SpinThenPark(100 µs)`
- `mpmc-agave`: `AgaveBackoff`

Do not interpret `AgaveBackoff` as a duration on the SpinThenPark axis.

Worker counts can be selected from 2, 4, 6, 8, 10, 12, 14, 16; the **default plan includes all eight worker counts**. Both BLS aggregate verification and `compute-heavy` (8×8 matrix multiplications, by default 256 independent operations per WU) are supported. Fixed and Weibull bursty (shape 0.5) arrivals are separate campaigns.

Load grid: **25, 30, 35, 40, 45, 50, 55, 60, 70, 80, 90, 95%**. The 5% grid is retained through the existing transition region; 10% steps at higher load save time. A calibrated saturated throughput for the *same workload and worker count* sets the offered-rate denominator; the denominator does **not** change between wait policies. Offered load is an arrival-rate design variable, not measured CPU utilization.

## Collection protocol

- 40 randomized blocks per campaign, with policies randomized within each block.
- Four warmups then two measured repeats per invocation; one `cpu-cache` perf pass.
- Metrics include elapsed/throughput, task-clock/WU, cycles/WU, cache-misses/WU and context-switches/WU.
- Clean machine: no deliberately generated cache interference; unpinned workers.
- Fixed work-unit computation, workload seed, queue capacity, workers, arrival rate and arrival schedule *within each comparison block*.
- Results are independent new cohorts; do not automatically pool with old MPMC data.
- One campaign per workload × worker count × arrival mode × load, with crash-safe resume and isolated output.
- Background software should be closed; keep thermal/power/perf scope settings constant.

## Commands

Run from the repository root as the normal user.

```bash
cd ~/adaptive-threadpool-thesis
git pull --ff-only
git status --short

python3 -m unittest discover -s tests -p test_mpmc_wait_policy_study.py
python3 -m experiments.studies.run_mpmc_wait_policy_study --check

# These 16 saturated references (two workloads × eight worker counts) are recorded separately, with the current
# git commit and release-binary SHA.
python3 -m experiments.studies.run_mpmc_wait_policy_study --calibrate

# IMPORTANT: inspect the printed work-unit counts, windows and memory bounds
# before committing to the overnight collection.
python3 -m experiments.studies.run_mpmc_wait_policy_study --check --workers 2 8

# About an 11-hour campaign-start budget; the currently running campaign
# is allowed to complete, so this is not an exact wall-time kill switch.
set -o pipefail
systemd-inhibit --what=sleep --why="MPMC wait-policy research" \
  python3 -m experiments.studies.run_mpmc_wait_policy_study \
  --run --budget-hours 11 \
  2>&1 | tee mpmc-wait-policy-overnight.log
```

On the following day, run the **identical** `--run` command to resume. Do not modify the study runner, Git revision, Rust compilation flags, task-size parameter or generated definitions while resuming. The runner validates archived experiment definitions and code/binary identities.

For selected counts, optionally narrow with e.g. `--workers 2 4 6 8` after running the default full calibration. Use `--workloads bls-aggregate-verify` or `--workloads compute-heavy` to focus one workload without changing the identities of existing campaigns. The full default matrix has **384 campaigns × 200 invocations = 76,800 invocations**, corresponding to **153,600 physical measured runs** and **307,200 warmups**. The 11-hour budget will probably complete only part of this plan; resume without changing the source revision. Partial completion is normal and safe.

## Compute-heavy memory guard

Each matrix operation stores roughly 1 KiB of matrix operands. With 256 operations/WU, one work unit represents about 256 KiB of operands. The default dataset budget is **1,024 MiB** and the target arrival window is 200 ms. If the calibrated saturated reference would exceed this memory budget, the window is shortened, but the runner rejects any configuration requiring less than 100 ms. There is **no silent memory-limit overrun**. Review memory availability before increasing `--max-compute-mib`; the current workload also has additional allocation/OS overhead. `--ops-per-wu` controls useful work per task and must be held fixed within a workload comparison.

When adjusting `--ops-per-wu`, use a new calibration with the same flag throughout `--check` and `--run`; if changes occur after some data are collected, treat them as separate experimental conditions rather than pooled replicates.

## Scope and analysis

The purpose is not to select a winner by CPU time alone. First assess saturation/throughput and submission lateness. Then compare CPU time/WU, cycles/WU, context switches/WU, cache misses/WU, and drain time under matched load. Compute block means, block-bootstrap 95% CIs, and **within-block paired policy contrasts**. Keep arrival modes separate, and use leave-one-load-level-out validation before presenting a fitted polynomial as predictive.

The existing benchmark records producer/worker CPU time jointly and **does not measure per-task enqueue-to-start or completion latency**. End-to-end task latency claims require further instrumentation. The experiment varies wait/wake policy with fixed MPMC delivery; it does not isolate task-delivery mechanism differences or batch-size B and batch-arrival interval T from the original proposal.

## Practical limitations

The scheduled window is derived from N-1 inter-arrival gaps: `T=(N-1)/arrival_rate`. Arrival lateness should be audited, especially at high rates. No background stressor is enabled in this experiment. The runner uses the same CPU counters as earlier deep `cpu-cache` captures, but intentionally omits the additional L1/branch pass to stay within the night budget. A later targeted diagnostic can run the second pass on selected conditions.

This study is deliberately **resumable rather than guaranteed to finish overnight**; disk space, CPU temperature, calibration, high memory usage and system scheduling overhead can make elapsed time exceed the nominal workload window.
