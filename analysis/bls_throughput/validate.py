"""Audit completed numerical/figure deliverables without changing source data."""
from collections import defaultdict
import argparse
import json
import math
from pathlib import Path
from datetime import datetime, timezone

from .archive_inputs import digest


def validate(output, archives=None):
    output = Path(output)
    absolute = json.loads((output/'absolute/results.json').read_text())
    relative = json.loads((output/'relative/results.json').read_text())
    index = json.loads((output/'figure_index.json').read_text())
    from .run import complete, safe
    from experiments.common.statistical_analysis.io import csv_write
    expected = set()
    for group in index:
        for kind in ('diagnostics', 'residuals', 'final'):
            for name in group[kind]:
                expected.add(name)
                if not complete(output/'figures'/Path(name).stem):
                    raise ValueError(f'Missing/incomplete figure pair: {name}')
    cells = defaultdict(list)
    for c in absolute['conditions']:
        cells[(tuple(c['stratum'].items()), c['scheduler'])].append(c)
    mapping = []
    for (facet, scheduler), group in cells.items():
        stratum = dict(facet)
        key = safe(f"{stratum['cohort']}-w{stratum['workers']}-{stratum['mode']}-{stratum['perf_pass']}")
        for i,c in enumerate(sorted(group, key=lambda c:c['x'])):
            name = f'01-diagnostics-{key}-{scheduler}-p{i//6+1}.png'
            if name not in expected:
                raise ValueError(f'Condition lacks diagnostics: {facet}/{scheduler}/{c["x"]}')
            mapping.append(dict(**stratum, scheduler=scheduler,
                                offered_rate=c['x'] if stratum['mode'] != 'all-at-once' else None,
                                diagnostic_png='figures/'+name, row_in_page=i%6+1))
    csv_write(output/'tables/condition_figure_map.csv', mapping)
    reference = {(c['stratum']['workers'],c['scheduler']):c['estimate'] for c in absolute['conditions'] if c['stratum']['cohort']=='saturation-recheck'}
    absolute_cells = {(tuple(c['stratum'].items()),c['scheduler'],c['x']):c for c in absolute['conditions']}
    for c in relative['conditions']:
        source = absolute_cells[(tuple(c['stratum'].items()),c['scheduler'],c['x'])]
        expected_ratio = 100*source['estimate']/reference[c['stratum']['workers'],c['scheduler']]
        if not math.isclose(c['estimate'], expected_ratio, rel_tol=1e-12):
            raise ValueError('Relative point estimate disagrees with matched baseline')
    for result in (absolute, relative):
        for c in result['conditions']:
            if not all(math.isfinite(x) for x in [c['estimate'], *c['ci95']]) or c['ci95'][0] > c['ci95'][1]:
                raise ValueError('Invalid estimate/CI')
    provenance = json.loads((output/'provenance.json').read_text())
    if archives is not None:
        for archive in provenance['archives']:
            if digest(Path(archives)/archive['archive']) != archive['sha256']:
                raise ValueError('Source archive changed during analysis')
    actual_png = set(p.name for p in (output/'figures').glob('*.png'))
    if actual_png != expected:
        raise ValueError('Unindexed or missing PNG files')
    result = dict(completed_utc=datetime.now(timezone.utc).isoformat(), physical_runs=len(absolute['observations']),
                  absolute_conditions=len(absolute['conditions']), relative_conditions=len(relative['conditions']),
                  condition_diagnostic_mappings=len(mapping), figure_pairs=len(expected),
                  diagnostic_pages=sum(len(g['diagnostics']) for g in index),
                  residual_figures=sum(len(g['residuals']) for g in index),
                  final_figures=sum(len(g['final']) for g in index), source_archives_unchanged=archives is not None,
                  checks='All indexed PNG/SVG pairs complete; all conditions mapped; relative estimates match own baseline; finite ordered CIs.')
    (output/'completion.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('analysis/bls_throughput/output'))
    parser.add_argument('--archives',type=Path,default=Path('b_thesis'))
    args=parser.parse_args()
    validate(args.output,args.archives)
