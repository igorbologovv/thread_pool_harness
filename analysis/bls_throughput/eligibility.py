"""Fail-closed, metric-free warmup audit. Never calculate performance statistics."""
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tomllib
import zipfile

from .archive_inputs import digest

KNOWN_BINARY = '537676037f8c8f3370f853d4d7abb06391d6f62abcaf722283b34644b6402534'
RUNNER_COMMIT = 'c1775157d2b3873f1e8067b0be27322d5051eb54'


def read_csv(path):
    with Path(path).open(newline='') as f:
        return list(csv.DictReader(f))


def git_source(commit, path):
    p = subprocess.run(['git', 'show', f'{commit}:{path}'], capture_output=True, check=True)
    return p.stdout


def audit(root, archives):
    root, archives = Path(root), Path(archives)
    deep = next((root/'bls-deep-analysis').rglob('campaign_audit.csv')).parent
    hashes = {r['path'].split('/adaptive-threadpool-thesis/')[-1]: r['sha256'] for r in read_csv(deep/'source_hashes.csv')}
    source_checks = []
    for path in ('harness/src/runner.rs', 'harness/src/main.rs', 'experiments/common/campaign.py'):
        actual = hashlib.sha256(git_source(RUNNER_COMMIT, path)).hexdigest()
        if actual != hashes[path]:
            raise ValueError('Warmup implementation hash mismatch: '+path)
        source_checks.append(dict(path=path, commit=RUNNER_COMMIT, sha256=actual, matched=True))
    runner = git_source(RUNNER_COMMIT, 'harness/src/runner.rs').decode()
    if not all(s in runner for s in ('if custom_passes.is_some()', 'run_warmups(warmup_runs, &mut run_once);', 'for _ in 0..warmup_runs', 'run_once();')):
        raise ValueError('Unrecognized warmup implementation')
    # Read archived verification CODE as evidence; never execute it.
    with zipfile.ZipFile(archives/'bls-deep-analysis.zip') as z:
        evidence = z.read('analysis/bls_deep_exploration/extract.py')
    expected = hashes['analysis/bls_deep_exploration/extract.py']
    if hashlib.sha256(evidence).hexdigest() != expected or b"assert e['warmup_runs']==4" not in evidence:
        raise ValueError('Archived per-experiment warmup verification not authenticated')
    verification = json.loads((deep.parent/'verification.json').read_text())
    validation = json.loads((deep.parent/'validation.json').read_text())
    provenance = json.loads((deep.parent/'source_provenance.json').read_text())
    if not (verification['passed'] and validation['primary_runs']==23040 and validation['reference_runs']==2160
            and provenance['current_binary_sha256']==KNOWN_BINARY
            and not any(p.startswith(('harness/', 'Cargo')) for p in provenance['changed_files_between_acquisition_commits'])):
        raise ValueError('Incomplete archived verification chain')
    original_hashes = read_csv(deep/'input_hashes.csv')
    records = []
    for a in read_csv(deep/'campaign_audit.csv'):
        name=a['campaign']
        input_hashes={Path(r['path']).name:r['sha256'] for r in original_hashes if '/'+name+'/' in r['path'] and Path(r['path']).name in ('experiment.toml','manifest.csv','plan.csv','benchmarks.sqlite3','environment.json')}
        ok = a['validated']=='True' and a['binary_sha256']==KNOWN_BINARY and len(input_hashes)==5
        records.append(dict(campaign=name, eligible=ok, warmup_count=4 if ok else None,
            evidence_tier='archived positive per-experiment verification + source/binary identity',
            evidence='Hash-matched archived extract.py asserts experiment.warmup_runs==4 for every physical run, manifest/plan/expt identity and success. Archived validation/verification passed; runner/main/campaign source SHA256 matches c177515; same binary across saturation/deep. Originals absent: checks cannot be freshly repeated.',
            reason='Four unprofiled warmups once per invocation before measured sequence established by preserved verification chain' if ok else 'Incomplete archived warmup evidence',
            git_commit=a['git_commit'], binary_sha256=a['binary_sha256'], dirty_sessions=int(a['dirty_sessions']),
            physical_runs=int(a['runs']), randomized_blocks=int(a['blocks']),
            source=str((deep/'campaign_audit.csv').relative_to(root)), original_member_hashes=input_hashes))
    for p in sorted(root.rglob('experiment.toml')):
        d=tomllib.loads(p.read_text()); name=p.parent.name
        envpath=p.parent/'environment.json'; env=json.loads(envpath.read_text()) if envpath.exists() else {}
        warmups={int(c.get('warmup_runs',d['repetitions']['warmup_runs_per_block'])) for c in d['configurations']}
        count=next(iter(warmups)) if len(warmups)==1 else None
        dbpath=p.parent/'benchmarks.sqlite3'; mpath=p.parent/'manifest.csv'
        reasons=[]; experiments=[]; sessions=[]; manifest=read_csv(mpath) if mpath.exists() else []
        raw=p.parent/'analysis_raw_runs.csv'
        if dbpath.exists():
            with closing(sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                experiments=[dict(r) for r in db.execute('SELECT * FROM experiment')]
                sessions=[dict(r) for r in db.execute('SELECT * FROM benchmark_session')]
                runs=db.execute('SELECT count(*) FROM run').fetchone()[0]
            if {e['warmup_runs'] for e in experiments}!={4}:reasons.append('Recorded experiment warmup count differs from four')
            if not manifest or {int(m['experiment_id']) for m in manifest}!={e['id'] for e in experiments}:reasons.append('Manifest does not cover recorded invocations')
            if any('--warmup 4 ' not in e['command_line'] or (e['profile_mode']=='deep' and '--perf-passes-json' not in e['command_line']) for e in experiments):reasons.append('Command line does not verify four warmups/custom-pass execution path')
            if {s['git_commit'] for s in sessions}!={env.get('git_commit')}:reasons.append('Environment/session commit mismatch')
        else:
            runs=len(read_csv(raw)) if raw.exists() else 0
            if count==4:reasons.append('No original experiment records to verify configured warmups')
        if warmups!={4}:reasons.insert(0,'Obsolete methodology: configured warmups '+str(sorted(warmups)))
        if env.get('binary_sha256')!=KNOWN_BINARY:reasons.append('Executable not linked to preserved warmup implementation; dirty/absent source provenance is insufficient')
        if 'smoke' in str(p):reasons.append('Smoke/counter plumbing diagnostic outside performance acquisition')
        records.append(dict(campaign=name,eligible=not reasons,warmup_count=count,
            evidence_tier='original config + manifest + SQLite command/experiment + known binary' if not reasons else 'excluded',
            evidence=f"Config counts {sorted(warmups)}; SQLite invocations {len(experiments)}; manifest invocations {len(manifest)}. Known binary SHA256 links to archived source hashes; source runner executes warmup loop before profiling, once across custom passes.",
            reason='; '.join(reasons) if reasons else 'Four unprofiled warmups verified before measured sequence; same audited binary',
            git_commit=env.get('git_commit','unrecorded'), binary_sha256=env.get('binary_sha256','unrecorded'),
            dirty_sessions=sum(s['git_dirty'] for s in sessions) if sessions else None,
            physical_runs=runs,randomized_blocks=int(d['repetitions']['blocks']),source=str(p.relative_to(root)),
            original_member_hashes={x.name:digest(x) for x in (p,mpath,envpath) if x.exists()}))
    # Preparation invocations are separate acquisitions, never pool with measured campaigns.
    for p in sorted(root.rglob('preparation.sqlite3')):
        with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            counts=db.execute('SELECT group_concat(distinct warmup_runs) FROM experiment').fetchone()[0]
            runs=db.execute('SELECT count(*) FROM run').fetchone()[0]
            commits=db.execute('SELECT group_concat(distinct git_commit) FROM benchmark_session').fetchone()[0]
        records.append(dict(campaign='preparation:'+str(p.relative_to(root)),eligible=False,warmup_count=counts,
            evidence_tier='preparation only',evidence='Original SQLite experiment records',reason='Preparation invocation, zero warmups; not randomized performance campaign',git_commit=commits,
            binary_sha256='unrecorded',physical_runs=runs,randomized_blocks=0,source=str(p.relative_to(root))))
    return dict(policy='Exactly four complete unprofiled executions once per process, before the measured sequence. No inference from campaign dates. Positive archived verification chains accepted and explicitly distinguished from fresh SQLite checks; no unauthenticated warmup=4 labels.',
        procedure='Standard profile: four run_once() calls before measurement loop. Custom deep: same four calls once before both PMU passes; pass order alternates by block. Later measured repeats/passes follow previous measured work; exactly four discarded warmups does not mean exactly four total prior executions for every repeat.',
        source_checks=source_checks,archived_verifier_sha256=expected,records=records,
        limitations=['The recorded source commit alone is insufficient for dirty sessions. Known binary/source chain is required.',
                    'Archived deep/saturation originals are absent. Eligibility relies on preserved positive per-experiment verification, not newly inspected original manifests. Original member hashes are retained for future recovery.',
                    'Four executions do not demonstrate thermal/cache/frequency stationarity. Order and repeat effects must still be diagnosed.'])


def write_audit(result, output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'warmup_eligibility.json').write_text(json.dumps(result,indent=2)+'\n')
    rows=[dict(r,original_member_hashes=json.dumps(r.get('original_member_hashes',{}),sort_keys=True)) for r in result['records']]
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with (output/'warmup_eligibility.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    counts=Counter(r['eligible'] for r in rows)
    lines=['# Warmup eligibility audit','',result['policy'],'',result['procedure'],'',
        f"Eligible campaign acquisitions: {counts[True]}; excluded acquisitions (including preparation): {counts[False]}.",
        f"Eligible physical runs: {sum(r['physical_runs'] for r in rows if r['eligible']):,}; eligible campaign-scoped randomized blocks: {sum(r['randomized_blocks'] for r in rows if r['eligible']):,}.",'',
        'Evidence and reasons for every acquisition are in [CSV](warmup_eligibility.csv) and [JSON](warmup_eligibility.json).','']
    lines+=['- '+s for s in result['limitations']]
    (output/'WARMUP_AUDIT.md').write_text('\n'.join(lines)+'\n')
