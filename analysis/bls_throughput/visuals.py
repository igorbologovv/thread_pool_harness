"""Plot precomputed empirical estimates first; model figures are separate artifacts."""
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import json
from multiprocessing import get_context
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from .run import LABELS,safe,save,complete
from analysis.bls_cpu_time.figures import pages,detail
from experiments.common.statistical_analysis.io import csv_write
from experiments.common.statistical_analysis.plotting import plot_regression


def dump(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def distribution_atlas(result,output):
    directory=output/'figures';directory.mkdir(exist_ok=True)
    rows=defaultdict(list)
    for r in result['observations']:rows[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    groups=defaultdict(list);conditions=[]
    for c in result['conditions']:
        c=deepcopy(c);c['_plot_records']=rows[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        s=c['stratum'];key=safe(f"{s['cohort']}-w{s['workers']}-{s['mode']}-{s['perf_pass']}-{c['scheduler']}")
        conditions.append(c);groups[key].append(c)
    tasks=[(k,sorted(v,key=lambda c:c['offered_rate']),directory) for k,v in sorted(groups.items())]
    atlas=[]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as pool:
        for i,page in enumerate(pool.map(pages,tasks)):
            atlas.append(page)
            if (i+1)%12==0:print(f'{output.name}: distributions {i+1}/{len(tasks)} groups',flush=True)
    suspect=[c for c in conditions if c['suspected_shape']]
    # Every suspected condition gets a detail; this is not a selective shortlist.
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as pool:
        details=list(pool.map(detail,[(c,directory) for c in suspect]))
    mapping=[dict(condition_id=m['condition_id'],diagnostic_png='figures/'+m['png'],row_in_page=m['row']) for group in atlas for m in group['mappings']]
    csv_write(output/'tables/condition_figure_map.csv',mapping)
    index=dict(atlas=atlas,details=details,conditions=len(conditions),suspected_conditions=len(suspect))
    dump(output/'distribution_index.json',index)
    write_index(output)
    for group in atlas:
        for name in group['files']:
            if not complete(directory/Path(name).stem):raise ValueError('Incomplete distribution '+name)
    for name in details:
        if not complete(directory/Path(name).stem):raise ValueError('Incomplete detail '+name)
    print(f'All {len(conditions)} distributions mapped; {len(details)} block detail pages',flush=True)


def group_conditions(conditions):
    d=defaultdict(list)
    for c in conditions:d[tuple(c['stratum'].items())].append(c)
    return d


def empirical(cells,*,xlabel,ylabel,title,xvalue=lambda c:c['offered_load_pct'],xci=None,zoom=False):
    from experiments.common.statistical_analysis.plotting import plot_estimates
    # Plotting adapter only: true statistical records/models remain untouched.
    # Hardware panels compare separate acquired worker scenarios without pooling.
    schedulers=sorted({c['scheduler'] for c in cells})
    levels=np.array(sorted({xvalue(c) for c in cells}));spacing=float(np.ptp(levels))*.012 or .1
    if len(levels)>1:spacing=min(spacing,max(np.diff(levels))*.08)
    view=dict(design=dict(x=xlabel,metric=ylabel),conditions=[dict(c,x=xvalue(c),stratum={}) for c in cells])
    fig=plot_estimates(view,stratum={},display_span=spacing,labels=LABELS)
    fig.set_size_inches(10,6);ax=fig.axes[0]
    for j,scheduler in enumerate(schedulers):
        cs=sorted((c for c in cells if c['scheduler']==scheduler),key=xvalue);color=f'C{j}'
        shift=(j-(len(schedulers)-1)/2)*spacing/max(1,len(schedulers)-1)
        for c in cs:
            if xci:
                low,high=xci(c);ax.hlines(c['estimate'],low+shift,high+shift,color=color,alpha=.55,lw=.8)
            if zoom and 24<=xvalue(c)<=101:
                ax.annotate(f"{c['estimate']:.0f}",(xvalue(c)+shift,c['estimate']),xytext=(3,4),textcoords='offset points',fontsize=6,color=color)
    ax.set(xlabel=xlabel,ylabel=ylabel,title=title)
    if zoom:ax.set_xlim(24,101) # retain near-25/100 measurements under own-capacity normalization
    ax.legend(fontsize=8,ncol=2);ax.grid(alpha=.18)
    fig.text(.5,.012,'Unconnected equal-block means; 95% whole-block percentile CIs; display offsets only. All observations retained.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.035,1,1))
    return fig


def performance(absolute,relative,cpu,outputs):
    indices=[[],[]]
    for i,(result,kind,ylabel) in enumerate([(relative,'relative','Relative throughput (% of own saturation recheck)'),(absolute,'absolute','Achieved throughput (WU/s)'),(cpu,'cpu-load','CPU time (µs/WU)')]):
        outindex=0 if i<2 else 1;directory=outputs[outindex]/'figures'
        for facet,cells in sorted(group_conditions(result['conditions']).items(),key=repr):
            s=dict(facet)
            if s['mode']=='all-at-once':continue
            key=safe(f"{s['cohort']}-w{s['workers']}-{s['mode']}-{s['perf_pass']}")
            title=f"{s['cohort']} | {s['workers']} workers | {s['mode']} | {s['perf_pass']}"
            name='performance-'+kind+'-'+key
            if not complete(directory/name):
                fig=empirical(cells,xlabel='Scheduled offered load (% of same scheduler’s own saturation WU/s)',ylabel=ylabel,title=title)
                if kind=='relative':fig.axes[0].axhline(100,color='gray',ls=':',lw=.8)
                save(fig,directory/name)
            indices[outindex].append(dict(kind=kind,stratum=s,png=name+'.png'))
            if kind=='cpu-load' and s['workers']==2 and any(25<=c['offered_load_pct']<=100 for c in cells):
                name='performance-cpu-zoom25-100-'+key
                if not complete(directory/name):save(empirical(cells,xlabel='Scheduled offered load (% of own saturation WU/s)',ylabel=ylabel,title=title+' | detailed 25–100% region',zoom=True),directory/name)
                indices[1].append(dict(kind='cpu-zoom25-100',stratum=s,png=name+'.png'))
    alookup={(c['acquisition'],c['scheduler'],c['stratum']['perf_pass']):c for c in absolute['conditions']}
    for c in cpu['conditions']:
        a=alookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        c['_achieved']=a['estimate'];c['_achieved_ci']=a['ci95']
    for facet,cells in sorted(group_conditions(cpu['conditions']).items(),key=repr):
        s=dict(facet);key=safe('-'.join(str(v) for v in s.values()));name='performance-cpu-achieved-'+key
        if not complete(outputs[1]/'figures'/name):
            fig=empirical(cells,xvalue=lambda c:c['_achieved'],xci=lambda c:c['_achieved_ci'],xlabel='Achieved throughput (WU/s; horizontal 95% CI)',ylabel='CPU time (µs/WU)',title=' | '.join(f'{k}={v}' for k,v in s.items()))
            save(fig,outputs[1]/'figures'/name)
        indices[1].append(dict(kind='cpu-achieved',stratum=s,png=name+'.png'))
    # One plot per cohort/mode/pass/nominal scenario; no aggregation of workers.
    hw=defaultdict(list)
    for c in cpu['conditions']:
        s=c['stratum'];hw[(s['cohort'],s['mode'],s['perf_pass'],c['nominal_load_pct'])].append(c)
    for key,cells in sorted(hw.items(),key=repr):
        name='performance-cpu-workers-'+safe('-'.join(map(str,key)))
        if not complete(outputs[1]/'figures'/name):
            fig=empirical(cells,xvalue=lambda c:c['stratum']['workers'],xlabel='Measured worker count (discrete hardware scenarios)',ylabel='CPU time (µs/WU)',
                title=f'{key[0]} | {key[1]} | {key[2]} | nominal scenario {key[3]:g}%\nOffered WU/s and batch lengths vary with workers; see exported observations')
            fig.axes[0].set_xticks([2,4,8,16]);save(fig,outputs[1]/'figures'/name)
        indices[1].append(dict(kind='cpu-workers',stratum=dict(zip(('cohort','mode','perf_pass','nominal_load_pct'),key)),png=name+'.png'))
    for output,index in zip(outputs,indices):
        dump(output/'performance_index.json',index)
        for item in index:
            if not complete(output/'figures'/Path(item['png']).stem):raise ValueError('Incomplete performance figure')
        write_index(output)
        print(output,len(index),'empirical performance figure pairs',flush=True)


def model_page(payload):
    relationship,models,directory,*options=payload
    refresh=bool(options and options[0])
    xlabel=('Worker count (measured configurations)' if relationship=='cpu_workers' else 'Achieved throughput (WU/s)' if relationship=='cpu_achieved' else 'Scheduled offered load (% of own saturation WU/s)')
    unit='µs/WU' if relationship.startswith('cpu') else 'percentage points' if relationship.startswith('relative') else 'WU/s'
    # Residuals are one row per scheduler, each relationship/facet page.
    s=models[0]['stratum'];key=safe(relationship+'-'+'-'.join(str(v) for v in s.values()))
    files=[]
    ok=[m for m in models if m['status']=='ok']
    if not ok:return []
    name='model-curves-'+key
    if refresh or not complete(directory/name):
        fig,axes=plt.subplots(len(ok),2,figsize=(13,3.1*len(ok)),squeeze=False)
        for m,(ax,cvax) in zip(ok,axes):
            curve=m['curve'];band=m.get('bootstrap_curve',{});p=m.get('predictive_comparison',{})
            byx=defaultdict(list)
            for r in m['residuals']:byx[r['x']].append(r['observed'])
            x=sorted(byx);y=[np.mean(byx[v]) for v in x]
            ax.scatter(x,y,s=22,color='black',label='Empirical estimates')
            for point in m.get('empirical_estimates',[]):
                if point['ci95'][0] is not None:ax.vlines(point['x'],*point['ci95'],color='black',lw=1)
            ax.plot(curve['x'],curve['y'],ls='--',color='C0',label='Selected polynomial (descriptive if CV fails)')
            if 'ci95_low' in band:ax.fill_between(curve['x'],band['ci95_low'],band['ci95_high'],color='C0',alpha=.18,label='Conditional pointwise 95% mean band')
            if 'curve' in p:ax.plot(p['curve']['x'],p['curve']['y'],color='C1',label=p['selected']['family'])
            if 'alternative_bootstrap_curve' in p:
                b=p['alternative_bootstrap_curve'];ax.fill_between(p['curve']['x'],b['ci95_low'],b['ci95_high'],color='C1',alpha=.18,label='Conditional spline mean band')
            rmse=p.get('nested_cv_rmse');screen=p.get('passes_accuracy_screen')
            ax.set(title=f"{LABELS[m['scheduler']]} | degree {m['model']['degree']} | accuracy screen {'passes (exploratory)' if screen else 'fails/unavailable'}",xlabel=xlabel,ylabel='µs/WU' if relationship.startswith('cpu') else '%' if relationship.startswith('relative') else 'WU/s')
            ax.legend(fontsize=7)
            for isedge,color,label in [(False,'C2','Held-out interior level'),(True,'C3','Held-out boundary (extrapolation)')]:
                rows=[r for r in p.get('nested_cv',[]) if r['extrapolation']==isedge]
                cvax.scatter([r['x'] for r in rows],[r['residual'] for r in rows],color=color,label=label,s=25)
            cvax.axhline(0,color='gray',lw=.8);cvax.set(xlabel='Omitted '+xlabel,ylabel='Observed − predicted ('+unit+')',title=f'Nested family/degree selection RMSE={rmse:.3g}' if rmse is not None else 'No nested CV')
            cvax.legend(fontsize=7)
        fig.suptitle(str(s)+'\nObserved domain only; hardware curves do not validate intermediate worker configurations. No Gaussian inference.',fontsize=10)
        fig.tight_layout(rect=(0,0,1,.96));save(fig,directory/name)
    files.append(name+'.png')
    name='model-residuals-'+key
    if refresh or not complete(directory/name):
        fig,axes=plt.subplots(len(ok),4,figsize=(17,3.0*len(ok)),squeeze=False)
        for m,row in zip(ok,axes):
            plot_regression(m,axes=row)
            row[0].set_xlabel('Fitted ('+unit+')');row[0].set_ylabel('Block residual ('+unit+')')
            row[1].set_xlabel(xlabel);row[1].set_ylabel('Block residual ('+unit+')')
            row[2].set_xlabel('Block residual ('+unit+')')
            row[3].set_xlabel('Omitted '+xlabel);row[3].set_ylabel('Nested CV residual ('+unit+')')
            row[0].set_title(LABELS[m['scheduler']]+' | degree '+str(m['model']['degree']),fontsize=9)
        fig.suptitle(relationship+' | '+str(s)+'\nPolynomial block residuals and nested omitted-X-level validation; no IID residual tests.',fontsize=10)
        fig.tight_layout(rect=(0,0,1,.96));save(fig,directory/name)
    files.append(name+'.png')
    return files


def model_figures(modelsets,output):
    version=output/'model_plot_version.json'
    refresh=not version.exists() or json.loads(version.read_text()).get('version')!=2
    groups=defaultdict(list)
    for rel,models in modelsets.items():
        for m in models:groups[(rel,tuple(m['stratum'].items()))].append(m)
    tasks=[(rel,ms,output/'figures',refresh) for (rel,_),ms in sorted(groups.items(),key=repr)]
    files=[]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as pool:
        for i,names in enumerate(pool.map(model_page,tasks)):
            files+=names
            if (i+1)%12==0:print('Model figure groups',i+1,'/',len(tasks),flush=True)
    dump(output/'model_index.json',files)
    dump(version,dict(version=2,axis_units_explicit=True))
    for name in files:
        if not complete(output/'figures'/Path(name).stem):raise ValueError('Incomplete model figure')
    write_index(output)


def write_index(output):
    lines=['# Figure index','',
        'All exports are paired PNG/SVG. Conditions, schedulers, cohorts, workers, arrival modes and perf passes remain separate. Display offsets never enter calculations. No experimental points are connected.','',
        '## All-condition distributions','',
        'Histogram: NumPy auto (FD/Sturges), KDE: Scott with half/double bandwidth sensitivity. Every condition shows ECDF, individual block/repeat observations, time and randomized order. [Condition/page mapping](tables/condition_figure_map.csv).','']
    p=output/'distribution_index.json'
    if p.exists():
        index=json.loads(p.read_text())
        for g in index['atlas']:
            lines+=['### '+g['key'],'']
            for f in g['files']:lines += [f'[{f}](figures/{f}) · [SVG](figures/{Path(f).stem}.svg)','']
        lines+=['## Block/repeat details for every suspected shape','']
        for f in index['details']:lines += [f'[{f}](figures/{f}) · [SVG](figures/{Path(f).stem}.svg)','']
    p=output/'relative_distribution_index.json'
    if p.exists():
        lines+=['## Relative-throughput diagnostic scales','', 'The same physical distributions appear with achieved WU/s and own-scheduler saturation percentage axes. Dip and shape are invariant under this positive rescaling; relative CIs additionally include baseline uncertainty.','']
        for f in json.loads(p.read_text()):lines += [f'[{f}](figures/{f}) · [SVG](figures/{Path(f).stem}.svg)','']
    p=output/'performance_index.json'
    if p.exists():
        lines+=['## Empirical performance figures','']
        for v in json.loads(p.read_text()):
            f=v['png'];lines += [f"{v['kind']} | {v['stratum']}: [{f}](figures/{f}) · [SVG](figures/{Path(f).stem}.svg)",'']
    p=output/'model_index.json'
    if p.exists():
        lines+=['## Regression curves, residuals and omitted-level validation','']
        for f in json.loads(p.read_text()):lines += [f'[{f}](figures/{f}) · [SVG](figures/{Path(f).stem}.svg)','']
    (output/'FIGURES.md').write_text('\n'.join(lines)+'\n')


def relative_scale_group(payload):
    key,cells,directory,capacities=payload
    from .run import distribution_pages
    return distribution_pages(cells,directory,'relative-scale-'+key,capacities)


def relative_scales(absolute,output):
    """Positive rescaling does not change modality: expose both numeric axes."""
    capacities={(c['stratum']['workers'],c['scheduler']):c for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck'}
    groups=defaultdict(list)
    for c in absolute['conditions']:
        s=c['stratum']
        if s['mode']=='all-at-once':continue
        key=safe(f"{s['cohort']}-w{s['workers']}-{s['mode']}-{s['perf_pass']}-{c['scheduler']}")
        groups[key].append(c)
    files=[]
    tasks=[(k,sorted(cs,key=lambda c:c['offered_load_pct']),output/'figures',capacities) for k,cs in sorted(groups.items())]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as pool:
        for i,names in enumerate(pool.map(relative_scale_group,tasks)):
            files+=names
            if (i+1)%24==0:print('Relative distribution scales',i+1,'/',len(tasks),flush=True)
    dump(output/'relative_distribution_index.json',files)
    for name in files:
        if not complete(output/'figures'/Path(name).stem):raise ValueError('Incomplete relative-scale diagnostic')
    write_index(output)


def alternative_residual_page(payload):
    relationship,models,directory=payload
    xlabel=('Worker count (measured configurations)' if relationship=='cpu_workers' else 'Achieved throughput (WU/s)' if relationship=='cpu_achieved' else 'Scheduled offered load (% of own saturation WU/s)')
    unit='µs/WU' if relationship.startswith('cpu') else 'percentage points' if relationship.startswith('relative') else 'WU/s'
    models=[m for m in models if 'alternative_residuals' in m.get('predictive_comparison',{})]
    if not models:return []
    s=models[0]['stratum'];name='model-alternative-residuals-'+safe(relationship+'-'+'-'.join(str(v) for v in s.values()))
    if not complete(directory/name) or not (directory.parent/'alternative_plot_version.json').exists():
        fig,axes=plt.subplots(len(models),4,figsize=(17,3.0*len(models)),squeeze=False)
        for m,row in zip(models,axes):
            p=m['predictive_comparison'];view=dict(m,residuals=p['alternative_residuals'],nested_cv=p['nested_cv'])
            plot_regression(view,axes=row)
            row[0].set_xlabel('Fitted ('+unit+')');row[0].set_ylabel('Block residual ('+unit+')')
            row[1].set_xlabel(xlabel);row[1].set_ylabel('Block residual ('+unit+')')
            row[2].set_xlabel('Block residual ('+unit+')')
            row[3].set_xlabel('Omitted '+xlabel);row[3].set_ylabel('Nested CV residual ('+unit+')')
            row[0].set_title(LABELS[m['scheduler']]+' | '+p['selected']['family'],fontsize=9)
        fig.suptitle(relationship+' | '+str(s)+'\nSelected alternative block residuals; nested family-selection omitted-level errors.',fontsize=10)
        fig.tight_layout(rect=(0,0,1,.96));save(fig,directory/name)
    return [name+'.png']


def alternative_residual_figures(modelsets,output):
    groups=defaultdict(list)
    for rel,models in modelsets.items():
        for m in models:
            if 'alternative_residuals' in m.get('predictive_comparison',{}):groups[(rel,tuple(m['stratum'].items()))].append(m)
    tasks=[(rel,ms,output/'figures') for (rel,_),ms in sorted(groups.items(),key=repr)]
    files=[]
    with ProcessPoolExecutor(max_workers=3,mp_context=get_context('spawn')) as pool:
        for names in pool.map(alternative_residual_page,tasks):files+=names
    old=json.loads((output/'model_index.json').read_text())
    dump(output/'model_index.json',list(dict.fromkeys(old+files)))
    dump(output/'alternative_plot_version.json',dict(version=2,axis_units_explicit=True))
    for name in files:
        if not complete(output/'figures'/Path(name).stem):raise ValueError('Incomplete alternative residual figure')
    write_index(output)
