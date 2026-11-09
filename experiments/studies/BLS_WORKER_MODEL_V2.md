# BLS worker-count model v2 — independent 3/7/9/11/14 holdout

## Research question and hypothesis

Can CPU-time-per-work-unit models fitted on the **seven measured worker
counts 2,4,6,8,10,12,16** predict previously unseen 3,7,9,11,14 workers,
without using those held-out results when choosing the model or coefficients?

Models are conditional on **Ryzen 7 5700X**, the 2500-certificate cached BLS
aggregate-verification workload, the six scheduler configurations, arrival
mode, nominal 50% offered load, and the experimental runtime/environment.
They are *not* theoretical CPU-time laws.

All 12 chosen equations, the historical seven means, model-selection scores,
and all 60 predictions are **frozen in**
`experiments/studies/bls_worker_model_v2_frozen.json` before collecting
new holdout measurements.

The source of the historical means is the completed v1 validation file
`held_out_predictions.csv` (SHA256:
`46d32d69e6c01b71af354f007e2a6f79d365a7b73cf22cd51543444e6623ec84`).
Means for 6/10/12 come from the `observed_ms_per_wu` column; the
2/4/8/16 means are reconstructed *exactly* from the original, frozen
four-point cubic coefficients (rather than mixing in unrelated experiments).

**Critical limitation:** v1 combined 20-block original measurements for
2/4/8/16 and 40-block separate-campaign measurements for 6/10/12;
the older nominal 50% rates were roughly 48.85–49.72% of later rechecked
Rayon capacity. The larger 1024-WU campaign is **not** pooled here.
Differences between campaigns/host conditions may affect the fitted curve.

## Mathematical models

Define `z=(workers - 8)/8`. Units of `y` are milliseconds of task-clock per WU.
Candidate model bases (fitted by least squares with equal weight for each X):

- linear: `y=a+b*z`
- quadratic: `y=a+b*z+c*z^2`
- cubic: `y=a+b*z+c*z^2+d*z^3`
- continuous piecewise quadratic with a *pre-specified* knot at 8:
  `y=a + bL*min(z,0) + cL*min(z,0)^2 + bR*max(z,0) + cR*max(z,0)^2`

For each of the twelve scheduler/mode combinations, remove one entire
worker count, fit to the remaining six, predict the excluded mean, and
calculate RMS percentage error over seven held-out X values (LOOCV).
The model with lowest RMSPE is selected, then its coefficients are
refitted using all seven X values. The frozen JSON contains exact
coefficients and predictions; there is no post-acquisition reselection.
This is exploratory, small-n model selection: LOOCV is not a fully
independent test because it is used to choose the form.

| Mode | Scheduler | Frozen model |
|---|---|---|
| Fixed | Rayon | Cubic |
| Fixed | Bevy | Cubic |
| Fixed | MPMC Spin(0) | Piecewise quadratic (knot 8) |
| Fixed | MPMC Agave | Linear |
| Fixed | RTRB backlog 1 | Linear |
| Fixed | RTRB backlog 2 | Linear |
| Bursty | Rayon | Piecewise quadratic (knot 8) |
| Bursty | Bevy | Linear |
| Bursty | MPMC Spin(0) | Piecewise quadratic (knot 8) |
| Bursty | MPMC Agave | Piecewise quadratic (knot 8) |
| Bursty | RTRB backlog 1 | Linear |
| Bursty | RTRB backlog 2 | Linear |

The piecewise knot is **a pre-specified hardware-motivated candidate**.
Without worker pinning, the experiment cannot prove that the actual threads
cross the physical-core/SMT boundary exactly at eight. These are
phenomenological approximations; don't call them physical laws.

## Experimental protocol

| Field | Value |
|---|---|
| Frozen training X | 2, 4, 6, 8, 10, 12, 16 |
| Independent validation X | **3, 7, 9, 11, 14** |
| Modes | fixed-arrivals, bursty-arrivals |
| Configurations | Rayon, Bevy, MPMC-SP0, MPMC-AB, RTRB b1/b2 |
| Offered rate | 0.5 × measured Rayon finite-batch 2500-WU saturation for each X |
| Paced work units | `ceil(0.2 * offered_rate) + 1` (at least 200 ms window) |
| BLS dataset | 2500 cached certificates; validators 2000, signers 1600, seed 1 |
| Randomized blocks | 40 per new worker count/mode |
| Warmups | 4, then two measured repeats in each of two PMU passes |
| PMU passes | cpu-cache, l1-branches |
| New saturation calibrations | 5 Rayon-only campaigns, 2500 WU, 30 blocks × 3 |
| New paced campaigns | **10**: 5 workers × 2 modes |
| New paced invocations | **2400**: 10 × 40 × 6 |
| New measured physical runs | **9600**: 2400 × 2 repetitions × 2 passes |
| New output root | `results/studies/bls-worker-model-v2/` |

All measured worker configurations remain isolated by design. Neither the
v1 200-ms nor the separate 1024-WU results are overwritten.

## Commands

Run from the checkout at branch `experiment/bls-worker-model-v2-holdout`.

```bash
git status --short
python3 -m unittest tests.test_bls_worker_model_v2
python3 -m experiments.studies.run_bls_worker_model_v2 --check

sudo sysctl -w kernel.perf_event_paranoid=1
python3 -m experiments.studies.check_perf_scope

# Only new 3/7/9/11/14 all-at-once throughput references.
set -o pipefail
systemd-inhibit --what=sleep:idle --why='BLS model v2 capacity calibration' \
  python3 -m experiments.studies.run_bls_worker_model_v2 --capacity \
  2>&1 | tee -a bls-model-v2-capacity.log

# Ten independent fixed/bursty paced campaigns.
systemd-inhibit --what=sleep:idle --why='BLS model v2 held-out experiment' \
  python3 -m experiments.studies.run_bls_worker_model_v2 --run \
  2>&1 | tee -a bls-model-v2-holdout.log

# Check predictions without changing the frozen equations.
python3 -m experiments.studies.run_bls_worker_model_v2 --evaluate
```

**Do not edit the branch or switch commits between calibration and collection.**
The runner binds its capacity reference to the exact source revision and hash
of the frozen model. Run as normal user (not `sudo`) after configuring perf
permissions, with browser/other interference stopped.

The `--evaluate` stage reads original archived training raw runs from
`results/studies/bls-normalized-deep-final/campaigns` for 2/4/8/16 and
`results/studies/bls-worker-cubic-holdout-200ms/campaigns` for 6/10/12.
The pre-frozen historical means must agree, otherwise the script aborts.
It reads the new holdouts from the v2 output root.

**Inference:** for each holdout X, compare frozen predictions to the observed
40-block mean. The evaluation's 95% percentile bootstrap interval for the
prediction error resamples full randomized blocks, combines refits of the
*frozen model class* to training block means with independent holdout
resampling, and does **not** reselect the class. Report error magnitude,
confidence interval, and a practically acceptable percent-error threshold
chosen before inspecting holdouts. Distinguish goodness of fit from
prediction, and avoid treating a model prediction as exact merely because its
error CI includes zero. Multiple testing among 60 errors is exploratory;
provide multiplicity-aware discussion.

Results: `results/studies/bls-worker-model-v2/validation/frozen_v2_holdout_predictions.csv`.
