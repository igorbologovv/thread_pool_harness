"""A bounded contrast set, using bls_full's block bootstrap and effect implementation."""
from data import *
from estimate import effect_rows
from scipy import stats

MEAS=['elapsed_ms','task_clock_ms','cycles_per_wu','instructions_per_wu','ipc','effective_ghz',
      'context_switches','cpu_migrations','branch_miss_pct','branch_misses_per_wu','cpu_equivalents',
      'schedule_excess_ms','drain_delay_ms','last_lateness_ms']

def main():
    block=read('block_means'); cache={}; wc={}; rows=[]; cross=[]; sens=[]; claims=[]
    for s,g in block.groupby('scenario'):
        camp=g.campaign.iloc[0]; wc.setdefault(camp,weights(camp))
        cache[s]=(camp,{c:gg.sort_values('block') for c,gg in g.groupby('configuration')})
    def get(s,c,m): return cache[s][1][c][m].to_numpy()
    def w(s,length=1):
        camp=cache[s][0]
        key=(camp,length)
        if key not in wc: wc[key]=weights(camp if length==1 else camp+f'moving{length}',length=length)
        return wc[key]
    edges=[('reference',c,'rayon') for c in ['bevy',M0,R0,R2,MCW,MAB,RAB]]
    edges += [('topology',R0,M0),('topology',RAB,MAB),('backlog',R2,R0)]
    edges += [('spin',a,b) for a,b in [(M40,M0),(M100,M0),(R40,R0),(R100,R0)]]
    edges += [('wait',MCW,M0),('wait',MAB,M0),('wait',RAB,R0)]
    # Core family chosen after descriptive inspection: conservative across all displayed
    # topology, spin, reference anchors and arrival/scaling effects, not post hoc winners.
    boot_family=[]; family_rows=[]
    def add_family(row,boot):
        se=boot.std(ddof=1)
        row['se']=se; row['family_member']=True
        boot_family.append(np.abs(boot-row['absolute_effect'])/max(se,1e-12));family_rows.append(row)
    for s in cache:
        for factor,a,r in edges:
            if s.startswith(('w2_','w4_')) and not (a in [M0,R0,R2,'bevy'] and r=='rayon'): continue
            metrics=[m for m in MEAS if cache[s][1][a][m].notna().all()]
            A=cache[s][1][a][metrics].to_numpy(); R=cache[s][1][r][metrics].to_numpy()
            rr=effect_rows(dict(scenario=s,campaign=cache[s][0]),A,R,w(s),metrics,factor,a,r)
            for row in rr:
                if not s.startswith(('w2_','w4_')) and row['metric'] in ['elapsed_ms','task_clock_ms']:
                    add_family(row,w(s)@(get(s,a,row['metric'])-get(s,r,row['metric'])))
            rows.extend(rr)
    def independent(s1,s0,c,kind,ref=None):
        for m in MEAS:
            x=get(s1,c,m); y=get(s0,c,m)
            if not np.isfinite(x).all() or not np.isfinite(y).all():continue
            if ref:
                x=x-get(s1,ref,m);y=y-get(s0,ref,m)
            X=w(s1)@x;Y=w(s0)@y;z=X-Y
            row=dict(kind=kind,scenario=s1,reference_scenario=s0,configuration=c,reference=ref or c,metric=m,
                     mean_configuration=x.mean(),mean_reference=y.mean(),absolute_effect=x.mean()-y.mean(),
                     ci_low=np.quantile(z,.025),ci_high=np.quantile(z,.975))
            if not ref and np.all(y>0):
                ratio=100*(X/Y-1)
                row.update(ratio_of_means_pct=100*(x.mean()/y.mean()-1),ratio_ci_low=np.quantile(ratio,.025),ratio_ci_high=np.quantile(ratio,.975))
            if kind=='scaling' and m=='elapsed_ms':
                speed=Y/X
                row.update(speedup=y.mean()/x.mean(),speedup_low=np.quantile(speed,.025),speedup_high=np.quantile(speed,.975))
            if kind in ['scaling','arrival'] and s0.startswith('w8_') or kind=='arrival' and s0.startswith('w16_'):
                if m in ['elapsed_ms','task_clock_ms']:add_family(row,z)
            cross.append(row)
    for c in FOCUS+[R2]:
        for hi,lo in [(4,2),(8,4),(16,8)]:independent(f'w{hi}_all',f'w{lo}_all',c,'scaling')
        for n in [8,16]:
            for rate in [900,1500]:independent(f'w{n}_bursty_{rate}',f'w{n}_fixed_{rate}',c,'arrival')
        independent('w16_bursty_1500','w8_bursty_1500',c,'paced_workers')
        if c!='rayon':
            independent('w16_all','w8_all',c,'change_vs_Rayon','rayon')
            independent('w16_bursty_1500','w8_bursty_1500',c,'change_vs_Rayon','rayon')
            for mode in ['all','fixed_1500','bursty_1500','bursty_900']:
                independent(f'w16_{mode}',f'w4_{mode}',c,'change_vs_Rayon','rayon')
                independent(f'w8_{mode}',f'w4_{mode}',c,'change_vs_Rayon','rayon')
    critical=np.quantile(np.max(boot_family,axis=0),.95)
    for row in family_rows:
        row.update(sim_ci_low=row['absolute_effect']-critical*row['se'],sim_ci_high=row['absolute_effect']+critical*row['se'])
    save(rows,'paired_effects');save(cross,'independent_effects')
    # Sensitivity for substantive claims, including paired topology/spin and all SMT anchors.
    targets=[(f'w{n}_all',f'w8_all',c,c,'scaling') for n in [16] for c in FOCUS]
    targets += [(s,s,a,r,'paired') for s,a,r in [
        ('w8_all',R0,'rayon'),('w16_all',R0,'rayon'),('w8_fixed_1500',R0,M0),('w16_fixed_1500',R0,M0),
        ('w8_bursty_1500',R2,'rayon'),('w16_bursty_1500',R2,'rayon'),
        ('w8_fixed_1500',M100,M0),('w16_fixed_1500',M100,M0)]]
    targets += [('w16_bursty_1500','w16_fixed_1500',c,c,'arrival') for c in ['rayon',M0,R0]]
    for s1,s0,a,r,kind in targets:
        for m in ['elapsed_ms','task_clock_ms','cycles_per_wu','instructions_per_wu']:
            x=get(s1,a,m);y=get(s0,r,m);paired=s1==s0;d=x-y
            base=dict(scenario=s1,reference_scenario=s0,configuration=a,reference=r,metric=m,kind=kind)
            for length in [1,3,5]:
                z=w(s1,length)@d if paired else w(s1,length)@x-w(s0,length)@y
                sens.append(dict(**base,method=f'circular{length}' if length>1 else 'iid',effect=x.mean()-y.mean(),ci_low=np.quantile(z,.025),ci_high=np.quantile(z,.975)))
            for label,idx in [('first10',np.arange(10)),('last10',np.arange(10,20)),('omit_early_2_3',np.array([i for i in range(20) if i not in [1,2]]))]:
                W1=weights(s1+label,n=len(idx));W0=W1 if paired else weights(s0+label,n=len(idx))
                z=W1@x[idx]-W0@y[idx]
                sens.append(dict(**base,method=label,effect=x[idx].mean()-y[idx].mean(),ci_low=np.quantile(z,.025),ci_high=np.quantile(z,.975)))
            if paired:
                # Independent SciPy BCa implementation provides a check on skew sensitivity.
                ci=stats.bootstrap((d,),np.mean,n_resamples=B,method='BCa',rng=rng_for(str(base))).confidence_interval
                sens.append(dict(**base,method='BCa',effect=d.mean(),ci_low=ci.low,ci_high=ci.high))
                leave=(d.sum()-d)/19
                sens.append(dict(**base,method='leave_one_out_range',effect=d.mean(),ci_low=leave.min(),ci_high=leave.max()))
    save(sens,'sensitivity')
    # The historical 4-worker bursty campaign had a long interruption in block 3.
    history=[]
    for m in ['elapsed_ms','task_clock_ms']:
        x=get('w16_bursty_1500',R2,m)-get('w16_bursty_1500','rayon',m)
        y=get('w4_bursty_1500',R2,m)-get('w4_bursty_1500','rayon',m)
        keep=np.array([i for i in range(20) if i!=2])
        z=w('w16_bursty_1500')@x-weights('historical_omit_block3',n=19)@y[keep]
        history.append(dict(metric=m,effect=x.mean()-y[keep].mean(),ci_low=np.quantile(z,.025),ci_high=np.quantile(z,.975)))
    save(history,'historical_interruption_sensitivity')
    # Only two scientifically motivated correlations, no broad correlation/model search.
    corr=[]
    for s in ['w8_all','w16_all','w8_fixed_1500','w16_fixed_1500','w8_bursty_1500','w16_bursty_1500']:
        for c in FOCUS:
            x=get(s,c,'context_switches');y=get(s,c,'task_clock_ms')
            rho,p=stats.spearmanr(x,y)
            corr.append(dict(scenario=s,configuration=c,comparison='within_configuration',rho=rho,p=p))
        for a,r in [(R0,M0),(M100,M0)]:
            rho,p=stats.spearmanr(get(s,a,'context_switches')-get(s,r,'context_switches'),get(s,a,'task_clock_ms')-get(s,r,'task_clock_ms'))
            corr.append(dict(scenario=s,configuration=a,comparison='paired_change_vs_'+r,rho=rho,p=p))
    save(corr,'context_cpu_correlations')
    settings=dict(seed=SEED,resamples=B,replication_unit='20 randomized blocks; two physical runs averaged',
        family_size=len(family_rows),simultaneous_critical=float(critical),family='All 8/16-worker paired elapsed/CPU contrasts in the explicit edge list, all five 8-to-16 all-at-once anchors, and 20 fixed-to-bursty anchors; exploratory retrospective family.',
        relative_estimand='Ratios of grand block means; paired absolute differences within campaigns; independent resampling across campaigns')
    (OUT/'statistical_settings.json').write_text(json.dumps(settings,indent=2)+'\n')
    print(settings,flush=True)

if __name__=='__main__':main()
