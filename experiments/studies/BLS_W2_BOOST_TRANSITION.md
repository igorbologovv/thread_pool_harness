# BLS w2 boost-on transition (35–75%)

## Research question

At which offered load does RTRB backlog=2 start using the second worker,
and does that coincide with the collapse of its effective-frequency and
CPU-time advantage relative to MPMC spin=0?

**Hypothesis, not a proven mechanism:** Near 50% of two-worker reference
throughput, one boosted hot-first worker may approach its service capacity.
Beyond that point a second worker joins. This may change the available boost
and remove the CPU-time advantage.

The percentages represent **offered arrival rate divided by 1294 WU/s**,
not observed CPU occupancy. We do not claim an exact single-core saturation
point before measuring it.

## Protocol

- Two workers, 256 work units, fixed arrivals only; fixed deterministic BLS
  dataset prefix (2000 validators, 1600 signers per certificate).
- Conditions: MPMC spin=0 and RTRB backlog=2, with identical queue capacity,
  completion logic, and other workload parameters, inherited from the
  validated two-worker thread-perf study.
- CPU Boost must be ON. Neither launcher nor runner changes boost, governor,
  or frequency bounds. Policies and boost are checked before and after a cell.
- Loads: **35, 40, 45, 47.5, 50, 52.5, 55, 60, 65, 70, 75%**.
  Rates: 452.9, 517.6, 582.3, 614.65, 647, 679.35, 711.7, 776.4,
  841.1, 905.8, 970.5 work units per second.
- Two waves of 10 randomized blocks/load. Wave 1 uses a deterministic
  shuffled order of loads, wave 2 reverses that order. Scheduler order within
  blocks is randomized independently.
- Four warmups per invocation; two measured repeats per PMU pass, two
  separate PMU passes, with alternating pass order.
- **880 measured physical runs** = 11 loads x 2 waves x 10 blocks x
  2 schedulers x 2 passes x 2 repeats. Warmups are separate.

Per-run counters are strictly scoped to pacer and two worker TIDs, retained
with raw perf JSON and SQLite data. Worker identity comes from the TID map
and worker thread names; we never infer it from numeric TID order.

Main outcomes: CPU time/WU, cycles/WU, estimated effective GHz
(cycles / task-clock), continuous worker CPU times, second-worker active
fraction, cache misses, branch misses and context switches, and actual
submission lateness. Worker1 activity is defined as >1 ms task-clock per
run; alternative thresholds 0.1 and 5 ms are retained. **Worker activity is
not a direct count of completed tasks or wakeups.**

Block-level paired differences are computed within each load and PMU pass.
Wave-stratified block bootstrap (10,000 replicates) produces 95% CIs;
physical runs are not treated as independent randomized blocks.
Check individual wave results, drift, distribution shape/multimodality,
and scheduling lateness before causal interpretation.

## Commands (from repository root)

1. Fetch and check out the experiment branch (do not overwrite local work).
2. Run cargo fmt --all -- --check and cargo test -p threadance-harness.
3. Run python3 -m unittest tests.test_bls_w2_boost_transition.
4. Run python3 -m experiments.studies.run_bls_w2_boost_transition --check.
5. Run sudo sysctl -w kernel.perf_event_paranoid=1.
6. Verify cat /sys/devices/system/cpu/cpufreq/boost prints 1.
7. Run python3 -m experiments.studies.run_bls_w2_boost_transition --smoke.
8. Run bash scripts/run_bls_w2_boost_transition.sh.

Run under the normal user, not sudo. The smoke uses a distinct directory.
The launcher uses systemd-inhibit and writes a full log. The acquisition is
resumable by load/wave cell and refuses conflicting experiment definitions,
modified tracked code, incomplete perf records, changed CPU power policies,
and mismatched campaign metadata.

After 22 cells complete, analysis executes automatically; it can be rerun
using python3 -m experiments.studies.analyze_bls_w2_boost_transition --run.

Outputs are written under results/studies/bls-w2-boost-transition/analysis/:
raw_runs.csv, block_means.csv, summaries.csv, paired_differences.csv,
analysis_manifest.json, and PNG charts (if matplotlib is installed).

## Interpretation limits

Correlated second-worker activation, lower effective GHz and convergence in
CPU time are **not** proof that Precision Boost changed *because* of worker
activation: core affinity, thermal effects and power constraints remain.
Standard cache-miss counters do not directly isolate coherence transitions.
This experiment tests the transition signature, not false sharing.
