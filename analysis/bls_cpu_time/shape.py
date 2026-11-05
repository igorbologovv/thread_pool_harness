"""Exploratory shape/dependence diagnostics; no calibrated mode-count claims."""
from collections import defaultdict
import numpy as np
from scipy import stats
from scipy.signal import find_peaks
from diptest import diptest
from experiments.common.statistical_analysis.core import correlation


def bandwidth_sensitivity(values):
    y=np.asarray(values, dtype=float)
    if len(y)<8 or np.ptp(y)==0:
        return {'status':'insufficient shape resolution', 'curves':[]}
    kde=stats.gaussian_kde(y)
    # Extend support to show tails; Gaussian kernels can extend below zero.
    h=float(np.sqrt(kde.covariance[0,0]))
    grid=np.linspace(max(0,float(y.min()-h)),float(y.max()+h),400)
    curves=[]
    for multiplier in (.5,1.,2.):
        kde.set_bandwidth(kde.scotts_factor()*multiplier)
        density=kde(grid)
        indices,_=find_peaks(density, prominence=.05*density.max())
        curves.append(dict(multiplier=multiplier, x=grid.tolist(), density=density.tolist(),
                           peaks=grid[indices].tolist(), n_peaks=len(indices)))
    return dict(status='exploratory bandwidth sensitivity; 5% density prominence, no mode-count test',
                scott_bandwidth=h, curves=curves)


def investigate(records, condition, *, metric="cpu_us_per_wu"):
    y=np.array([r[metric] for r in records])
    blocks=defaultdict(list)
    for r in records: blocks[r['block']].append(r[metric])
    means=np.array([np.mean(v) for v in blocks.values()])
    temporal_blocks=defaultdict(list)
    for r in records:temporal_blocks[r['block_local']].append(r[metric])
    chronological_means=[float(np.mean(temporal_blocks[b])) for b in sorted(temporal_blocks)]
    kset={len(v) for v in blocks.values()}
    icc=None
    if len(means)>1 and len(kset)==1 and next(iter(kset))>1:
        k=next(iter(kset)); msb=k*np.var(means,ddof=1)
        msw=sum(sum((np.array(v)-np.mean(v))**2) for v in blocks.values())/(len(means)*(k-1))
        denominator=msb+(k-1)*msw
        icc=float((msb-msw)/denominator) if denominator>0 else None
    sensitivity=bandwidth_sensitivity(y)
    repeats=[]
    for repeat in sorted({r['pass_run_index'] for r in records}):
        v=np.array([r[metric] for r in records if r['pass_run_index']==repeat])
        d,p=diptest(v) if len(v)>=4 and np.ptp(v)>0 else (None,None)
        repeats.append(dict(repeat=repeat,n=len(v),mean=float(v.mean()), median=float(np.median(v)),
                            dip_statistic=float(d) if d is not None else None,
                            dip_p_value=float(p) if p is not None else None,values=v.tolist()))
    ordered=np.sort(y)
    left=max(1,int(np.ceil(.2*len(y)))); right=min(len(y)-1,int(np.floor(.8*len(y))))
    cutindex=left+int(np.argmax(np.diff(ordered)[left-1:right]))
    cutoff=float((ordered[cutindex-1]+ordered[cutindex])/2)
    high=(y>cutoff).astype(int)
    gap=float(ordered[cutindex]-ordered[cutindex-1])
    splitbyblock=defaultdict(list)
    for r,h in zip(records,high): splitbyblock[r['block']].append(int(h))
    sameblocks=sum(len(set(v))==1 for v in splitbyblock.values())
    pairs=[]
    for block in blocks:
        rs=sorted((r for r in records if r['block']==block),key=lambda r:r['pass_run_index'])
        if len(rs)>1: pairs.append(rs[-1][metric]-rs[0][metric])
    proxy=[r.get('cycles_per_cpu_ms_proxy') for r in records]
    proxy_rho=correlation(proxy,y) if all(v is not None for v in proxy) else None
    associations=dict(repeat_rho=correlation([r['pass_run_index'] for r in records],y),
                      block_sequence_rho=correlation([r['block_local'] for r in records],y),
                      block_mean_lag1_rho=correlation(chronological_means[:-1],chronological_means[1:]),
                      cycles_per_cpu_ms_proxy_rho=proxy_rho,
                      repeat_last_minus_first_mean=float(np.mean(pairs)) if pairs else None,
                      block_icc1=icc, split_same_side_blocks=sameblocks,
                      split_total_blocks=len(splitbyblock), split_repeat_rho=correlation([r['pass_run_index'] for r in records],high))
    scott_peaks=next((c['n_peaks'] for c in sensitivity['curves'] if c['multiplier']==1),0)
    broad_peaks=next((c['n_peaks'] for c in sensitivity['curves'] if c['multiplier']==2),0)
    if scott_peaks>=2 and broad_peaks>=2:
        shape='exploratory separated peaks survive bandwidth doubling'
    elif scott_peaks>=2:
        shape='bandwidth-sensitive secondary peaks'
    else:
        shape='no robust secondary peak resolved; not proof of unimodality'
    if len(blocks)<10:
        shape+='; few independent blocks'
    condition['diagnostics']['descriptive'].update({f'q{p:02}':float(np.quantile(y,p/100)) for p in (1,5,10,90,95,99)})
    return dict(shape=shape, sensitivity=sensitivity, repeats=repeats, associations=associations,
                largest_central_gap=dict(cutoff=cutoff,gap=gap,lower_n=cutindex,upper_n=len(y)-cutindex,
                    gap_over_iqr=gap/condition['diagnostics']['descriptive']['iqr'] if condition['diagnostics']['descriptive']['iqr'] else None,
                    note='Descriptive largest gap restricted to 20–80% splits; not an inferred biological/physical mode boundary.'),
                metadata=dict(warmups=sorted({r['warmups'] for r in records}),
                              boost=sorted({str(r.get('boost','unrecorded')) for r in records}),
                              units=sorted({r['cpu_unit_evidence'] for r in records}),
                              scopes=sorted({r['cpu_scope'] for r in records})),
                warnings=['KDE peaks and gap splits are exploratory and bandwidth/sample dependent, not confirmed population modes.',
                          'Within-repeat dip p-values still assume independent continuous draws and are exploratory under temporal dependence/multiple inspection.',
                          'ICC is a descriptive balanced one-way random-intercept estimate; serial dependence/unequal variances limit interpretation.',
                          'Cycles/task-clock is a shared-denominator proxy, not independent frequency telemetry; correlation is partly algebraic.',
                          'Warmup and power policy are constant within conditions; no causal attribution of modes to warmup/processor state is identifiable.'])
