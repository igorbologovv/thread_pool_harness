"""Distribution and acquisition-order review BEFORE focused effect estimation."""
from data import *
from diagnostics import shape, temporal
from scipy import stats

def main():
    raw=read('raw_runs'); blocks=read('block_means')
    sh=[]; tm=[]; reps=[]; summary=[]
    for (s,c),g in blocks.groupby(['scenario','configuration']):
        g=g.sort_values('block'); rr=raw[(raw.scenario==s)&(raw.configuration==c)].sort_values(['block','run_index'])
        for m in ['elapsed_ms','task_clock_ms','cycles_per_wu','instructions_per_wu','ipc','effective_ghz','context_switches','cpu_migrations','branch_miss_pct','schedule_excess_ms','drain_delay_ms','last_lateness_ms','cpu_equivalents']:
            if g[m].isna().all(): continue
            summary.append(dict(scenario=s,configuration=c,metric=m,mean=g[m].mean(),sd=g[m].std(),minimum=g[m].min(),maximum=g[m].max(),raw_min=rr[m].min(),raw_max=rr[m].max()))
        if g.workers.iloc[0]<8: continue
        if c not in FOCUS+[MCW,MAB,RAB,R2,M100,R100]: continue
        ref=blocks[(blocks.scenario==s)&(blocks.configuration=='rayon')].sort_values('block')
        for m in ['elapsed_ms','task_clock_ms']:
            x=g[m].to_numpy(); d=x-ref[m].to_numpy()
            for level,y in [('block',x)]+([('paired_difference',d)] if c!='rayon' else []):
                tm.append(dict(scenario=s,configuration=c,metric=m,level=level,**temporal(y,g.order_in_block.to_numpy())))
            # Mixtures are restricted to the four architecture anchors, all-at-once and bursty 1500.
            if c in FOCUS and (s.endswith('_all') or s.endswith('_bursty_1500')):
                for level,y in [('raw',rr[m].to_numpy()),('block',x)]+([('paired_difference',d)] if c!='rayon' else []):
                    sh.append(dict(scenario=s,configuration=c,metric=m,level=level,**shape(y,(s,c,m,level))))
            re=rr.pivot(index='block',columns='run_index',values=m)
            reps.append(dict(scenario=s,configuration=c,metric=m,replicate1_minus0=(re[1]-re[0]).mean(),correlation=re[0].corr(re[1])))
    save(summary,'summary');save(sh,'shapes');save(tm,'temporal');save(reps,'replicates')
    print('Distribution/temporal diagnostics complete. No observations removed.',flush=True)
    print(pd.DataFrame(summary).query("scenario in ['w8_all','w16_all'] and metric in ['elapsed_ms','task_clock_ms']").to_string(index=False))

if __name__=='__main__': main()
