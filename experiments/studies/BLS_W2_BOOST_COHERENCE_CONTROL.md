# BLS w2 CPU boost / cache-sharing control

**Question:** Why do MPMC Spin(0) and RTRB backlog 2 show essentially
equal retired cycles/WU but 12–15% different CPU time/WU at 50% fixed load?

**Mechanisms that must remain distinct:**
1. Hot-first uses predominantly one worker; MPMC distributes work across two.
2. A one-core-heavy vs two-core-heavy workload can have different CPU boost.
3. The completion counter (`Completion.remaining.fetch_sub(AcqRel)`) is
   modified by every completed work unit. With both workers active it could
   experience ownership transfers. MPMC queue coordination is another
   possible cache-line-contention source.
4. Same-cache-line *reads* do not themselves imply invalidation; and ordinary
   cache-miss counters are not a direct false-sharing or remote-HITM detector.

## Experiment

Identical BLS dataset, 256 work units, 647 fixed arrivals/s, 2 workers,
MPMC Spin(0) and RTRB backlog 2. Scheduler order randomised within each
of 40 blocks (20 boost-on, 20 boost-off); phases ordered:

ON OFF OFF ON ON OFF OFF ON.

Five blocks per phase; each scheduler measured twice per each of two
distinct PMU passes. **160 physical benchmark runs**, not counting
preparation or warmups. Per-TID PMU raw records and run timings are archived.
Power-mode metadata, governor and policy frequency bounds are recorded before
and after each phase. Each phase is audited, and a stand-alone symmetric
statistical report is written. Data is not pooled across PMU passes.

The sysfs boost switch is an *operating mode*, not a fixed-frequency guarantee.
Frequency is inferred using cycles/task-clock, not asserted from sysfs.
A causal boost effect requires a stable on/off contrast after checking actual
frequency behavior and the existence of substantial workload differences.

This experiment **does not isolate cache-line bouncing**: differences in
worker activity, PMU event counts and effective frequency are observational.
A follow-up causal experiment would require modifying layout of the shared
completion counter / queue metadata, adding instrumentation for task-worker
routing, and testing cross-core vs same-core SMT placement under matched
frequency. Do not report coherence as proven from cache-misses alone.

## Host setup and one-block smoke

From repository root, with no other bench running:

```bash
git fetch origin
git switch --track origin/experiment/bls-w2-boost-coherence-control
cargo fmt --all -- --check
cargo test -p threadance-harness
python3 -m unittest tests.test_bls_w2_boost_control
python3 -m experiments.studies.run_bls_w2_boost_control --check
sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.run_bls_w2_boost_control --phase 1 --smoke
```

Smoke requires boost ON (sysfs value 1). Run **only if already enabled**;
otherwise use the guarded full launcher, which manages the setting.

## Full acquisition (after smoke validation)

```bash
bash scripts/run_bls_w2_boost_control.sh
```

The launcher restores the original `/sys/devices/system/cpu/cpufreq/boost`
setting on normal exit or shell interruption; it only changes that sysfs node,
and does not change governors or frequency limits. The repository runner
refuses power-setting mismatches, uncommitted tracked files, missing counters,
PMU multiplexing, incomplete blocks or changes to governors/frequency policy
bounds during a phase. If the sysfs node does not exist, the launcher refuses
to run. Check the final boost state manually after any power loss or SIGKILL.

Keep the generated phase directories, SQLite files, `power_before.json`,
`power_after.json`, raw per-thread perf, and console log. Compare statistics
by phase and boost mode; do not equate 20 blocks to 160 independent
randomized blocks.

## Further coherence isolation

For a convincing coherence claim, add a separate controlled modification
that removes shared writes (per-worker local completion counts plus
single coordinator aggregation), and test both layouts under identical
worker mapping, CPU affinity, PMU pass and boost configuration. Separately
inspect MPMC queue lines; changing completion logic alone does not eliminate
shared MPMC queue state. Benchmark instrumentation must be identical across
scheduler conditions.
