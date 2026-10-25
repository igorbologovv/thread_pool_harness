"""Targeted diagnostics of raw modes and timestamp semantics. No hypothesis confirmation."""
import diptest
from common import *
from diagnostics import shape

def main():
    raw=read('raw_runs');sh=read('distribution_diagnostics');rep=read('replicate_order_effects')
    selected=sh[(sh.level=='raw')&(sh.metric=='elapsed_ms')].nsmallest(5,'dip_p')
    rows=[]
    for i,rr in enumerate(selected.itertuples()):
        r=raw[(raw.scenario==rr.scenario)&(raw.configuration==rr.configuration)].sort_values(['block','run_index'])
        for run in [-1,0,1]:
            x=r.elapsed_ms.to_numpy() if run==-1 else r[r.run_index==run].elapsed_ms.to_numpy()
            dip,p=diptest.diptest(x,boot_pval=True,n_boot=100000,seed=SEED+i*3+run+1,n_threads=1)
            p=(p*100000+1)/100001 # Avoid interpreting zero Monte Carlo hits as zero probability.
            row=dict(scenario=rr.scenario,configuration=rr.configuration,run_index=run,n=len(x),dip=dip,uniform_MC_p=p,
                simulations=100000,mean=x.mean(),sd=x.std(ddof=1),**{k:v for k,v in shape(x,'followup').items() if k not in ['n','dip','dip_p']})
            rows.append(row)
    save(rows,'modality_followup')
    # Per-configuration, not pooled across different settings. Relationships involving ratios are algebraically coupled.
    rr=[]
    for (s,c),r in raw.groupby(['scenario','configuration']):
        for metric in ['elapsed_ms','task_clock_ms']:
            x=r.pivot(index='block',columns='run_index',values=metric)
            rr.append(dict(scenario=s,configuration=c,metric=metric,replicate_correlation=x[0].corr(x[1]),run0_mean=x[0].mean(),run1_mean=x[1].mean()))
    save(rr,'replicate_correlations')
    print('Follow-up modality checks complete',flush=True)

if __name__=='__main__':main()
