"""Audit long acquisition gaps and quantify sensitivity to interrupted blocks."""
import sqlite3
from common import *

def main():
    gaps=[]
    for folder in sorted(INPUT.glob('bls-*')):
        if not folder.is_dir():continue
        db=sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1',uri=True)
        r=pd.read_sql_query('select id run_id,experiment_id,run_index,started_unix_ns from run order by started_unix_ns',db);db.close()
        man=pd.read_csv(folder/'manifest.csv')
        r=r.merge(man[['experiment_id','block','order_in_block','configuration']],on='experiment_id').sort_values('started_unix_ns').reset_index(drop=True)
        for i in np.flatnonzero(r.started_unix_ns.diff().to_numpy()/1e9>300):
            a,b=r.iloc[i-1],r.iloc[i]
            gaps.append(dict(campaign=folder.name,gap_seconds=(b.started_unix_ns-a.started_unix_ns)/1e9,block_before=a.block,block_after=b.block,
                run_before=a.run_id,run_after=b.run_id,experiment_before=a.experiment_id,experiment_after=b.experiment_id,
                order_before=a.order_in_block,order_after=b.order_in_block,configuration_before=a.configuration,configuration_after=b.configuration,
                utc_before=pd.to_datetime(a.started_unix_ns,unit='ns',utc=True).isoformat(),utc_after=pd.to_datetime(b.started_unix_ns,unit='ns',utc=True).isoformat()))
    save(gaps,'acquisition_interruptions')
    block=read('block_means');rows=[]
    for gap in gaps:
        for s,g in block[block.campaign==gap['campaign']].groupby('scenario'):
            bad={gap['block_before'],gap['block_after']}
            kept=g[~g.block.isin(bad)];n=kept.block.nunique();W=weights('interruptions_'+s,n=n)
            ref=kept[kept.configuration=='rayon'].sort_values('block')
            for c,cg in kept.groupby('configuration'):
                if c=='rayon':continue
                cg=cg.sort_values('block')
                for m in CORE:
                    if cg[m].isna().any():continue
                    d=cg[m].to_numpy()-ref[m].to_numpy();rel=100*(cg[m].to_numpy()/ref[m].to_numpy()-1)
                    lo,hi=np.quantile(W@d,[.025,.975]);rl,rh=np.quantile(W@rel,[.025,.975])
                    rows.append(dict(scenario=s,configuration=c,reference='rayon',metric=m,n_blocks=n,omitted_blocks=';'.join(map(str,sorted(bad))),absolute_effect=d.mean(),ci_low=lo,ci_high=hi,relative_pct=rel.mean(),relative_ci_low=rl,relative_ci_high=rh))
    save(rows,'interruption_sensitivity')
    print('Audited',len(gaps),'long acquisition gaps; omitted-block sensitivity saved',flush=True)

if __name__=='__main__':main()
