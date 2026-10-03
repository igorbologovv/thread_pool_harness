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
