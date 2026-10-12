# Agave batching regression: industrial case study

This note records a production-motivated case that is directly relevant to the thesis methodology. It is useful as an example of why thread-pool performance cannot be evaluated independently of batching, queueing, and workload-admission policy.

## Background

Agave's TVU pipeline processes incoming shreds through several stages, including shred sigverify and retransmit. Historically, shred sigverify used Rayon and limited the number of packet batches drained in one iteration with:

```rust
const SIGVERIFY_SHRED_BATCH_SIZE: usize = 1024;
```

In the discussion around Agave PR [#15585](https://github.com/anza-xyz/agave/pull/15585), the pipeline was described as follows:

- fetch receives packets in batches of up to 64 packets;
- shred sigverify could drain up to 1024 packet batches in one iteration;
- under the production load examined at the time, sigverify usually processed approximately one batch per iteration on average.

Based on those observations, PR #15585 changed `SIGVERIFY_SHRED_BATCH_SIZE` from `1024` to `4` while also replacing Rayon in retransmit with a custom worker pool. The PR was merged on 2026-10-06.

## Regression discovered under heavier traffic

On 2026-10-12, Agave PR [#15991](https://github.com/anza-xyz/agave/pull/15991) was opened to revert PR #15585.

The stated reason was that changing `SIGVERIFY_SHRED_BATCH_SIZE` from `1024` to `4` caused significantly lower throughput under approximately 5 Gbit/s of shred traffic. The revert restores the previous value of `1024` together with the rest of the reverted commit.

This is an important methodological example because the smaller value looked reasonable when judged from average behavior under the earlier observed load: sigverify normally saw close to one batch per iteration, so allowing four batches appeared to provide ample headroom. That observation did not generalize to the heavier traffic condition.

## Why this matters for the thesis

The case illustrates a central problem in thread-pool benchmarking: a change that appears to be a thread-pool optimization can simultaneously change the amount and timing of work exposed to the workers.

In this example, the following factors are distinct but interacting:

- worker-pool implementation;
- worker count;
- number of input batches drained per iteration;
- queue depth and backpressure;
- batch size;
- arrival rate and burstiness;
- saturation level.

If several of these change at the same time, a throughput difference cannot safely be attributed to the worker pool alone.

A drain cap is especially important under high offered load. Even if workers are capable of processing more work, a producer or coordinator that exposes only a small number of batches per iteration can become part of the bottleneck. Conversely, very large batches can increase latency or produce unfairness. The correct value is therefore workload- and load-dependent rather than simply "larger is better" or "smaller is better".

## Connection to the shred-sigverify worker-pool work

Agave PR [#14641](https://github.com/anza-xyz/agave/pull/14641) replaces the dedicated Rayon shred-sigverify pool with persistent workers. In the current design, the number of packet batches drained in one sigverify iteration is tied to the number of workers rather than to the historical fixed value of `1024`.

Conceptually, this means that two parameters are coupled:

1. **parallelism**: how many workers may execute simultaneously;
2. **admission/drain depth**: how many batches the stage exposes to the pool per iteration.

The regression reported in #15991 makes this coupling an explicit experimental question. It does **not** by itself prove that limiting the drain depth to the worker count is wrong, because #15585 changed more than one aspect of the pipeline and #15991 reverts the whole commit. A controlled experiment is required to isolate the effect.

A useful experiment for the thesis or PR validation is therefore to hold the worker pool and workload constant while varying only the drain/admission cap, for example:

- `drain_cap = workers`;
- `drain_cap = 16`;
- `drain_cap = 64`;
- `drain_cap = 1024`.

The comparison should be repeated across offered-load levels, especially near and above saturation, while measuring throughput, latency, CPU time, context switches, and queue/backlog behavior.

## Methodological lesson

This incident motivates several rules for the thesis experiments:

1. Change one performance-relevant mechanism at a time whenever possible.
2. Treat batching, queue capacity, drain/admission depth, and worker count as separate parameters rather than silently coupling them.
3. Do not validate a configuration only at the average or normal operating load.
4. Include near-saturation and overloaded conditions because bottlenecks may appear only when backlog forms.
5. Preserve the same workload and arrival trace when comparing scheduler configurations.
6. Record raw repetitions and distribution shape rather than relying only on a single average.
7. When an industrial regression is observed after a multi-part change, treat the proposed cause as a hypothesis until a controlled experiment isolates it.

## Thesis-defense relevance

This case can be used during the thesis defense as a concrete industrial motivation for the benchmark methodology.

A concise interpretation is:

> A batching limit that appeared harmless under normal Agave traffic was associated with a major throughput regression when shred traffic increased to about 5 Gbit/s. The incident shows why thread-pool evaluation must control not only worker count and execution policy, but also how work is admitted and batched. It also shows why benchmarking only the common operating regime can miss performance failures that appear near saturation.

The value of the example is not that the thesis already knows the optimal Agave batching policy. The value is that a real system exposed exactly the kind of confounding interaction that the thesis methodology is designed to separate experimentally.

## Sources

- Agave PR #15585, `retransmit: replace rayon with worker pool`: https://github.com/anza-xyz/agave/pull/15585
- Discussion of the TVU batching constants in PR #15585: https://github.com/anza-xyz/agave/pull/15585#issuecomment-5932713807
- Agave PR #15991, `Revert "retransmit: replace rayon with worker pool (#15585)"`: https://github.com/anza-xyz/agave/pull/15991
- Agave PR #14641, `Replace Rayon shred sigverify with dedicated workers`: https://github.com/anza-xyz/agave/pull/14641
