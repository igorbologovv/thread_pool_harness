"""Seven selected vector figures; all numbers derive from saved block analyses."""
from data import *
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

COL={'rayon':'#3567A6','bevy':'#8862AA',M0:'#168B87',R0:'#D17A30',R2:'#A84F16'}
LAB={'rayon':'Rayon','bevy':'Bevy',M0:'MPMC SP0',R0:'RTRB SP0 b1',R2:'RTRB SP0 b2'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8.5,'axes.titlesize':10,'axes.labelsize':8.5,
    'legend.fontsize':7.5,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.17,
    'pdf.fonttype':42,'svg.fonttype':'none','figure.facecolor':'white','axes.titlelocation':'left'})

def finish(fig,name):
    fig.savefig(FIGURES/(name+'.pdf'),bbox_inches='tight',pad_inches=.07)
    fig.savefig(FIGURES/(name+'.svg'),bbox_inches='tight',pad_inches=.07)
    plt.close(fig)

def main():
    raw=read('raw_runs');block=read('block_means');effects=read('paired_effects');cross=read('independent_effects')
    def values(s,c,m):return block[(block.scenario==s)&(block.configuration==c)].sort_values('block')[m].to_numpy()
    def effect(s,a,r,m):return effects[(effects.scenario==s)&(effects.configuration==a)&(effects.reference==r)&(effects.metric==m)].iloc[0]
    def ebar(ax,x,y,lo,hi,**kw):ax.errorbar(x,y,yerr=[[y-lo],[hi-y]],capsize=2,**kw)
    def hbar(ax,x,y,lo,hi,**kw):ax.errorbar(x,y,xerr=[[x-lo],[hi-x]],capsize=2,**kw)
    # 1. Source facts, including the shared slow path in segmented queues.
    fig,ax=plt.subplots(figsize=(7,4.5));ax.set_xlim(0,10);ax.set_ylim(0,4.6);ax.axis('off')
    def box(x,y,w,h,text,color):
        ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.06',facecolor=color,edgecolor='none',alpha=.12))
        ax.text(x+w/2,y+h/2,text,ha='center',va='center',fontsize=8.5,color='#203347')
    for y,name,color,queue,worker,note in [
        (3.5,'Rayon',COL['rayon'],'Shared injector\nexternal spawn','Worker: local → peer\ndeques → injector','Peer-deque probes are possible; successful steal counts are unknown.'),
        (2.4,'Bevy',COL['bevy'],'Shared async queue\ndetached Runnable','Runner local queue\nglobal refill → peers','Synchronous BLS finishes in one poll; no cooperative yield inside it.'),
        (1.3,'MPMC',COL[M0],'Bounded shared q64\nCrossbeam channel','Any worker consumes\nshared wake / select','Head/tail padded; consumers still contend on the shared head.'),
        (.2,'RTRB',COL[R0],'Low-index gate scan\nSPSC ring per worker','Only owner consumes\naddressed park/unpark','Padded ring cursors; every pop still increments one shared epoch.')]:
        ax.text(0,y+.47,name,color=color,weight='bold',fontsize=11)
        box(1.4,y+.22,3.1,.62,queue,color);box(5.1,y+.22,4.6,.62,worker,color)
        ax.annotate('',xy=(5.05,y+.53),xytext=(4.55,y+.53),arrowprops=dict(arrowstyle='->',color=color,lw=1.6))
        ax.text(1.4,y-.04,note,fontsize=8)
    finish(fig,'01_architecture')
    # 2. Raw, block and paired distributions; explicit temporal diagnostics.
    fig,axs=plt.subplots(2,3,figsize=(7.1,4.8),layout='constrained')
    for level,ax in [('raw',axs[0,0]),('block',axs[0,1])]:
        for c in FOCUS:
            data=raw if level=='raw' else block
            x=np.sort(data[(data.scenario=='w8_all')&(data.configuration==c)].elapsed_ms)
            ax.step(x,np.arange(1,len(x)+1)/len(x),where='post',color=COL[c],label=LAB[c])
        ax.set(title=('A  Raw runs (n=40)' if level=='raw' else 'B  Block means (n=20)'),xlabel='8-worker elapsed (ms)',ylabel='Empirical CDF')
    for n,color in [(8,COL['rayon']),(16,COL[R0])]:
        d=np.sort(values(f'w{n}_all',R0,'elapsed_ms')-values(f'w{n}_all','rayon','elapsed_ms'))
        axs[0,2].step(d,np.arange(1,21)/20,where='post',color=color,label=f'{n} workers')
    axs[0,2].axvline(0,color='grey',lw=.7);axs[0,2].set(title='C  Paired differences',xlabel='RTRB b1 − Rayon (ms)',ylabel='Empirical CDF');axs[0,2].legend()
    for n,ax in [(8,axs[1,0]),(16,axs[1,1])]:
        for c in FOCUS:
            rr=raw[(raw.scenario==f'w{n}_all')&(raw.configuration==c)]
            xx=rr.block+(rr.order_in_block-8)/15*.7+(rr.run_index-.5)*.03
            ax.scatter(xx,rr.elapsed_ms,s=5,alpha=.25,color=COL[c])
            ax.plot(np.arange(1,21),values(f'w{n}_all',c,'elapsed_ms'),color=COL[c],lw=1)
        ax.set(title=f'{"D" if n==8 else "E"}  {n}-worker acquisition',xlabel='Randomized block / order',ylabel='Elapsed (ms)',xticks=[1,5,10,15,20])
    d=values('w16_bursty_1500',R2,'task_clock_ms')-values('w16_bursty_1500','rayon','task_clock_ms')
    axs[1,2].plot(np.arange(1,21),d,'o-',color=COL[R2],markersize=3)
    axs[1,2].axhline(d.mean(),color='grey',lw=.8,ls='--')
    axs[1,2].set(title='F  16-worker bursty 1500',xlabel='Randomized block',ylabel='RTRB b2 − Rayon CPU ms',xticks=[1,5,10,15,20])
    handles,labels=axs[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=4,frameon=False)
    finish(fig,'02_diagnostics')
    # 3. All-at-once scaling. Historical links are dashed, new links solid.
    fig,axs=plt.subplots(2,2,figsize=(7.1,5.0),layout='constrained')
    for c in FOCUS:
        for ax,m,label in [(axs[0,0],'elapsed_ms','Batch elapsed (ms)'),(axs[0,1],'task_clock_ms','Process CPU time (ms)')]:
            means=[];lo=[];hi=[]
            for n in [2,4,8,16]:
                v=values(f'w{n}_all',c,m);camp=block[block.scenario==f'w{n}_all'].campaign.iloc[0]
                ci=np.quantile(weights(camp)@v,[.025,.975]);means.append(v.mean());lo.append(ci[0]);hi.append(ci[1])
            ax.plot([0,1,2],means[:3],'--',color=COL[c],alpha=.7)
            ax.plot([2,3],means[2:],'-',color=COL[c])
            ax.errorbar(np.arange(4),means,yerr=[np.array(means)-lo,np.array(hi)-means],fmt='o',color=COL[c],capsize=2,markersize=3,label=LAB[c])
            ax.set(xticks=range(4),xticklabels=[2,4,8,16],xlabel='Workers',ylabel=label)
    axs[0,0].set_title('A  Throughput plateaus near core capacity');axs[0,1].set_title('B  SMT consumes much more CPU time')
    for i,c in enumerate(FOCUS):
        r=cross[(cross.kind=='scaling')&(cross.scenario=='w16_all')&(cross.configuration==c)&(cross.metric=='elapsed_ms')].iloc[0]
        hbar(axs[1,0],r.speedup,i,r.speedup_low,r.speedup_high,fmt='o',color=COL[c])
    axs[1,0].set(yticks=range(4),yticklabels=[LAB[c] for c in FOCUS],xlabel='8 → 16 speedup (ideal doubling: 2.0)',title='C  No supported scaling winner',xlim=(1.065,1.115))
    for i,(m,label) in enumerate([('task_clock_ms','CPU time'),('cycles_per_wu','Cycles/WU'),('instructions_per_wu','Instructions/WU'),('ipc','IPC'),('effective_ghz','GHz proxy')]):
        r=cross[(cross.kind=='scaling')&(cross.scenario=='w16_all')&(cross.configuration=='rayon')&(cross.metric==m)].iloc[0]
        hbar(axs[1,1],r.ratio_of_means_pct,i,r.ratio_ci_low,r.ratio_ci_high,fmt='o',color=COL['rayon'])
    axs[1,1].axvline(0,color='grey',lw=.8);axs[1,1].set(yticks=range(5),yticklabels=['CPU time','Cycles/WU','Instructions/WU','IPC','GHz proxy'],xlabel='16 vs 8 change (%)',title='D  Rayon: more cycles, same instructions');axs[1,1].invert_yaxis()
    handles,labels=axs[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=4,frameon=False)
    finish(fig,'03_scaling')
    # 4. Counter disagreement is displayed as matched differences, not pooled overhead.
    fig,axs=plt.subplots(1,2,figsize=(7.1,3.6),layout='constrained')
    metrics=['task_clock_ms','cycles_per_wu','instructions_per_wu','effective_ghz']
    for ax,s,title in [(axs[0],'w16_fixed_1500','A  Fixed 1500 · 16 workers'),(axs[1],'w16_bursty_1500','B  Bursty 1500 · 16 workers')]:
        for j,c in enumerate(['bevy',M0,R0]):
            for i,m in enumerate(metrics):
                r=effect(s,c,'rayon',m);hbar(ax,r.ratio_of_means_pct,i+(j-1)*.2,r.ratio_ci_low,r.ratio_ci_high,fmt='o',color=COL[c],markersize=4,label=LAB[c] if i==0 else None)
        ax.axvline(0,color='grey',lw=.8);ax.set(yticks=range(4),yticklabels=['CPU time','Cycles/WU','Instructions/WU','GHz proxy'],xlabel='Change versus Rayon (%)',title=title);ax.invert_yaxis()
    handles,labels=axs[0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=3,frameon=False)
    finish(fig,'04_counter_disagreement')
    # 5. A compact CPU/completion trade-off across workers and arrival regimes.
    fig,axs=plt.subplots(1,2,figsize=(7.1,3.8),layout='constrained')
    for ax,mode,title in [(axs[0],'fixed','A  Fixed 1500'),(axs[1],'bursty','B  Bursty 1500')]:
        for n,mk,ls in [(8,'o','--'),(16,'s','-')]:
            for c in [M0,R0,R2,'bevy']:
                ex=effect(f'w{n}_{mode}_1500',c,'rayon','elapsed_ms');ey=effect(f'w{n}_{mode}_1500',c,'rayon','task_clock_ms')
                ax.errorbar(ex.absolute_effect,ey.ratio_of_means_pct,xerr=[[ex.absolute_effect-ex.ci_low],[ex.ci_high-ex.absolute_effect]],
                    yerr=[[ey.ratio_of_means_pct-ey.ratio_ci_low],[ey.ratio_ci_high-ey.ratio_of_means_pct]],fmt=mk,color=COL[c],capsize=2,markersize=5)
            x=[effect(f'w{n}_{mode}_1500',c,'rayon','elapsed_ms').absolute_effect for c in [R0,R2]]
            y=[effect(f'w{n}_{mode}_1500',c,'rayon','task_clock_ms').ratio_of_means_pct for c in [R0,R2]]
            ax.plot(x,y,ls,color=COL[R0],alpha=.6)
        ax.axhline(0,color='grey',lw=.8);ax.axvline(0,color='grey',lw=.8)
        ax.set(title=title,xlabel='Extra completion time vs Rayon (ms)',ylabel='CPU-time change vs Rayon (%)')
    from matplotlib.lines import Line2D
    hh=[Line2D([],[],marker='o',color=COL[c],ls='',label=LAB[c]) for c in [M0,'bevy',R0,R2]]
    hh += [Line2D([],[],marker=mk,color='grey',ls='',label=f'{n} workers') for n,mk in [(8,'o'),(16,'s')]]
    fig.legend(handles=hh,loc='outside lower center',ncol=3,frameon=False)
    finish(fig,'05_tradeoff')
    # 6. Spin duration is an isolated factor within each queue implementation.
    fig,axs=plt.subplots(1,2,figsize=(7.1,4.5),layout='constrained')
    conditions=[(n,mode) for n in [8,16] for mode in ['fixed_900','fixed_1500','bursty_900','bursty_1500']]
    for i,(n,mode) in enumerate(conditions):
        s=f'w{n}_{mode}'
        for j,(a,r,color,mk) in enumerate([(M40,M0,COL[M0],'o'),(M100,M0,COL[M0],'s'),(R40,R0,COL[R0],'o'),(R100,R0,COL[R0],'s')]):
            for ax,m in [(axs[0],'elapsed_ms'),(axs[1],'task_clock_ms')]:
                e=effect(s,a,r,m);hbar(ax,e.absolute_effect,i+(j-1.5)*.15,e.sim_ci_low,e.sim_ci_high,fmt=mk,color=color,markersize=3,alpha=.9)
    for ax,m,title in [(axs[0],'Elapsed difference (ms)','A  Completion effects remain uncertain'),(axs[1],'CPU-time difference (ms)','B  Timed spinning costs CPU')]:
        ax.axvline(0,color='grey',lw=.8);ax.set(yticks=range(8),yticklabels=[f'{n}w {mode.replace("_"," ")}' for n,mode in conditions],xlabel=m,title=title);ax.invert_yaxis()
    hh=[Line2D([],[],marker=mk,color=color,ls='',label=label) for color,mk,label in [(COL[M0],'o','MPMC 40 µs'),(COL[M0],'s','MPMC 100 µs'),(COL[R0],'o','RTRB b1 40 µs'),(COL[R0],'s','RTRB b1 100 µs')]]
    fig.legend(handles=hh,loc='outside lower center',ncol=4,frameon=False)
    finish(fig,'06_spin')
    # 7. Burst sensitivity and historical within-campaign contrasts.
    fig,axs=plt.subplots(1,2,figsize=(7.1,3.8),layout='constrained')
    for j,c in enumerate(FOCUS):
        for i,m in enumerate(['task_clock_ms','cycles_per_wu','instructions_per_wu','context_switches']):
            r=cross[(cross.kind=='arrival')&(cross.scenario=='w16_bursty_1500')&(cross.configuration==c)&(cross.metric==m)].iloc[0]
            hbar(axs[0],r.ratio_of_means_pct,i+(j-1.5)*.16,r.ratio_ci_low,r.ratio_ci_high,fmt='o',color=COL[c],markersize=3)
    axs[0].axvline(0,color='grey',lw=.8);axs[0].set(yticks=range(4),yticklabels=['CPU time','Cycles/WU','Instructions/WU','Context switches'],title='A  Bursty vs fixed · 16w · 1500',xlabel='Independent-campaign change (%)');axs[0].invert_yaxis()
    for c in [M0,R0,R2]:
        ys=[]
        for i,n in enumerate([2,4,8,16]):
            r=effect(f'w{n}_bursty_1500',c,'rayon','task_clock_ms');ys.append(r.absolute_effect)
            ebar(axs[1],i,r.absolute_effect,r.ci_low,r.ci_high,fmt='o',color=COL[c],markersize=4)
        axs[1].plot([0,1,2],ys[:3],'--',color=COL[c]);axs[1].plot([2,3],ys[2:],'-',color=COL[c],label=LAB[c])
    axs[1].axhline(0,color='grey',lw=.8);axs[1].axvline(1.5,color='grey',lw=.8,ls=':')
    axs[1].set(xticks=range(4),xticklabels=[2,4,8,16],xlabel='Workers (3 s settle | 1 s settle)',ylabel='CPU difference vs Rayon (ms)',title='B  Exact q64 · bursty 1500');axs[1].legend(loc='lower left')
    hh=[Line2D([],[],color=COL[c],marker='o',ls='',label=LAB[c]) for c in FOCUS]
    fig.legend(handles=hh,loc='outside lower center',ncol=4,frameon=False)
    finish(fig,'07_arrivals_history')
    print('Seven vector figures written.',flush=True)

if __name__=='__main__':main()
