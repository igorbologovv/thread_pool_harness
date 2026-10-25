# Reproduce the BLS full-study report

This pipeline analyzes the six completed campaigns in
`results/studies/bls-full/campaigns/`. It does **not** execute performance benchmarks,
build Rust, modify the harness, regenerate datasets, or write to a campaign.

## Commands

Tested with Python 3.14 and the exact versions in `requirements.txt`.
System dependencies: XeTeX/XeLaTeX, the standard LaTeX packages used in `report.py`,
Noto Serif and DejaVu fonts, and Poppler (`pdfinfo`, `pdftoppm`) for manual inspection.
If the XeLaTeX format is absent but its sources exist, the report builder creates
the format in a temporary directory, without modifying TeX installation settings.

```bash
python -m venv /tmp/bls-analysis-venv
/tmp/bls-analysis-venv/bin/pip install -r analysis/bls_full/requirements.txt
/tmp/bls-analysis-venv/bin/python analysis/bls_full/run.py
```

With complete derived tables and figures already present, rebuild and validate only
the report with `python analysis/bls_full/run.py --report-only`.
The full run takes several minutes. Outputs are in `results/reports/bls_full/`.
`report.pdf` is the final report; `report.tex` and `report.md` are editable generated
sources. The executive summary, methodology audit, diagnostic narrative, vector
figures, and all CSV tables accompany it.

## Analysis design

* Read SQLite with `mode=ro&immutable=1`. Audit the full 17,600 measurements, then
  whitelist Rayon, Bevy, MPMC Threadance, and `threadance-hot-first-rtrb`.
  The obsolete implementation appears only in archive/audit records.
* Retain all 26 distinct Threadance configurations, including nominal RTRB
  capacities. No average across different configurations is a performance estimate.
* Average the two physical runs within each configuration/block/rate. Resample the
  20 blocks, paired across configurations/rates within a campaign, with 20,000
  replicates and master seed 20261025. Different campaigns are independently resampled.
* Report pointwise percentile intervals for absolute effects, mean paired percent
  changes, and ratios of grand means. A relative block effect is undefined when a
  baseline block is zero; never invent a replacement denominator.
* Retrospective primary family: 520 Threadance-versus-Rayon elapsed/task-clock
  contrasts. Additional approximate simultaneous intervals use bootstrap maxima
  of centered effects divided by observed standard errors. Factor families combine
  all contrasts/conditions for elapsed/task-clock within each factor.
* Exact paired sign tests assess direction, not a mean null. Holm adjustment uses
  the primary family, secondary reference/metric families, and factor/metric families.
  Cross-campaign interactions are exploratory with pointwise intervals.
* Raw, block-mean and paired-difference diagnostics are separate. Raw dip p-values
  are descriptive because the two replicates are dependent. BIC preference for a
  two-component mixture is not proof of bimodality. Small p-values receive a targeted
  100,000-simulation uniform-null check; this does not remove selection/dependence caveats.
* Main analyses retain every eligible run. Sensitivities use BCa, circular blocks
  of length 3/5, first/last halves, influential/screened block omissions and separate
  omissions for the two long within-block acquisition interruptions.
* Pareto intervals are simultaneous across all configuration pairs and both objectives
  within a scenario/objective family. Frontier frequencies are stability estimates,
  not posterior probabilities. No combined score is used.

## Metric and implementation caveats

`elapsed_ms` includes producer submission, pacing, task execution and completion wait.
`drain_delay_ms` is elapsed minus the **pre-submission** timestamp of the final task.
It can decrease because bounded submission delayed the producer. `schedule_excess_ms`
is elapsed minus the planned final offset and equals drain plus final lateness.
Neither measures task scheduling or response latency. Lateness percentiles describe
producer timestamps within a run. Perf covers a slightly broader interval than elapsed.

`task_clock_ms` is process CPU time, not energy. Count metrics have raw totals and
per-work-unit forms; all batches have 1,000 work units. IPC and branch-miss percentage
are calculated per physical run before block averaging. `cpu_equivalents` is CPU
milliseconds / elapsed milliseconds. `effective_ghz` is cycles / CPU milliseconds /
1e6; it is not a per-core frequency or temperature observation.

RTRB allocates `workers * backlog` queued slots; nominal `queue_capacity` only checks
feasibility. Topology comparisons also change routing and wake addressing. Agave's
unused spin setting does not disable its internal spin/yield backoff. Source inspection
requires the existing sibling `../threadance` repository; its files are only read.

## Files and traceability

`extract.py` performs the integrity audit and raw extraction; `estimate.py` computes
effects and frontiers; `diagnostics.py`, `followup.py`, and `interruptions.py` retain
all diagnostics and sensitivities; `figures.py` emits PDF/SVG figures and captions;
`report.py` generates the narratives/PDF; `validate.py` independently checks original
hashes, SQL arithmetic, directly indexed bootstraps and PDF structure.

Every extracted row retains campaign and SQLite identifiers. `input_inventory.csv`
hashes every original archive file and cached dataset before analysis; final validation
checks these hashes again. `source_provenance.json` and `audited_source_hashes.csv`
record code/binary evidence; dirty flags and missing historical build state remain
limitations. `analysis_environment.json` and `statistical_settings.json` record versions
and resampling choices. `validation.json` and `visual_validation.md` document final checks.

The regenerated PDF need not be byte-identical because TeX embeds build metadata;
statistical tables and seeds are deterministic. Existing benchmark and user files are
never cleaned or overwritten. Output files in this report directory are regenerated.
