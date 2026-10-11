# Fixed-Arrival Wait-Policy Experiment: Analysis Plan

## 1. Purpose

This experiment evaluates how Threadance worker wait policy interacts with offered load under deterministic fixed arrivals.

The two experimental variables are:

- offered load:
  \[
  \rho = \frac{\lambda S}{W}
  \]
- normalized spin duration:
  \[
  \alpha = \frac{\tau}{S}
  \]

where:

- \(\lambda\) is the task arrival rate,
- \(S\) is the scheduler-free mean single-thread service time per work unit,
- \(W\) is the worker count,
- \(\tau\) is the configured spin-before-block duration.

The experiment tests whether the relative performance of wait policies changes around the simple predicted boundary

\[
\rho^* = \frac{1}{1 + \tau/S}.
\]

The prediction is used as a mechanistic reference, not as a fitted model.

## 2. Fixed Experimental Design

The final experiment uses:

- workload: BLS aggregate verification;
- workers: 8;
- work units: 200;
- validators: 2000;
- signers per certificate: 1600;
- certificates per work unit: 1;
- deterministic fixed arrivals;
- fixed workload seed;
- one cached BLS dataset reused by all configurations;
- standard profiling mode.

The tested offered-load values are:

- 0.25
- 0.50
- 0.67
- 0.80
- 0.91
- 0.95

The tested spin-before-block durations are:

- 0 us
- 8 us
- 16 us
- 24 us
- 32 us
- 48 us
- 64 us
- 96 us

These values concentrate measurement density in the low-latency region
identified by pilot measurements before the final campaign. The normalized
value \(\tau/S\) is derived from the calibrated service time and is not used
to define the grid.

There are 48 configurations in total.

The experiment uses 10 randomized blocks. Every block contains all 48 configurations in independently randomized order.

Each configuration receives 5 measured runs per block, for 50 measured runs per configuration in total.

### Final Grid Revision

Before collection of the final campaign, pilot measurements showed that the
wait/wake transition of interest occurred below approximately 96 us. The
earlier coarse normalized grid therefore placed most measurements well above
the empirically relevant region.

The final campaign was consequently specified with the denser absolute
0--96 us grid above. This revision was made before collection of the final
campaign data.

## 3. Dataset and Calibration

The workload dataset is generated once and cached on disk.

All configurations in the experiment use the same dataset bytes.

Dataset generation and loading occur outside the measured interval.

The service time \(S\) is measured separately using the scheduler-free calibration binary.

The mean service time is used for offered-load calculations because

\[
\rho = \lambda E[S] / W.
\]

The calibration artifact, raw calibration samples, source revision, CPU model, and environment information must be retained.

A calibration is valid only for the matching experiment code revision and machine configuration.

## 4. Primary Outcomes

The primary performance outcomes are:

1. elapsed wall-clock time per benchmark run;
2. throughput in work units per second.

Because work-unit count is fixed, elapsed time and throughput contain equivalent performance information. Elapsed time is treated as the primary latency-style measure, while throughput is reported for interpretability.

For each configuration, report:

- number of valid runs;
- mean;
- median;
- standard deviation;
- variance;
- first quartile;
- third quartile;
- interquartile range;
- minimum;
- maximum;
- 95% confidence interval for the mean;
- 95% confidence interval for the median where useful.

Confidence intervals should be obtained using a nonparametric bootstrap unless there is a specific reason to use another estimator.

The analysis must not assume Gaussian sampling distributions merely for convenience.

## 5. Secondary System-Level Outcomes

Standard profiling measurements are secondary explanatory outcomes.

These include, where available:

- CPU task-clock;
- cycles;
- instructions;
- branches;
- branch misses;
- context switches;
- CPU migrations.

Derived quantities such as instructions per cycle may be calculated when useful.

These metrics are used to explain observed performance differences and are not substitutes for the primary elapsed-time measurements.

## 6. Distribution Diagnostics

Raw run-level observations must be retained.

For every configuration, inspect the empirical distribution before interpreting summary statistics.

At minimum, examine:

- histogram or density estimate;
- empirical quantiles;
- run-order plot;
- block-level distribution;
- skewness or visibly asymmetric tails;
- possible multimodality or bimodality.

Multimodal or otherwise unusual distributions must be reported rather than collapsed into a single mean without comment.

No transformation should be used solely to make the data appear Gaussian.

## 7. Outliers

No observation is removed solely because it is statistically extreme.

An observation may be excluded only when there is independent evidence of measurement invalidity, such as:

- benchmark failure;
- missing or corrupt performance counters;
- interrupted execution;
- known system disturbance;
- invalid database record.

Any exclusion must be documented together with its reason.

The main analysis should otherwise retain the raw observations.

Sensitivity analysis may additionally report results with clearly identified anomalous observations excluded, but such results must not silently replace the full-data analysis.

## 8. Randomized Blocks and Temporal Drift

The randomized block structure must be preserved in the analysis.

Run order and block number must be used to inspect:

- thermal drift;
- CPU-frequency drift;
- long-term machine-state changes;
- background-system disturbances;
- systematic differences between early and late blocks.

At minimum, plot the primary outcome against global experiment order and compare block-level summaries.

If block effects are visible, report them explicitly.

Comparisons between configurations should account for the fact that every configuration occurs once per block.

## 9. Pairwise and Relative Comparisons

The main scientific comparison is how different spin durations perform at the same offered load.

For each \(\rho\), compare the tested \(\tau/S\) configurations using:

- absolute difference in elapsed time;
- relative difference in elapsed time;
- absolute or relative throughput difference;
- uncertainty intervals for these differences where practical.

The zero-spin configuration serves as the immediate-block reference.

The analysis should emphasize effect sizes and uncertainty rather than only binary significance decisions.

## 10. Prediction Boundary

For each tested spin duration, compare the observed transition in relative performance with

\[
\rho^* = \frac{1}{1 + \tau/S}.
\]

The prediction is considered qualitatively supported if the observed change in relative behavior occurs near the predicted load region.

The analysis must not claim that the formula is exact unless the data support that conclusion.

Disagreement between the prediction and measurement is itself an experimental result and should be investigated using system-level measurements.

## 11. Multiple Comparisons

The experiment contains many related configuration comparisons.

The analysis should therefore avoid interpreting isolated nominal p-values as strong evidence.

Primary conclusions should be based on:

- effect magnitude;
- confidence intervals;
- consistency across neighboring load points;
- consistency across randomized blocks;
- supporting system-level measurements.

If formal hypothesis tests are later added across many configurations, the multiple-comparison procedure must be stated explicitly.

## 12. Deep Profiling

Deep profiling is diagnostic and is not run across the complete configuration matrix.

Deep profiling configurations should be selected only after the primary standard-profile sweep is complete.

Selection criteria may include:

- configurations near an observed crossover;
- unexpectedly good or bad configurations;
- configurations with unusually high variance;
- configurations where system-level counters do not explain the wall-time result.

Deep profiling passes are separate physical benchmark runs.

Measurements from different deep profiling passes must not be described as simultaneous counters from the same execution.

## 13. Time-Series Analysis

Measurement order must be preserved.

Time-series methods, including Kalman filtering, may be considered only if the run sequence shows a meaningful latent drift process or correlated temporal structure.

A Kalman filter will not be applied automatically to independent benchmark repetitions.

If used, its state model and purpose must be stated explicitly.

## 14. Reproducibility

The following artifacts must be retained:

- raw benchmark database;
- raw run-level measurements;
- experiment plan;
- randomized execution order;
- calibration artifact;
- cached dataset identity/path;
- environment metadata;
- git commit;
- CPU and kernel information;
- analysis scripts;
- generated tables and figures.

Analysis must be reproducible from the retained raw data without rerunning the benchmark campaign.

## 15. Decision Rule for Main Conclusions

A performance difference will be treated as practically meaningful only when:

- its magnitude is non-trivial relative to run-to-run variation;
- its uncertainty interval is sufficiently narrow to support the interpretation;
- the direction is reasonably consistent across randomized blocks;
- the result is not driven by a small number of anomalous runs.

The final conclusions will prioritize stable performance regions and crossover behavior rather than single-point winners.
