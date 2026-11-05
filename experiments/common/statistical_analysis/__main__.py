"""Read-only inputs and exclusive new output directory for reproducible exports."""
import argparse
from dataclasses import fields
import hashlib
from pathlib import Path

from . import Design, analyze, load_csv, load_sqlite, regress
from .exporting import export



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    for name in ('metric', 'x'):
        parser.add_argument('--' + name, required=True)
    for f in fields(Design):
        if f.name in ('metric', 'x', 'strata'):
            continue
        parser.add_argument('--' + f.name.replace('_', '-'), default=f.default,
                            type=int if f.name in ('seed', 'bootstrap_replicates') else str)
    parser.add_argument('--strata', nargs='*', default=[])
    parser.add_argument('--max-degree', type=int, default=3)
    parser.add_argument('--no-figures', action='store_true')
    args = parser.parse_args()
    options = {f.name: getattr(args, f.name) for f in fields(Design)}
    for name in ('block', 'repeat', 'order', 'time'):
        if options[name] == 'none':
            options[name] = None
    options['strata'] = tuple(options['strata'])
    rows = load_csv(args.input) if args.input.suffix.lower() == '.csv' else load_sqlite(args.input, manifest=args.manifest)
    result = analyze(rows, Design(**options))
    with args.input.open('rb') as source:
        source_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    result['source'] = {'input': str(args.input.resolve()),
                        'input_sha256': source_hash,
                        'manifest': str(args.manifest.resolve()) if args.manifest else None}
    regress(result, max_degree=args.max_degree)
    export(result, args.output, figures=not args.no_figures)
    print(f'Exported {len(result["conditions"])} conditions to {args.output}')


if __name__ == '__main__':
    main()
