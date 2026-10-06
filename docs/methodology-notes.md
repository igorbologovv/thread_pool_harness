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
