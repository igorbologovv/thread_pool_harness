# Corrected BLS reanalysis

[Results](output/SUMMARY.md), [warmup eligibility](output/WARMUP_AUDIT.md),
[distribution report](output/DIAGNOSTIC_REPORT.md), [figure atlas](output/FIGURES.md).
The same driver produces [CPU results](../bls_cpu_time/output/SUMMARY.md).

Run from the repository root, with new output directories:

```sh
python -m venv --system-site-packages .venv-analysis
.venv-analysis/bin/python -m pip install -r experiments/common/statistical_analysis/requirements.txt
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-mpl .venv-analysis/bin/python -m analysis.bls_throughput.reanalyze \
  --stage all --archives b_thesis --extracted analysis/bls_throughput/extracted \
  --throughput-output /tmp/bls-throughput-reproduced --cpu-output /tmp/bls-cpu-reproduced \
  --bootstrap 5000 --curve-bootstrap 2000 --seed 20261105
```

Stages run in order: `distributions`, `relative-distributions`, `performance`, `models`, `report`.
The first stage verifies archive hashes, audits warmup eligibility before metric
collection, exports the clean physical dataset and all-condition diagnostics, then
renders all distributions and every suspected-shape detail. The relative-distributions stage exposes own-capacity percentage axes alongside
absolute units for the same physical distributions. The performance stage
adds serial-block bootstrap sensitivity and empirical figures. Only then does the
model stage run polynomial/nested X-level validation, physical alternatives and
conditional block-bootstrap curve uncertainty. The report stage verifies outputs.
Stages record completion timestamps. Numerical exports refuse replacement; figure
rendering reuses verified complete PNG/SVG pairs. No benchmarks or PDF generation.

`eligibility.py` fails closed: exactly four warmup executions must be established
by original config/manifest/command/SQLite records plus known executable identity,
or by a preserved positive per-experiment verification chain with matching source
and binary hashes. It distinguishes freshly checked from archived evidence. Old
one-warmup campaigns and unauthenticated dirty executables never enter primary
statistics. Raw archives and measurements are preserved. Preparation/smoke data and
alternative representations receive explicit exclusion/deduplication records.

The driver reuses `experiments/common/statistical_analysis` for adapters, shape/dip
statistics, equal-block estimation/bootstrap, independent baseline normalization,
polynomial selection/nested CV, exports and estimate/residual plotting. It reuses
the existing CPU shape investigations and distribution atlas. Added functionality
is limited to eligibility, multiplicity, serial-block sensitivity, physically
constrained alternatives, curve uncertainty, figure adapters and BLS reporting.

Offered load is scheduled WU/s; achieved throughput is completed WU/s. Percentage
axes divide by the same scheduler's four-warmup saturation recheck at the same
worker count. Explicit denominators are exported. Cohorts, perf passes, scheduler
variants and arrival modes stay separate. Each load campaign has its own globally
namespaced blocks; matching local block numbers never invent pairing. CPU-vs-worker
plots compare separate acquired hardware scenarios and disclose changing offered
rates/batch lengths. See the reports for uncertainty, modality and provenance limits.

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-tests .venv-analysis/bin/python -m unittest discover -s tests -p 'test_statistical*.py' -v
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-tests .venv-analysis/bin/python -m unittest discover -s tests -p 'test_bls_reanalysis.py' -v
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-tests .venv-analysis/bin/python -m unittest discover -s tests -p 'test_bls_cpu_distributions.py' -v
.venv-analysis/bin/python -m unittest discover -s tests -p 'test_bls_throughput_archives.py' -v
```
