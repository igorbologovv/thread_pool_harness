#!/usr/bin/env python3
"""Regenerate the complete analysis; never collect, compile or modify benchmarks."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-only',action='store_true',help='Rebuild the PDF and validate existing derived tables/figures')
    args=parser.parse_args()
    source=Path(__file__).resolve().parent
    root=source.parents[1]
    out=root/'results/reports/bls_full'
    out.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MPLCONFIGDIR='/tmp/bls-matplotlib')
    versions={p:importlib.metadata.version(p) for p in ['numpy','pandas','scipy','matplotlib','scikit-learn','statsmodels','diptest','pymupdf','tabulate']}
    (out/'analysis_environment.json').write_text(json.dumps({'python':sys.version,'packages':versions,'bootstrap_seed':20261025,'bootstrap_replicates':20000},indent=2)+'\n')
    stages=['report','validate'] if args.report_only else ['extract','estimate','diagnostics','followup','interruptions','figures','report','validate']
    for stage in stages:
        print('\nStage:',stage,flush=True)
        subprocess.run([sys.executable,str(source/(stage+'.py'))],cwd=root,env=env,check=True)
    print('Complete:',out/'report.pdf',flush=True)

if __name__=='__main__':main()
