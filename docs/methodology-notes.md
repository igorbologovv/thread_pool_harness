# Methodology Notes

This document records methodological and design decisions made during the thesis project. The purpose is to preserve not only what was implemented, but also why particular choices were made.

## Current research direction

The thesis investigates the performance of CPU-oriented thread-pool configurations in Rust.

The immediate goal is to establish a controlled methodology for comparing existing thread-pool implementations before continuing development of the custom Threadance pool.

The comparison should use the same workloads, input data, worker counts, and measurement procedure wherever possible.

## Current implementation

The benchmark harness separates workloads from schedulers.

A `Workload` defines the computation and independently executable work units.

A `Scheduler` defines how those work units are executed by a thread pool.

This allows the same workload to be executed using different scheduler implementations without changing the workload logic.

The current scheduler implementations are:

- Rayon;
- Bevy `TaskPool`;
- Threadance with a bounded Crossbeam channel.

All three schedulers are exposed through the same harness-level scheduler
interface and receive the same workload and workload-delivery schedule.

Threadance remains the custom implementation under investigation, while Rayon
and Bevy provide external baselines.

## External baselines

### Rayon

Rayon is used as the first baseline for CPU-parallel execution.

It is also relevant to the motivating use case because Rayon has been used in Agave for CPU-bound parallel workloads.

### Bevy TaskPool

Bevy `TaskPool` is used as the second external thread-pool baseline.

The comparison uses `bevy_tasks::TaskPool`, rather than Bevy ECS scheduling.

Bevy ECS introduces additional functionality such as system scheduling,
dependency handling, and resource-access coordination. These costs would make
it difficult to attribute observed performance differences specifically to the
underlying thread-pool behavior.

## Workloads

The main evaluation should use realistic CPU-bound workloads rather than synthetic busy loops.

Currently planned workload categories are:

- cryptographic verification;
- matrix computation using an optimized numerical library;
- CPU neural-network inference.

The existing `ComputeHeavyWorkload` is retained for development, correctness checks, and pilot experiments.

It is not intended to be the main workload used for the thesis conclusions.

## Experiment development order

The current planned order is:

1. Define the experimental methodology.
2. Add and validate external scheduler baselines.
3. Implement realistic workloads.
4. Compare the schedulers when all work is immediately available.
5. Introduce controlled workload-delivery modes.
6. Measure and analyze scheduling and system-level behavior.
7. Use the observations to guide further Threadance design.
8. Evaluate Threadance against the established external baselines.

This order is intended to avoid designing the custom thread pool around assumptions that have not yet been experimentally verified.

## Workload delivery

The harness currently supports four workload-delivery conditions:

- `all-at-once`;
- `steady-arrivals`;
- `variable-arrivals`;
- `bursty-arrivals`.

`all-at-once` submits work units immediately without intentional pacing.

The three scheduled modes use deterministic pseudo-random inter-arrival gaps.
For a fixed number of work units and arrival window, they contain the same total
work and the same mean offered arrival rate while differing in temporal
variability.

The scheduled delivery modes are generated before measurement and reused across
scheduler implementations and benchmark repetitions.

## Measurements

The main performance outcomes are expected to include:

- throughput;
- elapsed time / latency;
- CPU efficiency.

System-level measurements will be collected using external profiling tools such as Linux `perf`.

Additional thread-pool-level instrumentation may be introduced when needed to explain observed performance differences.

## Open questions

The following details are not yet fixed:

- whether another external thread-pool implementation should be included;
- which additional realistic workloads should be included;
- final workload dataset sizes;
- worker-count values used in the final experiment;
- number of warm-up and measured runs;
- exact statistical reporting procedure;
- which arrival-window values should be used to represent different offered-load levels;
- which internal Threadance configurations should be evaluated after the baseline study.

## 2026-10-03: Bevy baseline implementation

### Decision

Bevy `TaskPool` is added as the second external thread-pool baseline.

The harness uses `TaskPoolBuilder` with an explicitly configured worker-thread
count so that the requested worker count is controlled in the same way as for
the Rayon baseline.

For the initial all-work-available-at-once experiment, Bevy tasks are submitted
using scoped execution.

`scope_with_executor(false, None, ...)` is used instead of the simpler
`scope(...)` API. This prevents the multithreaded task-pool executor from being
ticked by the calling benchmark thread while the scope is waiting.

### Rationale

The objective of the initial experiment is to compare pools using a controlled
number of worker threads. Allowing the benchmark thread to participate in task
execution could make the effective execution resources different between
scheduler implementations.

The initial Bevy comparison therefore attempts to keep CPU work on the
configured Bevy worker threads.

This decision should be revisited when workload-delivery modes are introduced,
because paced task submission may require a different scheduler adapter API.

## 2026-10-03: Initial Rayon and Bevy validation

The Rayon and Bevy scheduler adapters were validated using the existing
`ComputeHeavyWorkload`.

Configuration:

- 8 worker threads;
- 1000 work units;
- 10 matrix operations per work unit;
- 5 measured runs.

Both scheduler implementations produced the same checksum:

`17882811132803870948`

This confirms that the two scheduler adapters executed the same logical
workload correctly.

Observed pilot medians were approximately:

- Rayon: 1.048 ms;
- Bevy TaskPool: 1.231 ms.

These measurements are treated only as implementation validation and not as
research results. The run count, workload, worker count, and workload size are
not yet part of the final experimental methodology.

## 2026-10-05: Result aggregation removed from measured runs

### Decision

Benchmark work units no longer return deterministic checksum values to the
scheduler adapters.

The measured scheduler contract is now:

1. submit or expose the configured work units;
2. execute every work unit;
3. wait until all submitted work has completed;
4. stop the elapsed-time measurement.

All three scheduler adapters now use the same harness-level completion
mechanism. Each completed work unit performs one atomic decrement, and only the
last completed work unit wakes the waiting benchmark thread.

### Rationale

Checksum aggregation was a benchmark-harness correctness mechanism rather than
part of the CPU workloads under study. It also produced different result paths
for the scheduler implementations: Rayon used parallel reduction, Bevy
returned scoped task results, and Threadance sent one result through a channel
for every work unit.

Removing result aggregation avoids measuring these adapter-specific result
transport mechanisms.

Workload correctness is instead checked by workload and thread-pool tests.
The BLS workload also continues to assert successful signature verification
during execution.

Completion synchronization remains inside the measured interval because
determining that all submitted work has finished is necessary scheduler
behavior.

## 2026-10-05: Deterministic workload-delivery schedules

### Decision

The harness supports four workload-delivery conditions:

- `all-at-once`: all work units are submitted immediately;
- `steady-arrivals`: low-variability pseudo-random inter-arrival gaps;
- `variable-arrivals`: moderately variable pseudo-random inter-arrival gaps;
- `bursty-arrivals`: highly variable pseudo-random inter-arrival gaps.

Scheduled delivery uses a separate arrival seed from the workload-generation
seed. The complete delivery schedule is generated before warm-up and
measurement and is reused for every benchmark repetition.

Raw inter-arrival weights are drawn from seeded Weibull distributions with
shape parameters 4.0, 1.0, and 0.5 for steady, variable, and bursty delivery,
respectively. The generated gaps are then normalized so that the first arrival
occurs at time zero and the final arrival occurs exactly at the configured
arrival-window boundary.

Consequently, for a fixed work-unit count and arrival window, the scheduled
delivery modes have the same total work and the same mean offered arrival rate.
They differ in the temporal variability of task arrivals.

All scheduler adapters use the same harness-level asynchronous submission and
completion protocol. Each completed work unit performs one atomic decrement,
and the final completion wakes the benchmark thread. This avoids changing the
scheduler adapter implementation when the delivery condition changes.

Absolute delivery deadlines are calculated relative to the benchmark start
time. The pacer sleeps until `start + emit_offset`, rather than sleeping for a
sequence of relative gaps, so timing error does not accumulate across the
schedule.
## 2026-10-05: Scheduler parity validation

### Decision

The scheduler adapters are validated with a dedicated instrumented workload
that records the number of executions of every work unit.

The validation is run separately for:

- Rayon;
- Bevy TaskPool;
- Threadance.

Each work unit must be executed exactly once.

The completion counter also uses a release-mode assertion to detect accidental
over-completion.

### Rationale

The benchmark result path no longer transports per-work-unit checksum values,
so correctness is validated separately from the measured workload path.

Using a dedicated validation workload avoids adding per-task result aggregation
or additional synchronization overhead to performance runs.

This validation checks that scheduler adapters do not lose, duplicate, or
incorrectly complete submitted work.

## 2026-10-05: Pacer timing validation

### Decision

The delivery pacer was validated with a manual diagnostic that compares planned
delivery deadlines with actual wake-up times.

The diagnostic uses the same absolute-deadline waiting logic as the benchmark
pacer but is excluded from normal benchmark runs.

For a schedule containing 64 work units over a 100 ms arrival window, measured
lateness was approximately:

- steady arrivals: mean 52.9 µs, maximum 59.9 µs;
- variable arrivals: mean 53.6 µs, maximum 70.6 µs;
- bursty arrivals: mean 49.5 µs, maximum 59.4 µs.

### Rationale

The delivery modes are meaningful only if the benchmark thread can reproduce
the generated arrival schedule with sufficiently small timing error.

Absolute deadlines prevent timing error from accumulating across the sequence
of arrivals.

No spin-waiting is used because additional busy waiting by the pacer would
contaminate CPU-time and cycle measurements intended to describe scheduler and
worker behavior.

## 2026-10-05: Perf measurement boundaries

### Decision

Linux `perf` counters are enabled separately for every measured benchmark
repetition and disabled immediately after that repetition completes.

Warm-up runs are executed with perf counters disabled.

The measured perf interval therefore includes:

- workload pacing;
- task submission;
- worker execution;
- completion synchronization.

It excludes:

- workload generation;
- scheduler construction;
- delivery-schedule generation;
- warm-up runs;
- result-vector bookkeeping between measured repetitions.

Repeated enable/disable intervals were validated experimentally. Perf counters
continue accumulating across enabled intervals without counting the disabled
inter-run periods.

A validation run using five measured repetitions produced approximately five
times the instructions, cycles, and task-clock of an equivalent single
repetition, confirming the intended accumulation behavior.

### Kernel scheduler events

Kernel scheduler events such as context switches and CPU migrations require
sufficient perf permissions.

On the benchmark host, `kernel.perf_event_paranoid=2` restricted the relevant
measurements to user-space events and produced unusable zero values for context
switches.

The setting was therefore changed to:

`kernel.perf_event_paranoid=1`

This allows context-switch and CPU-migration events to be collected for the
benchmark process.

The final experimental environment should record this setting together with
the other host configuration.

## 2026-10-05: Threadance queue capacity in parity experiments

Threadance currently uses a bounded task queue.

For scheduler-parity experiments, the queue capacity should be configured large
enough that queue saturation does not block the benchmark submission thread.

This prevents Threadance queue backpressure from changing the externally
generated workload-delivery schedule.

Queue capacity may later be varied deliberately as a separate Threadance
configuration parameter.

## 2026-10-06: Per-run perf measurement

### Decision

The earlier accumulated `perf stat` measurement across all measured
repetitions is superseded by per-run profiling.

When profiling is enabled, each measured benchmark repetition receives a
dedicated `perf stat` session attached to the benchmark process.

The perf counters are initially disabled. They are enabled immediately before
the scheduler run begins and disabled immediately after the scheduler run
finishes.

Warm-up repetitions are not profiled.

Perf process startup, shutdown, JSON parsing, and SQLite storage occur outside
the measured benchmark interval.

The harness supports three profiling modes:

- `none`: harness wall-clock measurements only;
- `standard`: primary CPU and scheduler counters;
- `deep`: standard counters plus cache and TLB counters.

The standard event set is:

- `task-clock`;
- `cycles`;
- `instructions`;
- `branches`;
- `branch-misses`;
- `context-switches`;
- `cpu-migrations`;
- `page-faults`.

Deep profiling additionally requests:

- `cache-references`;
- `cache-misses`;
- `L1-dcache-loads`;
- `L1-dcache-load-misses`;
- `LLC-loads`;
- `LLC-load-misses`;
- `dTLB-loads`;
- `dTLB-load-misses`;
- `iTLB-loads`;
- `iTLB-load-misses`.

Every measured run stores its raw perf JSON output and parsed counters in
SQLite.

The perf event runtime and percentage-running fields are also retained so
counter multiplexing can be detected during analysis.

Results from different profiling modes should not be mixed when comparing
elapsed-time measurements, because the enabled counter sets and profiling
overhead differ.

## 2026-10-06: Multi-pass deep perf profiling

The public profiling modes are `none`, `standard`, and `deep`.

`standard` is the primary measurement mode. Each measured repetition executes
the workload once and collects task-clock, cycles, instructions, branches,
branch misses, context switches, CPU migrations, and page faults. This event
set was validated with 100% event running time on the development machine.

`deep` is diagnostic. It does not request all additional PMU events
simultaneously because pilot measurements showed substantial multiplexing.

Instead, the same benchmark configuration is repeated using several small perf
event groups:

- cache: task-clock, cache-references, cache-misses;
- l1d: task-clock, L1-dcache-loads, L1-dcache-load-misses;
- dtlb: task-clock, l1_dtlb_misses, l2_dtlb_misses;
- itlb: task-clock, bp_l1_tlb_miss_l2_tlb_hit, l2_itlb_misses.

For `--runs N`, each deep group receives N separate physical measured
executions. Warm-up repetitions are performed before each group.

Measurements from different deep groups are therefore separate executions and
must not be described as counters collected from the same physical run.

Deep elapsed times are diagnostic and are not mixed with the primary standard
performance results.

SQLite records the physical perf pass and the repetition index inside that
pass.

On the Zen 3 development machine, the generic dTLB/iTLB load and miss aliases
were not used for final diagnostic interpretation. The deep TLB passes instead
use the available Zen 3-specific perf events.

For instruction translation, L1 ITLB misses can be interpreted as the sum of
`bp_l1_tlb_miss_l2_tlb_hit` and `l2_itlb_misses`.


## 2026-10-07: Arrival-process parameters and BLS service-time calibration

### Motivation

The initial workload-delivery interface uses the qualitative labels
`steady-arrivals`, `variable-arrivals`, and `bursty-arrivals`. These modes are
useful as controlled presets, but the thesis needs numerical parameters so that
arrival conditions can be selected and reported for a methodological reason
rather than by choosing arrival-window values arbitrarily.

For the scheduled modes, let \(N\) denote the number of work-unit arrivals.
There are \(N-1\) inter-arrival gaps between those arrivals. Because the current
pacer places the first arrival at time zero and the final arrival at the
configured arrival-window boundary, the mean inter-arrival gap is approximately

\[
\overline{g} = \frac{\text{arrival window}}{N-1}.
\]

The corresponding mean arrival rate is approximately

\[
\lambda = \frac{N-1}{\text{arrival window}},
\]

where \(\lambda\) is measured in work units per second when the arrival window
is expressed in seconds.

Arrival rate alone does not describe how evenly work reaches the pool. Arrival
variability is therefore described using the coefficient of variation of the
inter-arrival gaps,

\[
CV = \frac{\sigma_g}{\mu_g},
\]

where \(\mu_g\) is the mean inter-arrival gap and \(\sigma_g\) is its standard
deviation. A larger \(CV\) means that the gaps are more variable.

`CV` is not a complete description of arbitrary production traffic because it
does not encode the temporal ordering or correlation of the gaps. Two traces
can have the same mean and the same `CV` while arranging short and long gaps
differently.

For the controlled thesis experiments this ambiguity is limited by keeping the
arrival-process family fixed. The existing scheduled modes are generated from
seeded Weibull-distributed gap weights and normalized to the same total arrival
window. The qualitative delivery-mode names can therefore remain as convenient
presets while numerical arrival-rate and variability values are used to
describe the generated traffic.

### Research parameters

The current working research axis is

\[
(\rho,\ CV,\ \tau,\ S),
\]

where:

- \(\rho\) is offered load relative to the configured worker capacity;
- \(CV\) is the coefficient of variation of inter-arrival times;
- \(\tau\) is the worker idle/spin threshold before blocking or sleeping;
- \(S\) is the mean service time of one work unit.

For \(W\) workers, a useful nominal offered-load definition is

\[
\rho = \frac{\lambda S}{W}.
\]

This should be interpreted as an idealized load parameter based on the
single-thread service time. It is not the same as measured CPU utilization and
does not assume that real multi-worker throughput scales perfectly linearly.
Actual saturation and scaling remain empirical benchmark results.

Introducing \(S\) allows arrival rates to be selected relative to the actual
cost of the workload. The same numerical arrival rate may represent a light load
for a cheap work unit and overload for an expensive one.

The parameter \(\tau\) is currently a planned Threadance configuration
parameter. The current Threadance implementation blocks on the channel receive
operation when the queue is empty and does not yet expose a configurable
spin-before-block threshold. Therefore \(\tau\) is not yet an implemented
experimental axis.

### Why pure service-time calibration is required

A one-worker scheduler run is not a scheduler-free measurement. Even with one
worker, Rayon still performs task submission, queueing and dequeueing, closure
execution, and completion synchronization.

An initial BLS pilot was run using Rayon with one worker and all work available
at once.

Configuration:

- 100 work units;
- 2000 validators;
- 1600 signers per certificate;
- 1 certificate verification per work unit;
- 5 warm-up runs;
- 20 measured runs.

The observed median elapsed time was 153.686 ms, corresponding to approximately

\[
\frac{153.686\text{ ms}}{100}
=
1.537\text{ ms/work unit}.
\]

This value is useful as a one-worker scheduled baseline, but it should not be
defined as the pure service time \(S\).

It is also not valid to estimate scheduler overhead by simply subtracting a
pure workload measurement from this Rayon measurement. During the all-at-once
Rayon run, the benchmark thread can continue submitting later work while the
single worker is already executing earlier work. The two measurements therefore
have different execution paths. Small differences between them can include
ordinary run-to-run variation as well as scheduler behavior.

### Pure BLS service-time calibration

A separate `service_time` calibration binary was added to measure the workload
without Rayon, Bevy, or Threadance.

Dataset generation occurs before the measured interval. During each measured
run, the calibration executes the generated work units sequentially on the
calling thread:

~~~rust
for unit in workload.work_units() {
    workload.execute(unit);
}
~~~

The same BLS configuration was used:

- 100 work units;
- 2000 validators;
- 1600 signers per certificate;
- 1 certificate verification per work unit;
- 5 warm-up runs;
- 20 measured runs.

Observed calibration results:

- mean run elapsed: 154.754 ms;
- median run elapsed: 154.737 ms;
- mean service time: 1.548 ms/work unit;
- median service time: 1.547 ms/work unit.

For this BLS configuration on the current development machine, the working
service-time estimate is therefore

\[
\boxed{S \approx 1.547\text{ ms/work unit}}.
\]

The earlier one-worker Rayon result was approximately 0.7% lower than the pure
calibration result. This is treated as measurement variation between different
execution paths, not as evidence of negative scheduler overhead.

### Consequence for arrival-rate selection

With \(S\) calibrated, future arrival-rate values can be derived from a target
nominal offered load instead of being selected arbitrarily.

Rearranging

\[
\rho = \frac{\lambda S}{W}
\]

gives

\[
\lambda = \frac{\rho W}{S}.
\]

This provides a reproducible way to define light, moderate, and high offered-load
conditions for a fixed workload and worker count.

The resulting arrival-rate values must still be validated empirically because
real multi-worker scaling, contention, cache behavior, and scheduler overhead
can move the actual saturation point away from the idealized estimate.

The next methodological step is to use the calibrated \(S\) to select initial
arrival-rate points, then introduce explicit Threadance wait/wake policies and
thread-pool-level counters so that performance differences can be related to
worker idle, block, sleep, and wake behavior.

## 2026-10-28: Normalized offered-load study and reusable BLS dataset

### Motivation and research decision

The next experimental goal is to make the choice of Threadance configuration
depend on offered load, rather than producing only a ranking at two arbitrary
arrival rates. A comparison at a single absolute arrival rate also gives very
different relative loads to pools with 2, 4, 8, and 16 workers.

For this study, define the *reference-relative offered load* as

```text
load_fraction = scheduled_arrival_rate / saturated_reference_throughput(workers)
scheduled_arrival_rate = load_fraction * saturated_reference_throughput(workers)
```

The reference is the same measured Rayon all-at-once throughput for every
scheduler with a given worker count. This controls the arrival-rate treatment
*within* a worker count; it does not assert equal actual CPU utilization or
equal physical-core occupancy across configurations. This parameter is also
distinct from the earlier nominal `rho = lambda * S / workers`, which assumes
idealized worker capacity.

The preliminary reference rates in WU/s are 1294 (2 workers), 2583 (4),
4792 (8), and 5224 (16), taken from the archived BLS report. Before
confirmatory runs, validate or replace them with version-matched, documented
reference measurements; keep the chosen reference vector with the campaign.

### Compact queueing-motivated design

Six load levels were selected: 0.25, 0.50, 0.75, 0.9375, 1.10, and 2.00.
The subcritical values cover light, moderate, high, and near-saturation
conditions, where queueing-delay sensitivity rises sharply as utilization
approaches the service-capacity boundary. The two supercritical levels are
finite-window overload stress tests, **not** steady-state operating points.
Separate 0.875, 1.0, and 1.05 levels were omitted to limit campaign cost.

The primary matrix varies 2/4/8/16 workers, fixed/bursty arrivals, and
five scheduler anchors: Rayon; shared-MPMC SpinThenPark(0) and AgaveBackoff;
RTRB SpinThenPark(0) with backlog 1 or 2. Queue-capacity label q64 is fixed.
Twenty randomized blocks contain one warm-up and two measured runs per
configuration; profiling uses standard perf. This yields 48 campaigns, 4800
invocations, 9600 measured physical runs and 4800 warm-ups. The matrix is
intentionally not a full policy/spin/backlog factorial.

### Dataset and window handling

The BLS workload now permits a fixed dataset of 2500 distinct work units
and a separate per-run `work_units` count. The cached dataset is loaded once
per invocation, then only its first `work_units` are submitted: no within-run
wrapping or repeated verification of the same certificate. Each scheduled
test uses:

```text
N = ceil(scheduled_arrival_rate * 0.200 seconds) + 1
scheduled_arrival_window = (N - 1) / scheduled_arrival_rate >= 200 ms
```

The same N and arrivals are used for every scheduler configuration in a
worker/load/arrival-mode campaign. For the highest preliminary point (16
workers, 200%), N = 2091, within the 2500-certificate dataset capacity.
The 200 ms minimum concerns the **scheduled submission window**, not a promise
of 200 ms actual producer progress under backpressure. Elapsed time also
includes draining all submitted tasks. Inspect observed arrival lateness
especially at overload points; task-level latency remains unmeasured.

The Rust CLI accepts optional `--dataset-work-units`; omitting it preserves
the previous cache-size behavior. The existing dataset-identity helper and
campaign invocation path were adjusted to fingerprint the fixed full input
dataset rather than each variable-length run. Existing historical campaigns
are unaffected.

### Reproducibility and remaining validation

The experiment generator is
`experiments/studies/bls_normalized_load.py` and its separate explanation
is `experiments/studies/BLs_NORMALIZED_LOAD.md`. `--check` previews the
plan and `--write` creates campaign TOMLs plus a JSON manifest. It does
not run measurements. Planned test cases cover the load grid, minimum
window, dataset bounds, matched arrival modes, and the 16-worker 200% case.

These changes were committed on
`experiment/normalized-bls-load`. No Rust compilation, perf execution,
or host-level benchmark validation has yet been completed. Before collecting
the full study, run formatting, tests, clippy, a small end-to-end pilot, and
check the arrival-lateness and distribution diagnostics. New runs must be
analyzed as their own acquisition series; historical 1000-work-unit results
are not interchangeable repeated measurements.

## 2026-10-31: Empirical throughput baselines before a denser load sweep

### Why this measurement was added

The normalized BLS offered-load campaign has been collected and analyzed:
48 campaigns across 2/4/8/16 workers, fixed and bursty arrivals, and six
reference-relative load levels (25%, 50%, 75%, 93.75%, 110%, 200%). It
compares Rayon with four Threadance queue/wait configurations; Bevy was
implemented in the harness but was not included in that campaign. The
analysis retains raw runs and uses randomized-block comparisons and 95%
paired bootstrap confidence intervals for the reported effects.

The preliminary capacity vector used to select offered arrival rates was
based on earlier Rayon all-at-once results. It was suitable for choosing
common *offered* rates within a worker count, but it did not tell us the
measured saturated throughput of **each** scheduler. Moreover, some
load-response plots appear to change character somewhere between the
25% and 50% sampled points, while others change near saturation.
Six widely spaced load levels cannot locate these changes or distinguish
a gradual curve from a sharp regime transition.

**Decision:** before adding more load-sweep samples, measure
scheduler-specific empirical saturated throughput under a common BLS
workload. Include Bevy alongside Rayon and Threadance. Use the resulting
distributions and uncertainty estimates to design the next experiment,
rather than treating the old Rayon capacity assumptions as known exact
values for every scheduler.

### Implemented finite-batch saturation experiment

The independent study was added on branch
`experiment/bls-saturated-throughput`, with definitions at:

- `experiments/studies/bls_saturated_throughput_template.toml`
- `experiments/studies/bls_saturated_throughput.toml`
- `experiments/studies/BLS_SATURATED_THROUGHPUT.md`

The experiment uses the common randomized-block campaign runner; no new
scheduler implementation or custom measurement engine was necessary.
Bevy's existing harness adapter is `bevy_tasks::TaskPool`, not the
Bevy ECS system scheduler.

Design:

- **Workload:** 2500 distinct cached BLS certificate verifications per
  measured run (2000 validators, 1600 signers, one certificate per work
  unit, workload seed 1). Dataset creation/loading is outside the
  measurement interval, and all schedulers use the same input data.
- **Workers:** 2, 4, 8, and 16.
- **Schedulers:** Rayon; Bevy TaskPool; Threadance bounded MPMC with
  SpinThenPark(0); Threadance MPMC with AgaveBackoff; segmented RTRB
  hot-first with SpinThenPark(0) and backlog 1 or 2. Threadance queue
  capacity is 64 in all four configurations.
- **Delivery:** `all-at-once`; all work units are submitted as fast as
  the respective scheduler permits. A bounded queue may block its
  producer, so the measured interval includes any resulting
  backpressure.
- **Repetition:** 30 randomized blocks per worker count, six
  configurations per block, one warm-up and two measured repetitions
  per condition. Four campaigns yield 720 harness invocations, 720
  warm-ups, and 1440 measured physical runs.
- **Collection:** standard Linux perf profile, SQLite raw runs,
  campaign metadata, block summaries, and paired bootstrap 95%
  confidence intervals relative to Rayon. Standard profile does **not**
  include cache-misses; those need separate deep perf passes.
- **Execution:** `settle_seconds = 0.0`; there is no artificial delay
  between configuration invocations. This reduces wall-clock cost but
  makes temporal drift diagnostics (frequency, temperature, background
  activity, order/block effects) especially important.

The experimental plan was previewed successfully on the benchmark
machine. An initial test failed because it expected short scheduler
IDs, whereas the generic study expander deliberately uses canonical
IDs encoding queue topology and policy; the **test**, not the
experiment design, was corrected in commit `0a295ac`.
Collection was launched on the user's benchmark host; **no throughput
measurement values are recorded in this note yet**. Conclusions must
wait for the completed raw runs and statistical analysis.

### What throughput means in this experiment

For each completed run, the harness records:

```text
observed_throughput_WU_per_s = completed_work_units / elapsed_seconds
```

Since each run contains 2500 independent work units, these are
**measured finite-batch saturated throughputs**, not theoretical
capacities. They estimate how quickly each actual pool can process a
large immediately available batch under this hardware, dataset, and
configuration, including submission, queueing, potential producer
backpressure, execution, and final completion synchronization.

They do **not** independently establish the maximum *sustainable*
throughput under indefinite steady arrivals, nor do they directly
measure CPU utilization. Separate `task-clock` and hardware perf
counters quantify resource consumption. A long-running capacity
experiment may be useful later, but is not a prerequisite for the
next exploratory sweep, provided this finite-batch definition and its
uncertainty remain explicit.

After collection, report throughput distributions, mean and median,
sample variance, 95% block-bootstrap confidence intervals and paired
comparisons to Rayon for every worker count. Check outliers,
multimodality, non-stationarity/time drift, and scaling from 8 physical
cores to 16 SMT threads. Avoid treating a single point estimate as a
perfectly known capacity.

### How this informs the next stage

1. Retain **absolute scheduled arrival rate** (WU/s) as the common
   treatment for direct, same-workload scheduler comparisons. Also
   report scheduled arrival rate divided by each scheduler's own
   empirically measured finite-batch saturated throughput. Label the
   latter as a derived scheduler-relative *proxy* for load, not true
   CPU utilization or an independently validated steady-state
   traffic intensity.
2. Measure **achieved throughput** and actual producer-submission
   timing alongside scheduled offered rate. The old normalized-load
   campaign demonstrated why this matters: at overload, bounded-queue
   backpressure can delay actual submissions beyond the intended
   arrival window. Do not attribute an effect solely to wait/wake
   behavior when queue saturation and producer blocking also change.
3. Add finer arrival-rate sampling to test the apparent changes
   between 25% and 50%: provisionally use 5-percentage-point steps
   over roughly 25–70%, then refine around 70–120% and any empirically
   identified change points. Choose exact WU/s values after the
   throughput measurements and a diagnostic review of the old curves;
   do not presume a real threshold at 40% from unsampled space.
4. Retain fixed and bursty arrival modes, consistent input data,
   randomized blocks, raw per-run data, and bootstrap intervals.
   Focus on elapsed/drain, achieved throughput, CPU time and
   cycles/WU, branch misses/WU, context switches/WU, and arrival
   lateness; use targeted deep profiling for cache misses if needed.
5. Explore quadratic/cubic **polynomial fits** as descriptive models
   only after collecting a denser grid. Compare out-of-block
   predictive performance against segmented regression or smoothing
   splines, use block-level resampling for confidence bands, inspect
   residual structure and multimodality, and avoid extrapolation
   beyond the measured range. A sharp-looking change is a hypothesis
   about queue/wake/backpressure regime changes, **not proof** of such
   a mechanism.

The longer-term thesis goal remains a configuration-selection rule
for Threadance: identify when a wait/wake or queue policy reduces CPU
resource use without unacceptable latency or throughput loss, and
support that rule with statistically defensible measurements.

## 2026-10-31: Observations from the saturated-throughput campaign

The four all-at-once BLS throughput campaigns completed on the benchmark
host: 2, 4, 8 and 16 workers, six scheduler configurations and 30 randomized
blocks per worker count. They produced 1440 measured runs (60 per
worker/scheduler condition). The archived environment metadata identifies
harness commit `0a295ac741d1a410eec3976bbff22fcdd7bb5bba` and
Threadance commit `7435d61195d7a0144cc3aa3d6bbe77ff179b929d`.

The raw runs contain two measured repetitions after one warm-up in each
invocation. With 8 workers, Rayon averaged 4728.7 WU/s in the first measured
repetition and 4897.8 WU/s in the second (+3.58%). With 16 workers,
the corresponding means were 5095.8 and 5276.5 WU/s (+3.57%).
For Rayon at 8 workers, the second measured repetition was faster in
all 30 blocks. The order-related difference was also visible in
other schedulers; its cause has not been established.

Hartigan's dip test was applied to the 24 pooled throughput samples
(4 worker counts × 6 configurations, 60 measurements each).
Nine of the 24 tests rejected unimodality at adjusted p < 0.05 using
Holm's multiple-testing correction. These rejections occurred among
the 8- and 16-worker conditions; none occurred at 2 or 4 workers
after that correction. Separate dip tests on first and second measured
repetitions (48 samples of n=30) produced no Holm-adjusted
rejections at 0.05.

For pooled Rayon throughput, the observed dip statistics were
D=0.10447 at 8 workers and D=0.13691 at 16 workers.
The corresponding Holm-adjusted Monte Carlo p-values were 0.00048
for both. The dip test indicates departures from unimodality in those
pooled samples; it does not establish exactly two modes. Failure to
reject unimodality in the smaller separated samples does not establish
that those distributions are unimodal. The dip-test calibration also
assumes independent identically distributed samples; the observed
within-invocation run-order effect needs to be considered when
interpreting these p-values.

No causal explanation for the run-order effect or for the pooled
distribution shapes has been established.

## 2026-10-31: BLS Rayon run-order and warmup-count diagnostics

### Initial observation and run-position replication

The finite-batch saturated-throughput campaign used one unprofiled warmup
followed by two standard-perf measured repetitions. It exhibited a
reproducible run-order effect at 8 and 16 Rayon workers, as documented above.

To inspect the progression rather than pool the two repetitions, a separate
Rayon-only run-order diagnostic executed 20 independent process invocations
for each of 4, 8, and 16 workers. Each invocation constructed a new Rayon
pool, executed one unprofiled 2500-certificate warmup, and then recorded
eight consecutive standard-perf measured 2500-certificate batches, retaining
their positions and raw counters (480 measured runs in total).

Mean throughput (WU/s) by measured position:

| Workers | Position 1 | Position 2 | Position 3 | Position 8 |
| --- | ---: | ---: | ---: | ---: |
| 4 | 2585.46 | 2583.18 | 2585.00 | 2583.34 |
| 8 | 4780.53 | 4905.20 | 4918.92 | 4909.80 |
| 16 | 5095.77 | 5305.15 | 5299.67 | 5299.28 |

For 16 workers, position 2 exceeded position 1 in all 20 independent
invocations: mean within-invocation relative difference +4.109%, 95%
block-bootstrap CI [+4.011%, +4.207%]. For 8 workers the corresponding
difference was +2.621%, CI [+1.940%, +3.187%], positive in 18/20
invocations. The later positions were close in their averages; this
observation alone does not prove strict stationarity or a specific cause.

### Controlled warmup-count sweep

A follow-up study varied the number of unprofiled complete BLS batches
before measurement: **1, 2, 4, 8 warmups**. Rayon was tested at 4, 8 and
16 workers. Each worker-count campaign used 20 randomized blocks; each
block randomized four distinct processes/conditions (one per warmup
count). A pool was constructed once per process and reused for its
warmups and eight subsequent standard-perf measured batches. The same
cached 2500-certificate workload, seed, release harness path and
finite-batch all-at-once delivery were retained.

The completed sweep yielded **240 independent process invocations,
900 unprofiled warmup batches, and 1920 measured physical runs**.

Mean throughput of the **first measured batch** (WU/s):

| Workers | 1 warmup | 2 warmups | 4 warmups | 8 warmups |
| --- | ---: | ---: | ---: | ---: |
| 4 | 2599.82 | 2599.66 | 2600.23 | 2601.33 |
| 8 | 4758.40 | 4920.75 | 4918.79 | 4924.22 |
| 16 | 5109.43 | 5291.55 | 5300.66 | 5299.41 |

Mean **within-process** change from measured position 1 to position 2
(95% bootstrap CI across the 20 independent processes per condition):

| Workers | Warmups | Position 2 vs 1 | 95% CI |
| --- | ---: | ---: | --- |
| 4 | 1 | -0.019% | [-0.076%, +0.028%] |
| 8 | 1 | +3.056% | [+2.597%, +3.560%] |
| 8 | 2 | -0.272% | [-0.514%, -0.090%] |
| 8 | 4 | -0.170% | [-0.377%, +0.038%] |
| 8 | 8 | -0.405% | [-0.691%, -0.141%] |
| 16 | 1 | +3.457% | [+2.644%, +3.941%] |
| 16 | 2 | -0.119% | [-0.258%, +0.051%] |
| 16 | 4 | -0.133% | [-0.216%, -0.063%] |
| 16 | 8 | -0.173% | [-0.272%, -0.072%] |

**Observation:** increasing the Rayon warmup count from one to two
removed the large +3–4% improvement of the second measured batch at
8/16 workers in this finite-batch protocol. More warmups did not show
a comparably large increase in first-position throughput. Residual
sub-percent differences, including small statistically distinguishable
decreases in some conditions, remain; "two warmups" is therefore
an empirically supported *minimum for removing the major first-run
anomaly*, **not proof of complete stationarity**.

The first measured batch is standard-perf profiled under every warmup
count. Thus the large difference is not uniquely caused by the first
measured batch's perf attachment. The experiment does **not** isolate
cache/TLB state, CPU frequency, CPU affinity, branch prediction, or
kernel scheduling, and it cannot establish a physical mechanism.

Input correctness checks in the independent audit found the same
2500 distinct BLS tasks executed each run; this study does not indicate
that cryptographic results were skipped or memoized. The study
measures finite-batch completion throughput, not independently validated
sustainable service capacity. User-only perf event scope in the archived
saturated-throughput data also makes recorded zero context-switch and
cpu-migration counts unusable for comparative scheduler claims.

### Consequence for the benchmarking methodology

- **Retain all original raw datasets and acquisition metadata.** They are
  valid observations of the one-warmup protocol and useful for exploring
  run-order effects. Do not quietly relabel them as steady-state data,
  discard only the inconvenient positions, or combine new runs with old
  ones without labeling the protocol change.
- **Do not use the pooled one-warmup finite-batch throughput estimates
  as a validated steady-condition reference.** The original 1440-run
  multi-scheduler campaign needs a new acquisition with an explicitly
  justified warmup protocol before its rankings are treated as
  confirmatory. Run-position analysis of the old samples remains useful
  for diagnosis.
- **Verify warmup adequacy for Bevy and every Threadance configuration**
  rather than presuming the Rayon result transfers unchanged. At least
  two complete warmups are supported by these Rayon measurements for
  this BLS task/host; a universal warmup count is not established.
- Previously collected normalized offered-load campaigns can remain
  **exploratory** under their archived protocols. Repeat only the
  decision-critical comparisons for confirmation once reference-rate
  definitions, warmup adequacy and usable perf counters are settled;
  do not automatically re-run all 48 campaigns before reviewing them.
- After confirming warmup behavior across schedulers, rerun the
  saturation comparison on the same fixed workload with consistent
  per-condition warmup rules, retain individual physical-run positions
  and block-paired uncertainty, and reassess the reference throughput
  for future load sweeps. Separately address the kernel-event perf scope
  and the distinction between finite-batch throughput and sustainable
  capacity.

Raw diagnostic definitions and reports:

- `experiments/studies/bls_run_order_diagnostic.toml`,
  `experiments/studies/analyze_bls_run_order.py`;
- `experiments/studies/bls_warmup_sweep.toml`,
  `experiments/studies/analyze_bls_warmup_sweep.py`;
- `results/studies/bls-run-order-diagnostic/campaigns/`;
- `results/studies/bls-warmup-sweep/campaigns/`.

No causal explanation for the warmup effect has yet been established.

## 2026-10-31: Decision to repeat the BLS saturation comparison

### Evidence and interpretation

The Rayon run-order diagnostic (20 independently launched processes per
worker count, one unprofiled warmup followed by eight standard-perf
measurements) showed that the first measured batch was systematically
slower than later batches at 8 and 16 workers, while the 4-worker control
had no comparable discontinuity. At 8 workers, measured positions 1/2
averaged 4780.53/4905.20 WU/s; at 16 workers, 5095.77/5305.15 WU/s.
Subsequent measured positions were close in their means, without proof
of strict stationarity.

The randomized warmup-count sweep (20 blocks for each of 4, 8, 16
workers, with 1/2/4/8 complete unprofiled warmups and eight measured
runs per process; 1920 physical measured runs) established an operational
remedy for this protocol. At 8 workers, first-position throughput
rose from 4758.40 WU/s after one warmup to 4920.75 WU/s after two;
at 16 workers, from 5109.43 to 5291.55 WU/s. The within-process
position-2 versus position-1 differences changed from +3.056%
(95% block-bootstrap CI [+2.597%, +3.560%]) to -0.272%
(CI [-0.514%, -0.090%]) at 8 workers, and from +3.457%
(CI [+2.644%, +3.941%]) to -0.119%
(CI [-0.258%, +0.051%]) at 16 workers. The 4-worker control
remained close to 2600 WU/s with every warmup count.

Thus *one full warmup was insufficient to remove the large first-run
effect for Rayon at 8/16 workers in the measured BLS batch protocol*;
two removed that large effect in this study. Small residual deviations
and other non-stationarities have not been ruled out. Neither CPU-cache
warming, branch prediction, frequency, affinity, nor scheduler
behavior has been causally isolated. The data should not be described
as proving that a cache takes exactly two batches to warm.

### Measurement defect and remedial instrumentation

An independent audit of the original 1440-run multi-scheduler saturation
campaign found that standard perf recorded `context-switches:u=0` and
`cpu-migrations:u=0` in every run. These user-only values do **not**
measure kernel scheduler events and must not be analyzed as genuine
zero context switches/migrations. On the benchmark host the observed
restriction was `kernel.perf_event_paranoid=2`. For new runs, request
a permissive enough scope (normally `kernel.perf_event_paranoid=1`
for per-process kernel-inclusive events) and check actual event names,
counter availability and nonzero context switches in a deliberate
sleeping-task preflight. Do not presume that the kernel configuration
has been successfully changed until the host-side check passes.

The new `experiments/studies/check_perf_scope.py` verifies that
preflight independently of the large campaign. It is a required manual
precondition, **not** a measurement result and not an automatic
per-run check inside the standard collection engine. Recorded zero
migrations can still be correct if event scope is verified.

### Decision and remaining limitations

The original one-warmup comparison is retained without alteration
as evidence for its exact measurement protocol, including the run-order
effect. It is not used as a validated stable-condition throughput
reference. Prior normalized-load studies also remain archived;
they do not automatically require wholesale repetition, but any
decision-critical results need revalidation under the amended protocol.

A fresh, independent comparison is defined in
`experiments/studies/bls_saturation_recheck.toml` on
`experiment/bls-saturation-recheck`: the original six schedulers
(Rayon, Bevy TaskPool, Threadance MPMC SpinThenPark(0) and
AgaveBackoff, Threadance RTRB backlog 1/2), 2/4/8/16 workers,
2500 identical BLS certificates and all-at-once delivery,
30 randomized blocks, **four unprofiled warmups** and **three
standard-perf measured runs** per condition. Its planned totals are
720 process invocations, 2880 warmup batches and 2160 measured
physical runs, stored in a new result directory.

Four warmups were selected conservatively, not because they were
proven sufficient for all schedulers: only Rayon has the above
warmup-count evidence. Preserve individual measured positions
during analysis and verify whether Bevy/Threadance also settle;
if not, modify the protocol based on diagnostics rather than pooling
biased positions. The new campaign has been defined and CI-checked,
**but no measurement outcomes from it are yet recorded here**.

Even a stable finite-batch throughput under this protocol is not an
independent estimate of indefinitely sustainable steady-state
capacity. Report its operational definition, run-position sensitivity,
resource counters with validated scope, and block-paired uncertainty
rather than asserting a physical mechanism or exact service-capacity
limit.
