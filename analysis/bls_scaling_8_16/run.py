#!/usr/bin/env python3
"""Reproduce the focused report without running benchmarks or modifying their inputs."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--render-only',action='store_true',help='Render and validate existing derived tables/figures')
    p.add_argument('--validate-only',action='store_true')
    args=p.parse_args()
    source=Path(__file__).resolve().parent
    env=os.environ.copy();env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MPLCONFIGDIR='/tmp/bls-matplotlib')
    stages=['validate'] if args.validate_only else ['provenance','report','validate'] if args.render_only else ['data','diagnose','analyze','figures','provenance','report','validate']
    for stage in stages:
        print('Stage:',stage,flush=True)
        subprocess.run([sys.executable,str(source/(stage+'.py'))],cwd=source.parents[1],env=env,check=True)

if __name__=='__main__':main()
