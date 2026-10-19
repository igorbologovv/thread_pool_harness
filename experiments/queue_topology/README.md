# Queue topology experiment

This experiment is now a TOML configuration consumed by the shared campaign
engine. See [experiment workflows](../README.md) for complete instructions.

From the repository root:

```sh
python experiments/queue_topology/run.py plan
python experiments/queue_topology/run.py prepare
sudo python experiments/queue_topology/run.py collect
python experiments/queue_topology/analysis.py results/queue_topology/<campaign-directory>
```

The TOML config compares Rayon, shared-queue Threadance, and Hot-first
Threadance at two bursty arrival rates, using 20 randomized blocks.
Each condition gets one warm-up and two measured runs. Workload data is
prepared outside the timed runs. Per-block arrival seeds are held constant
across conditions; the execution order is independently shuffled per block.

The primary metric is perf task-clock per work unit. Interpret comparisons
using paired differences, bootstrap confidence intervals, run-order plots,
and actual-arrival timing. The fixed settle pause is not an active system
stability gate. Bounded queue capacities are not semantically equivalent
to the unbounded Rayon queue.
