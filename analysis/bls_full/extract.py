"""Read-only raw SQLite extraction and comprehensive archive audit. Never runs Rust."""
import csv
import json
import random
import sqlite3
import struct
import subprocess
import tomllib
from common import *

def snapshot():
    paths = sorted(p for p in INPUT.rglob('*') if p.is_file())
    paths += sorted((ROOT/'results/datasets').glob('*.bin'))
    return [{'path':str(p.relative_to(ROOT)), 'bytes':p.stat().st_size, 'sha256':sha(p)} for p in paths]

def extract():
    for p in (OUT,TABLES,FIGURES): p.mkdir(parents=True,exist_ok=True)
    before = snapshot()
    save(before,'input_inventory')
    checks, audits, allruns, configs, perf_audit, schedules = [],[],[],[],[],[]
    environments = {}
    def check(campaign, name, passed, detail=''):
        checks.append(dict(campaign=campaign,check=name,passed=bool(passed),detail=str(detail)))
        if not passed: print('AUDIT ISSUE:',campaign,name,detail,flush=True)
    for folder in sorted(INPUT.glob('bls-*')):
        if not folder.is_dir(): continue
        name=folder.name
        print('Extracting',name,flush=True)
        d=tomllib.loads((folder/'experiment.toml').read_text())
        env=json.loads((folder/'environment.json').read_text()); environments[name]=env
        identity=json.loads((folder/'dataset_identity.json').read_text())
        dataset=ROOT/'results/datasets'/Path(identity['path']).name
        check(name,'dataset_sha256',sha(dataset)==identity['sha256'],identity['sha256'])
        with dataset.open('rb') as f:
            header=f.read(60)
        check(name,'dataset_header',header[:8]==b'TBLSDS01' and struct.unpack('<IQQQQQQ',header[8:])==(1,2000,1600,1,1000,1,1000))
        manifest=pd.read_csv(folder/'manifest.csv').fillna({'arrival_rate':0})
        plan=pd.read_csv(folder/'plan.csv').fillna({'arrival_rate':0})
        check(name,'plan_manifest_equal',manifest[plan.columns].equals(plan))
        # Independently reproduce the archived shuffle from configuration definitions.
        base=[]
        rates=d['delivery'].get('arrival_rates',[0])
        for cfg in d['configurations']:
            for rate in rates: base.append((cfg['id'],float(rate)))
        actual=[]
        for block in range(1,21):
            sh=base.copy(); random.Random(d['randomization']['seed']+block).shuffle(sh)
            actual.extend((block,i+1,c,r) for i,(c,r) in enumerate(sh))
        expected=list(plan[['block','order_in_block','configuration','arrival_rate']].itertuples(index=False,name=None))
        check(name,'randomized_plan_reproduced',actual==expected)
        check(name,'arrival_seed_by_block',bool((plan.arrival_seed==41+plan.block).all()))
        cfgmap={c['id']:c for c in d['configurations']}
        relevant=[c for c in d['configurations'] if c['scheduler'] in SCHEDULERS]
        check(name,'configuration_counts',len(relevant)==28 and sum(c['scheduler']=='threadance' for c in relevant)==10 and sum(c['scheduler']=='threadance-hot-first-rtrb' for c in relevant)==16)
        for cfg in relevant:
            configs.append(dict(campaign=name,configuration=cfg['id'],label=short(cfg['id']),scheduler=cfg['scheduler'],
                topology='MPMC' if cfg['scheduler']=='threadance' else ('RTRB' if cfg['scheduler'].endswith('rtrb') else cfg['scheduler']),
                policy=cfg.get('wait_policy','NA'),spin=cfg.get('spin_us',0),capacity=cfg.get('queue_capacity',0),backlog=cfg.get('hot_first_backlog',0),
                effective_slots=(d['workload']['workers']*cfg.get('hot_first_backlog',0) if cfg['scheduler'].endswith('rtrb') else cfg.get('queue_capacity',0))))
        db=sqlite3.connect(f'file:{folder}/benchmarks.sqlite3?mode=ro&immutable=1',uri=True)
        check(name,'sqlite_integrity',db.execute('pragma integrity_check').fetchall()==[('ok',)])
        check(name,'sqlite_foreign_keys',db.execute('pragma foreign_key_check').fetchall()==[])
        exp=pd.read_sql_query('select * from experiment',db).rename(columns={'id':'experiment_id'})
        runs=pd.read_sql_query('select * from run',db).rename(columns={'id':'run_id'})
        sessions=pd.read_sql_query('select * from benchmark_session',db)
        pm=pd.read_sql_query('select * from perf_metric',db)
        check(name,'run_counts',len(runs)==len(base)*20*2,f'{len(runs)} / {len(base)*40}')
        check(name,'experiment_mapping',set(exp.experiment_id)==set(manifest.experiment_id) and manifest.experiment_id.is_unique)
        check(name,'two_runs_per_experiment',runs.groupby('experiment_id').run_index.apply(lambda x:sorted(x)==[0,1]).all())
        check(name,'run_success',((runs.success==1)&(runs.completed_work_units==1000)&runs.error_text.isna()).all())
        check(name,'positive_elapsed_and_throughput',(runs.elapsed_ns>0).all() and np.allclose(runs.work_units_per_second,1e12/runs.elapsed_ns,rtol=1e-12))
        check(name,'standard_pass',set(runs.perf_pass)=={'standard'} and (runs.pass_run_index==runs.run_index).all())
        check(name,'unique_start_timestamps',runs.started_unix_ns.is_unique)
        check(name,'monotone_start_timestamps',runs.started_unix_ns.is_monotonic_increasing)
        for col,val in dict(workload='bls-aggregate-verify',profile_mode='standard',workers=d['workload']['workers'],work_units=1000,workload_seed=1,validators=2000,signers_per_certificate=1600,certificates_per_work_unit=1,warmup_runs=1,measured_runs=2,delivery_mode=d['delivery']['mode']).items():
            check(name,'identity_'+col,(exp[col]==val).all(),val)
        check(name,'session_revision',set(sessions.git_commit)=={env['git_commit']})
        check(name,'campaign_identity',set(sessions.campaign_id)=={name})
        for col in ['kernel_version','rustc_version','cargo_version','perf_version','cpu_model','physical_cores','logical_cpus','git_dirty']:
            check(name,'constant_'+col,sessions[col].nunique(dropna=False)==1,str(sessions[col].unique().tolist()))
        merged=exp.merge(manifest,on='experiment_id',suffixes=('','_manifest'),validate='one_to_one')
        ok=True
        for _,x in merged.iterrows():
            cfg=cfgmap[x.configuration]
            seed_matches=(x.arrival_seed==x.arrival_seed_manifest) if d['delivery']['mode']!='all-at-once' else x.arrival_seed==1
            ok &= x.scheduler==cfg['scheduler'] and (x.arrival_rate if pd.notna(x.arrival_rate) else 0)==x.arrival_rate_manifest and seed_matches
            if cfg['scheduler'].startswith('threadance'):
                spin_matches=(x.threadance_spin_us==cfg['spin_us']) if cfg['wait_policy']=='spin-then-park' else pd.isna(x.threadance_spin_us)
                ok &= x.queue_capacity==cfg['queue_capacity'] and x.threadance_wait_policy==cfg['wait_policy'] and spin_matches
            if cfg['scheduler'].endswith('rtrb'):
                extra=json.loads(x.extra_config_json)
                ok &= extra['hot_first_backlog']==cfg['hot_first_backlog'] and extra['ring_slots_per_worker']==cfg['hot_first_backlog'] and extra['ring_implementation']=='rtrb-0.4.0' and extra['threadance_git_commit']==env['threadance_commit']
        check(name,'configuration_identity_SQLite_TOML_manifest',ok,'Non-SpinThenPark spin stored as NULL. All-at-once ignores planned arrival seeds and records CLI default 1.')
        check(name,'all_logs_present',all((folder/x).is_file() for x in manifest.log))
        logfail=[]
        for x in manifest.itertuples():
            content=(folder/x.log).read_text()
            if f'experiment={x.experiment_id} ' not in content or 'panicked at' in content: logfail.append(x.log)
        check(name,'log_completion',not logfail,logfail)
        check(name,'perf_event_set',set(pm.event_name)==set(EVENTS) and (pm.groupby('run_id').size()==8).all() and len(pm)==len(runs)*8)
        check(name,'perf_status',(pm.status=='ok').all() and pm.counter_value.notna().all() and (pm.counter_value>=0).all())
        check(name,'perf_running',(pm.percent_running==100).all() and (pm.event_runtime_ns>0).all())
        pc=pd.read_sql_query('select * from perf_capture',db)
        check(name,'perf_captures',len(pc)==len(runs) and set(pc.perf_exit_code)=={130})
        lookup=pm.set_index(['run_id','event_name']).counter_value
        rawok=True
        for x in pc.itertuples():
            parsed=json.loads(x.raw_json)
            rawok &= not parsed['unparsed'] and len(parsed['records'])==8
            for e in parsed['records']:
                rawok &= float(e['counter-value'])==lookup.loc[(x.run_id,e['event'])] and e['pcnt-running']==100
        check(name,'perf_raw_json_matches_metrics',rawok)
        for event, g in pm.groupby('event_name'):
            perf_audit.append(dict(campaign=name,event=event,n=len(g),minimum=g.counter_value.min(),maximum=g.counter_value.max(),zero_count=(g.counter_value==0).sum(),min_running=g.percent_running.min(),max_running=g.percent_running.max(),statuses=';'.join(g.status.unique()),units=';'.join(g.unit.fillna('').unique())))
        r=runs.merge(merged[['experiment_id','configuration','scheduler','workers','delivery_mode','arrival_rate_manifest','block','order_in_block','global_order','arrival_seed']],on='experiment_id',validate='many_to_one').rename(columns={'arrival_rate_manifest':'arrival_rate'})
        counter=pm.pivot(index='run_id',columns='event_name',values='counter_value').rename(columns={e:e.replace('-','_') for e in EVENTS}).rename(columns={'task_clock':'task_clock_ms'})
        r=r.merge(counter,on='run_id',validate='one_to_one')
        mode=d['delivery']['mode']
        if mode!='all-at-once':
            arr=pd.read_sql_query('select * from run_arrival order by run_id,work_unit_index',db)
            check(name,'arrival_rows',len(arr)==len(runs)*1000 and (arr.groupby('run_id').size()==1000).all())
            check(name,'arrival_indices',(arr.work_unit_index.to_numpy().reshape(-1,1000)==np.arange(1000)).all())
            a=arr.actual_submit_offset_ns.to_numpy().reshape(-1,1000)
            s=arr.scheduled_offset_ns.to_numpy().reshape(-1,1000)
            lateness=arr.lateness_ns.to_numpy().reshape(-1,1000)
            check(name,'arrival_monotonic',(np.diff(a,axis=1)>=0).all() and (np.diff(s,axis=1)>=0).all())
            check(name,'arrival_lateness_identity',np.array_equal(lateness,np.maximum(a-s,0)))
            ids=arr.run_id.to_numpy()[::1000]
            ar=pd.DataFrame({'run_id':ids,'last_submit_ms':a[:,-1]/1e6,'schedule_window_ms':s[:,-1]/1e6,'last_lateness_ms':lateness[:,-1]/1e6,'mean_lateness_ms':lateness.mean(axis=1)/1e6,'p95_lateness_ms':np.quantile(lateness,.95,axis=1)/1e6,'max_lateness_ms':lateness.max(axis=1)/1e6})
            schedhash={int(i):hashlib.sha256(v.tobytes()).hexdigest() for i,v in zip(ids,s)}
            tmp=r[['run_id','block','arrival_rate']].copy(); tmp['schedule_sha256']=tmp.run_id.map(schedhash)
            check(name,'paired_identical_schedules',(tmp.groupby(['block','arrival_rate']).schedule_sha256.nunique()==1).all())
            schedules.extend(tmp.assign(campaign=name).to_dict('records'))
            r=r.merge(ar,on='run_id',validate='one_to_one')
            check(name,'schedule_window',np.allclose(r.schedule_window_ms,999000/r.arrival_rate,rtol=0,atol=1e-6))
            del arr,a,s,lateness
        else:
            check(name,'no_arrivals_all_at_once',db.execute('select count(*) from run_arrival').fetchone()[0]==0)
        r['elapsed_ms']=r.elapsed_ns/1e6; r['throughput']=r.work_units_per_second
        r['ipc']=r.instructions/r.cycles
        r['branch_miss_pct']=100*r.branch_misses/r.branches
        r['cpu_equivalents']=r.task_clock_ms/r.elapsed_ms
        r['effective_ghz']=r.cycles/r.task_clock_ms/1e6
        for metric in ['task_clock_ms']+[e.replace('-','_') for e in EVENTS[1:]]: r[metric+'_per_wu']=r[metric]/r.completed_work_units
        if mode!='all-at-once':
            r['drain_delay_ms']=r.elapsed_ms-r.last_submit_ms
            r['schedule_excess_ms']=r.elapsed_ms-r.schedule_window_ms
            check(name,'arrival_before_completion',(r.drain_delay_ms>=0).all())
        r['campaign']=name
        r['scenario']=[regime(w,m,a) for w,m,a in zip(r.workers,r.delivery_mode,r.arrival_rate)]
        old=pd.read_csv(folder/'analysis_raw_runs.csv').set_index('run_id')
        rr=r.set_index('run_id')
        check(name,'existing_csv_elapsed_validation',np.allclose(rr.elapsed_ms,old.loc[rr.index].elapsed_ms,atol=1e-9,rtol=1e-12))
        if mode!='all-at-once':check(name,'existing_csv_drain_validation',np.allclose(rr.drain_delay_ms,old.loc[rr.index].drain_delay_ms,atol=1e-9))
        audits.append(dict(campaign=name,workers=d['workload']['workers'],delivery=mode,rates=str(rates),expected_runs=len(base)*40,actual_runs=len(r),included_runs=r.scheduler.isin(SCHEDULERS).sum(),excluded_obsolete_runs=(r.scheduler=='threadance-hot-first').sum(),blocks=20,configurations=len(relevant),start_utc=pd.to_datetime(r.started_unix_ns.min(),unit='ns',utc=True).isoformat(),end_utc=pd.to_datetime(r.started_unix_ns.max(),unit='ns',utc=True).isoformat(),binary_sha256=env['binary_sha256'],git_commit=env['git_commit'],threadance_commit=env['threadance_commit'],git_dirty=int(sessions.git_dirty.iloc[0]),rustc_version=sessions.rustc_version.iloc[0]))
        allruns.append(r[r.scheduler.isin(SCHEDULERS)].copy())
        db.close()
    raw=pd.concat(allruns,ignore_index=True)
    save(raw,'raw_runs');save(configs,'configuration_dictionary');save(audits,'campaign_audit');save(checks,'audit_checks');save(perf_audit,'perf_audit');save(schedules,'schedule_hashes')
    assert all(x['passed'] for x in checks), 'Audit failure: inspect audit_checks.csv before analysis'
    assert len(raw)==11200 and len(audits)==6
    cfg=pd.DataFrame(configs).drop_duplicates('configuration')
    assert len(cfg)==28
    block=raw.groupby(['campaign','scenario','workers','delivery_mode','arrival_rate','configuration','block'],as_index=False)[METRICS+['order_in_block','global_order','started_unix_ns']].mean()
    save(block,'block_means')
    (OUT/'archived_environments.json').write_text(json.dumps(environments,indent=2)+'\n')
    current=[]
    for p in [ROOT/'Cargo.toml',ROOT/'Cargo.lock']+sorted((ROOT/'harness/src').rglob('*.rs'))+sorted((ROOT/'experiments/common').glob('*.py'))+sorted((ROOT.parent/'threadance/src').glob('*.rs')):
        current.append(dict(path=str(p),sha256=sha(p)))
    save(current,'audited_source_hashes')
    current_binary=ROOT/'target/release/threadance-harness'
    provenance={'current_binary_sha256':sha(current_binary) if current_binary.exists() else None,
        'recorded_binary_sha256':audits[0]['binary_sha256'],
        'tracked_harness_diff':subprocess.check_output(['git','diff','HEAD','--','harness','Cargo.toml','Cargo.lock','experiments/common'],cwd=ROOT,text=True),
        'tracked_threadance_diff':subprocess.check_output(['git','diff','HEAD','--','src','Cargo.toml','Cargo.lock'],cwd=ROOT.parent/'threadance',text=True)}
    (OUT/'source_provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print('Audit passed:',len(checks),'checks;',len(raw),'included physical runs',flush=True)

if __name__=='__main__':extract()
