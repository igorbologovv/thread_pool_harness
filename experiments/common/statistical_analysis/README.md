# Benchmark statistical analysis

This Python 3.11+ package reads measurements without changing them. Rust code,
original measurements, existing reports and `b_thesis` are outside its scope.
Run commands from the repository root.

## Install and test

```sh
python -m venv .venv-analysis
.venv-analysis/bin/python -m pip install -r experiments/common/statistical_analysis/requirements.txt
MPLCONFIGDIR=/tmp/benchmark-analysis-mpl .venv-analysis/bin/python -m unittest discover -s tests -p 'test_statistical_analysis.py' -v
```

Dependencies have compatible version ranges. Every exported result records actual
Python/library versions; retain these versions to reproduce the same environment.
The default random seed is 20261105. Dip p-values use deterministic interpolation
of the package's critical-value tables, not Monte Carlo calibration.

## Execute

CSV input requires one physical measurement per row, a finite numeric metric and
X column, a scheduler/configuration group, and explicit block/repeat identities:

```csv
scheduler,load,block,run_index,throughput,global_order,started_unix_ns
rayon,0.5,1,0,1234,1,1234567890000000000
rayon,0.5,1,1,1240,1,1234567891000000000
```

```sh
.venv-analysis/bin/python -m experiments.common.statistical_analysis measurements.csv \
  --metric throughput --x load --scheduler scheduler --block block --repeat run_index \
  --order global_order --time started_unix_ns --seed 42 \
  --bootstrap-replicates 5000 --max-degree 3 --output /tmp/new-analysis

.venv-analysis/bin/python -m experiments.common.statistical_analysis /path/to/benchmarks.sqlite3 \
  --manifest /path/to/manifest.csv --metric perf.context-switches --x arrival_rate \
  --scheduler configuration --block block --repeat run_index \
  --strata workers delivery_mode --order global_order \
  --time started_unix_ns --output /tmp/new-counter-analysis
```

Use a **new** output directory. Existing paths are refused to prevent overwriting
results. `--no-figures` exports numbers only. `--block none --repeat none` explicitly
asserts independent observations, with an ordinary observation bootstrap.
`--repeat none` permits missing repeat identities, but disables duplicate checks.
Never use independent mode to increase the apparent sample size of repeated runs.
Time columns must be numeric (for example `started_unix_ns`); convert ISO timestamps
explicitly before analysis. A manifest's global order describes invocations;
repeats can legitimately share it. Run indices are not global chronology.

## Python API

```python
from experiments.common.statistical_analysis import Design, load_sqlite, analyze, regress
from experiments.common.statistical_analysis import export
from experiments.common.statistical_analysis.plotting import plot_estimates

rows = load_sqlite('/path/to/benchmarks.sqlite3', manifest='/path/to/manifest.csv')
design = Design(metric='throughput', x='arrival_rate', scheduler='configuration',
                block='block', repeat='run_index', strata=('workers', 'delivery_mode'),
                order='global_order', time='started_unix_ns', seed=42,
                bootstrap_replicates=5000)
result = analyze(rows, design)
regress(result, max_degree=3)  # adds models; analysis works without regression
export(result, '/tmp/new-analysis')
# Choose a complete stratum dictionary from result['conditions']:
fig = plot_estimates(result, stratum=result['conditions'][0]['stratum'])
```

`load_csv()` returns all source strings without coercing metadata. The API also
accepts a list of measurement dictionaries, allowing arbitrary derived metrics
and explicit filtering. Filter by nuisance variables or put them in `strata`;
the module cannot infer the intended experiment from configuration metadata.
Failed/incomplete runs and nonfinite/missing metric or X values cause an error;
any exclusions must be explicit and justified by the caller.

The SQLite adapter uses `mode=ro`, joins runs with experiments/sessions, and joins
optional campaign metadata by `experiment_id`. Blocks are never inferred from
session IDs or run indices. Experiment/run columns retain their schema names;
IDs have `experiment_id`/`run_id` aliases, session columns use `session.*`, session
key/value metadata use `session_metadata.*`, and conflicting manifest values use
`manifest.*`. Exact perf events become `perf.<event_name>` with accompanying
`.status`, `.unit`, `.percent_running` and runtime/derived-counter fields. Select
privilege suffixes explicitly; they are not interchangeable. Unsupported counters
remain missing. Perf passes are **always separate strata**, including for throughput.
The adapter preserves perf quality metadata, but callers must decide whether
multiplexed counters are suitable for their measurement protocol.

## Statistical meaning and limitations

* Descriptive mean/median/sample variance/sample SD, histograms, Scott-bandwidth
  Gaussian KDE, ECDF and IQR outlier flags describe **raw runs**. Outliers remain
  in all calculations. KDE is unavailable for constant/tiny samples and can
  smooth away modes or create misleading shape near bounded support.
* Hartigan's dip test uses the maintained
  [diptest implementation](https://github.com/RUrlus/diptest). Its standard p-value
  calibration assumes IID continuous samples. Raw repeated/block observations
  and ties are explicitly marked exploratory; a p-value in those cases is not
  calibrated evidence at the nominal significance level. Small samples have low
  power. Constant samples or fewer than four observations have no reported
  p-value. Nonsignificance never establishes unimodality. Multiple condition tests
  are exploratory, with no multiplicity correction or automatic mode labels.
* Estimates are means of within-condition block means: each block has equal
  weight even with unequal numbers of repeats. Bootstrap samples whole blocks
  with the same draw across all conditions within each stratum, retaining all
  repeated measurements. This preserves randomized-block dependence and avoids
  pseudoreplication. No extra within-block resampling double-counts repeat noise.
  Complete blocks are required within a stratum. Split independent campaigns or
  define globally unique block IDs before combining them; strata do not establish
  independence automatically. The method assumes independent exchangeable blocks;
  trends/correlated blocks invalidate ordinary block bootstrap inference. It does
  not model time-series dependence across blocks. One independent unit gives no
  CI; fewer than ten units produce an explicit coverage caution. Pointwise 95%
  percentile CIs are not simultaneous intervals, prediction intervals, or robust
  protection against poor sampling. No Gaussian distribution is assumed.
* Temporal/run-order diagnostics use per-condition scatterplots, Spearman
  association and lag-one rank correlation in sorted metadata order. They are
  descriptive with no IID p-values. Missing chronology yields an unavailable
  diagnostic; ties and condition/block/time confounding limit interpretation.
* Regression minimizes squared errors on equal-block estimates, equally weighting
  X levels. X is centered/scaled for stability and coefficients use ascending
  powers of that transformed X. Candidate degrees 0..max_degree (default 3) are
  selected by leave-one-X-level-out MSE; all repeats/blocks of the held-out level
  are excluded. Training folds require a residual degree of freedom and reject
  rank deficiency/poor conditioning. Near ties favor simpler models. At least
  three X levels are required for selection. Nested outer leave-one-X-level-out
  CV redoes selection without the held-out level (requires four levels), reporting
  predictions, errors and RMSE; tuning scores alone are optimistic. With small
  designs, outer folds can support fewer degrees than the final fit. Shared blocks
  mean this assesses new X levels in the existing block population, not future
  campaigns. End-level folds extrapolate; inspect them separately.
* Inspect block-mean residuals against fitted value/X, their histogram, and nested
  CV errors for curvature, heteroscedasticity and instability. Residuals are
  clustered by block and are not assumed independent or Gaussian. No parametric
  coefficient tests, curve confidence bands or prediction intervals are supplied.
  Piecewise polynomials/splines are intentionally deferred: optional flexibility
  requires enough X levels and independent justification/validation; the package
  does not automatically justify knots from sparse observations.

## Outputs and reuse

`results.json` contains configuration, complete source rows/metadata, raw shape
arrays, diagnostics and cautions, block means, CIs, scaled model coefficients,
selection/nested CV predictions, residuals, fitted grids and software versions.
`observations.csv` and `estimates.csv` provide convenient numerical tables. JSON
uses null for undefined values. Figures export as PNG and SVG: distributions and
chronology per condition, estimates by stratum, residuals/CV per model. Small
scheduler offsets affect only point/CI display; model curves use actual X values.
Experimental points are never connected, estimates are never adjusted, and fits
are never constrained to intersect confidence intervals.

Calculation modules do not import plotting. The package reuses `describe` from
`experiments/fixed_wait/diagnostics.py` and the union-column CSV writer from
`experiments/common/analysis.py`. Existing paired-effect campaign analysis stays
available unchanged. Existing study-report SVG code is tailored to relative
reference effects rather than arbitrary metrics, so the new plotting functions
consume precomputed results via Matplotlib instead.

## Independent saturation normalization

`normalize_by_baseline(result, baselines, match=('workers',))` returns a new result
with percentage ratios and whole-block bootstrap CIs that include uncertainty in
both the numerator and independent denominator acquisitions. Baseline matching
requires the same scheduler and specified stratum fields, with exactly one
positive baseline per match. Use globally unique block IDs. Shared acquisitions
share resampling indices and the same denominator is reused across load levels;
no pairing is invented between independent baseline and paced runs. Raw relative
shape/dip diagnostics condition on the measured denominator. See
`analysis/bls_throughput/README.md` for the executed BLS archive analysis.

## Corrected BLS extension

The ordered driver is `python -m analysis.bls_throughput.reanalyze`; see
`analysis/bls_throughput/README.md`. It gates collection on a positive four-warmup
audit, before calling `analyze`. Metric-wide BH, BY and Holm dip adjustments remain
exploratory because they do not repair the IID calibration of dependent raw dips.
Block-mean/repeat/pass/centered and moving-block sensitivity diagnostics supplement
ordinary block-bootstrap CIs. All suspected-shape conditions receive detail pages.

`validation.py` extends the existing polynomial fitter/selector with nested
family-selection validation of capacity-limited throughput and an a priori CPU
hinge at own capacity and a three-parameter natural spline with its single knot
chosen as the training-median X. Spline boundary constraints and knots are rebuilt
inside every held-out fold. Unsupported folds explicitly reject the alternative.
Conditional-on-degree polynomial mean bands resample whole blocks, with shared
independent denominator uncertainty for relative responses. They are pointwise,
not prediction/simultaneous intervals, and omit model-selection and X-denominator
uncertainty. No bands are claimed for noisy achieved-throughput predictors or
sparse discrete hardware scenarios. Model failures retain empirical estimates.
`plot_estimates` supports optional labels and an explicit display-only offset span;
neither changes statistical X values.
