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

Worker counts can be selected from 2, 4, 6, 8, 10, 12, 14, 16; the **default plan includes all eight worker counts**. Both BLS aggregate verification and `compute-heavy` (8×8 matrix multiplications, by default **4096 operations per WU**) are supported. This study uses a separate **4096-pair immutable input pool (~4 MiB)** that is reused across work units, unlike the legacy unique-input streaming workload. Treat the two compute implementations as different memory/cache regimes. Fixed and Weibull bursty (shape 0.5) arrivals are separate campaigns.

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

## Compute-heavy memory, pacing and calibration

In the legacy workload every individual matrix multiplication used a separate 1-KiB input pair. At 4096 operations/WU and ~130k WU/s, a 200-ms window would require roughly 6 GiB and ~8-microsecond arrival intervals. This is not a viable wait/wake-policy experiment under the existing pacer.

The **new study variant explicitly sets** `compute_input_pairs=4096`. Its `operations_per_work_unit=4096` produces the same amount of mathematical computation per work unit across all policies, while cyclically reusing a bounded immutable input array of ~4 MiB. This preserves the legacy behavior when the `--compute-input-pairs` argument is absent in other experiments. The bounded variant has different cache behavior, so **never pool or directly compare its measurements with the old streaming compute-heavy workload**.

The study rejects a compute reference whose maximum offered rate exceeds 10,000 WU/s (mean interval <100 µs) rather than silently accepting unreliable short sleeps. The actual submission-lateness distribution must still be inspected after collection. If the check fails, increase `--ops-per-wu`, recalibrate, and run with the same setting.

Old calibration files remain untouched. New calibrations are stored in a **Git-revision-specific subdirectory** to avoid mixing datasets and binaries across code changes; both BLS and bounded compute-heavy must be recalibrated after changing HEAD. Calibration is performed outside the measurement campaigns.

## Commands (after this revision)

```bash
cd ~/adaptive-threadpool-thesis
git pull --ff-only
python3 -m unittest discover -s tests -p test_mpmc_wait_policy_study.py
python3 -m experiments.studies.run_mpmc_wait_policy_study --calibrate
python3 -m experiments.studies.run_mpmc_wait_policy_study --check
# Run without an arbitrary time cutoff:
set -o pipefail
systemd-inhibit --what=sleep --why="MPMC wait-policy study" \
  python3 -m experiments.studies.run_mpmc_wait_policy_study --run \
  2>&1 | tee mpmc-wait-policy.log
```

## Scope and analysis

The purpose is not to select a winner by CPU time alone. First assess saturation/throughput and submission lateness. Then compare CPU time/WU, cycles/WU, context switches/WU, cache misses/WU, and drain time under matched load. Compute block means, block-bootstrap 95% CIs, and **within-block paired policy contrasts**. Keep arrival modes separate, and use leave-one-load-level-out validation before presenting a fitted polynomial as predictive.

The existing benchmark records producer/worker CPU time jointly and **does not measure per-task enqueue-to-start or completion latency**. End-to-end task latency claims require further instrumentation. The experiment varies wait/wake policy with fixed MPMC delivery; it does not isolate task-delivery mechanism differences or batch-size B and batch-arrival interval T from the original proposal.

## Practical limitations

The scheduled window is derived from N-1 inter-arrival gaps: `T=(N-1)/arrival_rate`. Arrival lateness should be audited, especially at high rates. No background stressor is enabled in this experiment. The runner uses the same CPU counters as earlier deep `cpu-cache` captures, but intentionally omits the additional L1/branch pass to stay within the night budget. A later targeted diagnostic can run the second pass on selected conditions.

This study is deliberately **resumable rather than guaranteed to finish overnight**; disk space, CPU temperature, calibration, PMU overhead and system scheduling overhead can make elapsed time exceed the nominal workload window.
