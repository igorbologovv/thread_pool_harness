# BLS finite-batch saturated throughput (with Bevy)

This independent study compares Rayon, Bevy Tasks, Threadance MPMC Spin0 and
AgaveBackoff, and Threadance RTRB backlog 1/2. It reuses the **same cached 2500
BLS certificates** from the normalized-load study, with 2000 validators,
1600 signers and seed 1.

All 2500 work units are offered **all-at-once**. This is finite-batch saturated
throughput (completed WU / measured elapsed seconds), **not** an independently
measured infinite steady-state service capacity. Queue producer backpressure,
task submission overhead and final drain contribute to elapsed. It is an
apples-to-apples benchmark of equal work and input data for all pools; avoid
calling the observed throughput a per-scheduler steady-state arrival limit
without further validation.

Four worker counts: 2/4/8/16. Six scheduler configurations. 30 randomized
blocks × 6 conditions × 2 measured repeats plus 1 warmup per invocation:
720 invocations, 1440 measured runs, 720 warmups. Existing standard perf counters
are collected in the same pass. The 2500-certificate dataset is loaded from the
preexisting dataset cache; on a cache miss preparation regenerates it.

The standard campaign system archives the experiment definition, binary hash,
raw SQLite measurements, block summaries, and paired bootstrap 95% CIs against
Rayon. Inspect sample variance, run distributions, multimodality, thermal/time
drift, throughput-per-WU consistency, and CPU scaling before drawing conclusions.
The 16-worker configuration uses SMT on the 8C/16T Ryzen 5700X, so scaling is
not expected to stay linear.

## Run on the benchmark host

    git switch experiment/bls-saturated-throughput
    git pull --ff-only
    python3 -m experiments.common.study_run --spec experiments/studies/bls_saturated_throughput.toml --check
    set -o pipefail
    systemd-inhibit --what=sleep:idle --why="Threadance saturated BLS throughput" \
      python3 -m experiments.common.study_run \
        --spec experiments/studies/bls_saturated_throughput.toml --run \
      2>&1 | tee -a bls-saturated-throughput.log

Resume by rerunning the exact --run command. Run as a normal user, with perf
permissions; keep the tracked git worktree clean. The new study uses a separate
result root and does not replace the earlier normalized-load campaigns.

After acquisition, compare mean/median throughput and block-paired bootstrap
relative effects. Keep observed all-at-once throughput separate from measured
effective throughput under paced arrivals.
