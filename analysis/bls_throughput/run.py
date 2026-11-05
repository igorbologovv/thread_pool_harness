"""Reproduce throughput-only analysis of b_thesis archives; never run benchmarks."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing
import tomllib

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from experiments.common.statistical_analysis import Design, analyze, load_csv, load_sqlite, regress, normalize_by_baseline, export
from experiments.common.statistical_analysis.core import correlation
from experiments.common.statistical_analysis.exporting import clean
from experiments.common.statistical_analysis.io import csv_write
from experiments.common.statistical_analysis.plotting import plot_estimates, plot_regression
from .archive_inputs import extract, digest

LABELS = {'rayon': 'Rayon', 'bevy': 'Bevy', 'mpmc-sp0': 'MPMC Spin(0)', 'mpmc-ab': 'MPMC Agave',
          'rtrb-sp0-b1': 'RTRB b1', 'rtrb-sp0-b2': 'RTRB b2'}
MAPPING = {'threadance__spin-then-park__q64__s0': 'mpmc-sp0',
           'threadance__agave-backoff__q64': 'mpmc-ab',
           'threadance-hot-first-rtrb__spin-then-park__q64__b1__s0': 'rtrb-sp0-b1',
           'threadance-hot-first-rtrb__spin-then-park__q64__b2__s0': 'rtrb-sp0-b2',
           **{s: s for s in LABELS}}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe(value):
    return re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value))


def prepare(archives, extracted):
    if not extracted.exists():
        return extract(archives, extracted)
    inventory = json.loads((extracted/'inventory.json').read_text())
    for archive in inventory:
        require(digest(archives/archive['archive']) == archive['sha256'], 'Archive changed since extraction')
        root = extracted/archive['archive'].replace('.tar.gz', '').replace('.zip', '')
        for member in archive['selected']:
            require(digest(root/member['name']) == member['sha256'], f'Extracted input changed: {member["name"]}')
    return inventory


def collect(root, *, enrich=None, eligibility=None):
    from .eligibility import audit
    eligibility = eligibility or audit(root, Path("b_thesis"))
    allowed = {r["campaign"]: r for r in eligibility["records"] if r["eligible"]}
    rows, sources, excluded, duplicates = [], [], [r for r in eligibility["records"] if not r["eligible"]], []
    seen = {}
    def add(r, source, original):
        key = (r['campaign'], r['run_id'])
        if key in seen:
            previous = seen[key]
            require(all(str(previous[k]) == str(r[k]) for k in ('perf_pass', 'throughput', 'run_index', 'block')), f'Conflicting duplicate {key}')
            duplicates.append(dict(campaign=key[0], run_id=key[1], retained=previous['source'], excluded=str(source)))
            return
        require(r['campaign'] in allowed, 'Unaudited campaign rejected')
        require(r['warmups'] == 4, 'Non-four-warmup data rejected')
        r['warmup_evidence_tier'] = allowed[r['campaign']]['evidence_tier']
        r['binary_sha256'] = allowed[r['campaign']]['binary_sha256']
        r['source'] = str(source.relative_to(root))
        require(np.isfinite(r['throughput']) and r['throughput'] > 0, 'Invalid throughput')
        require(abs(r['throughput'] - r['work_units'] * 1000/r['elapsed_ms']) < 1e-6, 'Throughput disagrees with WU/elapsed')
        r['block_local'] = int(r['block'])
        r['block'] = r['campaign'] + ':' + str(r['block'])
        if enrich is not None:
            r.update(enrich(r, original, source))
        seen[key] = r
        rows.append(r)

    # The only later full-sweep data in these archives: raw physical-run exports.
    rawpath = next((root/'bls-deep-analysis').rglob('raw_runs.csv'))
    audits = {r['campaign']: r for r in load_csv(next((root/'bls-deep-analysis').rglob('campaign_audit.csv')))}
    for r in load_csv(rawpath):
        a = audits[r['campaign']]
        if r['campaign'] not in allowed:
            continue
        add(dict(cohort='deep-final' if r['study'] == 'bls-normalized-deep-final' else 'saturation-recheck',
                 campaign=r['campaign'], workers=int(r['workers']), mode=r['mode'], scheduler=r['scheduler'],
                 configuration=r['scheduler'], offered_rate=float(r['rate']), nominal_load=float(r['load_label']),
                 work_units=int(r['n']), block=int(r['block']), order_in_block=int(r['order']), global_order=int(r['global_order']),
                 run_id=int(r['run_id']), experiment_id=int(r['experiment_id']), run_index=int(r['run_index']),
                 perf_pass=r['perf_pass'], pass_run_index=int(r['pass_run_index']), started_ns=int(r['started_ns']),
                 throughput=float(r['throughput']), elapsed_ms=float(r['elapsed_ms']),
                 warmups=allowed[r['campaign']]['warmup_count'], git_commit=a['git_commit'], binary_sha256=a['binary_sha256'],
                 dataset_sha256=a['dataset_sha256'], dirty_sessions=int(a['dirty_sessions']),
                 data_tier='archived per-run CSV; underlying SQLite absent'), rawpath, r)
    for campaign, a in audits.items():
        if campaign not in allowed:
            continue
        sources.append(dict(campaign=campaign, cohort='deep-final' if 'deep-final' in campaign else 'saturation-recheck',
                            expected_runs=int(a['runs']), blocks=int(a['blocks']), source=str(rawpath.relative_to(root)),
                            original_sqlite_available=False, archived_validation=a['validated']))

    for path in sorted(root.rglob('experiment.toml')):
        folder = path.parent
        d = tomllib.loads(path.read_text())
        name = folder.name
        if name not in allowed:
            continue
        if 'smoke' in str(folder):
            excluded.append(dict(campaign=name, reason='Smoke/counter plumbing diagnostic, one block; excluded from performance inference'))
            continue
        is_sqlite = (folder/'benchmarks.sqlite3').is_file()
        raw = folder/'analysis_raw_runs.csv'
        if not is_sqlite and not raw.is_file():
            excluded.append(dict(campaign=name, reason='No physical per-run measurements'))
            continue
        if 'normalized-analysis' in str(folder): cohort = 'legacy-paced'
        elif 'throughput-results' in str(folder): cohort = 'saturation-original'
        elif 'lowload' in name: cohort = 'transition-lowload'
        elif 'transition-w2-sweep' in name: cohort = 'transition-sweep'
        elif 'boost-transition' in name:
            wave = re.search(r'wave(\d+)', name)[1]
            cohort = 'boost-transition-wave' + wave
        elif 'boost-coherence' in name:
            phase = re.search(r'phase-(\d+)-(on|off)', name)
            cohort = 'power-phase-' + phase[1] + '-' + phase[2]
        else:
            raise ValueError(f'Unclassified campaign {name}')
        if is_sqlite:
            with closing(sqlite3.connect((folder/'benchmarks.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
                require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', f'Bad SQLite {folder}')
                require(not db.execute('PRAGMA foreign_key_check').fetchall(), f'Foreign key failure {folder}')
            measurements = load_sqlite(folder/'benchmarks.sqlite3', manifest=folder/'manifest.csv')
            source = folder/'benchmarks.sqlite3'
            # CSV is an alternative representation of these same physical runs.
            if raw.exists():
                by_id = {int(r['run_id']): r for r in measurements}
                exported = load_csv(raw)
                require(len(exported) == len(measurements), 'SQLite/CSV run counts differ')
                for r in exported:
                    require(abs(float(r['throughput']) - by_id[int(r['run_id'])]['throughput']) < 1e-8, 'SQLite/CSV disagreement')
                duplicates.append(dict(campaign=name, run_id='all', retained=str(source.relative_to(root)),
                                       excluded=str(raw.relative_to(root)), n_runs=len(exported)))
            extra = folder/'thread_diagnostic_raw_runs.csv'
            if extra.exists():
                by_id = {int(r['run_id']): r for r in measurements}
                exported = load_csv(extra)
                require(len(exported) == len(measurements), 'Thread CSV/SQLite count mismatch')
                require(all(abs(float(r['throughput'])-by_id[int(r['run_id'])]['throughput']) < 1e-8 for r in exported), 'Thread CSV/SQLite mismatch')
                duplicates.append(dict(campaign=name, run_id='all', retained=str(source.relative_to(root)),
                                       excluded=str(extra.relative_to(root)), n_runs=len(exported)))
        else:
            measurements = load_csv(raw)
            source = raw
        manifest = load_csv(folder/'manifest.csv') if (folder/'manifest.csv').exists() else []
        m_lookup = {(r['block'], r['configuration']): r for r in manifest}
        config_ids = {c['id']: c for c in d['configurations']}
        for r in measurements:
            if is_sqlite:
                require(r['success'] == 1 and r['completed_work_units'] == r['work_units'], f'Failed/incomplete run {name}/{r["run_id"]}')
            config = r['configuration']
            m = m_lookup.get((str(r['block']), config), {})
            order = int(r['order_in_block'])
            power_path = folder.parent/'power_before.json'
            power = json.loads(power_path.read_text()) if power_path.exists() else {}
            add(dict(cohort=cohort, campaign=name, workers=int(d['workload']['workers']), mode=d['delivery']['mode'],
                     scheduler=MAPPING[config], configuration=config, block=int(r['block']),
                     offered_rate=float(d['delivery'].get('arrival_rates', [0])[0]),
                     nominal_load=float(re.search(r'load(\d+)', name)[1])/10000 if 'load' in name else 0,
                     order_in_block=order, global_order=int(m.get('global_order') or (int(r['block'])-1)*len(config_ids)+order),
                     run_id=int(r['run_id']), experiment_id=int(r.get('experiment_id') or m.get('experiment_id') or 0),
                     run_index=int(r['run_index']), perf_pass=r['perf_pass'], pass_run_index=int(r['pass_run_index']),
                     started_ns=int(r.get('started_unix_ns') or 0), throughput=float(r['throughput']),
                     elapsed_ms=float(r['elapsed_ms']), work_units=int(d['workload']['work_units']),
                     warmups=int(d['repetitions']['warmup_runs_per_block']),
                     git_commit=r.get('session.git_commit', ''), boost=power.get('boost', 'unrecorded'),
                     data_tier='original SQLite' if is_sqlite else 'archived per-run CSV; underlying SQLite absent'), source, r)
        npass = len(d.get('perf', {}).get('passes', [])) or 1
        expected = d['repetitions']['blocks']*len(config_ids)*d['repetitions']['measured_runs_per_block']*npass
        require(len(measurements) == expected, f'Incomplete campaign {name}: {len(measurements)}/{expected}')
        sources.append(dict(campaign=name, cohort=cohort, expected_runs=expected,
                            blocks=d['repetitions']['blocks'], repeats_per_pass=d['repetitions']['measured_runs_per_block'],
                            source=str(source.relative_to(root)), original_sqlite_available=is_sqlite))
    counts = Counter(r['campaign'] for r in rows)
    for source in sources:
        require(counts[source['campaign']] == source['expected_runs'], 'Deep-export campaign counts disagree with archived audit')
    # Validate complete condition/block/repeat cells, not merely total row counts.
    cells = defaultdict(list)
    for r in rows:
        cells[(r['campaign'], r['scheduler'], r['perf_pass'], r['block'])].append(r['pass_run_index'])
    for key, indices in cells.items():
        require(sorted(indices) == list(range(len(indices))), f'Missing/duplicate physical repeat {key}')
    aggregate = next((root/'bls-thesis-results').rglob('capacity_estimates.csv'))
    references = defaultdict(list)
    for r in rows:
        if r['cohort'] == 'saturation-recheck':
            references[(r['workers'], r['scheduler'])].append(r['throughput'])
    for r in load_csv(aggregate):
        require(abs(float(r['finite_batch_capacity_wu_s']) - np.mean(references[(int(r['workers']), r['scheduler'])])) < 1e-7,
                'Aggregate archived capacity disagrees with underlying raw runs')
    return rows, sources, excluded, duplicates


def run_statistics(rows, bootstrap, seed, *, metric='throughput', x='offered_rate'):
    results = []
    by_campaign = defaultdict(list)
    for r in rows:
        by_campaign[r['campaign']].append(r)
    # Each load is acquired in its own campaign: block #1 at different loads is
    # NOT a paired block. Analyze each acquisition before combining estimates.
    for i, (campaign, records) in enumerate(sorted(by_campaign.items())):
        r = records[0]
        has_time = all(x['started_ns'] > 0 for x in records)
        design = Design(metric, x, block='block', repeat='run_index',
                        strata=('cohort', 'workers', 'mode', 'campaign'), order='order_in_block',
                        time='started_ns' if has_time else None, seed=seed+i, bootstrap_replicates=bootstrap)
        result = analyze(records, design)
        for c in result['conditions']:
            source = [x for x in records if x['scheduler'] == c['scheduler'] and x['perf_pass'] == c['stratum']['perf_pass']]
            axis = [x['global_order'] for x in source]
            c['temporal']['sequence'] = dict(column='global_order', axis=axis, values=[record[metric] for record in source],
                                             spearman_rho=correlation(axis, [record[metric] for record in source]))
            c['acquisition'] = c['stratum'].pop('campaign')
            c['work_units'] = source[0]['work_units']
            c['repeats_per_block'] = len(source)//c['n_blocks']
            c['diagnostics']['warnings'].append('Load levels were acquired in separate campaigns; block numbers across loads are not paired. Load effects may be confounded with acquisition time.')
        results.append(result)
    design = asdict(Design(metric, x, seed=seed, bootstrap_replicates=bootstrap))
    design['strata'] = ['cohort', 'workers', 'mode']
    return dict(design=design, strata=['cohort', 'workers', 'mode', 'perf_pass'], observations=rows,
                conditions=[c for result in results for c in result['conditions']],
                warnings=['Per-run archived CSVs cannot be re-audited against absent original SQLite files.',
                          'CIs resample independent whole randomized blocks within each acquisition; temporal dependence between blocks is not corrected.'])


def assess_models(result, baseline_lookup):
    regress(result, max_degree=3)
    for model in result['regressions']:
        if model['status'] != 'ok':
            model['adequacy'] = 'inconclusive: insufficient X levels'
            continue
        reference = baseline_lookup[(model['stratum']['workers'], model['scheduler'])]['estimate'] if result['design']['metric'] == 'throughput' else 100
        errors = [abs(r['residual']) for r in model['nested_cv']]
        rmse = model['nested_cv_rmse']
        model['cv_rmse_percent_baseline'] = 100*rmse/reference if rmse is not None else None
        model['max_cv_error_percent_baseline'] = 100*max(errors)/reference if errors else None
        model['adequacy_rule'] = 'Exploratory practical screen: nested CV RMSE <=5% of baseline and every held-out error <=10%; not a formal lack-of-fit test.'
        if rmse is None:
            model['adequacy'] = 'inconclusive: no nested CV'
        elif rmse/reference > .05 or max(errors)/reference > .10:
            model['adequacy'] = 'inadequate predictive accuracy'
        else:
            model['adequacy'] = 'passes practical CV screen; not proof of correct model'
        if min(model['curve']['y']) < 0:
            model['adequacy'] = 'inadequate: negative fitted throughput'
        model['warnings'].append('Separate load campaigns confound load with acquisition time. Nested CV does not establish causal load response or future-host accuracy.')


def flatten(result):
    stats, models, cv, residuals = [], [], [], []
    for c in result['conditions']:
        d = c['diagnostics']
        stats.append(dict(**c['stratum'], scheduler=c['scheduler'], offered_rate=c['x'] if c['stratum']['mode'] != 'all-at-once' else None, acquisition=c['acquisition'],
                          work_units=c['work_units'], estimate=c['estimate'], ci95_low=c['ci95'][0], ci95_high=c['ci95'][1],
                          n_blocks=c['n_blocks'], repeats_per_block=c['repeats_per_block'], **d['descriptive'],
                          dip_statistic=d['dip']['statistic'], dip_p_value=d['dip']['p_value'], dip_status=d['dip']['status'],
                          n_iqr_flags=len(d['outlier_indices']),
                          **{label+'_spearman_rho': t['spearman_rho'] for label,t in c['temporal'].items()},
                          warnings=' | '.join(d['warnings'])))
    for m in result.get('regressions', []):
        prefix = dict(**m['stratum'], scheduler=m['scheduler'])
        models.append(dict(**prefix, status=m['status'], adequacy=m['adequacy'],
                           parameters=json.dumps(m.get('model')), selection_cv=json.dumps(m.get('selection_cv')),
                           nested_cv_rmse=m.get('nested_cv_rmse'), cv_rmse_percent_baseline=m.get('cv_rmse_percent_baseline'),
                           max_cv_error_percent_baseline=m.get('max_cv_error_percent_baseline'), warnings=' | '.join(m['warnings'])))
        cv.extend(dict(**prefix, **r) for r in m.get('nested_cv', []))
        residuals.extend(dict(**prefix, **r) for r in m.get('residuals', []))
    return stats, models, cv, residuals


def complete(path):
    from PIL import Image
    try:
        with Image.open(Path(str(path)+'.png')) as image:
            image.verify()
        with Path(str(path)+'.svg').open('rb') as stream:
            stream.seek(-20, 2)
            return b'</svg>' in stream.read()
    except (OSError, ValueError, SyntaxError):
        return False


def save(fig, path, *, bbox_inches='tight'):
    import os
    import tempfile
    for extension in ('png', 'svg'):
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.'+extension, delete=False) as tmp:
            temp = Path(tmp.name)
        try:
            fig.savefig(temp, dpi=125, bbox_inches=bbox_inches)
            os.replace(temp, Path(str(path)+'.'+extension))
        finally:
            temp.unlink(missing_ok=True)
    plt.close(fig)


def distribution_pages(conditions, directory, basename, baseline_lookup):
    """Four precomputed diagnostic panels per condition; no refitting in plots."""
    files = []
    for page, start in enumerate(range(0, len(conditions), 6)):
        selected = conditions[start:start+6]
        name = f'{basename}-p{page+1}'
        if complete(directory/name):
            files.append(name+'.png')
            continue
        fig, axes = plt.subplots(len(selected), 4, figsize=(17, 2.5*len(selected)), squeeze=False)
        for c, row in zip(selected, axes):
            d = c['diagnostics']; h = d['histogram']
            row[0].stairs(h['counts'], h['edges'], fill=True, alpha=.4)
            twin = row[0].twinx()
            if d['kde']['x']:
                twin.plot(d['kde']['x'], d['kde']['density'], color='tab:orange')
            twin.set_yticks([])
            row[0].set_ylabel('Count')
            label = f"Offered={c['x']:g} WU/s" if c['stratum']['mode'] != 'all-at-once' else 'All-at-once saturation; offered X not applicable'
            row[0].set_title(f"{label} | n={d['descriptive']['n']}, B={c['n_blocks']}\ndip p={d['dip']['p_value']:.3g} (exploratory)", fontsize=9)
            row[1].step(d['ecdf']['x'], d['ecdf']['probability'], where='post')
            row[1].set_ylabel('ECDF')
            # Relative distribution is a positive rescaling: provide both scales.
            capacity = baseline_lookup[(c['stratum']['workers'], c['scheduler'])]['estimate']
            for ax in row[:2]:
                ax.set_xlabel('Achieved throughput (WU/s)')
                top = ax.secondary_xaxis('top', functions=(lambda y, cap=capacity: y*100/cap, lambda p, cap=capacity: p*cap/100))
                top.set_xlabel('Relative throughput (%)', fontsize=8)
            for ax, label in zip(row[2:], ('order', 'time' if 'time' in c['temporal'] else 'sequence')):
                t = c['temporal'][label]
                axis = t['axis']
                xlabel = t['column']
                if label == 'time':
                    first_ns = min(axis)
                    axis = [(v-first_ns)/1e9 for v in axis]
                    xlabel = 'Seconds since first run in condition'
                ax.scatter(axis, t['values'], s=9, alpha=.7)
                rho = t['spearman_rho']
                ax.set(xlabel=xlabel, ylabel='Achieved WU/s', title=f"Rank correlation={rho:.2f}" if rho is not None else 'Correlation unavailable')
            row[2].text(.02,.03, f"IQR flags retained: {len(d['outlier_indices'])}", transform=row[2].transAxes, fontsize=8)
        first = selected[0]
        fig.suptitle(f"{first['stratum']} | {LABELS[first['scheduler']]}\nRaw distributions / randomized order / chronology. Dependent dip p-values are exploratory; nonsignificance does not prove unimodality.", fontsize=10)
        fig.tight_layout(rect=(0,0,1,.95))
        name = f'{basename}-p{page+1}'
        save(fig, directory/name)
        files.append(name+'.png')
    return files


def render_group(payload):
    facet, cells, absolute, relative, capacities, directory = payload
    stratum = dict(facet)
    key = f"{stratum['cohort']}-w{stratum['workers']}-{stratum['mode']}-{stratum['perf_pass']}"
    key = safe(key)
    # All distributions for the group precede its final plots in the index.
    images = []
    for scheduler in sorted({c['scheduler'] for c in cells}):
        subset = sorted((c for c in cells if c['scheduler'] == scheduler), key=lambda c:c['x'])
        images += distribution_pages(subset, directory, '01-diagnostics-'+key+'-'+scheduler, capacities)
    residual_images = []
    for model in absolute.get('regressions', []):
        if model['stratum'] == stratum and model['status'] == 'ok':
            name = '02-residuals-'+key+'-'+model['scheduler']
            if complete(directory/name):
                residual_images.append(name+'.png')
                continue
            fig = plot_regression(model)
            fig.suptitle(model['scheduler']+' | '+model['adequacy'], fontsize=10)
            save(fig, directory/name)
            residual_images.append(name+'.png')
    final_images = []
    if stratum['mode'] != 'all-at-once':
        for result, kind, ylabel in [(absolute, 'absolute', 'Absolute throughput (WU/s)'), (relative, 'relative', 'Relative throughput (% of own saturation baseline)')]:
            name='03-final-'+key+'-'+kind
            if complete(directory/name):
                final_images.append(name+'.png')
                continue
            fig = plot_estimates(result, stratum=stratum)
            ax = fig.axes[0]
            matching = sorted((m for m in result.get('regressions', []) if m['stratum'] == stratum and m['status'] == 'ok'), key=lambda m:m['scheduler'])
            # plot_estimates emits model curves in scheduler order.
            curves = [line for line in ax.lines if len(line.get_xdata()) == 250]
            for curve, model in zip(curves, matching):
                if not model['adequacy'].startswith('passes'):
                    curve.set_linestyle('--')
                    curve.set_alpha(.45)
            ax.set(xlabel='Offered load: scheduled arrival rate (WU/s; points slightly offset)', ylabel=ylabel,
                   title=f"{stratum['workers']} workers | {stratum['mode']} | {stratum['cohort']} | {stratum['perf_pass']}")
            if kind == 'relative': ax.axhline(100, color='gray', lw=.7, ls=':')
            fig.text(.5,-.02,'Whole-block 95% CIs; relative CIs include baseline uncertainty. Dashed curves fail or lack CV validation.', ha='center', fontsize=8)
            name='03-final-'+key+'-'+kind
            save(fig, directory/name)
            final_images.append(name+'.png')
    return dict(stratum=stratum, diagnostics=images, residuals=residual_images, final=final_images)


def figures(absolute, relative, capacities, output):
    directory = output/'figures'; directory.mkdir(exist_ok=True)
    groups = defaultdict(list)
    for c in absolute['conditions']:
        groups[tuple(c['stratum'].items())].append(c)
    index = []
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context
    tasks = []
    for facet, cells in sorted(groups.items(), key=repr):
        stratum = dict(facet)
        def subset(result):
            return dict(design=result['design'],
                        conditions=[c for c in result['conditions'] if c['stratum'] == stratum],
                        regressions=[m for m in result.get('regressions', []) if m['stratum'] == stratum])
        tasks.append((facet, cells, subset(absolute), subset(relative), capacities, directory))
    with ProcessPoolExecutor(max_workers=3, mp_context=get_context('spawn')) as executor:
        for i, group in enumerate(executor.map(render_group, tasks)):
            index.append(group)
            print(f"Figures {i+1}/{len(groups)}: {group['stratum']}", flush=True)
    (output/'figure_index.json').write_text(json.dumps(index, indent=2)+'\n')
    sections = ['# Throughput diagnostic and figure index', '',
                'Each acquisition cohort, worker count, delivery pattern and perf pass is separate.',
                'For each group, distribution and temporal diagnostics appear before residuals and final figures.',
                'Relative raw distributions are the same observations on the upper percentage scale; their shape and dip p-values are invariant to positive scaling.', '']
    for group in index:
        sections += ['## '+str(group['stratum']), '']
        for kind in ('diagnostics', 'residuals', 'final'):
            for image in group[kind]:
                sections += [f'![{image}](figures/{image})', '', f'[SVG](figures/{image[:-4]}.svg)', '']
    (output/'FIGURES.md').write_text('\n'.join(sections))


def main():
    """Use the ordered, audited reanalysis driver for both metric outputs."""
    from .reanalyze import main as reanalyze
    reanalyze()


if __name__ == '__main__':
    main()
