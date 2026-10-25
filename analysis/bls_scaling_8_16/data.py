"""Focused, read-only extension of bls_full's validated extraction conventions."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bls_full'))
from common import ROOT, EVENTS, METRICS, weights, rng_for, sha, regime, short, B, SEED
import numpy as np
import pandas as pd
import json
import random
import sqlite3
import tomllib

OUT = ROOT / 'results/reports/bls_scaling_8_16'
TABLES = OUT / 'tables'
FIGURES = OUT / 'figures'
M0 = 'threadance__spin-then-park__q64__s0'
M40 = M0[:-1] + '40'
M100 = M0[:-1] + '100'
MCW = 'threadance__crossbeam-wait__q64'
MAB = 'threadance__agave-backoff__q64'
R0 = 'threadance-hot-first-rtrb__spin-then-park__q64__b1__s0'
R40 = R0[:-1] + '40'
R100 = R0[:-1] + '100'
R2 = R0.replace('__b1', '__b2')
RAB = 'threadance-hot-first-rtrb__agave-backoff__q64__b1'
FOCUS = ['rayon', 'bevy', M0, R0]

def save(data, name):
    pd.DataFrame(data).to_csv(TABLES / (name + '.csv'), index=False, float_format='%.12g')

def read(name):
    return pd.read_csv(TABLES / (name + '.csv'))

def extract():
    for p in (OUT, TABLES, FIGURES): p.mkdir(parents=True, exist_ok=True)
    paths = [p for root in ['results/studies/bls-full', 'results/studies/bls-scaling-8-16',
                            'results/reports/bls_full', 'results/datasets', 'analysis/bls_full']
             for p in (ROOT / root).rglob('*') if p.is_file() and '__pycache__' not in str(p)]
    inventory = [{'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in sorted(paths)]
    save(inventory, 'input_inventory')
    checks, campaigns, raws = [], [], []
    def check(c, name, ok):
        checks.append(dict(campaign=c, check=name, passed=bool(ok)))
        assert ok, (c, name)
    newroot = ROOT / 'results/studies/bls-scaling-8-16/campaigns'
    for folder in sorted(newroot.glob('bls-*')):
        print('Read-only audit:', folder.name, flush=True)
        name = folder.name
        d = tomllib.loads((folder/'experiment.toml').read_text())
        env = json.loads((folder/'environment.json').read_text())
        ident = json.loads((folder/'dataset_identity.json').read_text())
        check(name, 'dataset_hash', sha(ROOT/'results/datasets'/Path(ident['path']).name) == ident['sha256'])
        man = pd.read_csv(folder/'manifest.csv').fillna({'arrival_rate': 0})
        plan = pd.read_csv(folder/'plan.csv').fillna({'arrival_rate': 0})
        check(name, 'plan_manifest', man[plan.columns].equals(plan))
        cfg = {x['id']: x for x in d['configurations']}
        base = [(c, float(rate)) for c in cfg for rate in d['delivery'].get('arrival_rates', [0])]
        expected = []
        for block in range(1, 21):
            sh = base.copy(); random.Random(d['randomization']['seed'] + block).shuffle(sh)
            expected.extend((block, i+1, c, r) for i, (c, r) in enumerate(sh))
        check(name, 'randomization_reproduced', expected == list(plan[['block','order_in_block','configuration','arrival_rate']].itertuples(index=False, name=None)))
        db = sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1', uri=True)
        check(name, 'sqlite_integrity', db.execute('pragma integrity_check').fetchall() == [('ok',)])
        check(name, 'foreign_keys', not db.execute('pragma foreign_key_check').fetchall())
        runs = pd.read_sql_query('select * from run', db).rename(columns={'id':'run_id'})
        exp = pd.read_sql_query('select * from experiment', db).rename(columns={'id':'experiment_id'})
        pm = pd.read_sql_query('select * from perf_metric', db)
        check(name, 'complete_successful_standard_runs', len(runs) == len(base)*40 and (runs.success==1).all() and
              (runs.completed_work_units==1000).all() and runs.error_text.isna().all() and set(runs.perf_pass)=={'standard'})
        check(name, 'two_replicates', runs.groupby('experiment_id').run_index.apply(lambda x: sorted(x)==[0,1]).all())
        check(name, 'perf_complete_unmultiplexed', set(pm.event_name)==set(EVENTS) and (pm.groupby('run_id').size()==8).all()
              and (pm.status=='ok').all() and (pm.percent_running==100).all() and (pm.counter_value>=0).all())
        captures = pd.read_sql_query('select * from perf_capture', db)
        lookup = pm.set_index(['run_id','event_name']).counter_value
        check(name, 'perf_raw_matches_SQL', len(captures)==len(runs) and set(captures.perf_exit_code)=={130} and all(
            not json.loads(r.raw_json)['unparsed'] and len(json.loads(r.raw_json)['records'])==8 and all(
                float(e['counter-value'])==lookup.loc[(r.run_id,e['event'])] and e['pcnt-running']==100
                for e in json.loads(r.raw_json)['records']) for r in captures.itertuples()))
        check(name, 'timestamps_monotone', runs.started_unix_ns.is_unique and runs.started_unix_ns.is_monotonic_increasing)
        check(name, 'experiments_match', set(exp.experiment_id)==set(man.experiment_id) and man.experiment_id.is_unique)
        for c, val in dict(workload='bls-aggregate-verify', workers=d['workload']['workers'], work_units=1000,
                           workload_seed=1, validators=2000, signers_per_certificate=1600,
                           certificates_per_work_unit=1, warmup_runs=1, measured_runs=2).items():
            check(name, c, (exp[c]==val).all())
        merged = exp.merge(man, on='experiment_id', suffixes=('', '_manifest'), validate='one_to_one')
        for x in merged.itertuples():
            c=cfg[x.configuration]
            assert x.scheduler==c['scheduler']
            if x.scheduler.startswith('threadance'):
                assert x.queue_capacity==c['queue_capacity']==64 and x.threadance_wait_policy==c['wait_policy']
                assert x.threadance_spin_us==c['spin_us'] if c['wait_policy']=='spin-then-park' else pd.isna(x.threadance_spin_us)
            if x.scheduler.endswith('rtrb'):
                extra=json.loads(x.extra_config_json)
                assert extra['hot_first_backlog']==extra['ring_slots_per_worker']==c['hot_first_backlog']
                assert extra['threadance_git_commit']==env['threadance_commit'] and extra['ring_implementation']=='rtrb-0.4.0'
        check(name, 'configuration_identity', True)
        r = runs.merge(merged[['experiment_id','configuration','scheduler','workers','delivery_mode','arrival_rate_manifest',
                              'block','order_in_block','global_order']], on='experiment_id').rename(columns={'arrival_rate_manifest':'arrival_rate'})
        counters=pm.pivot(index='run_id',columns='event_name',values='counter_value').rename(columns=lambda x:x.replace('-','_')).rename(columns={'task_clock':'task_clock_ms'})
        r=r.merge(counters,on='run_id',validate='one_to_one')
        mode=d['delivery']['mode']
        if mode!='all-at-once':
            arr=pd.read_sql_query('select * from run_arrival order by run_id,work_unit_index',db)
            check(name, 'arrival_indices', len(arr)==len(r)*1000 and np.array_equal(arr.work_unit_index.to_numpy().reshape(-1,1000),np.broadcast_to(np.arange(1000),(len(r),1000))))
            a=arr.actual_submit_offset_ns.to_numpy().reshape(-1,1000); s=arr.scheduled_offset_ns.to_numpy().reshape(-1,1000)
            l=arr.lateness_ns.to_numpy().reshape(-1,1000)
            check(name, 'arrival_monotonic_lateness', (np.diff(a,axis=1)>=0).all() and (np.diff(s,axis=1)>=0).all() and np.array_equal(l,np.maximum(a-s,0)))
            ar=pd.DataFrame(dict(run_id=arr.run_id.to_numpy()[::1000],last_submit_ms=a[:,-1]/1e6,schedule_window_ms=s[:,-1]/1e6,
                last_lateness_ms=l[:,-1]/1e6,mean_lateness_ms=l.mean(axis=1)/1e6,p95_lateness_ms=np.quantile(l,.95,axis=1)/1e6,max_lateness_ms=l.max(axis=1)/1e6))
            import hashlib
            ar['schedule_hash']=[hashlib.sha256(v.tobytes()).hexdigest() for v in s]
            r=r.merge(ar,on='run_id')
            check(name, 'paired_schedules', (r.groupby(['block','arrival_rate']).schedule_hash.nunique()==1).all())
            check(name, 'schedule_window', np.allclose(r.schedule_window_ms,999000/r.arrival_rate,atol=1e-6,rtol=0))
        r['elapsed_ms']=r.elapsed_ns/1e6; r['throughput']=r.work_units_per_second
        r['ipc']=r.instructions/r.cycles; r['branch_miss_pct']=100*r.branch_misses/r.branches
        r['cpu_equivalents']=r.task_clock_ms/r.elapsed_ms; r['effective_ghz']=r.cycles/r.task_clock_ms/1e6
        for m in ['task_clock_ms']+[e.replace('-','_') for e in EVENTS[1:]]: r[m+'_per_wu']=r[m]/1000
        if mode!='all-at-once':
            r['drain_delay_ms']=r.elapsed_ms-r.last_submit_ms; r['schedule_excess_ms']=r.elapsed_ms-r.schedule_window_ms
            check(name,'drain_decomposition',np.allclose(r.schedule_excess_ms,r.drain_delay_ms+r.last_lateness_ms,atol=1e-7))
        r['campaign']=name; r['scenario']=[regime(w,m,a) for w,m,a in zip(r.workers,r.delivery_mode,r.arrival_rate)]
        old=pd.read_csv(folder/'analysis_raw_runs.csv').set_index('run_id').loc[r.run_id]
        check(name,'independent_archived_elapsed',np.allclose(r.elapsed_ms,old.elapsed_ms,atol=1e-9))
        check(name,'independent_archived_task_clock',np.allclose(r.task_clock_ms/1000,old.task_clock_per_wu,atol=1e-10))
        if mode!='all-at-once': check(name,'independent_archived_drain',np.allclose(r.drain_delay_ms,old.drain_delay_ms,atol=1e-9))
        check(name,'logs_complete',all((folder/x.log).is_file() and f'experiment={x.experiment_id} ' in (folder/x.log).read_text() and 'panicked at' not in (folder/x.log).read_text() for x in man.itertuples()))
        campaigns.append(dict(campaign=name,workers=int(r.workers.iloc[0]),delivery=mode,runs=len(r),configs=len(cfg),blocks=20,
            settle_seconds=d['execution']['settle_seconds'],start_utc=pd.to_datetime(r.started_unix_ns.min(),unit='ns',utc=True).isoformat(),
            end_utc=pd.to_datetime(r.started_unix_ns.max(),unit='ns',utc=True).isoformat(),binary_sha256=env['binary_sha256'],
            git_commit=env['git_commit'],threadance_commit=env['threadance_commit']))
        raws.append(r); db.close()
    new=pd.concat(raws,ignore_index=True)
    hist=pd.read_csv(ROOT/'results/reports/bls_full/tables/raw_runs.csv')
    hist=hist[hist.configuration.isin(new.configuration.unique())].copy()
    # Validate cached historical data against original SQL, without repeating historical inference.
    for campaign,g in hist.groupby('campaign'):
        folder=ROOT/'results/studies/bls-full/campaigns'/campaign
        db=sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1',uri=True)
        direct=pd.read_sql_query("select r.id run_id,r.elapsed_ns/1e6 elapsed_ms,p.counter_value task_clock_ms from run r join perf_metric p on p.run_id=r.id and p.event_name='task-clock'",db).set_index('run_id').loc[g.run_id]
        check(campaign,'historical_cache_matches_SQL',np.allclose(g.elapsed_ms,direct.elapsed_ms,atol=1e-7) and np.allclose(g.task_clock_ms,direct.task_clock_ms,atol=1e-7))
        for m in EVENTS[1:]:
            direct=pd.read_sql_query('select run_id,counter_value from perf_metric where event_name=?',db,params=[m]).set_index('run_id').loc[g.run_id]
            check(campaign,'historical_'+m,np.allclose(g[m.replace('-','_')],direct.counter_value,atol=1e-5,rtol=1e-10))
        db.close()
    raw=pd.concat([hist,new],ignore_index=True)
    block=raw.groupby(['campaign','scenario','workers','delivery_mode','arrival_rate','configuration','block'],as_index=False)[METRICS+['order_in_block','global_order','started_unix_ns']].mean()
    check('all','20_blocks_two_replicates',(raw.groupby(['scenario','configuration','block']).size()==2).all() and (block.groupby(['scenario','configuration']).size()==20).all())
    save(raw,'raw_runs'); save(block,'block_means'); save(checks,'audit_checks'); save(campaigns,'campaign_audit')
    gaps=[]
    for c,g in raw.groupby('campaign'):
        g=g.sort_values('started_unix_ns').copy(); g['gap_s']=g.started_unix_ns.diff()/1e9
        gaps.extend(g.nlargest(2,'gap_s')[['campaign','block','configuration','run_index','gap_s']].to_dict('records'))
    save(gaps,'acquisition_gaps')
    print('Audit passed:',len(checks),'checks;',len(new),'new runs;',len(hist),'exactly matched historical runs',flush=True)

if __name__=='__main__': extract()
