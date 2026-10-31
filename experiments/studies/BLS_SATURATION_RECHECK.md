# BLS saturation rerun after warmup diagnostics

The 2026-10-31 diagnostics found a systematic slow first measured
Rayon batch with a single warmup at 8/16 workers. Two warmups removed
the large jump in Rayon. This fresh independent six-scheduler
comparison uses four unprofiled warmups as a conservative common
protocol; the sufficiency of four warmups has **not yet** been
independently validated for Bevy/Threadance. Three standard-perf
measurements per process retain order for later diagnostics.

Same immutable 2500-certificate BLS dataset, validators 2000,
signers 1600, seed 1, all-at-once delivery; four worker counts
2/4/8/16 and six schedulers. 30 randomized blocks per worker count.
Total 720 invocations, 2880 warmups and 2160 profiled runs.
Raw positions and perf captures remain in SQLite.
The old datasets and campaigns are not changed or combined.

On the benchmark machine, first verify kernel events:

    cat /proc/sys/kernel/perf_event_paranoid

If needed:

    sudo sysctl -w kernel.perf_event_paranoid=1

Confirm unrestricted process-scheduler counters using:

    python3 -m experiments.studies.check_perf_scope

This preflight must succeed before collection. In particular, the
archived old standard perf captures had zero user-only :u context
switches; those are **not** valid measurements of kernel scheduling.
A recorded zero migration count is permitted if scope is correct.
Do not retrospectively relabel old counters.

Review study matrix and run smoke checks:

    python3 -m unittest tests.test_bls_saturation_recheck
    python3 -m experiments.common.study_run \
      --spec experiments/studies/bls_saturation_recheck.toml --check

Run only with a clean tracked worktree, normal user, without competing
benchmark processes:

    set -o pipefail
    systemd-inhibit --what=sleep:idle --why="BLS saturation recheck" \
      python3 -m experiments.common.study_run \
        --spec experiments/studies/bls_saturation_recheck.toml --run \
      2>&1 | tee -a bls-saturation-recheck.log

The study is resumable. Outputs are stored independently at
`results/studies/bls-saturation-recheck/campaigns/`.

Interpret the result as finite-batch completion throughput, **not**
validated indefinitely sustainable service capacity. Before treating
scheduler rankings as confirmatory, examine the separate measured-run
positions, within-block uncertainty and differences in cache/frequency
and effective placement. Context switches are a resource metric,
not proof of causation. If another scheduler still exhibits run-order
bias after four warmups, reconsider the protocol rather than pooling
those repetitions.
