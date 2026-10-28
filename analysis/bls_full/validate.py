"""Independent SQL arithmetic, direct-index bootstrap and artifact/preservation validation."""
import json
import sqlite3
import subprocess
import pymupdf
from common import *

def main():
    checks=[]
    def check(name,ok,detail=''):
        checks.append(dict(check=name,passed=bool(ok),detail=str(detail)))
        print(('PASS ' if ok else 'FAIL ')+name,detail,flush=True)
    inv=read('input_inventory')
    changed=[]
    for r in inv.itertuples():
        p=ROOT/r.path
        if not p.exists() or p.stat().st_size!=r.bytes or sha(p)!=r.sha256:changed.append(r.path)
    check('original_archive_and_dataset_hashes_unchanged',not changed,f'{len(inv)} original files; changed={changed}')
    effects=read('baseline_effects');blocks=read('block_means');raw=read('raw_runs')
    check('eligible_count',len(raw)==11200 and len(blocks)==5600)
    for name in ['raw_runs','block_means','baseline_effects','factor_effects','cross_campaign_effects','factor_interactions','descriptive_statistics','pareto','distribution_diagnostics']:
        data=read(name)
        for c in ['configuration','reference']:
            if c in data:check(name+'_'+c+'_exclusion',not data[c].str.contains(r'^threadance-hot-first(?:__|$)',regex=True,na=False).any())
    maximum_errors={'elapsed':0.,'cpu':0.,'block':0.,'effect':0.,'bootstrap':0.}
    selected=['threadance__spin-then-park__q8__s0','threadance-hot-first-rtrb__spin-then-park__q8__b1__s0','threadance-hot-first-rtrb__spin-then-park__q8__b2__s0','bevy']
    for folder in sorted(INPUT.glob('bls-*')):
        if not folder.is_dir():continue
        db=sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1',uri=True)
        # This independent query avoids the extractor's pivot/merge/derived-metric path.
        sql=pd.read_sql_query("SELECT r.id AS run_id,r.experiment_id,r.elapsed_ns/1000000.0 AS elapsed_ms,p.counter_value AS task_clock_ms FROM run r JOIN perf_metric p ON p.run_id=r.id AND p.event_name='task-clock'",db)
        db.close()
        man=pd.read_csv(folder/'manifest.csv').fillna({'arrival_rate':0})
        x=sql.merge(man[['experiment_id','block','configuration','arrival_rate']],on='experiment_id')
        rr=raw[raw.campaign==folder.name].set_index('run_id')
        sx=x[x.run_id.isin(rr.index)].set_index('run_id').loc[rr.index]
        for m,k in [('elapsed_ms','elapsed'),('task_clock_ms','cpu')]:maximum_errors[k]=max(maximum_errors[k],float(np.max(abs(rr[m]-sx[m]))))
        for rate,g in x.groupby('arrival_rate'):
            bb=blocks[(blocks.campaign==folder.name)&(blocks.arrival_rate==rate)]
            s=bb.scenario.iloc[0]
            for m in ['elapsed_ms','task_clock_ms']:
                pivot=g.pivot_table(index='block',columns='configuration',values=m,aggfunc='mean').sort_index()
                for c in selected:
                    observed=bb[bb.configuration==c].sort_values('block')[m].to_numpy()
                    maximum_errors['block']=max(maximum_errors['block'],float(np.max(abs(observed-pivot[c]))))
                    d=(pivot[c]-pivot.rayon).to_numpy()
                    expected=effects[(effects.scenario==s)&(effects.configuration==c)&(effects.reference=='rayon')&(effects.metric==m)].iloc[0]
                    maximum_errors['effect']=max(maximum_errors['effect'],abs(d.mean()-expected.absolute_effect))
                    # Independently resample actual indices, rather than multiply count weights.
                    indices=rng_for(folder.name).integers(20,size=(B,20))
                    low,high=np.quantile(d[indices].mean(axis=1),[.025,.975])
                    maximum_errors['bootstrap']=max(maximum_errors['bootstrap'],abs(low-expected.ci_low),abs(high-expected.ci_high))
    for k,v in maximum_errors.items():check('independent_SQL_and_bootstrap_'+k,v<1e-7,v)
    paced=raw[raw.delivery_mode!='all-at-once']
    check('paced_decomposition_identity',np.allclose(paced.schedule_excess_ms,paced.drain_delay_ms+paced.last_lateness_ms,atol=2e-8,rtol=0))
    check('all_blocks_complete',(raw.groupby(['scenario','configuration','block']).size()==2).all())
    check('all_primary_comparisons',int(effects.primary.sum())==520)
    settings=json.loads((OUT/'statistical_settings.json').read_text())
    check('minimum_bootstrap_replicates',settings['bootstrap_replicates']>=10000)
    pdf=pymupdf.open(OUT/'report.pdf');texts=[p.get_text() for p in pdf];text='\n'.join(texts)
    check('PDF_readable',len(pdf)>30 and all(len(t.strip())>5 for t in texts),f'{len(pdf)} pages; {len(text)} extracted characters')
    for heading in ['Executive Summary','Research Questions','Benchmark Infrastructure','Data Integrity','Baseline Performance','Threadance Configuration','Wait/Wake','Queue Topology','Arrival Dynamics','CPU Resource','Distribution Diagnostics','Practical Configuration','Threats to Validity','Conclusions','Appendices']:
        check('PDF_section_'+heading,heading in text)
    check('PDF_no_unresolved_references','??' not in text)
    check('PDF_no_missing_glyph_marker','\ufffd' not in text)
    check('PDF_bookmarks',len(pdf.get_toc())>=15)
    # Detect text physically outside the page; includes rotated landscape pages in unrotated coordinates.
    outside=[]
    for i,p in enumerate(pdf):
        w,h=p.mediabox.width,p.mediabox.height
        for block in p.get_text('dict')['blocks']:
            if block['type']!=0:continue
            for line in block['lines']:
                for span in line['spans']:
                    a,b,c,d=span['bbox']
                    if a<-1 or b<-1 or c>w+1 or d>h+1:outside.append((i+1,span['text']))
    check('PDF_text_inside_page',not outside,outside[:10])
    catalog=json.loads((OUT/'figure_catalog.json').read_text());check('figure_catalog_complete',len(catalog)>=50)
    for f in catalog:
        path=FIGURES/(f['name']+'.pdf')
        fp=pymupdf.open(path)
        check('figure_'+f['name'],len(fp)==1 and len(fp[0].get_text().strip())>10 and (FIGURES/(f['name']+'.svg')).is_file())
    log=(OUT/'latex_pass_2.log').read_text()
    overfull=[x for x in log.splitlines() if 'Overfull' in x]
    check('LaTeX_no_overfull_boxes',not overfull,overfull[:12])
    check('LaTeX_no_oversized_floats','Float too large' not in log)
    result={'checks':checks,'passed':all(c['passed'] for c in checks),'pdf_pages':len(pdf),'original_files_verified':len(inv),
        'independent_maximum_absolute_errors':maximum_errors,'visual_review':'See visual_validation.md for manually inspected pages and figures.'}
    (OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['passed'],'Validation failures recorded in validation.json'
    print('All validation checks passed',flush=True)

if __name__=='__main__':main()
