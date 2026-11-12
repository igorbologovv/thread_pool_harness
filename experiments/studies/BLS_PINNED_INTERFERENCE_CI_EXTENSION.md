# Stressed pinned BLS — independent CI extension

The original 42 offered-load campaigns remain unchanged. This new runner
collects additional independent randomized blocks at 10 prioritized cells,
all with the **same 7 schedulers**, 200-ms target arrival window, worker
pinning, background CPU/cache stressor, 4 warmups and 2 measured runs
per block, and the original BLS dataset and cpu-cache PMU pass.

## Selection

A bootstrap of the 20 archived block means in each of 294 cells found that
118 mean CPU-time confidence intervals had full width exceeding 4% of their
mean (wider than ±2%); 248 exceeded 2% (wider than ±1%).

Priority cells for the initial precision/stability pilot:
- Two workers, fixed arrivals: 45%, 50%.
- Four workers, bursty arrivals: 25%.
- Eight workers, bursty arrivals: 25%, 40%, 50%.
- Sixteen workers, bursty arrivals: 25%, 40%, 50%, 65%.

Each campaign contains every scheduler. This is a **pilot**, not a claim that
40 extra blocks will bring all CIs below a precision target. In the worst
bursty cases, the extrapolated required block counts are very high and
between-batch stability should be checked before collecting hundreds.

## Commands

From the clean tracked repository root:

```sh
git pull --ff-only
python3 -m unittest tests.test_bls_pinned_interference_ci_extension
python3 -m experiments.studies.run_bls_pinned_interference_ci_extension --check
sudo sysctl -w kernel.perf_event_paranoid=-1

systemd-inhibit --what=sleep:idle --why='Stressed BLS precision pilot' \
  python3 -m experiments.studies.run_bls_pinned_interference_ci_extension \
  --run --batch 1 --blocks 40
```

You can first run a single cell by specifying
`--targets 2:fixed:50` to both `--check` and `--run`.
Repeated `--run --batch 1 --blocks 40` resumes existing matching campaigns.

Totals for all 10 cells: 2,800 invocations, **5,600 measured runs**,
11,200 warmups, 40 new blocks/cell. Original 20 blocks remain available
in `results/studies/bls-pinned-interference-load-sweep/campaigns`.
New runs go exclusively to
`results/studies/bls-pinned-interference-ci-extension/campaigns`.

## Essential validation and interpretation

- Do not pool block ID 1 from different campaigns; the resampling unit
  must be the unique (campaign, block) pair.
- Check actual producer arrival lateness, stressor logs, perf events and
  batch/time effects before pooling old and new campaigns.
- Report per-configuration CPU-time 95% CIs as well as paired scheduler
  contrasts. The uncertainty in differences can be much smaller than that
  in each separate mean.
- Check multimodality and time drift rather than assuming that a large
  number of runs fixes an unstable measurement distribution.
- Do not change the number of blocks for an already-started batch ID;
  use a new batch ID for further independent collection.
