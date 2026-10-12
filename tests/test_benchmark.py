"""Portable engineering tests. Synthetic scores never establish real-world accuracy."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmark', ROOT / 'experiments' / 'benchmark.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def fixture(authors=6, problems=10, timed=True):
    data = []
    for problem in range(problems):
        for author in range(authors):
            data.append({'id': f's{author}_{problem}', 'author_id': f'author{author}',
                         'problem_id': f'problem{problem}',
                         'timestamp': f'2020-01-{problem + 1:02d}T00:00:00Z' if timed else None,
                         'source': 'synthetic:engineering-only', 'label': 'normal',
                         'code': f'int main() {{ int value_{author} = {problem * 100 + author}; return value_{author} + {author}; }}'})
    return data


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def data_file(self, data, name='data.jsonl'):
        path = self.base / name
        path.write_text(''.join(json.dumps(x) + '\n' for x in data), encoding='utf-8')
        return path

    def args(self, data=None, out='out', extra=()):
        path = self.data_file(fixture() if data is None else data)
        return b.parser().parse_args(['--data', str(path), '--out', str(self.base / out), '--seed', '123', '--bootstrap', '100', '--synthetic', *extra])

    def test_three_baselines_run_reproducibly(self):
        first = b.run(self.args())
        second = b.run(self.args(out='other'))
        self.assertEqual(first, second)
        for name, result in first['baselines'].items():
            self.assertEqual(result['status'], 'ok', name)
            self.assertEqual(result['model']['fit_partition'], 'train')
            self.assertLessEqual(result['dev']['fpr'], .05)
            self.assertEqual(result['test_cluster_ci']['status'], 'insufficient')
        self.assertTrue(first['synthetic_engineering_test'])
        for artifact in ('split_manifest.json', 'config.json', 'provenance.json', 'results.json', 'predictions.jsonl', 'artifact_hashes.json'):
            self.assertTrue((self.base / 'out' / artifact).is_file())
        self.assertEqual((self.base / 'out' / 'artifact_hashes.json').read_bytes(), (self.base / 'other' / 'artifact_hashes.json').read_bytes())

    def test_time_and_problem_partitions_are_disjoint(self):
        b.run(self.args())
        manifest = json.loads((self.base / 'out' / 'split_manifest.json').read_text())
        self.assertTrue(manifest['temporal_evaluation'])
        partitions = manifest['partitions']
        for left, right in (('train', 'dev'), ('train', 'test'), ('dev', 'test')):
            for field in ('id', 'problem_id', 'code_hash', 'token_hash'):
                self.assertFalse({x[field] for x in partitions[left]} & {x[field] for x in partitions[right]})
            self.assertLess(max(x['time'] for x in partitions[left]), min(x['time'] for x in partitions[right]))
        train = {}
        for x in partitions['train']:
            train.setdefault(x['author_id'], set()).add(x['problem_id'])
        self.assertTrue(all(len(ps) >= 3 for ps in train.values()))

    def test_missing_time_is_explicit_group_problem(self):
        result = b.run(self.args(fixture(timed=False)))
        self.assertEqual(result['split_mode'], 'group_problem')
        with self.assertRaises(b.InsufficientData):
            b.run(self.args(fixture(timed=False), out='refuse', extra=('--split', 'time')))

    def test_overlap_problem_quarantined_not_moved_through_time(self):
        data = fixture()
        # An existing early problem receives a distinct later submission.
        data.append(dict(data[0], id='crossing', timestamp='2020-01-10T00:00:00Z', code='int crossing = 987654321;'))
        records = b.read_records(self.data_file(data))
        parts, excluded, mode, _ = b.split_records(records, 5)
        self.assertEqual(mode, 'time')
        self.assertTrue(any(x['id'] == 'crossing' for x in excluded))
        self.assertFalse(any(r['problem_id'] == 'problem0' for rs in parts.values() for r in rs))

    def test_time_grouped_keeps_strict_time_and_problem_disjointness(self):
        data = []
        for record in fixture():
            for phase, day in enumerate((1, 11, 21)):
                data.append(dict(record, id=record['id'] + f'_phase{phase}',
                                 timestamp=f'2020-01-{day:02d}T00:00:00Z',
                                 code=record['code'] + f' int phase_marker = {phase};'))
        result = b.run(self.args(data, extra=('--split', 'time_grouped')))
        self.assertEqual(result['split_mode'], 'time_grouped')
        self.assertEqual(result['data_efficiency']['retained_fraction'], 1 / 3)
        self.assertEqual(result['data_efficiency']['exclusion_reasons']['outside_assigned_problem_time_window'], 120)
        manifest = json.loads((self.base / 'out' / 'split_manifest.json').read_text())
        self.assertTrue(manifest['temporal_evaluation'])
        parts = manifest['partitions']
        assignment = manifest['time_boundaries']['problem_assignment']
        for name, records in parts.items():
            self.assertTrue(all(assignment[r['problem_id']] == name for r in records))
        for left, right in (('train', 'dev'), ('dev', 'test'), ('train', 'test')):
            self.assertLess(max(r['time'] for r in parts[left]), min(r['time'] for r in parts[right]))
            self.assertFalse({r['problem_id'] for r in parts[left]} & {r['problem_id'] for r in parts[right]})
        # Default strict time semantics are unchanged: whole straddling problems are dropped.
        with self.assertRaises(b.InsufficientData):
            b.run(self.args(data, out='strict'))
        refused = json.loads((self.base / 'strict' / 'split_manifest.json').read_text())
        self.assertEqual(len(refused['excluded']), 180)
        self.assertEqual(refused['split_mode'], 'time')

    def test_time_grouped_requires_complete_timestamps(self):
        with self.assertRaises(b.InsufficientData):
            b.run(self.args(fixture(timed=False), extra=('--split', 'time_grouped')))

    def test_exact_and_token_clones_quarantined(self):
        data = fixture()
        data.append(dict(data[0], id='cloned', author_id='author1', problem_id='problem9', code='// comment\n' + data[0]['code'].replace(' ', '  ')))
        clean, excluded = b.remove_clones(b.read_records(self.data_file(data)))
        self.assertEqual({r['id'] for r in excluded}, {'s0_0', 'cloned'})
        self.assertNotIn('s0_0', {r['id'] for r in clean})

    def test_small_author_or_problem_samples_refused(self):
        for index, data in enumerate((fixture(authors=3), fixture(problems=5))):
            with self.assertRaises(b.InsufficientData):
                b.run(self.args(data, out=f'out{index}'))
            report = json.loads((self.base / f'out{index}' / 'results.json').read_text())
            self.assertEqual(report['status'], 'refused')
            self.assertNotIn('baselines', report)

    def test_no_test_vocabulary_or_threshold_leakage(self):
        first = b.run(self.args())
        data = fixture()
        for r in data:
            if int(r['problem_id'].replace('problem', '')) >= 8:
                r['code'] += ' int NEVER_SEEN_TEST_ONLY = 999999; '
        second = b.run(self.args(data, out='changed'))
        for name in first['baselines']:
            a, c = first['baselines'][name], second['baselines'][name]
            self.assertEqual(a['model']['vocabulary_sha256'], c['model']['vocabulary_sha256'])
            self.assertEqual(a['model']['idf_sha256'], c['model']['idf_sha256'])
            self.assertEqual(a['threshold'], c['threshold'])

    def test_known_author_normal_labels_required(self):
        data = fixture()
        data[0]['label'] = 'ai'
        with self.assertRaises(ValueError):
            b.read_records(self.data_file(data))

    def test_token_normalization_is_identifier_only(self):
        self.assertEqual(b.normalized_tokens('int first=42; // hello'), b.normalized_tokens('int other=42;'))
        self.assertNotEqual(b.normalized_tokens('int first=42;'), b.normalized_tokens('int other=43;'))
        self.assertIn('"hello world"', b.raw_tokens('const char* s="hello world";'))
        self.assertNotIn('ignored', b.raw_tokens('int x; /* ignored */'))
        self.assertIn('R"tag(// untouched)tag"', b.raw_tokens('auto x=R"tag(// untouched)tag";'))

    def test_scientific_numbers_and_continued_comments(self):
        tokens = b.raw_tokens("double x=1e+3; double y=0x1p-2; float z=.5e-1; int n=1'000;")
        for literal in ("1e+3", "0x1p-2", ".5e-1", "1'000"):
            self.assertIn(literal, tokens)
        code = "int x; // first " + "\\" + "\nignored();\nint y;"
        self.assertEqual(b.raw_tokens(code), ['int', 'x', ';', 'int', 'y', ';'])
        self.assertEqual(b.raw_tokens(code.replace('\n', '\r\n')), ['int', 'x', ';', 'int', 'y', ';'])
        self.assertEqual(b.raw_tokens('int x; /* unterminated ignored()'), ['int', 'x', ';'])
        self.assertIn('R"tag(/* literal */ \\n)tag"', b.raw_tokens('auto s=R"tag(/* literal */ \\n)tag";'))

    def test_pairs_include_true_and_seeded_wrong_claims(self):
        candidates = [{'id': 'c', 'author_id': 'a'}]
        one = b.make_pairs(candidates, ['a', 'b', 'c', 'd'], 7, 2)
        self.assertEqual(one, b.make_pairs(candidates, ['a', 'b', 'c', 'd'], 7, 2))
        self.assertEqual(len(one), 3)
        self.assertEqual(sum(x['mismatch'] == 0 for x in one), 1)
        self.assertTrue(all(p['claimed_author'] != 'a' for p in one if p['mismatch']))

    def test_threshold_strict_ties_and_zero_fpr(self):
        scores = [.1, .2, .2, .4]
        self.assertEqual(b.select_threshold(scores, 0), .4)
        self.assertEqual(b.select_threshold(scores, .25), .2)

    def test_cluster_bootstrap_has_intervals_when_supported(self):
        pairs = [dict(true_author=str(a), candidate_id=str(a), mismatch=y, score=.1 if y == 0 else .9)
                 for a in range(12) for y in (0, 1)]
        ci = b.cluster_intervals(pairs, .5, 4, 100)
        self.assertEqual(ci['status'], 'approximate')
        self.assertEqual(ci['intervals']['fpr'], [0.0, 0.0])
        self.assertEqual(ci['intervals']['tpr'], [1.0, 1.0])

    @unittest.skipUnless(shutil.which('node'), 'Node optional for lexical baseline')
    def test_optional_lexical_baseline_reports_insufficient_coverage(self):
        result = b.run(self.args(extra=('--lexical',)))
        self.assertEqual(result['baselines']['lexical_12']['status'], 'insufficient_coverage')
        self.assertIsNone(result['baselines']['lexical_12']['threshold'])
        self.assertIsNone(result['baselines']['lexical_12']['test']['fpr'])

    @unittest.skipUnless(shutil.which('node'), 'Node optional for lexical baseline')
    def test_optional_lexical_baseline_runs_covered_claims(self):
        data = fixture()
        for record in data:
            author = int(record['author_id'].replace('author', ''))
            problem = int(record['problem_id'].replace('problem', ''))
            body = ''.join('for(int i=0;i<10;i++){if(i==0){total=1;}else{total=2;}cout << total;}' for _ in range(6))
            code = f'int solve_{author}(){{int total={1000 * problem + author};' + body + 'return total;}'
            if author % 2:
                code = code.replace('if(', 'if (').replace('for(', 'for (').replace('){', ') {').replace('=1;', ' = 1;').replace('=2;', ' = 2;')
            record['code'] = code
        result = b.run(self.args(data, extra=('--lexical',)))
        habits = result['baselines']['lexical_12']
        self.assertEqual(habits['status'], 'ok')
        self.assertGreater(habits['test']['covered_pairs'], 0)
        self.assertIsNotNone(habits['threshold'])
        self.assertEqual(habits['model']['features'], 12)

    def test_enrollment_caps_are_nested_with_identical_claims(self):
        manifests, claims = {}, {}
        for cap in (3, 5, 10):
            result = b.run(self.args(out=f'cap{cap}', extra=('--max-enrollment', str(cap))))
            folder = self.base / f'cap{cap}'
            manifests[cap] = json.loads((folder / 'split_manifest.json').read_text())
            claims[cap] = [(p['candidate_id'], p['true_author'], p['claimed_author'], p['mismatch'], p['partition'], p['baseline'])
                           for p in map(json.loads, (folder / 'predictions.jsonl').read_text().splitlines())]
            self.assertLessEqual(result['enrollment']['maximum_retained'], cap)
        self.assertEqual(claims[3], claims[5])
        self.assertEqual(claims[5], claims[10])
        ids = {cap: {r['id'] for r in m['partitions']['train']} for cap, m in manifests.items()}
        self.assertTrue(ids[3] < ids[5] < ids[10])
        # Most recent historical problems are retained, never dev/test records.
        self.assertEqual({r['problem_id'] for r in manifests[3]['partitions']['train']}, {'problem3', 'problem4', 'problem5'})

    def test_missing_time_cap_uses_reproducible_hash_order(self):
        data = fixture(timed=False)
        first = b.run(self.args(data, extra=('--max-enrollment', '3')))
        second = b.run(self.args(list(reversed(data)), out='other', extra=('--max-enrollment', '3')))
        self.assertEqual(first, second)

    @unittest.skipUnless(importlib.util.find_spec('tree_sitter') and importlib.util.find_spec('tree_sitter_cpp'), 'Pinned optional CST dependencies absent')
    def test_structural_baseline_train_only_and_parse_abstention(self):
        first = b.run(self.args(extra=('--structural',)))
        original = first['baselines']['raw_cst_tfidf']
        self.assertEqual(original['status'], 'ok')
        data = fixture()
        for record in data:
            if record['id'] == 's0_8':
                record['code'] = 'int malformed( { '
            elif int(record['problem_id'].replace('problem', '')) >= 8:
                record['code'] += ' struct TestOnly { virtual void method()=0; }; '
        second = b.run(self.args(data, out='changed', extra=('--structural',)))
        changed = second['baselines']['raw_cst_tfidf']
        self.assertEqual(original['model']['vocabulary_sha256'], changed['model']['vocabulary_sha256'])
        self.assertEqual(original['model']['idf_sha256'], changed['model']['idf_sha256'])
        self.assertEqual(original['threshold'], changed['threshold'])
        self.assertLess(changed['test']['pair_coverage'], 1)
        self.assertIn('s0_8', {r['id'] for r in changed['model']['parse_rejections']})
        predictions = [json.loads(line) for line in (self.base / 'changed' / 'predictions.jsonl').read_text().splitlines()]
        rejected = [p for p in predictions if p['baseline'] == 'raw_cst_tfidf' and p['candidate_id'] == 's0_8']
        self.assertTrue(rejected)
        self.assertTrue(all(p['score'] is None and p['flagged'] is None for p in rejected))

    def test_author_aggregates_and_fixed_length_strata(self):
        pairs = [dict(true_author='first', candidate_id='short', mismatch=0, score=.9),
                 dict(true_author='first', candidate_id='medium', mismatch=0, score=.1),
                 dict(true_author='second', candidate_id='long', mismatch=0, score=None)]
        aggregate = b.author_distribution(pairs, .5)
        self.assertEqual(aggregate['normal_fpr']['maximum'], .5)
        self.assertEqual(aggregate['normal_coverage']['minimum'], 0)
        self.assertEqual(aggregate['authors_with_zero_coverage'], 1)
        self.assertEqual(aggregate['authors_with_fewer_than_20_covered_normals'], 2)
        self.assertNotIn('first', json.dumps(aggregate))
        strata = b.length_strata(pairs, .5, {'short': 99, 'medium': 100, 'long': 500})
        self.assertEqual(strata['under_100_tokens']['fpr'], 1)
        self.assertEqual(strata['100_to_499_tokens']['fpr'], 0)
        self.assertIsNone(strata['at_least_500_tokens']['fpr'])
        self.assertEqual(strata['at_least_500_tokens']['pair_coverage'], 0)

    def test_synthetic_input_requires_explicit_marker(self):
        args = self.args()
        args.synthetic = False
        with self.assertRaises(ValueError):
            b.run(args)

    def test_cli_has_no_machine_specific_path(self):
        args = self.args()
        process = subprocess.run([sys.executable, str(ROOT / 'experiments' / 'benchmark.py'), '--data', args.data,
                                  '--out', str(self.base / 'cli'), '--synthetic', '--seed', '123'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['status'], 'ok')

    def test_rejects_output_inside_source_tree(self):
        args = self.args()
        args.out = str(ROOT / 'experiments' / 'unsafe-output')
        with self.assertRaises(ValueError):
            b.run(args)


if __name__ == '__main__':
    unittest.main()
