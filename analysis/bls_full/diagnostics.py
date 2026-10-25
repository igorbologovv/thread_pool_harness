"""Distribution shape, temporal stability and bootstrap sensitivity; no data deletion."""
import itertools
import json
import warnings
import diptest
from scipy import stats
from scipy.signal import find_peaks
from sklearn.mixture import GaussianMixture
from statsmodels.stats.diagnostic import acorr_ljungbox
from statsmodels.stats.multitest import multipletests
from common import *

def shape(x,key):
    x=np.asarray(x,dtype=float);sd=x.std(ddof=1)
    if sd==0:return dict(n=len(x),skew=0,excess_kurtosis=0,dip=0,dip_p=1,bic_1_minus_2=0,fitted_modes=1,gmm_converged=True,tukey_outliers=0)
    z=(x-x.mean())/sd
    dip,p=diptest.diptest(x)
    models=[GaussianMixture(n_components=k,n_init=3,random_state=SEED,reg_covar=1e-5,max_iter=500).fit(z[:,None]) for k in (1,2)]
    grid=np.linspace(z.min()-1,z.max()+1,1500)
    den=np.exp(models[1].score_samples(grid[:,None]))
    q1,q3=np.quantile(x,[.25,.75]);iqr=q3-q1
    return dict(n=len(x),skew=stats.skew(x,bias=False),excess_kurtosis=stats.kurtosis(x,bias=False),dip=dip,dip_p=p,
        bic_1_minus_2=models[0].bic(z[:,None])-models[1].bic(z[:,None]),fitted_modes=len(find_peaks(den)[0]),
        gmm_converged=all(m.converged_ for m in models),component_min_weight=models[1].weights_.min(),
        component_means=';'.join(str(a*sd+x.mean()) for a in sorted(models[1].means_.ravel())),
        tukey_outliers=int(((x<q1-1.5*iqr)|(x>q3+1.5*iqr)).sum()))

def temporal(x,order):
    t=np.arange(len(x));rho,p=stats.spearmanr(t,x)
    lag1=np.corrcoef(x[:-1],x[1:])[0,1]
    lb=acorr_ljungbox(x,lags=[3],return_df=True).lb_pvalue.iloc[0]
    # Partial rank correlation of within-block run order and value, controlling block sequence.
    X=np.column_stack([np.ones(len(x)),stats.rankdata(t)])
    rx=stats.rankdata(x);ro=stats.rankdata(order)
    rx=rx-X@np.linalg.lstsq(X,rx,rcond=None)[0];ro=ro-X@np.linalg.lstsq(X,ro,rcond=None)[0]
    pr=stats.pearsonr(rx,ro).statistic
    pp=2*stats.t.sf(abs(pr)*np.sqrt((len(x)-3)/max(1-pr*pr,1e-15)),len(x)-3)
    return dict(block_spearman=rho,block_p=p,lag1=lag1,ljung_box_lag3_p=lb,order_partial_rank_r=pr,order_approx_p=pp,
        first_half_mean=np.mean(x[:10]),second_half_mean=np.mean(x[10:]),linear_slope_per_block=stats.linregress(t,x).slope)

def diagnose():
    raw=read('raw_runs');block=read('block_means')
    shapes=[];time=[];replicate=[];corr=[];suspicious=[];sensitivity=[];lobo=[]
    diagmetrics=['elapsed_ms','task_clock_ms','drain_delay_ms','schedule_excess_ms']
    for scenario,g in block.groupby('scenario',sort=True):
        print('Diagnostics',scenario,flush=True)
        W=weights(g.campaign.iloc[0]);W3=weights(g.campaign.iloc[0]+'moving3',length=3);W5=weights(g.campaign.iloc[0]+'moving5',length=5)
        ref=g[g.configuration=='rayon'].sort_values('block')
        metrics=[m for m in diagmetrics if g[m].notna().all()]
        # Screening only: fraction of individually standardized configuration values, no pooled performance value.
        scores={b:[] for b in range(1,21)}
        for c,gg in g.groupby('configuration'):
            x=gg.sort_values('block').elapsed_ms.to_numpy();med=np.median(x);scale=1.4826*np.median(np.abs(x-med))
            z=np.abs(x-med)/max(scale,1e-9)
            for b,v in enumerate(z,1):scores[b].append(v>3.5)
        rx=ref.elapsed_ms.to_numpy();rz=np.abs(rx-np.median(rx))/max(1.4826*np.median(np.abs(rx-np.median(rx))),1e-9)
        flags=[]
        for b in range(1,21):
            flag=np.mean(scores[b])>=.2 or rz[b-1]>3.5
            if flag:flags.append(b)
            suspicious.append(dict(scenario=scenario,block=b,fraction_configurations_extreme=np.mean(scores[b]),rayon_robust_z=rz[b-1],flagged=flag))
        for c,gg in g.groupby('configuration'):
            gg=gg.sort_values('block');rr=raw[(raw.scenario==scenario)&(raw.configuration==c)].sort_values(['block','run_index'])
            for m in metrics:
                x=gg[m].to_numpy();r=ref[m].to_numpy();d=x-r
                for level,y in [('raw',rr[m].to_numpy()),('block',x)]+([('paired_difference',d)] if c!='rayon' else []):
                    shapes.append(dict(scenario=scenario,configuration=c,metric=m,level=level,**shape(y,(scenario,c,m,level))))
                for level,y in [('block',x)]+([('paired_difference',d)] if c!='rayon' else []):
                    time.append(dict(scenario=scenario,configuration=c,metric=m,level=level,**temporal(y,gg.order_in_block.to_numpy())))
                reps=rr.pivot(index='block',columns='run_index',values=m)
                diff=(reps[1]-reps[0]).to_numpy();lo,hi=np.quantile(W@diff,[.025,.975])
                replicate.append(dict(scenario=scenario,configuration=c,metric=m,run1_minus_run0=diff.mean(),ci_low=lo,ci_high=hi))
                if c=='rayon':continue
                base=dict(scenario=scenario,configuration=c,reference='rayon',metric=m)
                loomeans=(d.sum()-d)/19
                lobo.append(dict(**base,full_effect=d.mean(),minimum=loomeans.min(),maximum=loomeans.max(),largest_influence_block=int(np.argmax(np.abs(loomeans-d.mean()))+1),sign_changes=int(np.sum(np.sign(loomeans)!=np.sign(d.mean())))))
                for method,w in [('iid',W),('circular_block3',W3),('circular_block5',W5)]:
                    lo,hi=np.quantile(w@d,[.025,.975]);sensitivity.append(dict(**base,method=method,n_blocks=20,effect=d.mean(),ci_low=lo,ci_high=hi,removed_blocks=''))
                boot=W@d;prop=np.clip((np.sum(boot<d.mean())+.5*np.sum(boot==d.mean()))/B,1/B,1-1/B)
                z0=stats.norm.ppf(prop);jack=loomeans;delta=jack.mean()-jack
                acc=np.sum(delta**3)/(6*np.sum(delta**2)**1.5) if np.sum(delta**2)>0 else 0
                zz=stats.norm.ppf([.025,.975]);probs=stats.norm.cdf(z0+(z0+zz)/(1-acc*(z0+zz)))
                lo,hi=np.quantile(boot,np.clip(probs,0,1));sensitivity.append(dict(**base,method='BCa',n_blocks=20,effect=d.mean(),ci_low=lo,ci_high=hi,removed_blocks=''))
                subsets={'first10':list(range(10)),'last10':list(range(10,20)),
                         'omit_max_influence':[i for i in range(20) if i!=np.argmax(np.abs(loomeans-d.mean()))]}
                if flags and len(flags)<16:subsets['omit_screened_blocks']=[i for i in range(20) if i+1 not in flags]
                for method,idx in subsets.items():
                    dd=d[idx];w=weights(f'{scenario}_{method}_{len(idx)}',n=len(idx));lo,hi=np.quantile(w@dd,[.025,.975])
                    sensitivity.append(dict(**base,method=method,n_blocks=len(idx),effect=dd.mean(),ci_low=lo,ci_high=hi,removed_blocks=';'.join(str(i+1) for i in range(20) if i not in idx)))
            for a,b in itertools.combinations(['elapsed_ms','task_clock_ms','cycles','instructions','ipc','branch_misses','context_switches','effective_ghz'],2):
                rho,p=stats.spearmanr(gg[a],gg[b]);corr.append(dict(scenario=scenario,configuration=c,metric_a=a,metric_b=b,spearman=rho,p=p,n_blocks=20))
    sh=pd.DataFrame(shapes);tm=pd.DataFrame(time);co=pd.DataFrame(corr)
    for level,g in sh.groupby('level'):sh.loc[g.index,'dip_p_holm']=multipletests(g.dip_p,method='holm')[1]
    for field in ['block_p','ljung_box_lag3_p','order_approx_p']:
        for level,g in tm.groupby('level'):tm.loc[g.index,field+'_holm']=multipletests(g[field],method='holm')[1]
    co['p_bh']=multipletests(co.p,method='fdr_bh')[1]
    save(sh,'distribution_diagnostics');save(tm,'temporal_diagnostics');save(replicate,'replicate_order_effects');save(co,'within_configuration_correlations');save(suspicious,'suspicious_blocks');save(sensitivity,'bootstrap_sensitivity');save(lobo,'leave_one_block_out')
    print('Diagnostics saved',len(sh),'shape assessments',flush=True)

if __name__=='__main__':diagnose()
