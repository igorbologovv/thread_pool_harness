# Two-worker fixed-arrival low-load BLS sweep (25–50%)

Companion to the existing `bls-transition-w2-sweep` (50–75%). This study measures the missing low-load points with the **same 256-work-unit task prefix**, six schedulers, PMU pass definitions, randomized-block count, warmup count, and repetitions as the prior transition run. It does not modify earlier data or overwrite previous campaigns.

- Workers: **2**
- Delivery mode: **fixed-arrivals only**
- Offered-load design labels: **25, 30, 35, 40, 45, 50%**
- Arrival rates, derived from the archived 50% = 647 WU/s reference: **323.5, 388.2, 452.9, 517.6, 582.3, 647.0 WU/s**
- Schedulers: Rayon, Bevy, MPMC Spin(0), MPMC AgaveBackoff, RTRB b1, RTRB b2
- **20 randomized blocks**, **4 warmups** per process invocation, **2 measured runs per pass**, **2 independent PMU passes**
- Totals: **6 campaigns**, **720 process invocations**, **2880 warmup runs** and **2880 physically measured runs**

The runner requires the 48 original normalized-load archived definitions in `results/studies/bls-normalized-load/campaigns/`, including the original 2-worker 50% fixed-arrival definition. It uses that source's dataset settings and absolute scheduled rate; the other original definitions are checked by the shared loader. The original dataset should already exist locally and be unchanged.

## Commands

From the repository root, checkout this branch and verify the data archive is present. Run as your normal user (not root):

```bash
git switch experiment/bls-transition-w2-lowload
python3 -m unittest tests.test_bls_transition_w2_lowload tests.test_bls_transition_w2_sweep
python3 -m experiments.studies.run_bls_transition_w2_lowload --check
```

Grant permissions for process-level perf counters in a separate terminal if needed:

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
```

With resource-intensive applications closed, collect sequentially and log:

```bash
set -o pipefail
systemd-inhibit --what=sleep:idle --why="BLS w2 low-load sweep" \
  python3 -m experiments.studies.run_bls_transition_w2_lowload --run \
  2>&1 | tee -a bls-transition-w2-lowload.log
```

To resume, use the **same command**. The runner checks the tracked working tree, PMU scope, existing campaign definition, SQLite rows and counters, and generates the per-campaign analysis. Outputs are stored under `results/studies/bls-transition-w2-lowload/campaigns/`.

For combined plots, keep data from this series labeled separately from the preceding 50–75% campaign, and report 95% CIs from block-level bootstrap samples. Both low- and high-load transition studies use 256 WU; the older normalized-load study used variable work-unit counts, so do not treat them as additional replicates. The load percentages are design labels relative to the archived two-worker reference throughput, **not** direct measurements of CPU utilization.

No benchmark measurements were performed as part of this code change.
