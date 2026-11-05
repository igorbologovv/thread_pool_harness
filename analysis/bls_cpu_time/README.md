# Corrected BLS CPU efficiency analysis

[Results](output/SUMMARY.md), [warmup audit](output/WARMUP_AUDIT.md),
[distribution report](output/DIAGNOSTIC_REPORT.md), [figure atlas](output/FIGURES.md).

Use the shared [reanalysis driver and reproduction commands](../bls_throughput/README.md).
The previous distribution-only workflow is superseded by the ordered four-warmup
reanalysis. The original `inputs.py`, `shape.py` and distribution figures remain
reused; `run.py` delegates to the common driver.

CPU time/WU is measured task-clock converted to microseconds and divided by
completed WU. SQLite counter units/totals/status and raw perf captures are freshly
cross-checked. CSV-only units use an explicitly inferred millisecond convention,
with their missing raw scope/status evidence disclosed per observation. All
available eligible measurements remain, including outlier flags and unexplained
secondary clusters. No results from old one-warmup campaigns enter the analysis.

Distributions and block/order/time diagnostics precede all models. Final figures
show CPU/WU vs own-capacity offered load (%), discrete worker scenarios and achieved
WU/s. Each two-worker cohort/mode/pass has an additional 25–100% detail wherever
observations cover that domain; full-domain figures retain every observation.
Model validation holds out whole experimental X levels and repeats degree/family
selection inside the remaining levels. No model is accepted from training R².
