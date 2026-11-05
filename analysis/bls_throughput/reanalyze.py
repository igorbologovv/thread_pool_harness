"""Warmup-gated BLS reanalysis in auditable, restartable stages; no benchmarks."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import datetime
import importlib.metadata
import json
from pathlib import Path
import sys

import numpy as np

from .run import prepare,collect,run_statistics,require,flatten
from .eligibility import audit,write_audit
from .diagnostics import diagnose,report as diagnostic_report
from analysis.bls_cpu_time.inputs import CpuEnricher
from experiments.common.statistical_analysis import export,normalize_by_baseline,regress
from experiments.common.statistical_analysis.exporting import clean
from experiments.common.statistical_analysis.io import csv_write
from experiments.common.statistical_analysis.validation import compare


def write_json(path,value):
    path.write_text(json.dumps(clean(value),indent=2,allow_nan=False)+'\n')


def log(outputs,stage,**details):
    for output in outputs:
        p=output/'execution_log.json';data=json.loads(p.read_text()) if p.exists() else []
        data.append(dict(stage=stage,command=sys.argv,completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),**details))
        write_json(p,data)
    print(stage,details,flush=True)


def stats_stage(args,outputs):
    # The gate is built/exported before collect accesses metric values.
    inventory=prepare(args.archives,args.extracted)
    eligibility=audit(args.extracted,args.archives)
    for o in outputs:
        o.mkdir(parents=True,exist_ok=True);write_audit(eligibility,o);(o/'tables').mkdir(exist_ok=True)
    log(outputs,'1 warmup audit verified',eligible_campaigns=sum(r['eligible'] for r in eligibility['records']))
    enricher=CpuEnricher(args.extracted)
    rows,sources,excluded,duplicates=collect(args.extracted,enrich=enricher,eligibility=eligibility)
    require(all(r['warmups']==4 for r in rows),'Warmup gate failure')
    allowed={r['campaign'] for r in eligibility['records'] if r['eligible']}
    require({r['campaign'] for r in rows}==allowed,'Missing eligible acquisition')
    counts=dict(physical_runs=len(rows),campaigns=len(allowed),randomized_blocks=len({r['block'] for r in rows}),
        conditions=len({(r['campaign'],r['scheduler'],r['perf_pass']) for r in rows}))
    log(outputs,'2 clean dataset verified',**counts)
    absolute=run_statistics(rows,args.bootstrap,args.seed)
    capcs=[c for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck']
    caps={(c['stratum']['workers'],c['scheduler']):c for c in capcs}
    for r in rows:
        cap=caps[(r['workers'],r['scheduler'])]
        r.update(saturation_baseline_wu_s=cap['estimate'],offered_load_pct=100*r['offered_rate']/cap['estimate'],
            offered_load_denominator_campaign=cap['acquisition'],offered_load_denominator_scheduler=r['scheduler'])
    # Normalization before investigations: avoids stale unscaled shape arrays.
    paced=deepcopy(absolute);paced['conditions']=[c for c in paced['conditions'] if c['stratum']['mode']!='all-at-once'];paced['observations']=[r for r in paced['observations'] if r['mode']!='all-at-once']
    relative=normalize_by_baseline(paced,dict(conditions=capcs),match=('workers',),seed=args.seed+100000)
    absolute=diagnose(absolute,'throughput')
    relative=diagnose(relative,'relative_throughput_pct')
    bad={(r['campaign'],r['scheduler'],r['perf_pass']) for r in rows if not r['cpu_available']}
    cpurows=[r for r in rows if (r['campaign'],r['scheduler'],r['perf_pass']) not in bad]
    cpu=diagnose(run_statistics(cpurows,args.bootstrap,args.seed,metric='cpu_us_per_wu'),'cpu_us_per_wu')
    # The statistic, raw p and adjusted p of the positively rescaled throughput
    # must agree. No double-counting of multiplicity families.
    ad={(c['acquisition'],c['scheduler'],c['stratum']['perf_pass']):c for c in absolute['conditions']}
    for c in relative['conditions']:
        a=ad[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        require(np.isclose(a['diagnostics']['dip']['statistic'],c['diagnostics']['dip']['statistic'],atol=1e-12),'Dip scale invariance failed')
        c['diagnostics']['dip'].update({k:v for k,v in a['diagnostics']['dip'].items() if k.startswith('p_') or k in ('family_size','multiplicity_caveat')})
    provenance=dict(archives=inventory,sources=sources,excluded=excluded,duplicates=duplicates,
        eligibility_policy=eligibility['policy'],counts=counts,seed=args.seed,bootstrap_replicates=args.bootstrap,
        curve_bootstrap_replicates=args.curve_bootstrap,command=sys.argv,
        software={'python':sys.version,**{p:importlib.metadata.version(p) for p in ('numpy','scipy','diptest','matplotlib')}},
        cpu_excluded_conditions=list(bad),cpu_unavailable_runs=sum(not r['cpu_available'] for r in rows),
        unit_policy='task-clock microseconds / completed WU; SQLite units and raw captures freshly checked. CSV units inferred as milliseconds and clearly labelled; missing original records prevent fresh scope/status verification.')
    for o in outputs:
        write_json(o/'provenance.json',provenance)
        csv_write(o/'tables/campaigns.csv',sources)
        csv_write(o/'tables/load_denominators.csv',[dict(workers=w,scheduler=s,denominator_wu_s=c['estimate'],ci95_low=c['ci95'][0],ci95_high=c['ci95'][1],campaign=c['acquisition'],perf_pass=c['stratum']['perf_pass'],blocks=c['n_blocks']) for (w,s),c in caps.items()])
        if duplicates:csv_write(o/'tables/duplicate_representations.csv',duplicates)
    export(absolute,outputs[0]/'absolute',figures=False);export(relative,outputs[0]/'relative',figures=False)
    export(cpu,outputs[1]/'results',figures=False)
    csv_write(outputs[1]/'tables/counter_audit.csv',enricher.audit)
    summary_a=diagnostic_report(absolute,outputs[0]);summary_c=diagnostic_report(cpu,outputs[1])
    log(outputs,'3 distributions and 4 bootstrap diagnostics exported',absolute=summary_a,cpu=summary_c)
    # Render all-condition diagnostics BEFORE any performance figures/models.
    from .visuals import distribution_atlas
    distribution_atlas(absolute,outputs[0]);distribution_atlas(cpu,outputs[1])
    log(outputs,'3 distribution figures verified before regression',absolute_conditions=len(absolute['conditions']),cpu_conditions=len(cpu['conditions']))


def load(path):return json.loads(path.read_text())


def relationships(absolute,relative,cpu):
    result={}
    def view(source):
        keys=('stratum','scheduler','x','estimate','ci95','block_means','acquisition','offered_load_pct','nominal_load_pct')
        return dict(design=deepcopy(source['design']),observations=[],conditions=[{k:deepcopy(c[k]) for k in keys} for c in source['conditions']])
    for name,source in [('absolute_load',absolute),('relative_load',relative),('cpu_load',cpu),('cpu_achieved',cpu)]:
        r=view(source);r['conditions']=[c for c in r['conditions'] if c['stratum']['mode']!='all-at-once']
        for c in r['conditions']:
            c['x']=c['offered_load_pct'] if name!='cpu_achieved' else next(a['estimate'] for a in absolute['conditions'] if a['acquisition']==c['acquisition'] and a['scheduler']==c['scheduler'] and a['stratum']['perf_pass']==c['stratum']['perf_pass'])
        r['design']['x']='offered_load_pct' if name!='cpu_achieved' else 'achieved_throughput_wu_s'
        result[name]=r
    # Hardware relationship: keep nominal load, cohort/mode/pass separate. This
    # compares the acquired scenarios; absolute offered rates and batch lengths
    # change with workers and are explicitly confounded, never a causal scaling fit.
    r=view(cpu)
    for c in r['conditions']:
        c['x']=c['stratum'].pop('workers');c['stratum']['nominal_load_pct']=c['nominal_load_pct']
    r['design']['x']='workers';result['cpu_workers']=r
    return result


def model_stage(args,outputs):
    for o in outputs:
        require((o/'performance_index.json').exists(),'Performance figures must precede model fitting')
        require((outputs[0]/'relative_distribution_index.json').exists(),'Relative distribution scales must precede model fitting')
        require(not (o/'models.json').exists(),'Models already exported; use a fresh output directory')
    absolute=load(outputs[0]/'absolute/results.json');relative=load(outputs[0]/'relative/results.json');cpu=load(outputs[1]/'results/results.json')
    rels=relationships(absolute,relative,cpu)
    baselines={(c['stratum']['workers'],c['scheduler']):c for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck'}
    modelsets=[{},{}]
    for i,(name,r) in enumerate(rels.items()):
        regress(r,max_degree=3)
        if name=='relative_load':
            for c in r['conditions']:c['_baseline_blocks']=baselines[(c['stratum']['workers'],c['scheduler'])]['block_means']
        compare(r,relationship=name,seed=args.seed+200000+i,bootstrap=args.curve_bootstrap)
        for c in r['conditions']:c.pop('_baseline_blocks',None)
        models=r['regressions'];modelsets[0 if name.startswith(('absolute','relative')) else 1][name]=models
        print(name,Counter(m.get('predictive_comparison',{}).get('passes_accuracy_screen','unavailable') for m in models),flush=True)
    for o,ms in zip(outputs,modelsets):
        write_json(o/'models.json',ms)
        table=[];cv=[];res=[]
        for relationship,models in ms.items():
            for m in models:
                p=m.get('predictive_comparison',{});prefix=dict(relationship=relationship,scheduler=m['scheduler'],**m['stratum'])
                table.append(dict(**prefix,status=m['status'],model=json.dumps(m.get('model')),polynomial_cv=json.dumps(m.get('selection_cv')),
                    polynomial_nested_rmse=m.get('nested_cv_rmse'),selected_family=json.dumps(p.get('selected')),family_candidates=json.dumps(p.get('candidates')),
                    family_nested_rmse=p.get('nested_cv_rmse'),interior_nested_rmse=p.get('interior_cv_rmse'),worst_error=p.get('max_absolute_error'),
                    reference_scale=p.get('reference_scale'),passes_accuracy_screen=p.get('passes_accuracy_screen')))
                cv += [dict(**prefix,**row) for row in p.get('nested_cv',[])]
                res += [dict(**prefix,**row) for row in m.get('residuals',[])]
        csv_write(o/'tables/models.csv',table);csv_write(o/'tables/cross_validation.csv',cv);csv_write(o/'tables/residuals.csv',res)
    from .visuals import model_figures
    for o,ms in zip(outputs,modelsets):model_figures(ms,o)
    log(outputs,'6 models fit and X-level validation exported',curve_bootstrap=args.curve_bootstrap)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archives',type=Path,default=Path('b_thesis'));p.add_argument('--extracted',type=Path,default=Path('analysis/bls_throughput/extracted'))
    p.add_argument('--throughput-output',type=Path,default=Path('analysis/bls_throughput/output'));p.add_argument('--cpu-output',type=Path,default=Path('analysis/bls_cpu_time/output'))
    p.add_argument('--bootstrap',type=int,default=5000);p.add_argument('--curve-bootstrap',type=int,default=2000);p.add_argument('--seed',type=int,default=20261105)
    p.add_argument('--stage',choices=('distributions','relative-distributions','performance','models','report','all'),default='all')
    args=p.parse_args();outputs=[args.throughput_output,args.cpu_output]
    if args.stage in ('distributions','all'):stats_stage(args,outputs)
    if args.stage in ('relative-distributions','all'):
        from .visuals import relative_scales
        relative_scales(load(outputs[0]/'absolute/results.json'),outputs[0])
        log(outputs,'3 relative distribution scales verified before regression')
    if args.stage in ('performance','all'):
        for o in outputs:require((o/'DIAGNOSTIC_REPORT.md').exists() and (o/'distribution_index.json').exists(),'Complete diagnostics before performance work')
        from .diagnostics import dependence_sensitivity
        for i,o in enumerate(outputs):
            data=load(o/('absolute/results.json' if i==0 else 'results/results.json'))
            dependence_sensitivity(data,o,seed=args.seed+400000+i,replicates=args.bootstrap)
        log(outputs,'4 serial-block bootstrap sensitivity exported')
        from .visuals import performance
        performance(load(outputs[0]/'absolute/results.json'),load(outputs[0]/'relative/results.json'),load(outputs[1]/'results/results.json'),outputs)
        log(outputs,'5 empirical performance figures verified')
    if args.stage in ('models','all'):model_stage(args,outputs)
    if args.stage in ('report','all'):
        from .reporting import report,validate
        report(outputs);validate(outputs,args.archives,args.extracted)
        log(outputs,'7 reports and numerical/figure verification complete')

if __name__=='__main__':main()
