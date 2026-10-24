# Study planner (first iteration)

This layer generates bounded, deterministic **campaign definitions**, not measurements.
Existing `experiments/common/campaign.py` remains the execution and analysis engine.

From the repository root, first inspect the planned matrix:

```bash
python3 -m experiments.common.study --spec experiments/studies/pilot.toml --check
```

Then generate campaign definitions and a JSON study manifest:

```bash
python3 -m experiments.common.study --spec experiments/studies/pilot.toml --write
```

The generated TOMLs live under `experiments/generated/queue-pilot/`. For each
generated file, execute the existing workflow (as a normal user for prepare):

```bash
python3 -m experiments.common.campaign prepare --experiment experiments/generated/queue-pilot/FILE.toml
python3 -m experiments.common.campaign plan --experiment experiments/generated/queue-pilot/FILE.toml
sudo python3 -m experiments.common.campaign collect --experiment experiments/generated/queue-pilot/FILE.toml
```

Use `analyze --campaign PATH` for statistical output after collection. The
planner does not invoke `sudo`, build binaries, or schedule background runs.

The TOML study config declares dimensions under `[axes]` and execution
budgets under `[study]`. Compatible combinations expand into one campaign
per worker count, work-unit count, granularity, and delivery mode, with queue
capacities and per-worker Hot-first backlog values expanded within each
campaign. Rayon/Bevy are not duplicated for irrelevant queue axes. Invalid
Hot-first capacity/backlog combinations are rejected and listed. A stable
unexpanded reference configuration such as Rayon is required.

`max_invocations` is a hard limit on invocations, **not** a bound on elapsed
time. The manifest estimates the number of invocations and physical measured
runs (including deep passes); actual benchmark time is workload-dependent.
The `settle_floor_seconds` field includes only fixed settle delays and is
not a reliable estimate of full campaign runtime.

Current limitations: a study uses one workload template; only the listed axes
are supported; common analysis compares within individual campaigns. Cross-
campaign reports, plots, aggregate quality gates, and autonomous selection
of subsequent experiments are deliberately deferred. For `rho` experiments,
the generated plan preview requires the existing service-time calibration.

## Full scheduler-configuration pilot

```bash
python3 -m experiments.common.study --spec experiments/studies/full_pool_pilot.toml --check
python3 -m experiments.common.study --spec experiments/studies/full_pool_pilot.toml --write
```

The `full_pool_pilot.toml` specification includes Rayon, Bevy, and all three
harness-supported Threadance queue topologies: shared Crossbeam MPMC, custom
segmented Hot-first, and RTRB segmented Hot-first.

Wait-policy coverage is constrained to actual Threadance APIs:
- Crossbeam MPMC: native `crossbeam-wait`, `spin-then-park`, `agave-backoff`.
- Segmented Hot-first / RTRB: addressed `spin-then-park` or `agave-backoff`.
- Rayon and Bevy are baselines: they do not expose Threadance wait policies,
  queue capacity or Hot-first backlog.
- Spin durations apply only to `spin-then-park`; invalid capacity/backlog
  combinations and topology/wait pairs are skipped and explained.

This is only a small **coverage pilot**, not the complete thesis matrix: it
uses BLS and two worker counts to confirm the configuration combinations.
A compute-heavy study can reuse its corresponding template, after validation.
Do not mistake preliminary pilot comparisons for confirmatory findings.

## Study-wide reporting (first version)

After running and analyzing completed campaigns, generate one offline report:

```bash
python3 -m experiments.common.study_report \
  --root results/studies/all-pools-pilot/campaigns
```

This creates `study_report.html` (SVG paired-CI plots and descriptive
statistics) and `study_report.json` (all numerical comparisons). The report
runs the existing per-campaign analyzer and refuses incomplete campaigns.
It does **not** perform any benchmarks. Bootstrap paired comparisons still
use 20 randomized blocks for the thesis study; the two-block
`full_pool_pilot.toml` is a functional test only.

Current limitations: no automatic run-order diagnostics or multimodality
detection, no cross-workload normalization, and no automatic claim of a
winning scheduler. Full scientific conclusions require examining raw runs,
measurement quality, and confidence interval precision.
