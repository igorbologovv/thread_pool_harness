# Focused BLS scaling investigation

Produces `results/reports/bls_scaling_8_16/report.pdf` (13 pages, seven vector figures).
No benchmark execution, Rust build/edit, original measurement update or historical
report regeneration occurs. Only this analysis source directory should be committed.

## Reproduce

Use the versions in `analysis/bls_full/requirements.txt` and its documented XeLaTeX,
font and Poppler prerequisites. The existing analysis environment is sufficient:

```sh
/tmp/bls-analysis-venv/bin/python analysis/bls_scaling_8_16/run.py
```

`--render-only` renders/validates saved tables and figures. `--validate-only` runs
the final independent checks without recompiling. A missing XeLaTeX format is
built in a temporary directory, as in the previous report.

Inputs are the two study archives, their referenced cached BLS dataset, and the
previous report's `tables/raw_runs.csv` and `tables/campaign_audit.csv`. Reusing the
historical extraction avoids repeating that exhaustive analysis. Its elapsed,
CPU and hardware-counter values are independently verified against original SQL;
cross-worker comparisons retain only the 15 exact shared identifiers (q64).
SQLite is opened with `mode=ro&immutable=1`. The sibling `../threadance` source and
the exact Cargo.lock dependency sources are read for implementation evidence.

## Statistical boundaries

The source imports the validated `bls_full.common` RNG/weights, `estimate.effect_rows`
and `diagnostics.shape/temporal`; it does not modify them. The data reader adapts
that study's audit and metric conventions to the new 15-configuration design.
Two physical measurements are averaged within each of 20 randomized blocks.
20,000 seeded resamples preserve all within-campaign pairings; separate campaigns
are independently resampled. Relative effects in this report are **ratios of grand
block means**, not mean paired percentages. The latter remain in the reused
effect-function output for traceability but are not the reported relative estimand.

Diagnostics execute before inference: restricted mixture fits for the four
architecture anchors, raw/block/paired distribution review, temporal and
within-block order screens. All observations remain in primary estimates.
Circular moving blocks of lengths 3/5, half-samples, selected omissions, BCa and
leave-one-out ranges assess selected large effects, not every configuration pair.

The bounded contrast graph in `analyze.py` examines reference anchors, matched
topology/backlog, and SP40/100 versus SP0 plus AB/CW versus SP0. Its 390 elapsed/CPU
endpoints (340 paired, 10 saturated scaling, 40 arrival) receive the same approximate
centered bootstrap maximum-statistic approach as the prior study, using bootstrap
standard deviations for studentization. Pointwise hardware/cross-era intervals
remain exploratory; this is not a preregistered discovery family. No broad model
search, Pareto enumeration or all-pairs test is performed.

`report.tex` is the prose/layout template; `report.py` substitutes claims from the
tables and records the numeric claim manifest. The PDF is generated, not committed.
`validate.py` independently uses SQL and directly indexed bootstrap draws for every
generated claim, verifies all input hashes and checks PDF structure/geometry.
`provenance.py` records exact source hashes, binary evidence and read-only `perf list`
and sysfs support discovery. It never opens hardware counters or records perf data.
The list of advertised events is not a claim that counting was smoke-tested.

## Key interpretation limits

Unpinned 8/16 worker counts are not a controlled physical-core/SMT placement
experiment. Collection time and settling (historical 3 s versus new 1 s) are
confounded with worker count. Per-run IPC and effective GHz are computed before
block averaging; the frequency proxy is not a clock trace. Process counters also
cover producer/control activity. No cache-miss/coherence events or peer-steal
counters were recorded. The drain timestamp precedes final potentially blocking
submission. RTRB q64 is a feasibility check; allocated slots are workers × backlog.

The report's source audit and its minimal future measurements distinguish measured
effects from plausible mechanisms. In particular, it does not assume Zen 3 supports
`perf c2c` merely because IBS PMUs are exposed.
