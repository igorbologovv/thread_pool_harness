# Fixed-Arrival Wait-Policy Experiment: Analysis Plan

## 1. Purpose

This experiment evaluates how Threadance worker wait policy interacts with offered load under deterministic fixed arrivals.

The two experimental variables are:

- nominal offered load:
  \[
  \rho_{nominal} = \frac{\lambda S}{W}
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

The quantity \(\rho_{nominal}\) is an experimental control parameter rather than a claim about exact runtime worker utilization. It is derived from scheduler-free single-thread service time and therefore does not account for multicore frequency changes, shared-cache effects, memory-system contention, or scheduler overhead. Where useful, the final analysis should compare nominal load with an empirical load measure based on observed saturated multicore service capacity.

## 2. Fixed Experimental Design

The final experiment uses:

- workload: BLS aggregate verification;
- workers: 4;
- work units: 200;
- validators: 2000;
- signers per certificate: 1600;
- certificates per work unit: 1;
- deterministic fixed arrivals;
- fixed workload seed;
- one cached BLS dataset reused by all configurations;
- standard profiling mode.

The main wait-policy campaign deliberately uses four workers rather than saturating the full machine. This leaves CPU headroom for the benchmark thread, arrival pacer, performance-counter control, kernel work, and unavoidable background activity. Worker-count scaling is treated as a separate experiment, where worker counts can be varied from one worker through the physical-core and logical-CPU ranges. The fixed-wait experiment therefore studies wait-policy behavior at a controlled worker count rather than conflating wait policy with full-machine scaling.

The tested nominal offered-load values are:

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

The experiment uses 20 randomized blocks. Every block contains all 48 configurations in independently randomized order.

Each configuration receives 2 measured runs per block, for 40 measured runs per configuration in total. Every newly created scheduler instance receives one identical warm-up run before those measured runs.

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

The cached dataset used by the campaign must be identified by its path, byte size, cache-format version, workload parameters, and SHA-256 checksum. The identity record is retained with the campaign artifacts so that all reported configurations can be tied to the exact same input bytes.

The service time \(S\) is measured separately using the scheduler-free calibration binary.

The mean service time is used for nominal offered-load calculations because

\[
\rho_{nominal} = \lambda E[S] / W.
\]

This calibration is intentionally scheduler-free. Consequently, \(\rho_{nominal}\) should not be described as measured CPU utilization or exact queueing-system utilization.

The calibration artifact, raw calibration samples, source revision, CPU model, and environment information must be retained.

A calibration is valid only for the matching calibration-relevant experiment code and machine configuration.

Changing the fixed-wait worker count does not by itself invalidate the scheduler-free single-thread service-time calibration. The worker count enters the nominal offered-load calculation separately through \(W\).

## 4. Primary Outcomes

The main scientific question in the fixed-arrival experiment is the CPU cost of a wait policy while work arrives at an externally controlled rate.

The primary performance outcome is therefore:

1. CPU task-clock per completed work unit.

The main supporting CPU-cost outcome is:

2. cycles per completed work unit.

For paced runs below saturation, total elapsed wall-clock time is strongly constrained by the externally imposed arrival window. Two wait policies may therefore consume substantially different CPU resources while producing nearly identical total elapsed time. Total elapsed time is not used as the primary wait-policy efficiency measure in that regime.

A separate responsiveness outcome is the completion drain delay after the final actual submission:

\[
T_{drain} = T_{completion} - T_{last\ actual\ submission}.
\]

This quantity measures how long the pool takes to finish outstanding work after the paced input stream has ended. It is especially relevant near saturation, where backlog can accumulate.

Elapsed wall-clock time and throughput are retained and reported as secondary end-to-end outcomes. Because work-unit count is fixed, they contain equivalent information for a given run.

For each configuration, report at least:

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

Confidence intervals should be obtained using a nonparametric bootstrap unless there is a specific reason to use another estimator. For configuration comparisons in the randomized-block experiment, bootstrap resampling must preserve the block structure rather than treating all run-level observations as independent.

The analysis must not assume Gaussian sampling distributions merely for convenience.

## 5. Secondary System-Level Outcomes

Standard profiling measurements are explanatory outcomes supporting the primary CPU-efficiency result.

These include, where available:

- cycles;
- instructions;
- branches;
- branch misses;
- context switches;
- CPU migrations;
- page faults.

Derived quantities such as instructions per cycle may be calculated when useful.

These metrics are used to explain observed performance differences. They should not replace the primary task-clock-per-work-unit comparison unless a specific measurement limitation is documented.

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

The two repeated runs of one configuration inside one block are repeated measurements within the same local machine-state period; they must not be treated as if they provided the same temporal independence as measurements from different blocks.

For inferential comparisons, first summarize repeated runs within each block/configuration, then compare configurations within the same block. Uncertainty for cross-configuration effects should be estimated by resampling whole blocks or by another method that explicitly models the block structure.

## 9. Pairwise and Relative Comparisons

The main scientific comparison is how different spin durations perform at the same nominal offered load.

For each \(\rho_{nominal}\), compare every tested spin duration with the zero-spin immediate-block reference within the same randomized block.

For a metric \(y\), define the block-level relative effect as

\[
\Delta_{b,\rho,\tau}
=
\frac{\bar y_{b,\rho,\tau} - \bar y_{b,\rho,0}}
{\bar y_{b,\rho,0}},
\]

where \(\bar y_{b,\rho,\tau}\) is the mean of the repeated runs for that configuration inside block \(b\).

Primary pairwise comparisons should report:

- absolute difference in task-clock per work unit;
- relative difference in task-clock per work unit;
- absolute or relative difference in cycles per work unit;
- drain-delay difference where informative;
- uncertainty intervals based on the randomized-block structure.

Elapsed-time and throughput differences may also be reported, particularly near saturation, but are not the main efficiency comparison below saturation.

The analysis should emphasize effect sizes and uncertainty rather than only binary significance decisions.

## 10. Prediction Boundary

For each tested spin duration, compare the observed transition in relative performance with

\[
\rho^* = \frac{1}{1 + \tau/S}.
\]

The prediction is considered qualitatively supported if the observed change in relative behavior occurs near the predicted nominal-load region.

The analysis must not claim that the formula is exact unless the data support that conclusion.

Disagreement between the prediction and measurement is itself an experimental result and should be investigated using system-level measurements and, where relevant, the difference between nominal offered load and empirical multicore capacity.

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
- configurations where system-level counters do not explain the primary CPU-efficiency result.

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
- cached dataset identity/path and checksum;
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
