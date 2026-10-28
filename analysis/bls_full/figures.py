"""Vector scientific figures, with a machine-readable caption and source catalogue."""
import json
import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/bls-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from scipy import stats
from sklearn.mixture import GaussianMixture
from common import *

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'axes.titlesize':10,'axes.labelsize':9,
    'xtick.labelsize':9,'ytick.labelsize':9,'legend.fontsize':8,'pdf.fonttype':42,'svg.fonttype':'none',
    'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.17,'savefig.bbox':'tight'})
COLORS={'MPMC':'#0072B2','RTRB':'#D55E00','rayon':'#333333','bevy':'#009E73'}
CATALOG=[]
SCENARIOS=['w2_all','w4_all','w2_fixed_900','w4_fixed_900','w2_fixed_1500','w4_fixed_1500','w2_bursty_900','w4_bursty_900','w2_bursty_1500','w4_bursty_1500']

def finish(fig,name,caption,source):
    fig.savefig(FIGURES/(name+'.pdf'));fig.savefig(FIGURES/(name+'.svg'))
    plt.close(fig);CATALOG.append(dict(name=name,caption=caption,source=source))

def dotci(ax,df,col='relative_pct',lo='relative_ci_low',hi='relative_ci_high',labels=True):
    for j,(_,r) in enumerate(df.iterrows()):
        color=COLORS['RTRB' if r.configuration.startswith('threadance-hot-first-rtrb') else ('MPMC' if r.configuration.startswith('threadance') else r.configuration)]
        ax.plot([r[lo],r[hi]],[j,j],color=color,lw=1.5)
        ax.plot(r[col],j,'o',ms=4,color=color)
    ax.set_yticks(range(len(df)),[short(c) for c in df.configuration] if labels else [])
    ax.invert_yaxis();ax.axvline(0,color='#666',lw=.8);ax.margins(y=.04)

def main():
    ef=read('baseline_effects');fa=read('factor_effects');de=read('descriptive_statistics');bl=read('block_means');raw=read('raw_runs')
    cfg=read('configuration_dictionary').drop_duplicates('configuration').set_index('configuration')
    names=cfg.index.tolist();tn=[c for c in names if c.startswith('threadance')]
    for scenario in SCENARIOS:
        for m,tag,label in [('elapsed_ms','elapsed','Elapsed time'),('task_clock_ms','cpu','CPU task-clock')]:
            fig,axs=plt.subplots(1,2,figsize=(10,5.4),gridspec_kw={'width_ratios':[1,1]},layout='constrained')
            for ax,topology in zip(axs,['MPMC','RTRB']):
                ns=[c for c in tn if cfg.loc[c,'topology']==topology]
                z=ef[(ef.scenario==scenario)&(ef.reference=='rayon')&(ef.metric==m)].set_index('configuration').loc[ns].reset_index()
                dotci(ax,z);ax.set_title(topology);ax.set_xlabel(f'{label}: mean paired change (%)')
            fig.suptitle(pretty(scenario)+' — comparison with Rayon',fontsize=12)
            finish(fig,f'forest_{tag}_{scenario}',f'{label} versus Rayon for every individual Threadance configuration in {pretty(scenario)}. Dots are means of the 20 block percentage differences; lines are pointwise 95% paired percentile intervals (20,000 resamples). Negative values favor Threadance for this metric. These intervals are not simultaneous; absolute simultaneous intervals and Holm-adjusted sign tests are in baseline_effects.csv. No configuration averaging is used.','baseline_effects.csv')
    # A baseline overview, not a pool-level aggregate of Threadance.
    fig,axs=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    for ax,m,label in zip(axs,['elapsed_ms','task_clock_ms'],['Elapsed time (ms)','CPU task-clock (ms)']):
        for off,c in [(-.12,'rayon'),(.12,'bevy')]:
            z=de[(de.configuration==c)&(de.metric==m)&(de.level=='block')].set_index('scenario').loc[SCENARIOS]
            xx=np.arange(10)+off
            ax.errorbar(xx,z['mean'],yerr=[z['mean']-z.mean_ci_low,z.mean_ci_high-z['mean']],fmt='o',color=COLORS[c],label=c.title(),capsize=2,ms=4)
        ax.set_xticks(range(10),[s.replace('w','').replace('_','\n') for s in SCENARIOS]);ax.set_ylabel(label);ax.legend()
    finish(fig,'baseline_overview','Rayon and Bevy block means with pointwise bootstrap 95% intervals. Paced elapsed time includes the fixed delivery window; small elapsed differences may conceal substantial CPU differences. Separate campaigns are not paired.','descriptive_statistics.csv')
    for factor,topology in [('wait','MPMC'),('wait','RTRB'),('topology',None),('spin',None),('capacity',None),('backlog',None)]:
        z=fa[fa.factor==factor]
        if topology:z=z[z.configuration.map(lambda x:cfg.loc[x,'topology'])==topology]
        pairs=list(dict.fromkeys(zip(z.configuration,z.reference)))
        fig,axs=plt.subplots(1,2,figsize=(11,max(4.3,.25*len(pairs)+1.4)),layout='constrained')
        def contrast_label(a,r):
            x,y=cfg.loc[a],cfg.loc[r]
            top='M' if x.topology=='MPMC' else 'R'
            policy={'crossbeam-wait':'CW','agave-backoff':'AB','spin-then-park':'SP'}[x.policy]
            polref={'crossbeam-wait':'CW','agave-backoff':'AB','spin-then-park':'SP'}[y.policy]
            sp=f' s{x.spin}' if x.policy=='spin-then-park' else ''
            back=f' b{x.backlog}' if x.topology=='RTRB' else ''
            if factor=='spin':return f'{top} q{x.capacity}{back}: {x.spin} − {y.spin} µs'
            if factor=='wait':return f'{top} q{x.capacity}{back}: {policy}{sp} − {polref}'
            if factor=='topology':return f'{policy}{sp} q{x.capacity}: R b{x.backlog} − M'
            if factor=='capacity':return f'{top} {policy}{sp}{back}: q64 − q8'
            return f'{policy}{sp} q{x.capacity}: b2 − b1'
        for ax,m in zip(axs,['elapsed_ms','task_clock_ms']):
            zz=z[z.metric==m];v=np.array([[zz[(zz.scenario==s)&(zz.configuration==a)&(zz.reference==r)].relative_pct.iloc[0] for s in SCENARIOS] for a,r in pairs])
            lim=max(np.quantile(abs(v),.95),.1);norm=TwoSlopeNorm(vmin=-lim,vcenter=0,vmax=lim)
            im=ax.imshow(v,cmap='RdBu_r',norm=norm,aspect='auto')
            for i,(a,r) in enumerate(pairs):
                for j,s in enumerate(SCENARIOS):
                    row=zz[(zz.scenario==s)&(zz.configuration==a)&(zz.reference==r)].iloc[0]
                    mark='*' if row.sim_ci_low>0 or row.sim_ci_high<0 else ''
                    ax.text(j,i,f'{v[i,j]:.1f}{mark}',ha='center',va='center',fontsize=9,color='white' if abs(v[i,j])>.65*lim else '#222')
            ax.set_xticks(range(10),[s.replace('w','').replace('_','\n') for s in SCENARIOS],fontsize=8.2)
            ax.set_yticks(range(len(pairs)),[contrast_label(a,r) for a,r in pairs] if ax==axs[0] else [],fontsize=9)
            ax.set_title('Elapsed time (%)' if m=='elapsed_ms' else 'CPU task-clock (%)');ax.grid(False)
            fig.colorbar(im,ax=ax,shrink=.6,label='Mean paired change (%)',extend='both')
        tag=factor+('_'+topology.lower() if topology else '')
        fig.suptitle(f'Matched {factor} contrasts'+(' within '+topology if topology else ''),fontsize=12)
        finish(fig,'factor_'+tag,'Each cell compares exactly the row configurations at the column workload; no main effect is averaged across settings. Values are mean paired percentage changes. A star marks an absolute interval excluding zero after the bootstrap simultaneous correction across both primary metrics and all contrasts/workloads within this factor family. Color limits clip the most extreme cells, whose printed values remain exact to one decimal. M/R denote MPMC/RTRB; CW/AB/SP denote policies. RTRB q8/q64 are validation labels, not different allocated capacities; topology contrasts also change routing and wake addressing.','factor_effects.csv')
    # Full hardware-effects overview, faceted by metric (every configuration retained).
    for m,title in [('cycles_per_wu','Cycles per work unit'),('ipc','Instructions per cycle'),('branch_miss_pct','Branch miss percentage'),('context_switches_per_wu','Context switches per work unit')]:
        z=ef[(ef.metric==m)&(ef.reference=='rayon')]
        v=np.array([[z[(z.scenario==s)&(z.configuration==c)].relative_pct.iloc[0] for s in SCENARIOS] for c in tn])
        fig,ax=plt.subplots(figsize=(8,8.4),layout='constrained');lim=max(np.nanquantile(abs(v),.95),1)
        im=ax.imshow(v,cmap='RdBu_r',norm=TwoSlopeNorm(vmin=-lim,vcenter=0,vmax=lim),aspect='auto')
        for i,c in enumerate(tn):
            for j,s in enumerate(SCENARIOS):ax.text(j,i,f'{v[i,j]:.0f}',ha='center',va='center',fontsize=8.5,color='white' if abs(v[i,j])>.65*lim else '#222')
        ax.set_xticks(range(10),[s.replace('w','').replace('_','\n') for s in SCENARIOS]);ax.set_yticks(range(len(tn)),[short(c) for c in tn]);ax.set_title(title+' — relative to Rayon (%)');ax.grid(False)
        fig.colorbar(im,ax=ax,label='Mean paired change (%)',extend='both')
        finish(fig,'hardware_'+m,f'{title}, configuration by workload. Values are mean paired percentage changes versus Rayon; uncertainty is in baseline_effects.csv. IPC is a ratio, not a direct throughput measure. Counter associations do not establish a causal mechanism.','baseline_effects.csv')
    # Trade-offs and Pareto frontiers: indices prevent overlapping long labels.
    pa=read('pareto')
    for scenario in SCENARIOS:
        mx='elapsed_ms' if scenario.endswith('all') else 'schedule_excess_ms'
        fig,axs=plt.subplots(1,2,figsize=(10,4.5),layout='constrained')
        for ax,xmetric in zip(axs,[mx,'elapsed_ms' if mx=='elapsed_ms' else 'drain_delay_ms']):
            ymetric='task_clock_ms' if ax==axs[0] or mx!='elapsed_ms' else 'context_switches'
            z=pa[(pa.scenario==scenario)&(pa.objective_x==xmetric)&(pa.objective_y==ymetric)]
            for i,c in enumerate(names):
                row=z[z.configuration==c].iloc[0];top=cfg.loc[c,'topology']
                dx=de[(de.scenario==scenario)&(de.configuration==c)&(de.metric==xmetric)&(de.level=='block')].iloc[0]
                dy=de[(de.scenario==scenario)&(de.configuration==c)&(de.metric==ymetric)&(de.level=='block')].iloc[0]
                ax.errorbar(row.x,row.y,xerr=[[max(0,row.x-dx.mean_ci_low)],[max(0,dx.mean_ci_high-row.x)]],yerr=[[max(0,row.y-dy.mean_ci_low)],[max(0,dy.mean_ci_high-row.y)]],fmt='o',ms=4,color=COLORS[top],alpha=.65)
                if row.point_frontier:ax.scatter(row.x,row.y,s=65,facecolors='none',edgecolors='black',linewidth=.8)
            labelled=z[z.point_frontier|z.configuration.isin(['rayon','bevy'])].sort_values('y')
            for yy,row in zip(np.linspace(.08,.92,len(labelled)),labelled.itertuples()):
                ax.annotate(str(names.index(row.configuration)+1),(row.x,row.y),xytext=(1.015,yy),textcoords='axes fraction',fontsize=8,
                    arrowprops={'arrowstyle':'-','lw':.5,'color':'#777'},ha='left',va='center')
            frontier=z[z.point_frontier].sort_values('x');ax.plot(frontier.x,frontier.y,'k--',alpha=.4,lw=.7)
            ax.set_xlabel({'elapsed_ms':'Elapsed time (ms)','schedule_excess_ms':'Completion beyond planned window (ms)','drain_delay_ms':'Pre-submit drain proxy (ms)'}[xmetric]);ax.set_ylabel('CPU task-clock (ms)' if ymetric=='task_clock_ms' else 'Context switches / run')
        fig.suptitle(pretty(scenario)+' — measured trade-offs',fontsize=12)
        finish(fig,'pareto_'+scenario,'Dots show every configuration, with marginal pointwise 95% intervals (blue MPMC, orange RTRB, grey Rayon, green Bevy). Rings and dashed lines identify the sample-mean frontier, not certain superiority. Numbered labels identify frontier configurations and references using the configuration dictionary. pareto.csv reports bootstrap frontier frequencies and dominance supported by simultaneous pairwise intervals within this workload/objective family. Frequencies are resampling stability, not posterior probabilities. For paced runs, drain starts at the last pre-submission timestamp; completion beyond the planned window retains accumulated lateness.','descriptive_statistics.csv; pareto.csv; pareto_pairwise_intervals.csv')
    cr=read('cross_campaign_effects')
    for typ in ['workers','arrival_pattern','arrival_rate']:
        z=cr[cr.comparison==typ];pairs=list(dict.fromkeys(zip(z.scenario_a,z.scenario_b)))
        fig,axs=plt.subplots(1,2,figsize=(10,8),layout='constrained')
        for ax,m in zip(axs,['elapsed_ms','task_clock_ms']):
            zz=z[z.metric==m];v=np.array([[zz[(zz.scenario_a==a)&(zz.scenario_b==b)&(zz.configuration==c)].ratio_of_means_pct.iloc[0] for a,b in pairs] for c in names])
            lim=max(np.nanmax(abs(v)),1);im=ax.imshow(v,cmap='RdBu_r',norm=TwoSlopeNorm(vmin=-lim,vcenter=0,vmax=lim),aspect='auto')
            for i in range(len(names)):
                for j in range(len(pairs)):ax.text(j,i,f'{v[i,j]:.1f}',ha='center',va='center',fontsize=7,color='white' if abs(v[i,j])>.65*lim else '#222')
            ax.set_yticks(range(len(names)),[short(c) for c in names] if ax==axs[0] else [])
            ax.set_xticks(range(len(pairs)),[a+'\nvs\n'+b for a,b in pairs],fontsize=6.5,rotation=30,ha='right');ax.set_title(m.replace('_',' '));ax.grid(False)
            fig.colorbar(im,ax=ax,shrink=.5,label='Ratio of means change (%)')
        finish(fig,'cross_'+typ,'Each configuration is compared with itself across the indicated conditions. Values are percentage changes in grand block means. Separate campaigns use independent block resampling; arrival rates within one campaign preserve block pairing. Cross-campaign contrasts are observational with respect to collection time and cannot isolate worker or delivery effects from host drift. Full 95% intervals are in cross_campaign_effects.csv.','cross_campaign_effects.csv')
    # Baseline time series, preserving the two physical runs and block means.
    fig,axs=plt.subplots(5,2,figsize=(10,11),layout='constrained')
    for ax,s in zip(axs.flat,SCENARIOS):
        for c in ['rayon','bevy','threadance__crossbeam-wait__q8','threadance-hot-first-rtrb__spin-then-park__q8__b1__s0']:
            r=raw[(raw.scenario==s)&(raw.configuration==c)];g=bl[(bl.scenario==s)&(bl.configuration==c)].sort_values('block')
            color=COLORS[cfg.loc[c,'topology']]
            center=g.elapsed_ms.mean()
            ax.scatter(r.block+(r.run_index-.5)*.12,100*(r.elapsed_ms/center-1),s=7,alpha=.35,color=color)
            ax.plot(g.block,100*(g.elapsed_ms/center-1),'-o',ms=2,color=color,label=short(c))
        ax.set_title(pretty(s));ax.set_xlabel('Randomized block');ax.set_ylabel('Elapsed deviation (%)');ax.set_xticks([1,5,10,15,20])
    axs[0,0].legend(fontsize=6,ncol=2)
    finish(fig,'block_stability','Elapsed time centered on each configuration\'s own mean, for two references and two fixed illustrative Threadance settings. Small dots are physical runs and connected dots are block means. Centering is a diagnostic transformation, not aggregation across configurations. Different panels belong to independent campaigns. Drift, isolated excursions and disagreement between replicates can be inspected without deleting observations.','raw_runs.csv; block_means.csv')
    sh=read('distribution_diagnostics');td=read('temporal_diagnostics');sn=read('bootstrap_sensitivity')
    chosen=sh[(sh.level=='raw')&(sh.metric=='elapsed_ms')].sort_values('bic_1_minus_2',ascending=False).drop_duplicates('scenario').head(3)
    chosen2=sh[(sh.level=='paired_difference')&(sh.metric=='elapsed_ms')].sort_values('dip_p').drop_duplicates('scenario').head(2)
    choices=list(dict.fromkeys(zip(pd.concat([chosen,chosen2]).scenario,pd.concat([chosen,chosen2]).configuration)))
    for number,(s,c) in enumerate(choices,1):
        r=raw[(raw.scenario==s)&(raw.configuration==c)].sort_values(['block','run_index']);g=bl[(bl.scenario==s)&(bl.configuration==c)].sort_values('block');ref=bl[(bl.scenario==s)&(bl.configuration=='rayon')].sort_values('block')
        fig,axs=plt.subplots(2,3,figsize=(10,5.5),layout='constrained')
        for j,(level,x) in enumerate([('raw',r.elapsed_ms.to_numpy()),('block',g.elapsed_ms.to_numpy()),('paired_difference',(g.elapsed_ms.to_numpy()-ref.elapsed_ms.to_numpy()))]):
            ax=axs[0,j];ax.hist(x,bins='fd',density=True,color='#0072B2',alpha=.45);ax.plot(x,np.zeros(len(x)),'|',color='#333')
            if np.std(x)>0:
                sd=x.std(ddof=1);mean=x.mean();z=(x-mean)/sd;grid=np.linspace(x.min()-sd*.5,x.max()+sd*.5,500)
                for k,style in [(1,'--'),(2,'-')]:
                    model=GaussianMixture(n_components=k,n_init=3,random_state=SEED,reg_covar=1e-5).fit(z[:,None]);ax.plot(grid,np.exp(model.score_samples(((grid-mean)/sd)[:,None]))/sd,style,lw=1,label=f'{k} Gaussian(s)')
            row=sh[(sh.scenario==s)&(sh.configuration==c)&(sh.metric=='elapsed_ms')&(sh.level==level)]
            if len(row):ax.set_title(f'{level}: dip p={row.iloc[0].dip_p:.3g}\nΔBIC={row.iloc[0].bic_1_minus_2:.1f}')
            ax.set_xlabel('Elapsed (ms)' if j<2 else 'Difference vs Rayon (ms)');ax.set_ylabel('Density')
            ax=axs[1,j];xx=np.sort(x);ax.step(xx,np.arange(1,len(x)+1)/len(x),where='post',color='#0072B2');ax.set_ylabel('Empirical CDF');ax.set_xlabel('ms')
        axs[0,0].legend(fontsize=6);fig.suptitle(pretty(s)+' — '+short(c),fontsize=11)
        finish(fig,f'distribution_case_{number}','Diagnostic case selected by a large two-component BIC advantage or small paired-difference dip p-value, so selection itself is exploratory. Raw runs, block means and paired differences are shown separately. Gaussian overlays and ECDFs show whether a second component models a tail, outlier or distinct mode. Mixture selection alone does not establish bimodality; raw-run dip calibration ignores replicate dependence and is descriptive. Neither a nonsignificant dip test nor 20 blocks excludes multimodality.','distribution_diagnostics.csv; raw_runs.csv; block_means.csv')
    # All configuration diagnostics without unreadable labels.
    fig,axs=plt.subplots(1,3,figsize=(10,3.4),layout='constrained')
    for ax,level in zip(axs,['raw','block','paired_difference']):
        z=sh[(sh.level==level)&(sh.metric=='elapsed_ms')]
        ax.scatter(z.bic_1_minus_2,-np.log10(np.maximum(z.dip_p,1e-12)),s=12,alpha=.55,color='#0072B2');ax.axvline(10,color='#666',ls='--',lw=.8);ax.axhline(-np.log10(.05),color='#666',ls=':',lw=.8);ax.set_title(level.replace('_',' '));ax.set_xlabel('BIC(1 component) − BIC(2)');ax.set_ylabel('−log10(dip p)')
    finish(fig,'modality_overview','Every configuration/workload elapsed distribution, with separate raw, block and paired-difference panels. Dashed guides show ΔBIC=10 and unadjusted p=.05; they are screening conventions, not confirmatory cutoffs. Large BIC preference with nonsignificant dip evidence can reflect skew or small isolated components. Holm-adjusted diagnostics are tabulated.','distribution_diagnostics.csv')
    fig,axs=plt.subplots(1,3,figsize=(10,3.5),layout='constrained')
    z=td[(td.level=='paired_difference')&(td.metric=='elapsed_ms')]
    for ax,col,label in zip(axs,['block_spearman','lag1','order_partial_rank_r'],['Block-order Spearman ρ','Lag-1 correlation','Run-order partial rank r']):ax.hist(z[col],bins=18,color='#0072B2',alpha=.75);ax.set_xlabel(label);ax.set_ylabel('Configuration/workload series');ax.axvline(0,color='#333',lw=.8)
    finish(fig,'temporal_diagnostic_overview','Diagnostics of elapsed differences relative to Rayon across all individual configurations and workloads. These distributions summarize diagnostic statistics, not combined performance. Twenty blocks give limited power; multiplicity-adjusted p-values and the moving-block sensitivity analysis accompany these screens. Partial run-order association controls linear rank trend across blocks.','temporal_diagnostics.csv')
    # Sensitivity cases selected by maximal leave-one-block-out influence.
    loo=read('leave_one_block_out');loo=loo[loo.metric=='elapsed_ms'].copy();loo['range']=loo.maximum-loo.minimum
    pick=loo.sort_values('range',ascending=False).drop_duplicates('scenario').head(4)
    fig,axs=plt.subplots(2,2,figsize=(10,6),layout='constrained')
    for ax,row in zip(axs.flat,pick.itertuples()):
        z=sn[(sn.scenario==row.scenario)&(sn.configuration==row.configuration)&(sn.metric=='elapsed_ms')]
        for i,r in enumerate(z.itertuples()):ax.plot([r.ci_low,r.ci_high],[i,i],color='#0072B2');ax.plot(r.effect,i,'o',color='#0072B2',ms=4)
        ax.set_yticks(range(len(z)),z.method,fontsize=7);ax.invert_yaxis();ax.axvline(0,color='#666',lw=.7);ax.set_xlabel('Elapsed difference vs Rayon (ms)');ax.set_title(row.scenario+' · '+short(row.configuration),fontsize=8)
    finish(fig,'sensitivity_intervals','Four workload cases with large leave-one-block-out influence. Every interval uses 20,000 resamples. Circular block lengths 3 and 5 retain short-range dependence; BCa adjusts bias/skew. First/last-half and omission analyses change the estimand and are descriptive sensitivity checks, not replacements for the complete-data result. Screened or influential blocks remain in all main analyses.','bootstrap_sensitivity.csv; leave_one_block_out.csv; suspicious_blocks.csv')
    save([dict(number=i+1,configuration=c,label=short(c)) for i,c in enumerate(names)],'figure_configuration_key')
    (OUT/'figure_catalog.json').write_text(json.dumps(CATALOG,indent=2)+'\n')
    print('Generated',len(CATALOG),'vector figure pairs',flush=True)

if __name__=='__main__':main()
