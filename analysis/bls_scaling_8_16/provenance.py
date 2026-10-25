"""Record source/build evidence and read-only PMU discovery; never record perf data."""
from data import *
import importlib.metadata
import subprocess
import datetime

def main():
    paths=[ROOT/'Cargo.lock',ROOT/'Cargo.toml',ROOT/'harness/Cargo.toml']
    paths += list((ROOT/'harness/src').rglob('*.rs'))
    paths += list((ROOT.parent/'threadance/src').glob('*.rs'))
    paths += [ROOT/'analysis/bls_full'/x for x in ['common.py','estimate.py','diagnostics.py','requirements.txt']]
    paths += [p for p in Path(__file__).parent.iterdir() if p.is_file()]
    registry=Path.home()/'.cargo/registry/src'
    for pattern in ['*/rayon-core-1.13.0/src/registry.rs','*/rayon-core-1.13.0/src/sleep/mod.rs',
                    '*/crossbeam-channel-0.5.17/src/flavors/array.rs','*/crossbeam-deque-0.8.8/src/deque.rs',
                    '*/rtrb-0.4.0/src/lib.rs','*/bevy_tasks-0.19.1/src/task_pool.rs','*/bevy_tasks-0.19.1/src/executor.rs',
                    '*/async-executor-1.14.0/src/lib.rs']:
        found=list(registry.glob(pattern));assert len(found)==1,pattern;paths+=found
    save([dict(path=str(p),sha256=sha(p)) for p in sorted(paths)],'source_hashes')
    evidence={}
    for name,args,cwd in [('host',['lscpu'],ROOT),('perf_version',['perf','version'],ROOT),
            ('harness_diff',['git','diff','HEAD','--','harness','Cargo.toml','Cargo.lock'],ROOT),
            ('threadance_diff',['git','diff','HEAD','--','src','Cargo.toml','Cargo.lock'],ROOT.parent/'threadance')]:
        r=subprocess.run(args,cwd=cwd,capture_output=True,text=True)
        evidence[name]=dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)
    listing=subprocess.run(['perf','list','--details'],capture_output=True,text=True,check=True).stdout
    (OUT/'perf_event_listing.txt').write_text(listing)
    pmu=Path('/sys/bus/event_source/devices')
    evidence['pmus']=sorted(p.name for p in pmu.iterdir())
    evidence['ibs_op_format']={p.name:p.read_text().strip() for p in (pmu/'ibs_op/format').glob('*')}
    evidence['thread_siblings']={p.parents[1].name:p.read_text().strip() for p in Path('/sys/devices/system/cpu').glob('cpu[0-9]*/topology/thread_siblings_list')}
    evidence['current_binary_sha256']=sha(ROOT/'target/release/threadance-harness')
    evidence['historical_binary_sha256']=pd.read_csv(ROOT/'results/reports/bls_full/tables/campaign_audit.csv').binary_sha256.unique().tolist()
    evidence['new_binary_sha256']=read('campaign_audit').binary_sha256.unique().tolist()
    evidence['captured_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    evidence['event_probe']='Discovery only: no counter collection, benchmark or build invoked.'
    (OUT/'source_provenance.json').write_text(json.dumps(evidence,indent=2)+'\n')
    versions={p:importlib.metadata.version(p) for p in ['numpy','pandas','scipy','matplotlib','scikit-learn','statsmodels','diptest','pymupdf']}
    (OUT/'analysis_environment.json').write_text(json.dumps(dict(python=sys.version,packages=versions),indent=2)+'\n')

if __name__=='__main__':main()
