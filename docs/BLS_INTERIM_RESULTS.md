# Interim Results — BLS Thread-Pool Benchmarking

*Draft for the Master's thesis, 1 November 2026. This document separates measured results from interpretation and identifies unfinished analyses. All capacities here are finite-batch completion throughput rather than validated indefinitely sustainable rates.*

## Research objective and test platform

The study investigates how scheduler topology and waiting/waking strategy affect the performance and CPU resource cost of short-lived CPU-bound jobs. The host is an AMD Ryzen 7 5700X (8 physical cores, 16 logical threads), running Linux. The same deterministic dataset contains 2,500 BLS aggregate-verification work units; each certificate uses 2,000 validators and 1,600 signers. The experimental worker counts were 2, 4, 8 and 16. The last setting uses simultaneous multithreading, so it does not correspond to 16 physical cores.

The scheduler configurations were Rayon, Bevy TaskPool, Threadance MPMC with zero-spin parking, Threadance MPMC with Agave backoff, and two hot-first RTRB variants with backlog 1 and 2. The all-at-once throughput comparison included Bevy; the scheduled-load comparison used the other five configurations. Scheduler configurations were randomized within blocks. Every experiment retained individual runs and the original arrival schedule.

## Why the saturation experiment was repeated

The first throughput comparison used one unprofiled warmup per process. A separate Rayon run-order experiment established a reproducible slower first measured run at 8 and 16 workers. A subsequent randomized study varied 1, 2, 4 and 8 complete unprofiled warmups. At 8 workers, the first measured throughput increased from 4,758.40 work units/s after one warmup to 4,920.75 after two; at 16 workers, from 5,109.43 to 5,291.55. The paired second-versus-first throughput difference fell from +3.056% (95% CI [+2.597%, +3.560%]) to -0.272% (95% CI [-0.514%, -0.090%]) at 8 workers, and from +3.457% (95% CI [+2.644%, +3.941%]) to -0.119% (95% CI [-0.258%, +0.051%]) at 16 workers. The 4-worker control showed no comparable discontinuity.

The six-scheduler comparison was therefore acquired again with **four unprofiled warmups, three measured runs, and 30 independent randomized blocks per worker count**. This yielded 2,160 physical measured runs (720 process invocations). The marked 3–4% first-run effect was no longer observed, though some individual scheduler/worker settings showed smaller systematic changes. Four warmups were not independently proven optimal for every scheduler.

## Measured finite-batch throughput

The table reports the mean across the three measured positions and 30 randomized blocks, in work units/s. The raw paired confidence intervals are in each campaign's `paired_effects.csv`.

| Scheduler | 2 workers | 4 workers | 8 workers | 16 workers |
|---|---:|---:|---:|---:|
| Rayon | 1301.2 | 2601.2 | 4904.6 | 5283.3 |
| Bevy | 1301.5 | 2601.3 | 4904.3 | 5282.1 |
| Threadance MPMC, spin 0 | 1300.1 | 2595.7 | 4889.4 | 5286.7 |
| Threadance MPMC, Agave backoff | 1300.1 | 2595.2 | 4892.5 | 5284.1 |
| Threadance RTRB, backlog 1 | 1295.6 | 2579.4 | 4867.6 | 5215.4 |
| Threadance RTRB, backlog 2 | 1296.0 | 2584.7 | 4865.2 | 5217.0 |

At 8 workers, the paired throughput differences relative to Rayon were -0.31% (95% CI [-0.47%, -0.16%]) for MPMC spin 0; -0.25% ([-0.38%, -0.10%]) for MPMC Agave; -0.75% ([-0.94%, -0.58%]) for RTRB backlog 1; and -0.80% ([-1.08%, -0.55%]) for backlog 2. At 16 workers, MPMC spin 0 was +0.06% ([+0.01%, +0.12%]) while the two RTRB variants were approximately 1.25–1.28% slower. Bevy's throughput intervals relative to Rayon included zero at all four worker counts. These small differences are specific to the measured finite-batch setup.

## CPU resource cost

The study measures `perf task-clock` per completed work unit (CPU execution time accumulated across observed threads), cycles per work unit, branch misses per work unit, and context switches. Lower task-clock/WU indicates less measured CPU time per completed task; it is not a direct measurement of end-to-end latency.

In the saturation rerun, at 8 workers Threadance MPMC spin 0 required +1.27% task-clock/WU relative to Rayon (paired 95% CI [+0.98%, +1.61%]); MPMC Agave +1.08% ([+0.86%, +1.34%]); RTRB backlog 1 +1.45% ([+1.16%, +1.77%]); and backlog 2 +1.52% ([+1.14%, +1.95%]).

The new saturation run also collected unrestricted `context-switches` and `cpu-migrations` event names. At 8 workers, the average context switches per work unit were approximately 0.0127 for Rayon, 0.824 for MPMC spin 0, 0.838 for MPMC Agave, 0.911 for RTRB backlog 1 and 0.908 for backlog 2. These differences do not identify whether a switch was caused by worker waiting, producer backpressure, or other operating-system scheduling events. The earlier campaign's `context-switches:u=0` data were invalid for kernel scheduler analysis and must not be reused as real zero counts.

## Relative offered-load analysis

For each scheduler `s`, define the relative offered load as

    rho_s = scheduled_arrival_rate / measured_batch_capacity_s

and normalized achieved throughput as

    achieved_throughput_s / measured_batch_capacity_s.

The numerator for achieved throughput is the observed finite-window completed work units divided by elapsed time. These ratios are **not** physical CPU utilization and must not be presented as estimates of sustainable queueing-system load without further evidence.

An earlier acquisition comprises 48 campaigns: 2/4/8/16 workers, fixed and bursty arrivals, six requested rate levels originally selected relative to provisional Rayon batch throughput (25%, 50%, 75%, 93.75%, 110%, 200%) and five schedulers. Each combination has 20 randomized blocks and two measured repetitions. Those old absolute arrival schedules are unchanged. We now *re-express* each observed rate against the new scheduler-specific capacity denominators. This is an exploratory cross-acquisition normalization: old paced runs used one warmup, whereas new capacity runs used four. The different schedulers' points on the same relative X coordinate need not represent the same absolute number of arriving jobs per second.

The exploratory plots for 2-worker bursty arrivals show higher task-clock/WU at low relative offered load and lower values as load approaches and exceeds the batch-throughput reference. RTRB backlog 2 has the lowest descriptive CPU cost at the lowest measured load, but differences must be evaluated using block-paired contrasts at matched absolute arrival rates (the separate `vs-rayon` figures), not visual overlap of independently estimated confidence intervals. The six offered-load settings are distinct experimental conditions: the plots should show **unconnected markers and pointwise 95% confidence intervals**, not a continuously inferred curve, polynomial, or fitted saturation law.

## Statistical methodology

Runs are retained individually and the original randomized blocks are the resampling units. Repeated measured positions within a process are summarized at the block/configuration level before 95% block-bootstrap intervals are computed. Direct comparisons against Rayon use within-block pairing at the same arrival rate. For normalized throughput, the independent uncertainty of the scheduler-specific capacity estimate is propagated into the displayed intervals. Distribution shape, outliers and possible multimodality should be examined from raw observations rather than assumed Gaussian; descriptive smooth curves are not evidence of a parametric model.

## Limitations and interpretation

- One CPU platform: the measurements cannot be extrapolated quantitatively to 32–64 physical cores or different NUMA/cache/SMT structures.
- Batch throughput is a finite-workload metric, not independently established sustainable throughput.
- The older paced campaign used one warmup, has variable task count per arrival rate and may contain invalid user-only scheduler-software counters. Use its CPU and timing comparisons provisionally until acquisition protocol effects have been reviewed.
- The measured drain interval starts at the final submission call and should not automatically be interpreted as worker-only queue-drain time.
- Bounded queues can delay the producer. In the tested implementation, MPMC `q64` represents 64 queued tasks, whereas RTRB backlog 1 or 2 corresponds to workers × backlog queued slots, so the configuration differences cannot be attributed only to waiting policy.
- The reason for the original first-run effect and the causes of context switches have not been isolated experimentally.

## Next stage for the thesis

The core next step is **reporting**, not another exhaustive acquisition. Select the most informative CPU-cost and matched-arrival paired-effect plots for the main text, use the saturated-throughput rerun to explain reference normalization, and move exhaustive plots, raw data and protocol details to an appendix. Report measurable regime changes without asserting an untested polynomial or universal scheduler-selection rule.

Source definitions: `experiments/studies/bls_saturation_recheck.toml`, `experiments/studies/bls_normalized_load.py`; local analyses: `results/studies/bls-saturation-recheck/campaigns/`, `results/studies/bls-normalized-load/campaigns/` and `results/studies/bls-capacity-normalized/clear-plots/`.
