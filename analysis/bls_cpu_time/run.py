"""Stage 1 CPU-time/WU distributions only. No regression or performance curves."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys

from analysis.bls_throughput.run import prepare,collect,run_statistics,flatten,require
from experiments.common.statistical_analysis import export
from experiments.common.statistical_analysis.io import csv_write
from experiments.common.statistical_analysis.exporting import clean
from .inputs import CpuEnricher
from .shape import investigate


def diagnose(result):
    lookup=defaultdict(list)
    for r in result['observations']:
        lookup[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    for i,c in enumerate(result['conditions']):
        records=lookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        c['condition_id']=f'C{i+1:04}'
        c['offered_rate']=records[0]['offered_rate']
        c['nominal_load_pct']=records[0]['nominal_load']*100
        c['offered_load_pct']=records[0]['offered_load_pct']
        c['priority_2w_fixed_25_100']=bool(c['stratum']['workers']==2 and c['stratum']['mode']=='fixed-arrivals'
            and (25<=c['nominal_load_pct']<=100 or 25<=c['offered_load_pct']<=100))
        c['investigation']=investigate(records,c)
        c['diagnostics']['warnings'] += c['investigation']['warnings']
        warnings=sorted({r['cpu_quality_warning'] for r in records if r['cpu_quality_warning']})
        c['diagnostics']['warnings']+=warnings
        if abs(c['investigation']['associations']['block_mean_lag1_rho'] or 0)>=.5:
            c['diagnostics']['warnings'].append('Large block-mean lag-one rank association: independent-block bootstrap coverage is questionable; this association is not a calibrated serial-dependence test.')
        c['unit_evidence']=' | '.join(sorted({r['cpu_unit_evidence'] for r in records}))
        c['counter_scopes']=' | '.join(sorted({r['cpu_scope'] for r in records}))
    return result


def tables(result):
    statistics=flatten(result)[0]
    for row,c in zip(statistics,result['conditions']):
        row.update(condition_id=c['condition_id'], cpu_units='µs/completed WU',
                   nominal_load_pct=c['nominal_load_pct'],offered_load_pct=c['offered_load_pct'],
                   unit_evidence=c['unit_evidence'], counter_scopes=c['counter_scopes'],
                   priority_2w_fixed_25_100=c['priority_2w_fixed_25_100'],
                   shape=c['investigation']['shape'],**c['investigation']['associations'],
                   kde_peaks_scott=next((v['n_peaks'] for v in c['investigation']['sensitivity']['curves'] if v['multiplier']==1),None),
                   kde_peaks_double_scott=next((v['n_peaks'] for v in c['investigation']['sensitivity']['curves'] if v['multiplier']==2),None),
                   histogram_selection=c['diagnostics']['histogram']['selection'],
                   histogram_width=c['diagnostics']['histogram']['bin_width'])
    return statistics


def shortlist(result,stats):
    ranked=[]
    for row,c in zip(stats,result['conditions']):
        a=c['investigation']['associations']
        p=c['diagnostics']['dip']['p_value']
        broad=next((v['n_peaks'] for v in c['investigation']['sensitivity']['curves'] if v['multiplier']==2),0)
        score=3*(broad>=2)+2*(p is not None and p<.05)+int(abs(a['repeat_rho'] or 0)>=.5)+int(abs(a['block_sequence_rho'] or 0)>=.5)+int((a['block_icc1'] or 0)>=.5)
        copy=dict(row,investigation_score=score)
        ranked.append(copy)
    # The explicit shortlist prioritizes requested transition cohorts, then shape
    # evidence; ranking is a triage heuristic, never a hypothesis test.
    ranked.sort(key=lambda r:(not r['priority_2w_fixed_25_100'],r['cohort'] not in ('transition-lowload','transition-sweep'),-r['investigation_score'],r['condition_id']))
    selected=[]
    anchors={'transition-lowload':('mpmc-sp0',35),'transition-sweep':('rtrb-sp0-b2',50)}
    for cohort,quota in [('transition-lowload',8),('transition-sweep',8),('deep-final',4)]:
        candidates=[r for r in ranked if r['priority_2w_fixed_25_100'] and r['cohort']==cohort]
        candidates.sort(key=lambda r:(-r['investigation_score'],-r['iqr']/max(r['median'],1e-12),r['condition_id']))
        anchor=next((r for r in candidates if cohort in anchors and r['scheduler']==anchors[cohort][0]
                     and r['nominal_load_pct']==anchors[cohort][1] and r['perf_pass']=='cpu-cache'),None)
        if anchor is not None:
            selected.append(anchor)
            candidates=[r for r in candidates if r['condition_id']!=anchor['condition_id']]
            quota-=1
        selected.extend(candidates[:quota])
    # Four cross-cohort/worker anomalies ensure triage does not hide all non-priority data.
    other=[r for r in ranked if r['condition_id'] not in {s['condition_id'] for s in selected} and not r['priority_2w_fixed_25_100']]
    other.sort(key=lambda r:(-r['investigation_score'],-r['iqr']/max(r['median'],1e-12),r['condition_id']))
    selected.extend(other[:4])
    return ranked,selected


def summary(output,result,stats,short,unavailable,excluded):
    cs=result['conditions']; raw=result['observations']
    dip=[c for c in cs if c['diagnostics']['dip']['p_value'] is not None and c['diagnostics']['dip']['p_value']<.05]
    stable=[c for c in cs if c['investigation']['shape'].startswith('exploratory separated')]
    icc=[c for c in cs if (c['investigation']['associations']['block_icc1'] or 0)>=.5]
    lag_flags=sum(abs(c['investigation']['associations'].get('block_mean_lag1_rho') or 0)>=.5 for c in cs)
    right_skew=sum((c['diagnostics']['descriptive']['skewness_bias_corrected'] or 0)>1 for c in cs)
    left_skew=sum((c['diagnostics']['descriptive']['skewness_bias_corrected'] or 0)<-1 for c in cs)
    heavy_moment=sum((c['diagnostics']['descriptive']['excess_kurtosis_bias_corrected'] or 0)>3 for c in cs)
    outliers=sum(len(c['diagnostics']['outlier_indices']) for c in cs)
    priority=[c for c in cs if c['priority_2w_fixed_25_100']]
    lines=['# Stage 1: CPU time per completed work unit — distributions', '',
           f"Analyzed **{len(raw):,} physical runs in {len(cs)} distinct conditions**. No regression, fitted performance curves, benchmarks or PDFs were produced.", '',
           '## Measurement integrity and units', '',
           '- CPU cost = task-clock × unit-to-microseconds factor / completed WU, reported as **µs/WU**. It is accumulated observed CPU execution time, not wall time or latency. Raw values and unit evidence are preserved.',
           f"- {sum(r['cpu_unit_evidence'].startswith('verified') for r in raw):,} runs have units freshly verified against SQLite **and raw perf JSON**. Stored counter totals agree with raw aggregate/per-TID values and alternative per-run CSVs. The other {sum(r['cpu_unit_evidence'].startswith('inferred') for r in raw):,} CSV-only runs use the archived millisecond convention: original unit/status records are absent, so absolute units and counter scope cannot be independently revalidated. These remain explicitly exploratory.",
           '- Counter availability/quality is in tables/counter_audit.csv. Deep CSV availability comes from its archived audit, not a fresh counter check. Legacy exports lack runtime/status metadata; original one-warmup saturation recorded user-only task-clock. Later process attachment and explicit per-TID recording remain separate acquisition cohorts and passes. No full-process CPU-time equivalence is asserted across scopes.',
           f'- Unavailable/inconsistent physical counters: {len(unavailable)}. An entire condition is excluded if any constituent counter is unavailable, preserving complete block/repeat structure. Smoke exclusions inherited from throughput preparation: {len(excluded)}. Outliers are never removed.',
           '- Global block identities, repeat/pass indices, randomized position, acquisition order, timestamps and warmup counts are inherited from the throughput collector. CSV success/completion flags and missing original databases cannot be freshly recovered; archived successful-completion claims are qualified accordingly. Existing throughput outputs are never modified.',
           '- CSV denominators use archived n/planned WU under the archived successful-completion assertion; the original completed-WU field is absent. SQLite-backed denominators use the freshly checked completed-WU field.',
           '- Conditions include the paced cohorts plus separate all-at-once saturation diagnostics for protocol/warmup context. Offered load percentages use scheduled arrival rate / same-scheduler, same-worker saturation-recheck throughput ×100. Original nominal design percentages are also exported. Percentage X is descriptive metadata only; baseline uncertainty does not change CPU-cost CIs.', '',
           '## Distribution findings and uncertainty', '',
           f'- {len(dip)}/{len(cs)} conditions have dip p<0.05; **all are exploratory** under repeated/block-dependent observations and multiple inspection. Zero tabulated p-values are numerical limits, not exact zero probabilities. p≥0.05 is not proof of unimodality.',
           f'- {len(stable)} conditions retain at least two density peaks at both Scott bandwidth and double Scott bandwidth (5% prominence rule). This is bandwidth-stable exploratory shape evidence, **not confirmed population multimodality**. Half/Scott/double-Scott curves and peak locations are exported. Weak/missing peaks can reflect smoothing or insufficient power.',
           '- Double-Scott smoothing is deliberately severe and can merge visible sample clusters at n=40. Its zero/nonzero peak count is not a decisive test for or against multimodality. Conditional repeat distributions and block concentration provide additional, distinct diagnostics.',
           f'- {outliers} IQR-fence observations were flagged and retained. {len(icc)} conditions have descriptive block ICC≥0.5. ICC estimates can be noisy; no IID p-values are assigned to order/time/processor-proxy correlations.',
           f'- Sample moment flags: {right_skew} conditions have skewness>1, {left_skew} have skewness<−1, and {heavy_moment} have Fisher excess kurtosis>3. These summarize asymmetry/tail observations, not distribution-family tests. {lag_flags} conditions have |block-mean lag-one rank association|≥0.5, raising concern about independent-block CI coverage. Short sequences can also produce large associations by chance.',
           '- Tables include raw-run n; independent-block count; mean/median; sample variance/SD; IQR; bias-corrected skewness and Fisher excess kurtosis (Gaussian reference=0, without assuming Gaussian data); tail quantiles; dip statistic/p; percentile mean CIs; order/time associations; repeated-run and block diagnostics. Moment estimates are unstable with small n/heavy tails.',
           '- Mean CIs resample whole randomized blocks, retaining repeats and paired scheduler groups within each independent acquisition. Block numbers from different load campaigns are never paired. Coverage assumes independent exchangeable blocks and does not correct serial dependence, acquisition drift, shared host-state bias, multiplicity, or uncertain counter scope. Power phases with five blocks have particularly weak coverage/power.',
           '- Histograms use NumPy auto: finer of Freedman–Diaconis and Sturges, with Sturges fallback at zero IQR. Exact edges, actual bin widths and FD reference widths are exported. Gaussian KDE is descriptive; boundary/tail smoothing and bandwidth choice affect apparent modes. ECDF/raw plots retain every observation.', '',
           '## Priority: two workers, fixed arrivals, 25–100% load', '',
           f'- {len(priority)} conditions satisfy the nominal or measured offered-load priority window. transition-lowload and transition-sweep remain distinct: their overlapping 50% acquisitions are not duplicates, and differing acquisition times/randomization seeds are preserved.',
           '- The shortlist uses fixed quotas: eight transition-lowload, eight transition-sweep, four deep-final priority conditions, plus four non-priority anomalies from other workers/cohorts. Ranking uses separated peaks, dip evidence, repeat/order association, block concentration and relative IQR; it is not a significance ranking. Full priority rows and all-condition rankings are exported.',
           '- Two post-hoc illustrative anchors are retained within those quotas: MPMC Spin(0) at 35% low load and RTRB-b2 at 50% in transition-sweep. They illustrate separated sample clusters and a nonsignificant dip result with an upper cluster. The shortlist is exploratory triage, not an independent validation sample.',
           '- Warmup count and recorded power policy are constant within each condition. Their causal contribution to modes cannot be isolated. Where present, cycles/task-clock is a shared-denominator **proxy**, not independent CPU-frequency telemetry. Temperature, instantaneous frequency, throttling and cache-state telemetry are absent. Do not infer processor mechanisms from a proxy correlation.', '',
           '| ID | Cohort | Scheduler | Pass | Nominal / actual load % | Mean [95% CI] µs/WU | Dip p | Block ICC | Shape |',
           '|---|---|---|---|---|---|---|---|---|']
    for r in short:
        lines.append(f"| {r['condition_id']} | {r['cohort']} | {r['scheduler']} | {r['perf_pass']} | {r['nominal_load_pct']:.1f} / {r['offered_load_pct']:.1f} | {r['estimate']:.1f} [{r['ci95_low']:.1f}, {r['ci95_high']:.1f}] | {r['dip_p_value']:.3g} | {r['block_icc1']:.2f} | {r['shape']} |")
    lines+=['', '## Concrete priority observations (cpu-cache pass)', '',
            'These are sample-distribution descriptions, not scheduler rankings or confirmed mechanisms. Each example has 40 raw observations in 20 randomized blocks; all use four warmups.', '']
    examples=[('transition-lowload','mpmc-sp0',35),('transition-lowload','mpmc-sp0',45),
              ('transition-lowload','rtrb-sp0-b2',25),('transition-sweep','rtrb-sp0-b2',50)]
    for cohort,scheduler,load in examples:
        c=next(c for c in cs if c['stratum']['cohort']==cohort and c['scheduler']==scheduler
               and c['nominal_load_pct']==load and c['stratum']['mode']=='fixed-arrivals' and c['stratum']['perf_pass']=='cpu-cache')
        inv=c['investigation'];a=inv['associations']
        peaks=next(v['peaks'] for v in inv['sensitivity']['curves'] if v['multiplier']==1)
        peaktext=', '.join(f'{v:.0f}' for v in peaks)
        lines.append(f"- **{c['condition_id']}, {cohort}, {scheduler}, nominal {load}%:** Scott-KDE peak locations {peaktext} µs/WU; dip p={c['diagnostics']['dip']['p_value']:.4g}; block ICC={a['block_icc1']:.2f}. {a['split_same_side_blocks']}/{a['split_total_blocks']} blocks have both repeats on the same side of the descriptive central gap. Repeat rank association={a['repeat_rho']:.2f}; time rank association={c['temporal']['time']['spearman_rho']:.2f}. Peaks merge with double-Scott smoothing. These associations do not identify a first/second-repeat or monotone-drift explanation; other temporal dependence remains possible.")
    lines+=['- In particular, the 50% RTRB-b2 example has dip p≈0.057 while a small separated upper cluster is visible in KDE/gap diagnostics. This is an example of why nonsignificance must not be treated as evidence of unimodality. Conversely, a low dip p-value alone does not validate a mode count.', '',
            'The report describes observed distributions and associations; it establishes no population mode count, causal explanation or scheduler performance ranking.', '',
            '## Files and reproduction', '',
            '- [FIGURES.md](FIGURES.md): all-condition distribution pages followed by detailed priority pages. Histogram/KDE bandwidth sensitivity, ECDF/tails, individual observations and order/time structure are shown before any interpretation.',
            '- results/results.json contains raw observations, statistics, CIs, shape arrays and dependency diagnostics. tables/statistics.csv, shortlist.csv, priority_conditions.csv, repeat_diagnostics.csv and counter_audit.csv provide numerical exports. provenance.json preserves inherited archive/member hashes and deduplication decisions.', '',
            '```sh', 'python -m venv .venv-analysis', '.venv-analysis/bin/python -m pip install -r experiments/common/statistical_analysis/requirements.txt',
            'OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-cpu-mpl .venv-analysis/bin/python -m analysis.bls_cpu_time.run --output /tmp/bls-cpu-distributions --bootstrap 5000 --seed 20261105', '```', '',
            'Use a new output path. Existing extracted members and archives are rehashed before reuse. `--render-only` resumes figures from completed numerical exports without repeating statistical calculations.']
    (output/'SUMMARY.md').write_text('\n'.join(lines)+'\n')


def main():
    """The ordered audited driver now produces throughput and CPU deliverables."""
    from analysis.bls_throughput.reanalyze import main as reanalyze
    reanalyze()


if __name__=='__main__':main()
