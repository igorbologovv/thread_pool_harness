# BLS warmup-count diagnostic

Question: does increasing the number of unprofiled warmup batches eliminate
the slow first measured batch at 8 and 16 Rayon workers?

Each of 3 worker-count campaigns (4, 8, 16) uses 20 randomized blocks.
Within each block, **four separate Rayon processes** run in randomized
order, with warmup counts 1, 2, 4 or 8. Each process constructs a new pool,
loads the same 2500-certificate dataset outside timing, executes its assigned
number of unprofiled 2500-verification warmups and then performs **eight**
consecutive standard-perf measured 2500-verification batches. The underlying
BLS logic, dataset, perf event selection, perf startup/teardown and measured
intervals are unchanged. The randomized warmup-order experiment varies
warmup count, but also necessarily changes total work before recording.

Expected: 3 campaigns, 240 independent process invocations, 900 total
unprofiled warmup batches, 1920 measured physical runs. Each worker
count is a separate campaign, so worker-count acquisition time is not
randomized across campaigns. The same cached input is used in all runs.

Use all raw runs for analysis; inspect **measured run index 0** versus
indices 1..7 separately. Pair warmup variants by randomized block for
between-condition comparisons; pair measured positions within each process.
Do not treat eight measured batches within a process as independent
blocks. Test for drift, multimodality and convergence; the presence of
a plateau cannot identify a specific hardware mechanism.

    git switch experiment/bls-warmup-sweep
    git pull --ff-only origin experiment/bls-warmup-sweep
    python3 -m unittest tests.test_bls_warmup_sweep
    python3 -m experiments.common.study_run --spec experiments/studies/bls_warmup_sweep.toml --check

Run as a normal user, preferably with no other load:

    set -o pipefail
    systemd-inhibit --what=sleep:idle --why="BLS warmup sweep" \
      python3 -m experiments.common.study_run \
        --spec experiments/studies/bls_warmup_sweep.toml --run \
      2>&1 | tee -a bls-warmup-sweep.log

Results are archived separately at
`results/studies/bls-warmup-sweep/campaigns`.
The common report compares the four warmup conditions, but averages
measured run positions. For the primary diagnostic, use the
position-stratified CSV:

    python3 -m experiments.studies.analyze_bls_warmup_sweep \
      --root results/studies/bls-warmup-sweep/campaigns

Outputs: warmup_position_summary.csv, warmup_position_paired.csv,
warmup_first_position_comparison.csv, warmup_report.md.
No change to prior recorded campaigns.
