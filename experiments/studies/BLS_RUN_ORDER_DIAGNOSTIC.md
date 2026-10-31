# BLS measured-run position diagnostic (Rayon)

A narrow investigation of the observed run-order effect in the 2026-10-31
BLS saturated-throughput campaign, **not** a new scheduler comparison or
proof of sustained capacity.

- Same fixed cached dataset: 2500 distinct BLS certificates, 2000 validators,
  1600 signers, seed 1; each batch executes all 2500 verifications.
- Rayon only, at 4, 8 and 16 workers. 4 workers is the control.
- 20 separate process invocations per worker count (one configuration in each
  randomized block). Within each invocation: create one pool, run one
  **unprofiled** warmup, then eight sequential **standard-perf-profiled**
  measured batches with fresh perf attachment per measured batch.
- Preserve every position (run_index 0..7), plus block/invocation identity
  and raw hardware counters. Do **not** average the eight positions before
  diagnosing the effect.
- Three campaigns, 60 process invocations, 60 unprofiled warmups, 480
  measured physical runs. No explicit inter-invocation settling delay.
- Reuses the original common harness and acquisition protocol; no changes
  to BLS execution, scheduler implementation, or perf mechanism.
- As in the original, the :u-scoped context-switches and migrations cannot
  be used for scheduler diagnostics. The dedicated report intentionally
  excludes those counters.

## Run on the benchmark machine

    git switch experiment/bls-run-order-diagnostic
    git pull --ff-only origin experiment/bls-run-order-diagnostic
    python3 -m unittest tests.test_bls_run_order_diagnostic tests.test_study_report
    python3 -m experiments.common.study_run \
      --spec experiments/studies/bls_run_order_diagnostic.toml --check

Once satisfied with the preview, with other workloads shut down:

    set -o pipefail
    systemd-inhibit --what=sleep:idle --why="BLS run-order diagnostic" \
      python3 -m experiments.common.study_run \
        --spec experiments/studies/bls_run_order_diagnostic.toml --run \
      2>&1 | tee -a bls-run-order-diagnostic.log

The command uses the existing prepare/collect/resume/SQLite infrastructure.
Reusing --run resumes an interrupted campaign, preserving completed blocks.
Input data is loaded once per invocation outside measured time, just as in
the earlier experiment. Never run as root; existing perf permissions apply.

Then generate the **position-stratified diagnostic report**, which reads
only archived CSVs and does not run benchmarks:

    python3 -m experiments.studies.analyze_bls_run_order \
      --root results/studies/bls-run-order-diagnostic/campaigns

Outputs in that root:

- run_position_summary.csv: mean, median, variance, and 95% *descriptive*
  block-bootstrap intervals at each measured position.
- run_position_paired_deltas.csv: paired percentage changes against the
  first measured position **within the same process invocation**, with
  bootstrap intervals across the 20 invocations.
- run_position_throughput.svg: mean throughput against position 1..8 with
  worker-count-specific lines.
- run_position_report.md: concise per-worker comparison including
  CPU cycles, instructions, task-clock and branch misses.

The report does not automatically label a plateau; examine late-position
paired differences, variability and time trends. The bootstrap assumes
independent process invocations and is descriptive if temporal drift remains.
A plateau does not, by itself, identify cache warming, frequency, affinity
or perf side effects as the cause. A later controlled intervention would be
required to distinguish these mechanisms.

The generic study report is supported for this single-config design, with
no fictitious scheduler comparisons.
