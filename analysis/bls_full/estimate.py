"""Paired block estimation, matched factors, independent campaign comparisons, Pareto sets."""
import itertools
import json
from scipy.stats import binomtest
from statsmodels.stats.multitest import multipletests
from common import *

def describe(x):
    x=np.asarray(x); x=x[np.isfinite(x)]
    if not len(x):return {}
    return dict(n=len(x),mean=x.mean(),median=np.median(x),variance=x.var(ddof=1),sd=x.std(ddof=1),
        minimum=x.min(),q05=np.quantile(x,.05),q25=np.quantile(x,.25),q75=np.quantile(x,.75),q95=np.quantile(x,.95),maximum=x.max())

def interval(z):return np.quantile(z,[.025,.975],axis=0)

def contrasts(cfg):
    cs=cfg.set_index('configuration'); names=list(cs.index); edges=[]
    for a,b in itertools.combinations(names,2):
        x,y=cs.loc[a],cs.loc[b]
        if x.scheduler in ('rayon','bevy') or y.scheduler in ('rayon','bevy'):continue
        same=lambda *fields:all(x[f]==y[f] for f in fields)
        factor=None
        if same('topology','policy','spin','backlog') and x.capacity!=y.capacity:
            factor='capacity'; a,b=(a,b) if x.capacity>y.capacity else (b,a)
        elif same('topology','policy','spin','capacity') and x.backlog!=y.backlog:
            factor='backlog'; a,b=(a,b) if x.backlog>y.backlog else (b,a)
        elif same('topology','policy','capacity','backlog') and x.spin!=y.spin:
            factor='spin'; a,b=(a,b) if x.spin>y.spin else (b,a)
        elif same('topology','capacity','backlog') and x.policy!=y.policy:
            factor='wait'; order={'crossbeam-wait':0,'agave-backoff':1,'spin-then-park':2}
            a,b=(a,b) if order[x.policy]>order[y.policy] else (b,a)
        elif same('policy','spin','capacity') and x.topology!=y.topology:
            factor='topology'; a,b=(a,b) if x.topology=='RTRB' else (b,a)
        if factor:edges.append((factor,a,b))
    return edges

def effect_rows(meta, A, R, W, metrics, family, comparison, reference):
    # A,R: n blocks x metrics. One shared set of block weights preserves all pairings.
    D=A-R; boot=W@D
    lo,hi=interval(boot); means=D.mean(axis=0)
    with np.errstate(divide='ignore',invalid='ignore'):
        rel=100*(A/R-1); rel[:,np.any(R==0,axis=0)]=np.nan
        relboot=W@rel; rlo,rhi=interval(relboot)
        grand=100*(A.mean(axis=0)/R.mean(axis=0)-1)
        ratio_boot=100*((W@A)/(W@R)-1); glo,ghi=interval(ratio_boot)
    rows=[]
    for j,m in enumerate(metrics):
        dd=D[:,j]; nz=dd[dd!=0]
        p=binomtest(int((nz>0).sum()),len(nz),.5).pvalue if len(nz) else 1.
        rows.append(dict(**meta,configuration=comparison,reference=reference,factor=family,metric=m,n_blocks=len(dd),n_runs_per_configuration=2*len(dd),
            mean_configuration=A[:,j].mean(),mean_reference=R[:,j].mean(),absolute_effect=means[j],ci_low=lo[j],ci_high=hi[j],
            relative_pct=rel[:,j].mean(),relative_ci_low=rlo[j],relative_ci_high=rhi[j],ratio_of_means_pct=grand[j],ratio_ci_low=glo[j],ratio_ci_high=ghi[j],
            paired_median=np.median(dd),paired_variance=dd.var(ddof=1),paired_sd=dd.std(ddof=1),paired_q05=np.quantile(dd,.05),paired_q95=np.quantile(dd,.95),
            positive_blocks=(dd>0).sum(),negative_blocks=(dd<0).sum(),sign_p=p))
    return rows

def analyze():
    raw=read('raw_runs'); block=read('block_means'); cfg=read('configuration_dictionary').drop_duplicates('configuration')
    cfg=cfg[cfg.configuration.isin(raw.configuration.unique())]
    edges=contrasts(cfg)
    desc=[]; effects=[]; factors=[]; cross=[]; interactions=[]; pareto=[]; dominance=[]
    cache={}; wcache={}; primary_max=np.zeros(B); factor_max={}; primary_refs=[]
    for scenario,g in block.groupby('scenario',sort=True):
        print('Statistics',scenario,flush=True)
        meta={k:g.iloc[0][k] for k in ['scenario','campaign','workers','delivery_mode','arrival_rate']}
        metrics=[m for m in METRICS if g[m].notna().all()]
        names=cfg.configuration.tolist()
        values={c:g[g.configuration==c].sort_values('block')[metrics].to_numpy() for c in names}
        W=wcache.setdefault(meta['campaign'],weights(meta['campaign']))
        cache[scenario]=(meta,metrics,values,W)
        for c in names:
            rb=raw[(raw.scenario==scenario)&(raw.configuration==c)]
            for j,m in enumerate(metrics):
                for level,x in [('raw',rb[m].to_numpy()),('block',values[c][:,j])]:
                    row=dict(**meta,configuration=c,metric=m,level=level,**describe(x))
                    if level=='block':row.update(zip(['mean_ci_low','mean_ci_high'],interval(W@x)))
                    desc.append(row)
            for ref in ('rayon','bevy'):
                if c==ref:continue
                rows=effect_rows(meta,values[c],values[ref],W,metrics,'baseline',c,ref)
                for row in rows:
                    if ref=='rayon' and c.startswith('threadance') and row['metric'] in ('elapsed_ms','task_clock_ms'):
                        j=metrics.index(row['metric']);dd=values[c][:,j]-values[ref][:,j];se=dd.std(ddof=1)/np.sqrt(20)
                        primary_max=np.maximum(primary_max,np.abs(W@dd-dd.mean())/se)
                        row['primary']=True;row['se']=se;primary_refs.append(row)
                    else:row['primary']=False
                effects.extend(rows)
        for factor,a,r in edges:
            rows=effect_rows(meta,values[a],values[r],W,metrics,factor,a,r)
            for row in rows:
                if row['metric'] in ('elapsed_ms','task_clock_ms'):
                    j=metrics.index(row['metric']);dd=values[a][:,j]-values[r][:,j];se=dd.std(ddof=1)/np.sqrt(20)
                    factor_max[factor]=np.maximum(factor_max.get(factor,np.zeros(B)),np.abs(W@dd-dd.mean())/se)
                    row['se']=se
            factors.extend(rows)
        # Uncertainty-aware dominance: each scenario/objective pair has its own simultaneous family.
        objectives=[('elapsed_ms','task_clock_ms'),('elapsed_ms','context_switches')]
        if 'schedule_excess_ms' in metrics: objectives += [('schedule_excess_ms','task_clock_ms'),('drain_delay_ms','task_clock_ms')]
        for mx,my in objectives:
            X=np.stack([values[c][:,[metrics.index(mx),metrics.index(my)]] for c in names],axis=1)
            mu=X.mean(axis=0);boots=np.einsum('bn,nck->bck',W,X)
            maxz=np.zeros(B);pairs=[]
            for i,j in itertools.combinations(range(len(names)),2):
                dd=X[:,i]-X[:,j];se=dd.std(axis=0,ddof=1)/np.sqrt(20)
                maxz=np.maximum(maxz,np.max(np.abs(boots[:,i]-boots[:,j]-(mu[i]-mu[j]))/np.maximum(se,1e-15),axis=1))
                pairs.append((i,j,se))
            critical=np.quantile(maxz,.95);dominated=[[] for c in names]
            for i,j,se in pairs:
                delta=mu[i]-mu[j];low=delta-critical*se;high=delta+critical*se
                if np.all(high<0):dominated[j].append(names[i])
                if np.all(low>0):dominated[i].append(names[j])
                dominance.append(dict(scenario=scenario,objective_x=mx,objective_y=my,configuration=names[i],reference=names[j],dx=delta[0],dy=delta[1],x_sim_low=low[0],x_sim_high=high[0],y_sim_low=low[1],y_sim_high=high[1],critical=critical))
            for i,c in enumerate(names):
                pointdom=np.any(np.all(mu<mu[i],axis=1))
                bdom=np.zeros(B,dtype=bool)
                for j in range(len(names)):
                    if i!=j:bdom |= np.all(boots[:,j]<boots[:,i],axis=1)
                pareto.append(dict(scenario=scenario,configuration=c,objective_x=mx,objective_y=my,x=mu[i,0],y=mu[i,1],point_frontier=not pointdom,
                    bootstrap_frontier_frequency=1-bdom.mean(),simultaneously_dominated_by=';'.join(dominated[i]),n_confident_dominators=len(dominated[i]),critical=critical))
    crit=np.quantile(primary_max,.95)
    for row in primary_refs:
        row['sim_ci_low']=row['absolute_effect']-crit*row['se'];row['sim_ci_high']=row['absolute_effect']+crit*row['se']
    for row in factors:
        if 'se' in row:
            cr=np.quantile(factor_max[row['factor']],.95)
            row['sim_ci_low']=row['absolute_effect']-cr*row['se'];row['sim_ci_high']=row['absolute_effect']+cr*row['se']
    ef=pd.DataFrame(effects);fa=pd.DataFrame(factors)
    ef['sign_p_holm']=np.nan
    mask=ef.primary
    ef.loc[mask,'sign_p_holm']=multipletests(ef.loc[mask,'sign_p'],method='holm')[1]
    for _,g in ef[~mask].groupby(['reference','metric']):ef.loc[g.index,'sign_p_holm']=multipletests(g.sign_p,method='holm')[1]
    for _,g in fa.groupby(['factor','metric']):fa.loc[g.index,'sign_p_holm']=multipletests(g.sign_p,method='holm')[1]
    # No cross-campaign pairing, even though numerical block IDs and shuffle seed agree.
    for sa,sb in itertools.combinations(cache,2):
        ma,msa,va,wa=cache[sa];mb,msb,vb,wb=cache[sb]
        which=None
        if ma['delivery_mode']==mb['delivery_mode'] and ma['arrival_rate']==mb['arrival_rate'] and ma['workers']!=mb['workers']:which='workers'
        elif ma['workers']==mb['workers'] and ma['arrival_rate']==mb['arrival_rate'] and ma['delivery_mode']!=mb['delivery_mode']:which='arrival_pattern'
        elif ma['workers']==mb['workers'] and ma['delivery_mode']==mb['delivery_mode'] and ma['arrival_rate']!=mb['arrival_rate']:which='arrival_rate'
        elif ma['workers']==mb['workers'] and ('all-at-once' in (ma['delivery_mode'],mb['delivery_mode'])):which='paced_vs_all'
        if not which:continue
        # Orient 4-2, bursty-fixed, 1500-900, paced-all without numerical block pairing across campaigns.
        swap=(ma['workers']<mb['workers']) if which=='workers' else ((ma['delivery_mode']=='fixed-arrivals') if which=='arrival_pattern' else ((ma['arrival_rate']<mb['arrival_rate']) if which=='arrival_rate' else ma['delivery_mode']=='all-at-once'))
        if swap:sa,sb=sb,sa;ma,mb=mb,ma;msa,msb=msb,msa;va,vb=vb,va;wa,wb=wb,wa
        paired=ma['campaign']==mb['campaign']
        for c in cfg.configuration:
            for m in CORE:
                if m not in msa or m not in msb:continue
                a=va[c][:,msa.index(m)];r=vb[c][:,msb.index(m)]
                boots=wa@a-wb@r
                relboots=100*((wa@a)/(wb@r)-1)
                low,high=interval(boots);rl,rh=interval(relboots)
                cross.append(dict(comparison=which,scenario_a=sa,scenario_b=sb,configuration=c,metric=m,paired=paired,
                    n_blocks_a=20,n_blocks_b=20,mean_a=a.mean(),mean_b=r.mean(),absolute_effect=a.mean()-r.mean(),ci_low=low,ci_high=high,
                    ratio_of_means_pct=100*(a.mean()/r.mean()-1),relative_ci_low=rl,relative_ci_high=rh))
        for factor,a,r in edges:
            for m in ('elapsed_ms','task_clock_ms'):
                da=va[a][:,msa.index(m)]-va[r][:,msa.index(m)]
                db=vb[a][:,msb.index(m)]-vb[r][:,msb.index(m)]
                low,high=interval(wa@da-wb@db)
                interactions.append(dict(comparison=which,scenario_a=sa,scenario_b=sb,factor=factor,configuration=a,reference=r,metric=m,paired_campaigns=paired,
                    difference_in_differences=da.mean()-db.mean(),ci_low=low,ci_high=high))
    save(desc,'descriptive_statistics');save(ef,'baseline_effects');save(fa,'factor_effects');save(cross,'cross_campaign_effects');save(interactions,'factor_interactions');save(pareto,'pareto');save(dominance,'pareto_pairwise_intervals')
    (OUT/'statistical_settings.json').write_text(json.dumps(dict(seed=SEED,bootstrap_replicates=B,primary_family_size=len(primary_refs),primary_simultaneous_critical=float(crit),factor_family_critical={k:float(np.quantile(v,.95)) for k,v in factor_max.items()},factor_contrasts=len(edges),relative_estimand='mean of block-level percentage differences; also ratio of grand means',primary_metrics=['elapsed_ms','task_clock_ms']),indent=2)+'\n')
    print('Saved statistics:',len(ef),'baseline effects;',len(fa),'matched factor effects',flush=True)

if __name__=='__main__':analyze()
