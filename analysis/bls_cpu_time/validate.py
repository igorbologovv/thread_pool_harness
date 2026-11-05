"""Validate Stage 1 numerical identities, figure coverage and source immutability."""
from pathlib import Path
import argparse
import json
import math
from collections import defaultdict

import numpy as np
from .inputs import FACTORS_US
from analysis.bls_throughput.run import prepare,complete


def validate(output,archives,extracted):
    output=Path(output)
    prepare(Path(archives),Path(extracted))
    result=json.loads((output/'results/results.json').read_text())
    assert 'regressions' not in result, 'Stage 1 must contain no fitted models'
    records=defaultdict(list)
    keys=set()
    for r in result['observations']:
        key=(r['campaign'],r['run_id'])
        assert key not in keys,'Duplicate physical run'
        keys.add(key)
        expected=r['cpu_counter_value']*FACTORS_US[r['cpu_counter_unit']]/r['completed_work_units']
        assert math.isclose(r['cpu_us_per_wu'],expected,rel_tol=1e-12),'CPU unit/denominator mismatch'
        records[(r['campaign'],r['scheduler'],r['perf_pass'])].append(r)
    for c in result['conditions']:
        rows=records[(c['acquisition'],c['scheduler'],c['stratum']['perf_pass'])]
        y=[r['cpu_us_per_wu'] for r in rows]
        np.testing.assert_allclose(y,c['diagnostics']['raw'],rtol=1e-12)
        blocks=defaultdict(list)
        for r in rows:blocks[r['block']].append(r['cpu_us_per_wu'])
        assert len(blocks)==c['n_blocks']
        assert len(rows)==c['diagnostics']['descriptive']['n']
        assert math.isclose(c['estimate'],float(np.mean([np.mean(v) for v in blocks.values()])),rel_tol=1e-12)
        assert math.isclose(c['diagnostics']['descriptive']['variance'],float(np.var(y,ddof=1)),rel_tol=1e-10)
        assert all(math.isfinite(v) for v in c['ci95']) and c['ci95'][0]<=c['ci95'][1]
        assert sum(c['diagnostics']['histogram']['counts'])==len(rows)
    completion=json.loads((output/'completion.json').read_text())
    from experiments.common.statistical_analysis import load_csv
    mapping=load_csv(output/'tables/condition_figure_map.csv')
    assert {r['condition_id'] for r in mapping}=={c['condition_id'] for c in result['conditions']}
    pngs=list((output/'figures').glob('*.png'))
    for p in pngs:assert complete(p.with_suffix('')),'Incomplete figure pair '+str(p)
    assert len(pngs)==completion['figure_pairs'],'Unexpected/unindexed figure files'
    assert len(list((output/'figures').glob('*.svg')))==len(pngs)
    completion.update(source_archives_and_extracted_members_unchanged=True,
                      cpu_conversion_identities_validated=True,descriptive_and_block_mean_identities_validated=True,
                      sample_variances_and_ci_bounds_validated=True,physical_run_ids_unique=True,
                      all_condition_figures_present=True)
    (output/'completion.json').write_text(json.dumps(completion,indent=2)+'\n')
    print(json.dumps(completion,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('analysis/bls_cpu_time/distributions'))
    p.add_argument('--archives',type=Path,default=Path('b_thesis'))
    p.add_argument('--extracted',type=Path,default=Path('analysis/bls_throughput/extracted'))
    a=p.parse_args();validate(a.output,a.archives,a.extracted)
