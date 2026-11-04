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

## One-command local execution

The all-scheduler BLS study has its own 20-block specification:
`experiments/studies/bls_full.toml`. The shorter
`full_pool_pilot.toml` remains a two-block functional pilot.

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_full.toml --check
python3 -m experiments.common.study_run --spec experiments/studies/bls_full.toml --run
```

To run the same BLS campaign with the optional CPU-pinned background cache/
memory interference, add `--cache-interference`:

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_full.toml --run --cache-interference
```

Its parameters are only read from `experiments/studies/cache_interference.toml`.
The defaults are eight logical CPUs (0–7), each with its own 1 MiB buffer and
1 MiB of actual read/modify/write operations per 1000 us period: 8 MiB total
working set and 8 MiB requested writes per period. CPU IDs must be available
on the host; failed affinity is a startup failure. Benchmark worker affinity
is unchanged.

The background process is separate from each benchmark process, is held for
each complete campaign collection (warmups and all perf passes included), and
is stopped by the campaign runner on completion, failure, or interruption.
`READY` requires allocation, prefaulting, affinity, and a completed first sweep
on every configured CPU. Collection checks process health at most every 50 ms
while waiting or running a benchmark. On failure it terminates the benchmark
and its perf descendants, retains diagnostics, and exits unsuccessfully.
The existing perf PID/TID attachment and benchmark throughput calculations
exclude the separate interferer process.

For an individual existing BLS definition, use the shared execution engine:

```bash
python3 -m experiments.common.campaign prepare --experiment experiments/queue_topology/experiment.toml --cache-interference
python3 -m experiments.common.campaign collect --experiment experiments/queue_topology/experiment.toml --cache-interference
```

Use a separate output root for baseline and interfered studies. Reuse and
resume reject changed interference mode or configuration; collection also
checks the interferer binary hash. Pass the flag again to `resume`. Each
collection attempt runs one interferer; an explicit resume starts a new one.
The campaign archives `cache_interference.toml`, binary identity, and a latest
`cache_interference.json` status. Permanent logs and status files for **every**
attempt live in `cache_interference/<attempt>/`; manifest rows identify their
attempt. An abnormal shutdown or missing timing statistics prevents a success
report, even if all benchmark rows were already written.

Final timing statistics count each thread's sweeps, work exceeding the requested
period (`overruns`), completion after its scheduled deadline (`deadline_misses`,
including wakeup lateness), and skipped intervals. Average/max work and wakeup
lateness are retained. A summary reports whether all sweeps met the work budget.
The generator skips elapsed intervals instead of overlapping sweeps: 1 ms is a
requested period, not a real-time guarantee. Forced kills can lose final timing
statistics and are explicitly marked unsuccessful.

It introduces CPU execution, scheduling, cache, and memory-system contention;
it is not an L2-eviction-only measurement.

`--check` does not build, prepare or collect benchmarks. `--run` expands
the matrix and executes each campaign sequentially: release build, dataset
preparation, collection, per-campaign paired analysis, then one study-wide
HTML/JSON report. If interrupted, rerunning the same command attempts to
resume matching campaigns. Existing campaigns and binaries must satisfy
the underlying git/binary identity checks; ambiguous matches fail closed.
Never run this under sudo: grant perf permissions separately as appropriate.

**Research scope:** this BLS matrix varies worker counts, delivery modes,
wait policies, spin times, queue capacities and segmented backlogs, but it
does not exhaust every conceivable workload size or BLS task granularity.
A separate compute-heavy study will address fixed-total-work granularity
comparisons. Full-study run count is 8,800 invocations (20 blocks).
Check disk space, profiling access, and machine stability before the full run.


## Optional benchmark worker/producer CPU affinity

Use `--pin-workers` with `campaign collect` or `study_run --run`. Settings
come from `experiments/studies/cpu_affinity.toml`. Each worker is pinned to
its corresponding logical CPU, in the configured order; the main harness
thread (producer and arrival pacer) is pinned to `producer_cpu`. The
Linux affinity masks are checked before warmups and measurements start.
This covers Rayon, Bevy, Threadance MPMC and both segmented implementations.

The example selects logical CPUs 0..15 in order, using only the first
`workers` entries. The producer is placed on CPU15. With 16 workers, CPU15
is also assigned to worker 15, and a warning is emitted: the machine has
no seventeenth logical CPU. SMT siblings share physical resources, too.
Inspect `lscpu -e=CPU,CORE,SOCKET,ONLINE` to confirm your topology.

Pinned and unpinned experiments require separate campaign output roots; the
TOML config is archived and checked on resume. The background cache
interferer uses its own CPU pinning independently. Both flags can be combined.

## Advanced BLS PMU acquisition (second round)

Specification: `experiments/studies/bls_advanced_perf.toml` (separate from
historical 2/4 and 8/16 campaigns). It covers 2, 4, 8, and 16 workers;
all-at-once, fixed 900/1500, bursty 900/1500; Rayon, Bevy, shared-MPMC
Threadance, and RTRB Threadance. Spin-then-park durations are 0/20/40/80
microseconds. Each condition has 20 randomized blocks, two recorded runs
**per perf pass** (four measured runs per condition per block), and the
existing one-full-run warm-up **once per configuration**. There is no settling
sleep. The pass order is reversed in even-numbered blocks.

Two PMU passes avoid multiplexing a six-event hardware group on the
Ryzen 7 5700X with NMI watchdog enabled:

- `cpu-cache`: cycles, instructions, cache references/misses, L1D load
  misses.
- `l1-branches`: cycles, L1D loads/misses, branches, branch misses.

Both include software task-clock, context switches, migrations and page
faults. Hardware events use `:uk` (user and kernel). A prior single-process
manual test found no reported multiplexing with five hardware events, but
the actual multiworker configuration still requires a short local check.
Custom profiling aborts if an event is unsupported/not counted or reports
less than 99.5% running, preserving already completed campaign files.

To inspect without running benchmarks:

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_advanced_perf.toml --check
```

Only after validating PMU permissions and the new profile on a short local
test, the usual full-study command is:

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_advanced_perf.toml --run
```

The full plan has 7,200 invocations and 28,800 profiled measurements. Do
**not** combine these new no-settle measurements with historical campaigns
as though they were exchangeable repetitions. The benchmark remains on a
single cached BLS dataset; cache profiling cannot directly establish
cache-coherence traffic or identify which function caused an event.


## Paired pinned BLS controls (clean vs cache interference)

This uses the six scheduler configurations from the BLS saturation recheck
(Rayon, Bevy, MPMC spin0, MPMC AgaveBackoff, RTRB backlog 1 and 2) plus
an additional MPMC spin-then-park at 40 us. It reuses the two deep PMU passes
from `bls_advanced_perf.toml`, with 4 full warmups once per condition followed by
2 measured runs on each PMU pass. The pass order reverses on even blocks.

Both studies use the same 2500-unit cached BLS workload, 2/4/8/16 workers,
all-at-once or fixed/bursty arrivals at 900 and 1500 WU/s, 20 randomized
blocks, and the same randomization seed. Each arm has 12 campaigns, 2800 harness
invocations, 11200 warmups and 11200 profiled executions. The two separate
output roots are essential: the runner must never resume interfered measurements
inside a clean campaign.

Inspect the plans first:

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_pinned_control_clean.toml --check
python3 -m experiments.common.study_run --spec experiments/studies/bls_pinned_control_interference.toml --check
```

Collect the clean arm with pinning enabled, then the interfered arm with
**the same** pinning and the additional background process:

```bash
python3 -m experiments.common.study_run --spec experiments/studies/bls_pinned_control_clean.toml --run --pin-workers
python3 -m experiments.common.study_run --spec experiments/studies/bls_pinned_control_interference.toml --run --pin-workers --cache-interference
```

Do not combine these with historical unpinned results as equivalent repetitions.
The automatically generated report for each arm summarizes *within-arm* data;
a direct treatment-effect analysis must match the two arms by worker count,
delivery/rate, scheduler configuration, and randomized block and inspect raw
runs and interference timing statistics. At 16 workers the example producer CPU
15 overlaps worker 15; use the same topology in both arms and record the limitation.
