#!/usr/bin/env python3
"""Run the predeclared 3-split x 4-history-size matrix without result-driven choices."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    parser.add_argument('--out', required=True, help='Empty external directory for 12 independent runs')
    parser.add_argument('--provenance', required=True)
    parser.add_argument('--seed', type=int, default=20261012)
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.out).resolve()
    if out == root or root in out.parents:
        parser.error('Output must be outside the source tree.')
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error('Output directory must be empty.')
    frozen_paths = {'benchmark.py': root / 'experiments' / 'benchmark.py',
                    'structural_features.py': root / 'experiments' / 'structural_features.py',
                    'engine.js': root / 'dist' / 'engine.js',
                    'dataset': Path(args.data), 'provenance': Path(args.provenance)}
    frozen_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in frozen_paths.items()}
    configurations = [{'name': f'{split}_h{cap or "all"}', 'split': split, 'max_enrollment': cap}
                      for split in ('time', 'time_grouped', 'group_problem') for cap in (3, 5, 10, None)]
    plan = {'protocol': 'predeclared-3split-4history-5baseline-v1', 'seed': args.seed,
            'dataset_sha256': hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
            'runner_sha256': hashlib.sha256((root / 'experiments' / 'benchmark.py').read_bytes()).hexdigest(),
            'matrix_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'frozen_artifact_hashes': frozen_hashes, 'min_enrollment': 3, 'negatives': 3, 'target_fpr': .05, 'bootstrap': 500,
            'baselines': ['character_tfidf', 'raw_token_tfidf', 'normalized_token_tfidf', 'lexical_12', 'raw_cst_tfidf'],
            'synthetic_engineering_test': args.synthetic, 'configurations': configurations}
    (out / 'matrix_plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    runs = []
    for configuration in configurations:
        changed = [name for name, path in frozen_paths.items() if hashlib.sha256(path.read_bytes()).hexdigest() != frozen_hashes[name]]
        if changed:
            (out / 'matrix_results.json').write_text(json.dumps({'status': 'stopped_source_or_data_changed', 'changed_artifacts': changed, 'runs': runs}, indent=2) + '\n')
            print('Matrix stopped: frozen source/data changed; do not mix these runs.', file=sys.stderr)
            return 2
        command = [sys.executable, str(root / 'experiments' / 'benchmark.py'),
                   '--data', str(Path(args.data).resolve()), '--out', str(out / configuration['name']),
                   '--provenance', str(Path(args.provenance).resolve()), '--seed', str(args.seed),
                   '--split', configuration['split'], '--min-enrollment', '3', '--negatives', '3',
                   '--target-fpr', '.05', '--bootstrap', '500', '--lexical', '--structural']
        if configuration['max_enrollment'] is not None:
            command += ['--max-enrollment', str(configuration['max_enrollment'])]
        if args.synthetic:
            command.append('--synthetic')
        completed = subprocess.run(command, capture_output=True, text=True)
        (out / (configuration['name'] + '.stdout.log')).write_text(completed.stdout)
        (out / (configuration['name'] + '.stderr.log')).write_text(completed.stderr)
        report = out / configuration['name'] / 'results.json'
        result = json.loads(report.read_text()) if report.exists() else {'status': 'failed', 'reason': 'No result artifact; inspect stderr log.'}
        runs.append(dict(configuration, exit_code=completed.returncode, result=result))
        (out / 'matrix_results.json').write_text(json.dumps({'status': 'running', 'runs': runs}, indent=2, allow_nan=False) + '\n')
        print(json.dumps({'configuration': configuration['name'], 'exit_code': completed.returncode, 'status': result['status']}), flush=True)
    summary = {'status': 'completed_all_predeclared_attempts', 'runs': runs}
    (out / 'matrix_results.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    # A completed protocol can contain scientific refusals; this is not a claim of success.
    return 0 if all(r['exit_code'] == 0 or r['result']['status'] == 'refused' for r in runs) else 2


if __name__ == '__main__':
    raise SystemExit(main())
