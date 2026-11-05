"""Reuse existing per-run shape/block diagnostics; add multiplicity and drift audit."""
from collections import Counter,defaultdict
import json
import numpy as np
from scipy import stats
from diptest import diptest
from analysis.bls_cpu_time.shape import investigate
from experiments.common.statistical_analysis.core import correlation
from experiments.common.statistical_analysis.io import csv_write


def adjust(conditions):
    valid=[c for c in conditions if c['diagnostics']['dip']['p_value'] is not None]
    p=np.array([c['diagnostics']['dip']['p_value'] for c in valid])
    if not len(p):return
    bh=stats.false_discovery_control(p,method='bh');by=stats.false_discovery_control(p,method='by')
    order=np.argsort(p);holm=np.empty(len(p));holm[order]=np.minimum(1,np.maximum.accumulate(p[order]*(len(p)-np.arange(len(p)))))
    for c,a,b,h in zip(valid,bh,by,holm):
        c['diagnostics']['dip'].update(p_bh=float(a),p_by=float(b),p_holm=float(h),family_size=len(valid),
            multiplicity_caveat='Metric-wide condition family. BH assumes independence/positive dependence; BY and Holm tolerate dependence only for valid marginal p-values. Raw dip calibration is IID and uncalibrated for repeated randomized-block data; adjustment cannot repair that. Absolute/relative throughput dips are identical and form ONE family.')


def diagnose(result,metric):
    lookup=defaultdict(list)
    for r in result['observations']:lookup[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    for i,c in enumerate(result['conditions']):
        rs=lookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        c.update(condition_id=f'C{i+1:04}',offered_rate=rs[0]['offered_rate'],nominal_load_pct=100*rs[0]['nominal_load'],
            offered_load_pct=rs[0]['offered_load_pct'],_metric=metric,
            _metric_label='CPU time (µs/WU)' if metric=='cpu_us_per_wu' else 'Achieved throughput (WU/s)')
        inv=investigate(rs,c,metric=metric);c['investigation']=inv
        means=np.array([b['mean'] for b in c['block_means']])
        residuals=np.array([r[metric]-np.mean([s[metric] for s in rs if s['block']==r['block']]) for r in rs])
        def dip(v):
            if len(v)<4 or np.ptp(v)==0:return dict(statistic=None,p_value=None)
            d,p=diptest(v);return dict(statistic=float(d),p_value=float(p))
        inv['block_mean_dip']=dip(means)
        inv['within_block_centered_dip']=dip(residuals)
        inv['dip_interpretation']='Block-mean dip assumes independent exchangeable continuous blocks (20 or 30 only). Centered-repeat residuals are constrained/dependent; their dip is descriptive, not calibrated evidence. Neither is a causal decomposition.'
        passpos=np.array([r['run_index']//c['repeats_per_block'] for r in rs])
        inv['associations']['pass_position_rho']=correlation(passpos,[r[metric] for r in rs])
        inv['pass_positions']=[dict(position=int(p),n=int(sum(passpos==p)),mean=float(np.mean([r[metric] for r,q in zip(rs,passpos) if p==q]))) for p in sorted(set(passpos))]
        c['suspected_shape']=bool((c['diagnostics']['dip']['p_value'] or 1)<.05 or any(v['n_peaks']>=2 for v in inv['sensitivity']['curves'] if v['multiplier']==1))
        c['counter_scopes']=' | '.join(sorted({r.get('cpu_scope','') for r in rs}))
        c['unit_evidence']=' | '.join(sorted({r.get('cpu_unit_evidence','') for r in rs}))
        c['diagnostics']['warnings']+=inv['warnings']
        if abs(inv['associations']['block_mean_lag1_rho'] or 0)>=.5:c['diagnostics']['warnings'].append('Large absolute block-mean lag-one rank association; exchangeability of blocks and bootstrap coverage questionable.')
    adjust(result['conditions'])
    return result


def diagnostic_table(result):
    from .run import flatten
    rows=flatten(result)[0]
    for r,c in zip(rows,result['conditions']):
        dip=c['diagnostics']['dip']; inv=c['investigation']
        r.update(condition_id=c['condition_id'],offered_load_pct=c['offered_load_pct'],nominal_load_pct=c['nominal_load_pct'],
            dip_p_bh=dip.get('p_bh'),dip_p_by=dip.get('p_by'),dip_p_holm=dip.get('p_holm'),
            **inv['associations'],shape=inv['shape'],suspected_shape=c['suspected_shape'],
            block_mean_dip_p=inv['block_mean_dip']['p_value'],within_block_centered_dip_p=inv['within_block_centered_dip']['p_value'],
            histogram_selection=c['diagnostics']['histogram']['selection'],histogram_width=c['diagnostics']['histogram']['bin_width'],
            scott_bandwidth=inv['sensitivity'].get('scott_bandwidth'),unit_evidence=c['unit_evidence'],counter_scopes=c['counter_scopes'])
    return rows


def report(result,output):
    cs=result['conditions'];out=output
    csv_write(out/'tables/distribution_statistics.csv',diagnostic_table(result))
    repeats=[dict(condition_id=c['condition_id'],**{k:v for k,v in r.items() if k!='values'}) for c in cs for r in c['investigation']['repeats']]
    csv_write(out/'tables/repeat_diagnostics.csv',repeats)
    tests={k:sum(c['diagnostics']['dip'].get(k) is not None and c['diagnostics']['dip'][k]<.05 for c in cs) for k in ('p_value','p_bh','p_by','p_holm')}
    robust=[c for c in cs if c['investigation']['shape'].startswith('exploratory separated')]
    suspected=[c for c in cs if c['suspected_shape']]
    lag=sum(abs(c['investigation']['associations']['block_mean_lag1_rho'] or 0)>=.5 for c in cs)
    blocks=len({r['block'] for r in result['observations']})
    lines=['# Distribution diagnostic report (completed before regression)','',
        f"{len(result['observations']):,} physical measurements; {len(cs)} conditions; {blocks:,} campaign-scoped blocks; {len({c['acquisition'] for c in cs})} campaigns. {len(suspected)} conditions have an exploratory raw dip p<0.05 or at least two Scott-KDE peaks and receive block detail figures.",'',
        'Mean/median/variance/SD/IQR/skewness and excess kurtosis describe physical observations. Bias-corrected sample skewness and Fisher excess kurtosis are exported. The estimate/95% CI is the equal-block mean/whole-block percentile bootstrap. No observation is removed; IQR fences are indicators. N and independent block counts are separate. Histograms use NumPy auto (the finer of Freedman–Diaconis and Sturges; fallback at zero IQR); Scott Gaussian KDE and 0.5×/2× sensitivity are documented numerically.', '',
        f"Raw dip p<0.05: {tests['p_value']}/{len(cs)}. Adjusted <0.05: BH {tests['p_bh']}, BY {tests['p_by']}, Holm {tests['p_holm']}. These are exploratory screens, not calibrated rejection counts: standard dip p-values assume IID continuous observations, violated by block repeats. BY/Holm accommodate inter-test dependence only when individual p-values are valid. Adjustment does not repair the within-condition calibration. Absolute and relative dips are invariant to positive scaling and counted as one family.",'',
        f"{len(robust)} conditions retain at least two detected peaks at twice Scott bandwidth (5% density-prominence heuristic). This is visual stability, not statistical confirmation of multimodality. Dip p≥0.05 never proves unimodality. Block-mean and within-block-centered dips, repeat-stratified dips, pass position, ICC, order/time and lag associations are exported for every condition. Their dependence/small-sample caveats apply. {lag} conditions have |block-mean lag-one rank correlation|≥0.5; ordinary block-bootstrap intervals may then have poor coverage. The threshold is descriptive.",'',
        'Four discarded warmups are constant in this dataset. Repeat/pass associations can reveal residual settling, but cannot establish a warmup mechanism. Between-block shifts, repeated-run position, alternating pass order and chronology are examined separately. Missing temperature, placement and independent frequency telemetry prevent causal explanations. Unexplained peaks remain unexplained.', '',
        'All raw distributions precede performance/model work in the execution log. [Figure atlas](FIGURES.md); [condition statistics](tables/distribution_statistics.csv); [repeat diagnostics](tables/repeat_diagnostics.csv).','',
        '## Conditions with bandwidth-stable secondary peaks','',
        '| ID | Cohort / workers / mode / pass / scheduler | Offered % | Raw dip p | BY | ICC | Repeat rho | Time rho | Block mean lag1 rho |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for c in robust:
        a=c['investigation']['associations'];d=c['diagnostics']['dip'];s=c['stratum']
        fmt=lambda v:'NA' if v is None else f'{v:.3g}'
        lines.append(f"| {c['condition_id']} | {s['cohort']} / {s['workers']} / {s['mode']} / {s['perf_pass']} / {c['scheduler']} | {c['offered_load_pct']:.2f} | {fmt(d['p_value'])} | {fmt(d.get('p_by'))} | {fmt(a['block_icc1'])} | {fmt(a['repeat_rho'])} | {fmt(c['temporal'].get('time',{}).get('spearman_rho'))} | {fmt(a['block_mean_lag1_rho'])} |")
    (out/'DIAGNOSTIC_REPORT.md').write_text('\n'.join(lines)+'\n')
    return dict(tests=tests,stable_peak_conditions=len(robust),suspected_conditions=len(suspected),large_block_lag_conditions=lag)


def dependence_sensitivity(result,output,*,seed=20661105,replicates=5000):
    """Circular moving-block sensitivity across consecutive randomized blocks.

    This does not claim to remove nonstationary drift. Adjacent length-2/4 windows
    preserve short dependence; serial blocks are the resampling units here.
    """
    lookup=defaultdict(list)
    for r in result['observations']:lookup[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    metric=result['design']['metric'];rng=np.random.default_rng(seed);rows=[]
    for c in result['conditions']:
        rs=lookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        blocks=sorted({r['block_local'] for r in rs})
        means=np.array([np.mean([r[metric] for r in rs if r['block_local']==b]) for b in blocks])
        b=len(blocks)
        for length in (2,4):
            starts=rng.integers(0,b,(replicates,int(np.ceil(b/length))))
            indices=((starts[:,:,None]+np.arange(length))%b).reshape(replicates,-1)[:,:b]
            ci=np.quantile(means[indices].mean(axis=1),[.025,.975])
            rows.append(dict(condition_id=c['condition_id'],**c['stratum'],scheduler=c['scheduler'],
                offered_load_pct=c['offered_load_pct'],window_length=length,ci95_low=ci[0],ci95_high=ci[1],
                ordinary_block_ci95_low=c['ci95'][0],ordinary_block_ci95_high=c['ci95'][1],
                width_ratio=(ci[1]-ci[0])/(c['ci95'][1]-c['ci95'][0]) if c['ci95'][1]>c['ci95'][0] else None,
                seed=seed,replicates=replicates,n_randomized_blocks=b,
                caveat='Sensitivity for short serial dependence assuming weak stationarity; circular end wrapping and only 20/30 blocks limit reliability. Does not identify/remove deterministic drift or campaign confounding.'))
    csv_write(output/'tables/dependence_sensitivity.csv',rows)
    p=output/'DIAGNOSTIC_REPORT.md'
    text=p.read_text()
    if 'Moving-block sensitivity' not in text:
        text+='\n## Moving-block sensitivity\n\n[Length-2/4 circular moving-block mean CIs](tables/dependence_sensitivity.csv) preserve short dependence across adjacent randomized blocks, using 5000 window draws. These are sensitivity intervals, not a proven correction for nonstationary drift. The ordinary whole-block CI remains primary; serial association makes its coverage conditional. This sensitivity was exported before performance/model stages.\n'
        text+='\n## Examination of apparent secondary clusters\n\n'
        anchors=[]
        for cohort,scheduler,nominal in [('transition-lowload','mpmc-sp0',35),('transition-sweep','rtrb-sp0-b2',50),('deep-final','rtrb-sp0-b2',25)]:
            selected=next((c for c in result['conditions'] if c['stratum']['cohort']==cohort and c['stratum']['workers']==2 and c['stratum']['mode']=='fixed-arrivals' and c['stratum']['perf_pass']=='cpu-cache' and c['scheduler']==scheduler and np.isclose(c['nominal_load_pct'],nominal)),None)
            if selected:anchors.append(selected)
        for c in anchors:
            inv=c['investigation'];a=inv['associations'];d=c['diagnostics']['dip']
            fmt=lambda v:'NA' if v is None else f'{v:.4g}'
            peaks={v['multiplier']:v['n_peaks'] for v in inv['sensitivity']['curves']}
            text+=f"- {c['condition_id']}, {c['stratum']['cohort']}, {c['scheduler']}, nominal {c['nominal_load_pct']:g}% (actual {c['offered_load_pct']:.2f}%): half/Scott/double-Scott peak counts {peaks}; raw dip p={fmt(d['p_value'])}, BY={fmt(d.get('p_by'))}. ICC={fmt(a['block_icc1'])}; {a['split_same_side_blocks']}/{a['split_total_blocks']} blocks keep all repeats on the same side of the descriptive central gap. Repeat rho={fmt(a['repeat_rho'])}, randomized-order rho={fmt(c['temporal']['order']['spearman_rho'])}, time rho={fmt(c['temporal'].get('time',{}).get('spearman_rho'))}, pass-position rho={fmt(a['pass_position_rho'])}. Block-mean dip p={fmt(inv['block_mean_dip']['p_value'])}; within-block-centered dip p={fmt(inv['within_block_centered_dip']['p_value'])}. The latter calibration is constrained/dependent. These diagnostics distinguish block shifts from simple repeat/order associations descriptively; they establish no causal mechanism or population mode count.\n"
        text+='\nLarge positive ICC and same-side repeat clusters are consistent with between-block shifts. Weak monotone order/repeat association cannot rule out nonlinear or intermittent temporal effects. Constant warmup configuration prevents testing warmup-count causation; measurement conditions lack sufficient independent processor-state telemetry. All suspected cases, including those with nonsignificant dip p, receive detail pages; no unexplained observation was omitted.\n'
        p.write_text(text)
    return rows
