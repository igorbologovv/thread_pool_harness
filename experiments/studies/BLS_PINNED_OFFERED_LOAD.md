# Pinned BLS offered-load sweep: matched clean / interference arms

This is an **additional** acquisition study, independent of
`bls-worker-cubic-holdout-200ms`. It does **not** modify or refit that
study's frozen worker-count model.

## Scientific purpose

Measure CPU time/WU and perf counters as a function of arrival rate **at
fixed worker count** under two execution conditions: pinned workers and no
artificial interferer (clean), versus the same pinning with the existing
background CPU/cache interferer (interference).

Both arms use precisely the same BLS workload, arrival rates, seeds,
configuration set, randomized within-block plan, and PMU pass. Running
the two arms as separate campaigns does **not** establish randomized
assignment of interference or an independent causal effect of CPU pinning.

- Workers: 2, 4, 8; 16 available as an optional additional group.
- Seven configurations: Rayon, Bevy, MPMC Spin(0), MPMC Agave backoff,
  RTRB backlog 1/2, and MPMC Spin(40 µs).
- Arrivals: fixed and bursty, with identical per-worker absolute rates
  across clean/interference.
- Rates (WU/s):
  - w2: 270, 430, 540, 720, 900
  - w4: 600, 900, 1200, 1500, 1850
  - w8: 900, 1500, 2000, 2600, 3400
  - w16: 900, 1500, 2200, 3000, 3900
- Each measurement: **1024 BLS work units**, drawn from the existing
  2500-unit dataset. The smallest scheduled window at any selected
  arrival rate is at least 200 ms.
- 20 randomized blocks, 4 warmups, 2 measured repetitions, **one**
  `cpu-cache` PMU pass. This avoids the expense of the separate
  branch-pass collection while retaining task-clock, cycles,
  instructions, cache misses and context switches.
- Original pinned-control CPU affinity and stressor TOMLs are reused
  unchanged: `cpu_affinity.toml` and `cache_interference.toml`.
- Generated plans and results are isolated in new paths. Nothing is
  merged into the old controls or overwritten.

**Capacity and comparability:** Offered-load percentage must be
calculated after acquisition using a clearly stated capacity reference;
the present plan uses absolute rates. The older pinned-controls use
2500 work units, so the 900/1500 overlap is an *external consistency
check*, **not** a matched same-length numerical baseline. The clean
1024-WU arm collected by this study is the proper matched control for
the interference arm.

The 900-WU/s w2 point is near the stressed capacity, and producer
lateness/realized arrivals should be checked before treating it as
steady state. At 16 workers the producer also shares logical CPU 15
with a worker, as in the original study.

## Commands

On the benchmark host, on branch
`experiment/bls-worker-cubic-holdout`:

```bash
git pull
cargo fmt --all -- --check
cargo test --workspace
python3 -m unittest tests.test_bls_pinned_offered_load
sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.check_perf_scope

# No writes, builds, or measurements
python3 -m experiments.studies.run_bls_pinned_offered_load --check

# Initial smaller group: 2 and 4 workers
set -o pipefail
systemd-inhibit --what=sleep:idle --why='BLS pinned offered-load sweep' \
  python3 -m experiments.studies.run_bls_pinned_offered_load --run --workers 2 4 \
  2>&1 | tee -a bls-pinned-offered-load.log

# Follow with 8 and optionally 16 workers, without repeating 2 and 4.
systemd-inhibit --what=sleep:idle --why='BLS pinned offered-load sweep' \
  python3 -m experiments.studies.run_bls_pinned_offered_load --run --workers 8 \
  2>&1 | tee -a bls-pinned-offered-load.log

python3 -m experiments.studies.run_bls_pinned_offered_load --check --workers 16
```

The runner explicitly enables `--pin-workers` for **both** arms and
`--cache-interference` only for the stressed arm. It checks source
cleanliness and perf availability, collects campaigns sequentially,
supports resumption with the *same command*, verifies completed SQLite
counts and PMU event coverage, and generates per-campaign analysis.
Collection must be executed as the **normal user**, not with `sudo`.
Only the kernel perf sysctl command may need privilege.

Each worker/mode/arm combination is one campaign, with five arrival
rates and seven configurations randomized within each of 20 blocks:

| Worker groups | Campaigns | Invocations | Measured physical runs | Warmup runs |
| --- | ---: | ---: | ---: | ---: |
| 2 and 4 | 8 | 2,800 | 5,600 | 11,200 |
| 2, 4 and 8 | 12 | 4,200 | 8,400 | 16,800 |
| 2, 4, 8 and 16 | 16 | 5,600 | 11,200 | 22,400 |

This is a **new sweep**, not the old 2500-WU fixed/bursty pair and not
the cubic worker-count holdout. The two arms are separately collected;
confidence intervals for their differences must account for that.
Use block means as resampling units, report 95% CIs and arrival-lateness
diagnostics, and avoid extrapolating beyond the measured rates. These
measurements alone cannot establish a universal optimal spin duration
or an isolated effect of pinning.
