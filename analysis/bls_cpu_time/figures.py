"""Distribution-only PNG/SVG atlas. Never plots CPU cost against load as a curve."""
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from analysis.bls_throughput.run import LABELS,safe,save,complete
from experiments.common.statistical_analysis.io import csv_write

COLORS=['#0072B2','#D55E00','#009E73']


def pages(payload):
    key,cells,directory=payload
    names=[]; mappings=[]
    for page,start in enumerate(range(0,len(cells),6)):
        selected=cells[start:start+6]
        name='distribution-'+key+f'-p{page+1}'
        names.append(name+'.png')
        mappings.extend(dict(condition_id=c['condition_id'],png=name+'.png',row=i+1) for i,c in enumerate(selected))
        if complete(directory/name):continue
        fig,axes=plt.subplots(len(selected),6,figsize=(23,max(4.5,3.1*len(selected))),squeeze=False)
        for c,row in zip(selected,axes):
            d=c['diagnostics']; inv=c['investigation']; h=d['histogram']
            metric=c.get('_metric','cpu_us_per_wu'); label=c.get('_metric_label','CPU time (µs/WU)')
            row[0].stairs(h['counts'],h['edges'],fill=True,alpha=.35,color=COLORS[0])
            row[0].set(xlabel=label,ylabel='Count')
            row[0].set_title(f"{c['condition_id']} | n={d['descriptive']['n']}, blocks={c['n_blocks']}\nDip p={d['dip']['p_value']:.3g} (exploratory)\nBin width={h['bin_width']:.3g}",fontsize=8)
            for curve,color in zip(inv['sensitivity']['curves'],COLORS):
                row[1].plot(curve['x'],curve['density'],color=color,lw=1.2,
                            label=f"{curve['multiplier']:g}× Scott ({curve['n_peaks']} peaks)")
            row[1].set(xlabel=label,ylabel='Density')
            row[1].legend(fontsize=6,frameon=False)
            row[2].step(d['ecdf']['x'],d['ecdf']['probability'],where='post',color=COLORS[0])
            row[2].set(xlabel=label,ylabel='ECDF',ylim=(0,1.02))
            # No percentile trimming: tail observations remain in every panel.
            rec=c['_plot_records']; y=np.array([r[metric] for r in rec])
            b=[r['block_local'] for r in rec]; repeats=np.array([r['pass_run_index'] for r in rec])
            row[3].scatter(b,y,c=repeats,cmap='viridis',s=13,alpha=.8,vmin=0,vmax=max(1,repeats.max()))
            flagged=d['outlier_indices']
            if flagged:row[3].scatter(np.array(b)[flagged],y[flagged],s=36,facecolors='none',edgecolors='red',linewidths=.7)
            row[3].set(xlabel='Block index (color = repeat)',ylabel=label)
            icc=inv['associations']['block_icc1']
            row[3].set_title(f"ICC={icc:.2f}" if icc is not None else 'ICC unavailable',fontsize=9)
            if 'time' in c['temporal']:
                t=c['temporal']['time']; axis=np.array(t['axis']); axis=(axis-axis.min())/1e9
                xlabel='Seconds since first measured run'
            else:
                t=c['temporal']['sequence'];axis=t['axis'];xlabel='Acquisition order (timestamps absent)'
            row[4].scatter(axis,y,c=[r['order_in_block'] for r in rec],cmap='plasma',s=13,alpha=.8)
            rho=t['spearman_rho']
            row[4].set(xlabel=xlabel,ylabel=label,title=f"Time/order rho={rho:.2f}" if rho is not None else 'Association unavailable')
            mode=c['stratum']['mode']
            load=f"load {c['offered_load_pct']:.1f}% (nominal {c['nominal_load_pct']:.1f}%)" if mode!='all-at-once' else 'all-at-once reference; no paced load'
            row[2].set_title(f"{load}\nQ01–Q99: {d['descriptive']['q01']:.1f}–{d['descriptive']['q99']:.1f}",fontsize=8)
            row[5].scatter([r['order_in_block'] for r in rec], y, c=b, cmap='viridis', s=13, alpha=.8)
            row[5].set(xlabel='Randomized invocation position (color=block)', ylabel=label)
            for ax in row:ax.tick_params(labelsize=7);ax.grid(alpha=.12)
        first=selected[0]
        fig.suptitle(f"{first['stratum']['cohort']} | {first['stratum']['workers']} workers | {first['stratum']['mode']} | {first['stratum']['perf_pass']} | {LABELS[first['scheduler']]}\nHistogram: NumPy auto (FD/Sturges); all tails retained. KDE peaks/dip are exploratory under dependence; nonsignificance does not prove unimodality.",fontsize=10)
        fig.subplots_adjust(left=.055,right=.99,top=.79 if len(selected)==1 else .94,
                            bottom=.17 if len(selected)==1 else .045,wspace=.42,hspace=.75)
        save(fig,directory/name,bbox_inches=None)
    return dict(key=key,files=names,mappings=mappings)


def detail(payload):
    c,directory=payload
    name='priority-'+c['condition_id']
    if complete(directory/name):return name+'.png'
    metric=c.get('_metric','cpu_us_per_wu'); label=c.get('_metric_label','CPU time (µs/WU)')
    inv=c['investigation']; rec=c['_plot_records']; y=np.array([r[metric] for r in rec])
    fig,axes=plt.subplots(2,3,figsize=(13,7.8))
    h=c['diagnostics']['histogram']
    for i,r in enumerate(inv['repeats']):
        axes[0,0].hist(r['values'],bins=h['edges'],histtype='step',color=COLORS[i%3],label=f"Repeat {r['repeat']}, n={r['n']}",linewidth=1.5)
    axes[0,0].set(xlabel=label,ylabel='Count');axes[0,0].legend(fontsize=8)
    for curve,color in zip(inv['sensitivity']['curves'],COLORS):
        axes[0,1].plot(curve['x'],curve['density'],color=color,label=f"{curve['multiplier']:g}× Scott")
    axes[0,1].set(xlabel=label,ylabel='Density');axes[0,1].legend(fontsize=8)
    for repeat in inv['repeats']:
        v=np.sort(repeat['values']);axes[0,2].step(v,np.arange(1,len(v)+1)/len(v),where='post',label=f"Repeat {repeat['repeat']}")
    axes[0,2].set(xlabel=label,ylabel='Repeat-stratified ECDF');axes[0,2].legend(fontsize=8)
    axes[1,0].scatter([r['order_in_block'] for r in rec],y,c=[r['block_local'] for r in rec],cmap='viridis',s=24)
    axes[1,0].set(xlabel='Randomized scheduler position (color = block)',ylabel=label)
    groups=defaultdict(list)
    for r in rec:groups[r['block_local']].append(r[metric])
    axes[1,1].scatter(list(groups),[np.mean(v) for v in groups.values()],color='black',s=25,label='Block mean')
    axes[1,1].scatter([r['block_local'] for r in rec],y,s=16,alpha=.5,label='Raw runs')
    axes[1,1].set(xlabel='Block index',ylabel=label);axes[1,1].legend(fontsize=8)
    from experiments.common.statistical_analysis.core import correlation
    blockmeans=[float(np.mean(groups[b])) for b in sorted(groups)]
    lag=correlation(blockmeans[:-1],blockmeans[1:])
    axes[1,1].set_title(f'Block-mean lag-one rho={lag:.2f}' if lag is not None else 'Lag-one association unavailable',fontsize=9)
    proxies=[r.get('cycles_per_cpu_ms_proxy') for r in rec]
    if all(v is not None for v in proxies):
        axes[1,2].scatter(proxies,y,c=[r['pass_run_index'] for r in rec],cmap='viridis',s=24)
        axes[1,2].set(xlabel='Cycles/task-clock proxy (10⁶ cycles/ms)',ylabel=label)
        axes[1,2].set_title('Shared denominator; not independent frequency telemetry',fontsize=8)
    else:axes[1,2].text(.1,.5,'Processor-state proxy unavailable',transform=axes[1,2].transAxes)
    for ax in axes.flat:ax.grid(alpha=.15)
    fig.suptitle(f"{c['condition_id']} | {c['stratum']['cohort']} | {c['scheduler']} | {c['stratum']['perf_pass']} | actual offered {c['offered_load_pct']:.1f}%\nWithin-condition associations only; warmup/power causal effects are not identified.",fontsize=11)
    fig.tight_layout(rect=(0,0,1,.94));save(fig,directory/name)
    return name+'.png'


def render(result,output):
    output=Path(output);directory=output/'figures';directory.mkdir(exist_ok=True)
    records=defaultdict(list)
    for r in result['observations']:records[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    groups=defaultdict(list)
    for c in result['conditions']:
        c['_plot_records']=records[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        s=c['stratum'];key=safe(f"{s['cohort']}-w{s['workers']}-{s['mode']}-{s['perf_pass']}-{c['scheduler']}")
        groups[key].append(c)
    tasks=[(key,sorted(cells,key=lambda c:c['offered_rate']),directory) for key,cells in sorted(groups.items())]
    atlas=[]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as executor:
        for i,page in enumerate(executor.map(pages,tasks)):
            atlas.append(page)
            if (i+1)%15==0:print(f'Distribution groups {i+1}/{len(tasks)}',flush=True)
    # A curated detailed view for 24 high-priority conditions. The selection was
    # recorded numerically before any plotting; this is not inferential testing.
    from experiments.common.statistical_analysis import load_csv
    ids={r['condition_id'] for r in load_csv(output/'tables/shortlist.csv')}
    detailed=[c for c in result['conditions'] if c['condition_id'] in ids]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as executor:
        details=list(executor.map(detail,[(c,directory) for c in detailed]))
    mapping=[dict(condition_id=m['condition_id'],diagnostic_png='figures/'+m['png'],row_in_page=m['row']) for group in atlas for m in group['mappings']]
    csv_write(output/'tables/condition_figure_map.csv',mapping)
    lines=['# CPU time/WU — Stage 1 distribution atlas','','No regression or final performance curves. Every condition is kept separate; only diagnostic pages contain multiple labelled condition rows.','',
           'µs/WU; raw tails are retained. Bin selection/widths, bandwidth-sensitive KDE peaks, ECDF, block/repeat structure and chronology are shown. CSV-only unit assumptions and scope limitations are described in [SUMMARY.md](SUMMARY.md).','',
           '## Priority investigation details','']
    for name in details:lines += [f'![{name}](figures/{name})','',f'[SVG](figures/{name[:-4]}.svg)','']
    lines += ['## All conditions','']
    for group in atlas:
        lines += ['### '+group['key'],'']
        for name in group['files']:lines += [f'![{name}](figures/{name})','',f'[SVG](figures/{name[:-4]}.svg)','']
    (output/'FIGURES.md').write_text('\n'.join(lines)+'\n')
    expected={name for group in atlas for name in group['files']}|set(details)
    for name in expected:
        if not complete(directory/Path(name).stem):raise ValueError('Incomplete figure '+name)
    if len(mapping)!=len(result['conditions']):raise ValueError('Missing condition coverage')
    (output/'completion.json').write_text(json.dumps(dict(physical_runs=len(result['observations']),conditions=len(mapping),
        figure_pairs=len(expected),distribution_pages=sum(len(g['files']) for g in atlas),priority_detail_pages=len(details),
        regression_performed=False,final_performance_curves=False),indent=2)+'\n')
    print(f'Validated {len(expected)} PNG/SVG pairs; {len(mapping)} condition mappings',flush=True)
