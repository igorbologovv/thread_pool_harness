"""Quantitative reports and independent numerical/input/figure consistency checks."""
from collections import Counter,defaultdict
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np
from scipy import stats
from diptest import diptest

from .archive_inputs import digest
from .run import complete,prepare
from experiments.common.statistical_analysis import Design,analyze,normalize_by_baseline
from experiments.common.statistical_analysis.io import csv_write
from experiments.common.statistical_analysis.regression import predict


def load(path):return json.loads(path.read_text())


def fmt(c,scale=1):
    lo,hi=c['ci95'];return f"{scale*c['estimate']:.2f} [{scale*lo:.2f}, {scale*hi:.2f}]"


def contrasts(cpu,output):
    results=[]
    for cohort,low,high in [('transition-lowload',25,35),('transition-sweep',50,70)]:
        numerator=[c for c in cpu['conditions'] if c['stratum']['cohort']==cohort and np.isclose(c['nominal_load_pct'],high)]
        baseline=[c for c in cpu['conditions'] if c['stratum']['cohort']==cohort and np.isclose(c['nominal_load_pct'],low)]
        r=dict(design=cpu['design'],observations=[],conditions=numerator)
        out=normalize_by_baseline(r,dict(conditions=baseline),match=('workers','mode','cohort','perf_pass'),seed=20261105+300000+int(high),metric='cpu_ratio_pct')
        for c in out['conditions']:
            original=next(v for v in numerator if v['scheduler']==c['scheduler'] and v['stratum']==c['stratum'])
            b=next(v for v in baseline if v['scheduler']==c['scheduler'] and v['stratum']==c['stratum'])
            results.append(dict(cohort=cohort,scheduler=c['scheduler'],**{k:v for k,v in c['stratum'].items() if k!='cohort'},
                low_nominal_load_pct=low,high_nominal_load_pct=high,low_actual_offered_pct=b['offered_load_pct'],high_actual_offered_pct=original['offered_load_pct'],
                low_estimate_us_per_wu=b['estimate'],high_estimate_us_per_wu=original['estimate'],percent_change=c['estimate']-100,
                ci95_low=c['ci95'][0]-100,ci95_high=c['ci95'][1]-100,
                note='Exploratory cross-campaign contrast, independent block bootstrap; acquisition-time confounding remains. Pointwise CI, no multiplicity correction for contrasts.'))
    csv_write(output/'tables/transition_contrasts.csv',results)
    return results


def model_summary(modelsets):
    lines=['## Predictive models','',
        'Degrees 0–3 use existing centered/scaled polynomial least squares, selected by leave-one-X-level-out MSE. Residual degrees of freedom are required in every training fold. Nested outer X-level CV repeats degree and model-family selection. All repeats/blocks at the omitted level are withheld; campaign-scoped blocks are not falsely paired across loads. The outer boundary folds necessarily test extrapolation and are reported separately from interior interpolation folds. Display curves are restricted to the observed range.', '',
        'Throughput alternatives fit min(slope×load, capacity), estimating capacity using training levels only. CPU-load alternatives use a continuous hinge at the physically specified own-capacity level, 100%; it is eligible only when each CV training fold has at least two levels on both sides. No knot is invented from an unexplained secondary mode. With six levels and sparse above-capacity coverage, this hinge is often unidentifiable in CV. A separate three-parameter natural cubic regression spline uses one knot at the median of the training X levels, zero second derivative at the training boundaries, and linear continuation solely for omitted-boundary validation. Knots/bases are rebuilt inside every fold; the spline is a predictive candidate, not a physical explanation of the transition. Model-family selection is nested. Free-knot or highly flexible splines are not supported by these sparse designs. Failed/unsupported models retain empirical points.', '',
        'The accuracy screen is an exploratory engineering tolerance: nested family-selection RMSE ≤5% of mean response (100 percentage points for relative throughput), with worst error ≤10%. Passing does not prove model correctness or causal response. Training R² is not used. CPU-vs-achieved-throughput has correlated measurement error in both axes; its fits are descriptive and never accepted as reliable continuous models.', '',
        '| Relationship | Groups | Polynomial fits | Polynomial-only screen passes | Family-selection screen passes | Selected families |','|---|---:|---:|---:|---:|---|']
    for name,models in modelsets.items():
        good=[m for m in models if m['status']=='ok'];passed=sum(m.get('predictive_comparison',{}).get('passes_accuracy_screen',False) for m in good)
        families=Counter(m.get('predictive_comparison',{}).get('selected',{}).get('family') for m in good)
        poly_pass=0
        for m in good:
            p=m['predictive_comparison'];scale=p['reference_scale'];cv=m.get('nested_cv',[])
            if cv and m['nested_cv_rmse']/scale<=.05 and max(abs(v['residual']) for v in cv)/scale<=.10 and name!='cpu_achieved':poly_pass+=1
        lines.append(f'| {name} | {len(models)} | {len(good)} | {poly_pass} | {passed} | {dict(families)} |')
    lines+=['',
        'Conditional polynomial and selected natural-spline pointwise bootstrap bands, with polynomial coefficient intervals, resample whole blocks, preserving shared blocks across schedulers and independent acquisitions across load levels. Relative response bands include the shared independent saturation denominator. Degree/model selection uncertainty is not included; offered-load X percentages condition on the estimated denominator. Bands describe fitted mean response, not prediction limits or simultaneous coverage. They are not forced to intersect empirical CIs. No bands are claimed for noisy achieved-throughput X or sparse worker-count fits.', '',
        'Worker-count curves compare acquired scenarios at 2/4/8/16 workers. At fixed nominal load labels, offered WU/s and sometimes batch length change with workers; this is a confounded scenario comparison. Polynomial values at 3/6/12 workers are not experimentally validated hardware configurations. All model failures and inconclusive groups remain in models.json and CSV tables.', '',
        '[Full coefficients, residual diagnostics, family selection and conditional uncertainty](models.json); [model table](tables/models.csv); [nested omitted-level predictions](tables/cross_validation.csv); [polynomial block residuals](tables/residuals.csv); [selected-alternative block residuals](tables/alternative_residuals.csv).']
    return lines


def alternative_diagnostics(modelsets,output):
    from experiments.common.statistical_analysis.validation import physical_predict
    from experiments.common.statistical_analysis.core import correlation
    rows=[]
    for relationship,models in modelsets.items():
        for m in models:
            p=m.get('predictive_comparison',{})
            if 'alternative_model' not in p:continue
            model=p['alternative_model'];res=[]
            for r in m['residuals']:
                fitted=float(physical_predict(model,r['x']))
                res.append(dict(r,fitted=fitted,residual=float(r['observed']-fitted)))
            p['alternative_residuals']=res
            p['alternative_residual_diagnostics']=dict(abs_residual_fitted_spearman=correlation([r['fitted'] for r in res],[abs(r['residual']) for r in res]),
                residual_x_spearman=correlation([r['x'] for r in res],[r['residual'] for r in res]),
                note='Descriptive clustered block residuals; no IID normality/heteroscedasticity p-values.')
            rows += [dict(relationship=relationship,scheduler=m['scheduler'],**m['stratum'],fit_family=p['selected']['family'],**r) for r in res]
    (output/'models.json').write_text(json.dumps(modelsets,indent=2,allow_nan=False)+'\n')
    if rows:csv_write(output/'tables/alternative_residuals.csv',rows)
    from .visuals import alternative_residual_figures
    alternative_residual_figures(modelsets,output)


def report(outputs):
    # Repair/recheck derived empirical figures with exact decimal filenames.
    from .visuals import performance
    performance(load(outputs[0]/'absolute/results.json'),load(outputs[0]/'relative/results.json'),load(outputs[1]/'results/results.json'),outputs)
    from .visuals import model_figures
    for output in outputs:
        model_figures(load(output/'models.json'),output)
        alternative_diagnostics(load(output/'models.json'),output)
    absolute=load(outputs[0]/'absolute/results.json');relative=load(outputs[0]/'relative/results.json');cpu=load(outputs[1]/'results/results.json')
    comparisons=contrasts(cpu,outputs[1])
    provenance=load(outputs[0]/'provenance.json');a=load(outputs[0]/'warmup_eligibility.json')
    counts=provenance['counts'];cohorts=Counter(r['cohort'] for r in absolute['observations'])
    common=[f"**{counts['physical_runs']:,} eligible physical runs; {counts['campaigns']} campaigns; {counts['conditions']} conditions; {counts['randomized_blocks']:,} campaign-scoped randomized blocks.** Every included invocation uses exactly four discarded unprofiled warmups before its measured sequence. No raw measurements/archives were changed, no benchmarks rerun, and no PDF generated.", '',
        '## Eligibility and coverage','',
        '[Warmup audit](WARMUP_AUDIT.md), [every included/excluded acquisition and its evidence](warmup_eligibility.csv), [full evidence and original member hashes](warmup_eligibility.json), [archive/member provenance](provenance.json).', '',
        '52 obsolete campaigns (48 paced + 4 original saturation) are excluded for one warmup. The later power/boost campaigns and smoke acquisitions are excluded because their dirty binaries cannot be tied to the preserved warmup implementation; preparation databases are excluded separately. No old saturation result substitutes for a recheck.', '',
        'The 48 deep-final and 4 saturation-recheck campaigns have a preserved positive per-experiment verification chain: hash-matched archived extraction code asserted warmup_runs==4, manifest/plan/session identity, success and completion for all runs, with successful validation/verification records and original input hashes. The archived runner/main/campaign source hashes exactly match git c177515; the same binary SHA256 links those sources to these acquisitions. The recorded deep-final commit is absent locally, but its warmup implementation is recovered by exact content hashes. These are archived verification, not fresh original-database verification. Original configs/manifests/SQLite are absent and those checks cannot be rerun. The 18 transition campaigns additionally have original config/manifest/command/experiment records and the same binary, checked freshly. Dirty sessions remain disclosed; dates never determine eligibility.', '',
        'The warmup loop runs four complete run_once() calls before standard measurements; custom deep passes share those four warmups once per invocation. Later measured repeats/passes follow earlier measured work. Four warmups are a protocol criterion and do not establish stationarity.', '',
        '| Cohort | Physical runs | Conditions | Campaigns | Blocks |','|---|---:|---:|---:|---:|']
    for cohort,n in sorted(cohorts.items()):
        rs=[r for r in absolute['observations'] if r['cohort']==cohort];cs=[c for c in absolute['conditions'] if c['stratum']['cohort']==cohort]
        common.append(f"| {cohort} | {n:,} | {len(cs)} | {len({r['campaign'] for r in rs})} | {len({r['block'] for r in rs})} |")
    common+=['',
        'Six scheduler configurations remain separate: Rayon, Bevy, MPMC Spin(0), MPMC Agave, RTRB backlog 1 and backlog 2. Deep-final covers workers 2/4/8/16 and fixed/bursty arrivals in separate cpu-cache/l1-branches perf passes; rechecks are separate all-at-once standard-perf measurements. Transition cohorts only cover 2 workers. Each physical condition has 40 measurements (20 blocks ×2 repeats), except rechecks with 90 (30×3). Total unique blocks across the whole dataset count each scheduler/pass sharing a randomized block once. Blocks are treated as independent by the bootstrap; this assumption is diagnosed, not guaranteed.', '',
        '## Axes and inference','',
        'Absolute throughput is completed WU / elapsed seconds. Scheduled offered WU/s is an input; achieved throughput is an outcome and includes final drain. Relative throughput = 100×achieved WU/s / own-scheduler verified finite-batch saturation-recheck mean at the same worker count. Offered load (%) = 100×scheduled offered WU/s / that same denominator. [Every denominator and its CI](tables/load_denominators.csv) is explicit. Nominal labels, originally based on preliminary rates, remain metadata; Rayon never supplies another scheduler’s denominator. Same normalized X across schedulers generally means different absolute rates; point comparisons are matched scheduled acquisitions, not interpolated equivalence.', '',
        f"Relative-throughput results cover {len(relative['observations']):,} paced measurements in {len(relative['conditions'])} conditions; the 24 saturation conditions supply the denominator and have no paced offered-load X.", '',
        'The reference is 2500-WU finite-batch completion throughput under standard perf, not demonstrated indefinitely sustainable capacity. Paced batches vary; transition studies use 256 WU. Numerator and recheck acquisitions are independent; ratio CIs propagate uncertainty in both. Percent X values and fitted X are plug-in quantities conditional on the measured denominator. Perf passes/cohorts remain distinct even though reference normalization uses a separate standard-perf acquisition.', '',
        'CIs are pointwise 95% percentile intervals for equal-block means, with 5000 whole-block resamples. Each acquisition gets independent draws; paired schedulers share block draws within that acquisition. No Gaussian distribution is assumed. Outliers are retained. Independent exchangeable blocks are required; serial dependence and cross-campaign host changes can invalidate coverage. Different loads were acquired in different campaigns: load and time are confounded.', '',
        '[Distribution diagnostic report](DIAGNOSTIC_REPORT.md) was exported and figures completed before any regression; [stage execution timestamps](execution_log.json) preserve that order. Standard IID dip p-values and their BH/BY/Holm adjustments are exploratory under repeated-block dependence. Nonsignificance never establishes unimodality. Bandwidth-sensitive peaks are not confirmed population modes; unexplained shape remains unexplained.','']
    for i,(output,result) in enumerate(zip(outputs,[absolute,cpu])):
        lines=['# Corrected BLS throughput reanalysis' if i==0 else '# Corrected BLS CPU efficiency reanalysis','']+common
        if i==0:
            lines+=['## Saturation-recheck throughput','',
                'Mean [95% block-bootstrap CI], WU/s; standard perf, all-at-once, 30 blocks ×3 repeats.','',
                '| Workers | Scheduler | Verified saturation WU/s [95% CI] |','|---:|---|---:|']
            for c in sorted((c for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck'),key=lambda c:(c['stratum']['workers'],c['scheduler'])):
                lines.append(f"| {c['stratum']['workers']} | {c['scheduler']} | {fmt(c)} |")
            lines+=['','## Achieved throughput under the highest acquired offered load','',
                'Deep-final fixed arrivals, cpu-cache pass, nominal 200% scenario. Own-scheduler offered percentages differ slightly; all other passes/modes and loads remain in the tables/figures.','',
                '| Workers | Scheduler | Scheduled WU/s | Actual offered % | Achieved WU/s [95% CI] | Relative throughput % [95% CI] |','|---:|---|---:|---:|---:|---:|']
            for c in sorted((c for c in absolute['conditions'] if c['stratum']['cohort']=='deep-final' and c['stratum']['mode']=='fixed-arrivals' and c['stratum']['perf_pass']=='cpu-cache' and np.isclose(c['nominal_load_pct'],200)),key=lambda c:(c['stratum']['workers'],c['scheduler'])):
                rel=next(v for v in relative['conditions'] if v['acquisition']==c['acquisition'] and v['scheduler']==c['scheduler'] and v['stratum']==c['stratum'])
                lines.append(f"| {c['stratum']['workers']} | {c['scheduler']} | {c['offered_rate']:.2f} | {c['offered_load_pct']:.2f} | {fmt(c)} | {fmt(rel)} |")
        else:
            verified=sum(r['cpu_unit_evidence'].startswith('verified') for r in cpu['observations'])
            lines+=['## CPU counter integrity','',
                f"CPU time/WU = measured task-clock × unit-to-µs factor / completed WU. {verified:,} physical runs have task-clock units, stored totals, successful capture status and raw perf JSON freshly cross-checked. The remaining {len(cpu['observations'])-verified:,} CSV-only runs use the archived millisecond convention: original units, capture scope and status cannot be freshly inspected. These absolute CPU scales remain qualified by that explicit inference. No available counter was removed for distribution shape. [Per-run counter audit](tables/counter_audit.csv).",'',
                'Task-clock represents observed attached CPU execution time, including the pacer/worker scope recorded by the profiler; it is neither wall latency nor isolated scheduler overhead. Process attachment and explicitly per-TID acquisitions are not mixed: the latter were excluded by the eligibility audit. Frequency/temperature/affinity telemetry is insufficient to explain residual shifts.', '',
                '## Two-worker RTRB transition','',
                'Fixed arrivals, cpu-cache pass. The actual own-capacity offered percentages are used for figures. Mean [95% CI], µs/WU. Detailed 25–100% plots exist separately for every cohort/arrival mode/pass, together with uncropped full-domain plots.', '',
                '| Cohort | Scheduler | Nominal % | Actual offered % | CPU µs/WU [95% CI] | Raw dip p (exploratory) |','|---|---|---:|---:|---:|---:|']
            for c in cpu['conditions']:
                s=c['stratum']
                if s['workers']==2 and s['mode']=='fixed-arrivals' and s['perf_pass']=='cpu-cache' and c['scheduler'].startswith('rtrb') and 25<=c['nominal_load_pct']<=100:
                    lines.append(f"| {s['cohort']} | {c['scheduler']} | {c['nominal_load_pct']:g} | {c['offered_load_pct']:.2f} | {fmt(c)} | {c['diagnostics']['dip']['p_value']:.4g} |")
            lines+=['','Within the 256-WU low-load cohort, RTRB CPU/WU falls between nominal 25% and 35%; within the separate sweep cohort it rises from nominal 50% to 70%. The response is therefore not a single monotone efficiency transition over 25–100%. The same broad pattern appears in the independent deep-final points, but batch length and acquisition-time differences preclude pooling them or assigning a physical mechanism.','',
                '| Cohort / contrast | Scheduler | CPU change % [95% CI] |','|---|---|---:|']
            for r in comparisons:
                if r['mode']=='fixed-arrivals' and r['perf_pass']=='cpu-cache' and r['scheduler'].startswith('rtrb'):
                    lines.append(f"| {r['cohort']}, {r['low_nominal_load_pct']:g}→{r['high_nominal_load_pct']:g}% nominal | {r['scheduler']} | {r['percent_change']:.2f} [{r['ci95_low']:.2f}, {r['ci95_high']:.2f}] |")
            lines+=['','These are exploratory pointwise contrasts between independent load acquisitions, with whole-block ratio bootstrap; acquisition-time confounding remains. No causal change point or queue/frequency explanation is established. [All scheduler/mode/pass transition contrasts](tables/transition_contrasts.csv).']
        cs=result['conditions']
        tests={k:sum(c['diagnostics']['dip'].get(k) is not None and c['diagnostics']['dip'][k]<.05 for c in cs) for k in ('p_value','p_bh','p_by','p_holm')}
        flags=sum(len(c['diagnostics']['outlier_indices']) for c in cs)
        lag=sum(abs(c['investigation']['associations']['block_mean_lag1_rho'] or 0)>=.5 for c in cs)
        highicc=sum((c['investigation']['associations']['block_icc1'] or 0)>=.5 for c in cs)
        lines+=['','## Observed distributions','',
            f"Raw dip p<0.05 in {tests['p_value']}/{len(cs)} conditions; BH-adjusted {tests['p_bh']}, BY-adjusted {tests['p_by']}, Holm-adjusted {tests['p_holm']}. These counts are exploratory because IID dip calibration does not hold for dependent repeats. No detected secondary peaks survive doubling Scott bandwidth; this neither proves unimodality nor invalidates the raw dip screen. {flags:,} physical observations receive IQR outlier flags and remain in all calculations. {highicc} conditions have descriptive block ICC≥0.5; {lag} have |block-mean lag-one rank correlation|≥0.5. Block shifts and possible temporal dependence materially qualify inference.", '',
            'Every physical observation appears in a distribution; none was excluded for shape. The diagnostic report examines repeat-stratified and block-centered shapes, randomized scheduler position, chronological drift, pass order and the two-worker transition examples. Between-block clusters are observable, but neither four-warmup artifacts nor a CPU-frequency/cache mechanism is established. Weak monotone correlations cannot rule out intermittent temporal effects. [Serial-block CI sensitivity](tables/dependence_sensitivity.csv) retains this uncertainty explicitly.']
        if i==1:
            lines+=['','## CPU cost at the highest acquired load','',
                'Deep-final fixed arrivals, cpu-cache pass, nominal 200% scenario. Absolute µs/WU assumes the documented archived millisecond convention.','',
                '| Workers | Scheduler | Actual offered % | CPU µs/WU [95% CI] |','|---:|---|---:|---:|']
            for c in sorted((c for c in cpu['conditions'] if c['stratum']['cohort']=='deep-final' and c['stratum']['mode']=='fixed-arrivals' and c['stratum']['perf_pass']=='cpu-cache' and np.isclose(c['nominal_load_pct'],200)),key=lambda c:(c['stratum']['workers'],c['scheduler'])):
                lines.append(f"| {c['stratum']['workers']} | {c['scheduler']} | {c['offered_load_pct']:.2f} | {fmt(c)} |")
        lines+=['']+model_summary(load(output/'models.json'))
        if i==1:
            lines+=['','## Two-worker CPU model validation detail','',
                'Fixed arrivals, cpu-cache. RMSE and maximum error are in µs/WU; family/degree choice is repeated inside each outer omitted-level fold. Passing is the declared exploratory accuracy screen, not proof of a physical law.','',
                '| Cohort | Scheduler | Polynomial degree | Polynomial nested RMSE | Selected family | Family-selection nested RMSE | Worst held-out error | Screen |','|---|---|---:|---:|---|---:|---:|---|']
            for m in load(output/'models.json')['cpu_load']:
                s=m['stratum']
                if m['status']=='ok' and s['workers']==2 and s['mode']=='fixed-arrivals' and s['perf_pass']=='cpu-cache' and m['scheduler'].startswith('rtrb'):
                    p=m['predictive_comparison']
                    lines.append(f"| {s['cohort']} | {m['scheduler']} | {m['model']['degree']} | {m['nested_cv_rmse']:.2f} | {p['selected']['family']} | {p['nested_cv_rmse']:.2f} | {p['max_absolute_error']:.2f} | {'passes' if p['passes_accuracy_screen'] else 'fails'} |")
        lines+=['','## Figures and reproduction','',
            '[Organized PNG/SVG figure atlas](FIGURES.md). Distribution pages cover every condition; every suspected shape receives block detail pages. Empirical performance figures are unconnected estimates with 95% CIs and small display-only offsets. Fitted curves appear separately with validation failures disclosed.', '',
            'Run from the repository root with new output paths (existing statistical exports are refused):','',
            '```sh','python -m venv --system-site-packages .venv-analysis','.venv-analysis/bin/python -m pip install -r experiments/common/statistical_analysis/requirements.txt',
            'OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-mpl .venv-analysis/bin/python -m analysis.bls_throughput.reanalyze --stage all --archives b_thesis --extracted analysis/bls_throughput/extracted --throughput-output /tmp/bls-throughput-reproduced --cpu-output /tmp/bls-cpu-reproduced --bootstrap 5000 --curve-bootstrap 2000 --seed 20261105','```','',
            'Stages can run separately with `--stage distributions`, `relative-distributions`, `performance`, `models`, `report`, in that order. Distribution rendering resumes from complete figure pairs; numerical exports are immutable. Archive/member SHA256s are verified before reuse. Seed 20261105; per-campaign seed = seed + sorted campaign index; normalization seed = seed +100000; model/curve seed = seed +200000 + relationship index; transition-contrast seed = seed +300000 + higher nominal percentage. Dip p-values use deterministic package tables, not Monte Carlo null sampling.', '',
            'Software versions used:','', '```json',json.dumps(provenance['software'],indent=2),'```','',
            'Analysis source hashes, exact invocation and environment package versions are recorded alongside the outputs. The statistical meaning/limits of block inference and polynomial fits are documented in the shared framework README. Verification checks all PNG/SVG pairs, condition coverage, original archive/member hashes, derived units, descriptive statistics, dip recalculation, estimates, denominator arithmetic, independent CI recalculation, model domains, held-out predictions and local report links.']
        (output/'SUMMARY.md').write_text('\n'.join(lines)+'\n')
        if (output/'test_results.json').exists():
            with (output/'SUMMARY.md').open('a') as f:
                f.write('\n[Executed test results](test_results.json) · [full test log](TESTS.log) · [exact dependency pins](requirements.lock.txt)\n')
        # Capture exact analysis sources rather than a misleading clean git HEAD.
        sources=list(Path('analysis/bls_throughput').glob('*.py'))+list(Path('analysis/bls_cpu_time').glob('*.py'))+list(Path('experiments/common/statistical_analysis').glob('*.py'))
        csv_write(output/'analysis_source_hashes.csv',[dict(path=str(p),sha256=digest(p)) for p in sorted(sources)])
        (output/'requirements.lock.txt').write_text('\n'.join(f'{p}=={v}' for p,v in provenance['software'].items() if p!='python')+'\n')
        (output/'software_versions.json').write_text(json.dumps(provenance['software'],indent=2)+'\n')
        (output/'REPRODUCE.sh').write_text('# Run from repository root; pass fresh output directories.\nOMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MPLCONFIGDIR=/tmp/bls-mpl .venv-analysis/bin/python -m analysis.bls_throughput.reanalyze --stage all --archives b_thesis --extracted analysis/bls_throughput/extracted --throughput-output /tmp/bls-throughput-reproduced --cpu-output /tmp/bls-cpu-reproduced --bootstrap 5000 --curve-bootstrap 2000 --seed 20261105\n')


def validate(outputs,archives,extracted):
    prepare(archives,extracted) # original archive AND extracted member SHA256 checks
    absolute=load(outputs[0]/'absolute/results.json');relative=load(outputs[0]/'relative/results.json');cpu=load(outputs[1]/'results/results.json')
    eligibility=load(outputs[0]/'warmup_eligibility.json');allowed={r['campaign'] for r in eligibility['records'] if r['eligible']}
    chronology=defaultdict(lambda:[10**30,0])
    for r in absolute['observations']:
        interval=chronology[(r['campaign'],r['block_local'])];interval[0]=min(interval[0],r['started_ns']);interval[1]=max(interval[1],r['started_ns'])
    for campaign in allowed:
        ordered=sorted((b,v) for (c,b),v in chronology.items() if c==campaign)
        assert all(a[1][1]<b[1][0] for a,b in zip(ordered,ordered[1:])), 'Block index does not match time order'
    for result,metric in [(absolute,'throughput'),(cpu,'cpu_us_per_wu'),(relative,'relative_throughput_pct')]:
        rs=result['observations'];assert {r['campaign'] for r in rs}<=allowed
        assert all(r['warmups']==4 for r in rs)
        assert len({(r['campaign'],r['run_id']) for r in rs})==len(rs)
        lookup=defaultdict(list)
        for r in rs:lookup[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
        for c in result['conditions']:
            records=lookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
            y=np.array([r[metric] for r in records]);d=c['diagnostics']['descriptive']
            assert len(y)==d['n'];assert np.isclose(np.mean(y),d['mean']);assert np.isclose(np.var(y,ddof=1),d['variance'])
            assert np.isclose(np.std(y,ddof=1),d['sd']);assert np.isclose(np.median(y),d['median'])
            q1,q3=np.quantile(y,[.25,.75]);assert np.isclose(q1,d['q1']) and np.isclose(q3,d['q3']) and np.isclose(q3-q1,d['iqr'])
            assert np.flatnonzero((y<q1-1.5*(q3-q1))|(y>q3+1.5*(q3-q1))).tolist()==c['diagnostics']['outlier_indices']
            assert np.isclose(stats.skew(y,bias=False),d['skewness_bias_corrected'])
            assert np.isclose(stats.kurtosis(y,bias=False),d['excess_kurtosis_bias_corrected'])
            dip,p=diptest(y);assert np.isclose(dip,c['diagnostics']['dip']['statistic']);assert np.isclose(p,c['diagnostics']['dip']['p_value'])
            assert sum(c['diagnostics']['histogram']['counts'])==len(y)
            assert c['n_blocks']==len({r['block'] for r in records})
            means=[np.mean([r[metric] for r in records if r['block']==b['block']]) for b in c['block_means']]
            assert np.allclose(means,[b['mean'] for b in c['block_means']]);assert np.isclose(np.mean(means),c['estimate'])
            assert c['ci95'][0]<=c['ci95'][1]
        assert sum(c['diagnostics']['descriptive']['n'] for c in result['conditions'])==len(rs)
    for r in cpu['observations']:
        from analysis.bls_cpu_time.inputs import cpu_per_wu
        assert np.isclose(cpu_per_wu(r['cpu_counter_value'],r['cpu_counter_unit'],r['completed_work_units']),r['cpu_us_per_wu'])
        assert np.isclose(r['throughput'],r['completed_work_units']*1000/r['elapsed_ms'])
        assert np.isclose(r['offered_load_pct'],100*r['offered_rate']/r['saturation_baseline_wu_s'])
    # Independently rerun first acquisition CI with same complete scheduler set
    # and seed; this checks that no individual repeats were used as independent.
    from .run import run_statistics
    config=load(outputs[0]/'provenance.json')
    first=sorted(allowed)[0];records=[r for r in absolute['observations'] if r['campaign']==first]
    recalculated=run_statistics(records,config['bootstrap_replicates'],config['seed'])
    for c in recalculated['conditions']:
        target=next(v for v in absolute['conditions'] if v['acquisition']==first and v['scheduler']==c['scheduler'] and v['stratum']==c['stratum'])
        assert np.allclose(c['ci95'],target['ci95'])
    # Independent numerical oracle for all absolute/CPU mean CIs, directly from
    # physical rows. Reproduce the recorded RNG schedule, without calling analyze.
    full_ci_checks=0
    for result,metric in [(absolute,'throughput'),(cpu,'cpu_us_per_wu')]:
        grouped=defaultdict(list)
        for r in result['observations']:grouped[r['campaign']].append(r)
        condition_map={(c['acquisition'],c['scheduler'],c['stratum']['perf_pass']):c for c in result['conditions']}
        for i,(campaign,records) in enumerate(sorted(grouped.items())):
            rng=np.random.default_rng(config['seed']+i)
            for perf_pass in sorted({r['perf_pass'] for r in records}):
                selected=[r for r in records if r['perf_pass']==perf_pass]
                blocks=sorted({r['block'] for r in selected},key=repr)
                indices=rng.integers(0,len(blocks),(config['bootstrap_replicates'],len(blocks)))
                for scheduler in sorted({r['scheduler'] for r in selected}):
                    records_s=[r for r in selected if r['scheduler']==scheduler]
                    means=np.array([np.mean([r[metric] for r in records_s if r['block']==b]) for b in blocks])
                    bounds=np.quantile(means[indices].mean(axis=1),[.025,.975])
                    target=condition_map[(campaign,scheduler,perf_pass)]
                    assert np.allclose(bounds,target['ci95'],rtol=1e-12,atol=1e-10)
                    full_ci_checks+=1
    caps={(c['stratum']['workers'],c['scheduler']):c for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck'}
    absolute_lookup={(c['acquisition'],c['scheduler'],c['stratum']['perf_pass']):c for c in absolute['conditions']}
    for c in relative['conditions']:
        a=absolute_lookup[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])];cap=caps[(c['stratum']['workers'],c['scheduler'])]
        assert np.isclose(c['estimate'],100*a['estimate']/cap['estimate']);assert c['baseline']['stratum']['cohort']=='saturation-recheck'
    # Bootstrap ratio consistency for all CIs, from absolute exported block means.
    from copy import deepcopy
    paced=dict(design=absolute['design'],observations=[],conditions=[{k:deepcopy(v) for k,v in c.items() if k!='investigation'} for c in absolute['conditions'] if c['stratum']['mode']!='all-at-once'])
    recomputed=normalize_by_baseline(paced,dict(conditions=list(caps.values())),match=('workers',),seed=config['seed']+100000)
    for a,b in zip(recomputed['conditions'],relative['conditions']):assert np.allclose(a['ci95'],b['ci95'])
    from experiments.common.campaign import make_plan
    import tomllib
    fresh_randomizations=0
    for path in Path(extracted).rglob('experiment.toml'):
        if path.parent.name not in allowed:continue
        definition=tomllib.loads(path.read_text())
        with (path.parent/'manifest.csv').open() as f:manifest=list(csv.DictReader(f))
        planned=make_plan(definition,None)
        assert len(manifest)==len(planned)
        assert all(all(str(m[k])==str(v) for k,v in r.items()) for m,r in zip(manifest,planned))
        fresh_randomizations+=1
    for output in outputs:
        modelsets=load(output/'models.json')
        for relationship,models in modelsets.items():
            for m in models:
                if m['status']!='ok':continue
                x=sorted({r['x'] for r in m['residuals']});g=m['curve']['x']
                assert g[0]==min(x) and g[-1]==max(x);assert np.allclose(predict(m['model'],g),m['curve']['y'])
                if 'alternative_model' in m.get('predictive_comparison',{}):
                    from experiments.common.statistical_analysis.validation import physical_predict
                    p=m['predictive_comparison']
                    assert np.allclose(physical_predict(p['alternative_model'],p['curve']['x']),p['curve']['y'])
                    for r in p['alternative_residuals']:
                        assert np.isclose(r['fitted'],float(physical_predict(p['alternative_model'],r['x'])))
                        assert np.isclose(r['observed']-r['fitted'],r['residual'])
                for r in m['nested_cv']:assert np.isclose(r['observed']-r['predicted'],r['residual'])
                for r in m['predictive_comparison']['nested_cv']:assert np.isclose(r['observed']-r['predicted'],r['residual'])
        # Every condition appears in a valid atlas page, with one complete pair.
        with (output/'tables/condition_figure_map.csv').open() as f:mapping=list(csv.DictReader(f))
        assert len(mapping)==len(absolute['conditions']) # same CPU availability here
        assert len({r['condition_id'] for r in mapping})==len(mapping)
        for r in mapping:assert complete((output/r['diagnostic_png']).with_suffix(''))
        # Preserve superseded derivative renders; keep active figures reproducible.
        referenced=set()
        for group in load(output/'distribution_index.json')['atlas']:referenced.update(group['files'])
        referenced.update(load(output/'distribution_index.json')['details'])
        if (output/'relative_distribution_index.json').exists():referenced.update(load(output/'relative_distribution_index.json'))
        referenced.update(v['png'] for v in load(output/'performance_index.json'))
        referenced.update(load(output/'model_index.json'))
        superseded=[]
        for png in (output/'figures').glob('*.png'):
            if png.name not in referenced:
                destination=output/'superseded_figures';destination.mkdir(exist_ok=True)
                superseded.append(png.name)
                for extension in ('png','svg'):
                    artifact=png.with_suffix('.'+extension)
                    if artifact.exists():artifact.rename(destination/artifact.name)
        if superseded:(output/'rendering_corrections.json').write_text(json.dumps(dict(reason='Decimal filename suffixes were truncated by the original renderer; original derivative renders preserved here, active figures re-exported with explicit appended extensions.',preserved_files=superseded),indent=2)+'\n')
        pngs=list((output/'figures').glob('*.png'));svgs=list((output/'figures').glob('*.svg'))
        assert len(pngs)==len(svgs)
        for p in pngs:assert complete(p.with_suffix(''))
        for path in [output/'SUMMARY.md',output/'FIGURES.md',output/'DIAGNOSTIC_REPORT.md',output/'WARMUP_AUDIT.md']:
            for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
                if '://' not in link and not link.startswith('#'):assert (path.parent/link.split('#')[0]).exists(),link
        logs=load(output/'execution_log.json');stages=[r['stage'] for r in logs]
        assert next(i for i,s in enumerate(stages) if s.startswith('3 distribution figures'))<next(i for i,s in enumerate(stages) if s.startswith('6 models'))
        validation=dict(passed=True,physical_runs=len(absolute['observations']),conditions=len(mapping),campaigns=len(allowed),randomized_blocks=len({r['block'] for r in absolute['observations']}),
            figure_pairs=len(pngs),fresh_seeded_randomization_manifests_verified=fresh_randomizations,block_chronology_verified=True,archive_and_extracted_hashes_verified=True,raw_units_and_numerics_verified=True,
            independent_ci_recalculation=True,independent_raw_mean_ci_checks=full_ci_checks,independent_ratio_ci_recalculation=True,model_domains_checked=True,
            all_condition_figures_verified=True,benchmarks_rerun=0,raw_archives_modified=0,pdf_generated=0)
        (output/'verification.json').write_text(json.dumps(validation,indent=2)+'\n')
        print('Verified',output,validation,flush=True)
