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

- Rayon
- Threadance with a bounded Crossbeam channel

Threadance is currently kept as an early prototype and will not be the main focus until the external baselines and workloads are established.

## External baselines

### Rayon

Rayon is used as the first baseline for CPU-parallel execution.

It is also relevant to the motivating use case because Rayon has been used in Agave for CPU-bound parallel workloads.

### Bevy TaskPool

Bevy TaskPool will be added as another external thread-pool baseline.

The primary comparison will use `bevy_tasks::TaskPool`, rather than Bevy ECS scheduling.

Bevy ECS introduces additional functionality such as system scheduling, dependency handling, and resource-access coordination. These costs would make it difficult to attribute observed performance differences specifically to the underlying thread-pool behavior.

Bevy ECS may later be evaluated separately as an application-level scheduling case.

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

The initial comparison will use an all-work-available-at-once mode because it provides a simple and controlled baseline.

Later experiments will introduce time-dependent workload delivery.

The planned delivery modes are:

- uniform;
- moderate burst;
- aggressive burst.

Burst behavior will be controlled primarily through:

- batch size (B);
- batch-arrival interval (T).

The exact ranges will be selected during pilot experiments.

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
- exact workload implementations and dataset sizes;
- worker-count values used in the final experiment;
- number of warm-up and measured runs;
- exact statistical reporting procedure;
- exact definitions of the uniform and burst workload-delivery modes;
- how to provide equivalent paced task submission across schedulers with different APIs;
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

Rayon uses completion of the parallel iterator, Bevy uses completion of the
scoped task execution, and Threadance uses a lightweight completion counter.
Each completed Threadance job performs one atomic decrement, and only the last
job wakes the waiting benchmark thread.

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

