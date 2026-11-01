# Final BLS scheduled-load rerun — two PMU passes and Bevy

This study replaces the **acquisition protocol** of the 48 archived normalized-load campaigns, but does not overwrite them. The source for each new campaign is the corresponding archived `experiment.toml`; the original scheduled arrival rate, number of tasks, dataset seed, 20 randomized blocks, fixed/bursty arrival mode, queue settings and task prefix are retained. Add Bevy to Rayon and four Threadance configurations: MPMC spin(0), MPMC AgaveBackoff, RTRB backlog 1, RTRB backlog 2.

## Repeat policy

- 4 complete *unprofiled* warmups before perf measurement, per scheduler/process.
- Two **separate** performance counter passes named `cpu-cache` and `l1-branches`, taken from `experiments/studies/bls_advanced_perf.toml`.
- 2 physically measured runs per pass = **4 measured BLS runs per configuration and randomized block**, not two runs with all counters at once. Each physical run completes the same work-unit prefix.
- The perf pass order is swapped in even-numbered blocks to address order effects. A fresh process and pool are created for every scheduled configuration/block. Counters from the two different passes are *not* pooled into one observation.
- The PMU audit requires non-null measurements, a successful perf exit, no reported multiplexing below 99.5%, unrestricted context-switch/migration events, and the expected event names.
- **48 campaigns x 6 schedulers x 20 blocks = 5,760 process invocations; 23,040 warmup runs; 23,040 physically profiled measured runs.** All new outputs use `results/studies/bls-normalized-deep-final/`.

The separate already completed four-warmup all-at-once throughput recheck remains the finite-batch reference used to re-express relative offered loads. Do **not** rerun its 2,160 measurements solely because this scheduled-load acquisition now uses two perf profiles.

## Execution

Work from the committed study branch in a clean working tree. As a normal (non-root) user:

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m unittest tests.test_bls_normalized_deep_final
python3 -m experiments.studies.run_bls_normalized_deep_final --check
python3 -m experiments.studies.run_bls_normalized_deep_final --smoke
```

The short smoke checks *actual custom PMU passes*, including Bevy, task counters and the intended profiling workflow. If the kernel or PMU does not support an event, fix the configuration **before** full data collection. The smoke dataset and SQLite results are stored in a separate folder.

Then, after closing resource-intensive applications:

```bash
set -o pipefail
systemd-inhibit --what=sleep:idle --why="Final BLS deep load study" \
  python3 -m experiments.studies.run_bls_normalized_deep_final --run \
  2>&1 | tee -a bls-normalized-deep-final.log
```

The runner is sequential, uses a lock, verifies each completed campaign's stored SQLite data and profiling counters, and supports safe resume of matching incomplete campaigns. Do not change tracked source or the Threadance crate while a campaign is in progress.

## Reporting notes

Use `paired_effects.csv` for within-block inference and `block_summaries.csv` / `analysis_raw_runs.csv` to retain each profile and run position. Rebuild normalized/absolute CPU-cost and throughput figures using **the new paced directory** rather than the old 48-campaign data. The existing `plot_bls_capacity_normalized.py` is presently tailored to `profile=standard` and must be adapted to compare *within one selected pass*; it must not arbitrarily combine separate CPU-cache and L1-branches runs. Include Bevy as a sixth configuration in the new plots.

The data are per-process finite-workload measurements from one 8-core/16-thread CPU, not proof of a universal scaling law. Producer backpressure may delay actual arrivals, particularly at 110–200% offered load. The normalized X-axis uses finite-batch completion throughput, **not** steady-state capacity. Preserve absolute scheduled arrival rate for matched-input comparisons. Confidence intervals should resample the independent randomized blocks, retaining repeated measurements within the relevant profiling pass; use unconnected experimental points and 95% CI bars rather than fitted curves.
