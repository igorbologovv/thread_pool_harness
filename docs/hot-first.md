# Experimental hot-first ring scheduler

This scheduler is **harness-only**. It implements the public `QueueTopology` and `WakePolicy` traits of the unchanged crates.io `threadance = "0.0.1"` dependency. The published crate and the baseline Threadance scheduler are unaffected.

## Algorithm

- Allocate one contiguous task-slot array and divide it into one single-consumer ring per worker.
- Serialize producer submissions (the API allows concurrent callers), then try the hot worker W0 first, followed by W1, W2, etc.
- Each worker may have at most `backlog + 1` unfinished tasks: one executing and `backlog` queued. Slots are released for reuse only when task execution completes, including unwind.
- On the first submission to an idle worker, set its pending-wake bit. Threadance's existing `wake_one()` call consumes pending bits and unparks the matching worker(s).
- Each worker reads only its own ring, without competing for tasks with other consumers. After its queue empties it uses the selected spin-then-park or Agave-style backoff policy.
- If all workers are at their configured limits, producers wait for capacity. Shutdown drains accepted work; panic handling is delegated to Threadance.

`backlog = 0` is a direct-handoff-like mode (one in-flight task per worker), **not** a literal zero-slot ring. It is not equivalent to an unrestricted global MPMC scheduler.

## Run

```bash
cargo run --release -p threadance-harness -- \
  --scheduler threadance-hot-first \
  --workload compute-heavy \
  --workers 4 \
  --queue-capacity 1024 \
  --hot-first-backlog 8 \
  --threadance-wait-policy agave-backoff \
  --work-units 10000 \
  --operations-per-work-unit 20 \
  --bursty-arrivals \
  --arrival-rate 5000 \
  --warmup 5 \
  --runs 50 \
  --profile standard
```

The reserved slot count is `workers * (backlog + 1)`, which must not exceed `--queue-capacity`. Actual capacity is that reserved count, not the whole global bound. The backlog is stored in `experiment.extra_config_json` and the scheduler is recorded as `threadance-hot-first`.

## Experimental caveats

This is not yet a production pool. To preserve the published 0.0.1 API, every queued task is wrapped with a completion guard, producing an additional allocation and reference-count activity. Producer submissions are serialized with a mutex, unlike a true lock-free MPSC implementation. Interpret throughput comparisons with these costs included, and record producer blocking alongside CPU/wake costs in later experiments.

Tests cover zero and nonzero backlog, handoff to a second worker, wraparound under concurrent producers, exact-once execution and repeated idle transitions. A passing CI run is required before treating the topology as correct.
