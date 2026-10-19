# Queue-topology randomized-block replication

This campaign is a **confirmation experiment**, not a new wait-policy search.
The TOML file is the source of truth. The six experimental conditions are
three schedulers (Hot-first, shared-queue Threadance, Rayon) crossed with two
bursty arrival rates. Each condition occurs once per randomized block and
receives one warm-up plus two measured runs. Each block uses a new burst seed;
the seed is shared between schedulers at the same rate.

## Usage

Work on branch `experiment/segmented-gate-ring` with the sibling
`../threadance` crate available (as required by the harness Cargo path
dependency). Ensure the Threadance crate matches the intended revision.

First, as a normal user:

```sh
python experiments/queue_topology/run.py plan
python experiments/queue_topology/run.py prepare
```

Preparation builds the release harness and generates/reuses the deterministic
1000-WU BLS dataset. It records a separate non-profiled preparation run in
`results/queue_topology/preparation.sqlite3`, outside the campaign.

Then execute the campaign. On hosts where `perf stat` requires privileges:

```sh
sudo python experiments/queue_topology/run.py collect
```

**Do not use sudo for prepare** (Rustup/Cargo environment). Each campaign has
a unique output directory under `results/queue_topology/`. Running under
sudo can create root-owned files; adjust ownership afterward if needed.

Analysis is separate and unprivileged:

```sh
python experiments/queue_topology/analysis.py results/queue_topology/<campaign-directory>
```

The output includes `experiment.toml`, `plan.csv`, `manifest.csv`,
`benchmarks.sqlite3`, `dataset_identity.json`, `environment.json`, and
per-condition logs. Analysis writes `analysis_raw_runs.csv`,
`block_summaries.csv`, and `paired_effects.csv`.

## Interpretation and limitations

- Primary metric: perf `task-clock` per completed work unit.
- Secondary metrics: cycles/WU, instructions/WU, context switches/WU,
  migrations/WU, elapsed time, and throughput.
- Inference uses **20 paired block differences**, not 40 independent runs.
  Two runs per block/condition are averaged before computing the difference.
  The reported confidence interval is the percentile bootstrap over paired
  block differences (10,000 resamples).
- `queue_capacity=8` matches the physical Hot-first ring size at four workers
  and backlog two. Rayon has no equivalent bounded queue, so this is not a
  complete queue-capacity equivalence.
- `settle_seconds` is a fixed delay between conditions, **not** an active
  thermal/frequency stability monitor.
- Perf quality (missing events, low percent-running, etc.) needs separate
  inspection before final conclusions.
- The campaign is a replication of the earlier 6000-WU pilot under a
  **different workload size**. Agreement is informative but not exact
  same-condition reproducibility.
- The run script does not pin CPU frequency or control background services;
  order randomization helps mitigate time-related confounding, not eliminate it.
