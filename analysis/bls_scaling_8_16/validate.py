"""Independent SQL, direct-index bootstrap and final preservation/PDF checks."""
from data import *
import pymupdf
import re

def main():
    checks=[]
    def check(name,ok,detail=''):
        checks.append(dict(check=name,passed=bool(ok),detail=str(detail)))
        print(('PASS ' if ok else 'FAIL ')+name+': '+str(detail),flush=True)
    inventory=read('input_inventory')
    changed=[r.path for r in inventory.itertuples() if not (ROOT/r.path).is_file() or (ROOT/r.path).stat().st_size!=r.bytes or sha(ROOT/r.path)!=r.sha256]
    check('all_original_inputs_unchanged',not changed,f'{len(inventory)} files; changed={changed}')
    check('audits_pass',read('audit_checks').passed.all())
    raw=read('raw_runs');block=read('block_means');e=read('paired_effects');cross=read('independent_effects')
    check('new_and_exact_historical_counts',len(raw[raw.workers>=8])==6000 and len(raw[raw.workers<8])==6000)
    check('only_matching_q64',all(c in ['rayon','bevy'] or '__q64' in c for c in raw.configuration.unique()))
    check('complete_twenty_blocks', (raw.groupby(['scenario','configuration','block']).size()==2).all() and (block.groupby(['scenario','configuration']).size()==20).all())
    # Direct independent SQL path: original identifiers joined through the archived manifest.
    errors=[]
    for campaign,g in block.groupby('campaign'):
        study='bls-scaling-8-16' if g.workers.iloc[0]>=8 else 'bls-full'
        folder=ROOT/'results/studies'/study/'campaigns'/campaign
        db=sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1',uri=True)
        q=pd.read_sql_query("SELECT r.experiment_id, r.elapsed_ns/1000000.0 elapsed_ms, p.counter_value task_clock_ms FROM run r JOIN perf_metric p ON p.run_id=r.id AND p.event_name='task-clock'",db)
        db.close()
        man=pd.read_csv(folder/'manifest.csv').fillna({'arrival_rate':0})
        q=q.merge(man[['experiment_id','configuration','block','arrival_rate']],on='experiment_id')
        q=q.groupby(['configuration','block','arrival_rate'])[['elapsed_ms','task_clock_ms']].mean()
        for r in g.itertuples():
            v=q.loc[(r.configuration,r.block,r.arrival_rate)]
            errors.extend([abs(r.elapsed_ms-v.elapsed_ms),abs(r.task_clock_ms-v.task_clock_ms)])
    check('independent_SQL_all_block_elapsed_and_CPU',max(errors)<1e-7,max(errors))
    # Rebuild every printed bootstrap claim using indices, never weight multiplication.
    v={(s,c):g.sort_values('block') for (s,c),g in block.groupby(['scenario','configuration'])}
    indices={camp:rng_for(camp).integers(20,size=(B,20)) for camp in block.campaign.unique()}
    def vector(s,c,m):return v[(s,c)][m].to_numpy()
    def draws(s,c,m):
        g=v[(s,c)];return g[m].to_numpy()[indices[g.campaign.iloc[0]]].mean(axis=1)
    bootstrap_errors=[]
    for r in read('report_claims').itertuples():
        kind=r.kind if isinstance(r.kind,str) else 'paired'
        if kind=='paired':
            x=vector(r.scenario,r.configuration,r.metric);y=vector(r.scenario,r.reference,r.metric)
            X=draws(r.scenario,r.configuration,r.metric);Y=draws(r.scenario,r.reference,r.metric)
        elif kind=='change_vs_Rayon':
            x=vector(r.scenario,r.configuration,r.metric)-vector(r.scenario,'rayon',r.metric)
            y=vector(r.reference_scenario,r.configuration,r.metric)-vector(r.reference_scenario,'rayon',r.metric)
            X=draws(r.scenario,r.configuration,r.metric)-draws(r.scenario,'rayon',r.metric)
            Y=draws(r.reference_scenario,r.configuration,r.metric)-draws(r.reference_scenario,'rayon',r.metric)
        else:
            x=vector(r.scenario,r.configuration,r.metric);y=vector(r.reference_scenario,r.configuration,r.metric)
            X=draws(r.scenario,r.configuration,r.metric);Y=draws(r.reference_scenario,r.configuration,r.metric)
        if r.field=='ratio_of_means_pct':point=100*(x.mean()/y.mean()-1);z=100*(X/Y-1)
        elif r.field=='speedup':point=y.mean()/x.mean();z=Y/X
        else:point=x.mean()-y.mean();z=X-Y
        if r.reduction:point=-point;z=-z
        lo,hi=np.quantile(z,[.025,.975]);bootstrap_errors.extend([abs(point-r.value),abs(lo-r.low),abs(hi-r.high)])
    check('independent_direct_index_all_printed_intervals',max(bootstrap_errors)<1e-6,max(bootstrap_errors))
    paced=raw[raw.delivery_mode!='all-at-once']
    check('drain_identity',np.allclose(paced.schedule_excess_ms,paced.drain_delay_ms+paced.last_lateness_ms,atol=2e-7))
    sim=e[(e.factor=='spin')&(e.metric=='elapsed_ms')]
    check('all_40_spin_elapsed_CIs_include_zero',len(sim)==40 and ((sim.sim_ci_low<=0)&(sim.sim_ci_high>=0)).all())
    prov=json.loads((OUT/'source_provenance.json').read_text())
    check('same_binary_all_campaigns',prov['historical_binary_sha256']==prov['new_binary_sha256']==[prov['current_binary_sha256']])
    check('implementation_sources_unchanged',not prov['harness_diff']['stdout'] and not prov['threadance_diff']['stdout'])
    pdf=pymupdf.open(OUT/'report.pdf');texts=[p.get_text() for p in pdf];text='\n'.join(texts)
    check('PDF_13_pages',len(pdf)==13,len(pdf))
    check('PDF_all_pages_substantive',all(len(t)>500 for t in texts))
    check('PDF_exactly_seven_figures',len(list(FIGURES.glob('*.pdf')))==7 and set(re.findall(r'Figure (\d+):',text))==set(map(str,range(1,8))))
    check('PDF_required_limitations_section','What the measurements cannot yet prove' in text)
    check('PDF_no_unresolved_placeholders','@@' not in text and '??' not in text and '\ufffd' not in text)
    check('PDF_bookmarks',len(pdf.get_toc())==13,len(pdf.get_toc()))
    outside=[]
    for i,p in enumerate(pdf):
        for b in p.get_text('dict')['blocks']:
            if b['type']!=0:continue
            for line in b['lines']:
                for sp in line['spans']:
                    a,b,c,d=sp['bbox']
                    if a<0 or b<0 or c>p.rect.width+1 or d>p.rect.height+1:outside.append((i+1,sp['text']))
    check('PDF_text_within_page',not outside,outside[:10])
    log=(OUT/'latex_pass_2.log').read_text()
    check('LaTeX_no_overfull_boxes','Overfull' not in log)
    check('LaTeX_no_missing_glyphs','Missing character' not in log)
    for f in sorted(FIGURES.glob('*.pdf')):
        fig=pymupdf.open(f);check('vector_figure_'+f.stem,len(fig)==1 and len(fig[0].get_text())>40 and f.with_suffix('.svg').is_file())
    result=dict(passed=all(x['passed'] for x in checks),checks=checks,pages=len(pdf),figures=7,
                original_files=len(inventory),direct_bootstrap_max_error=max(bootstrap_errors))
    (OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['passed'],'See validation.json'

if __name__=='__main__':main()
