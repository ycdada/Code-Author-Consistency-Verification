#!/usr/bin/env python3
"""Reproducible author-claim verification; never compiles or executes input code."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys

import numpy as np
import scipy
import sklearn
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer, TfidfTransformer
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import normalize

VERSION = '1.1.0'
TOKENIZER_VERSION = 'python-cpp-lexical-approximation-1.1'
KEYWORDS = set('alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t class compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor xor_eq'.split())
TOKEN_RE = re.compile(r'(?P<comment>//(?:\\\r?\n|[^\r\n])*|/\*[\s\S]*?(?:\*/|\Z))|(?P<raw>(?:u8|u|U|L)?R"(?P<delim>[^ ()\\\t\r\n]{0,16})\([\s\S]*?\)(?P=delim)")|(?P<literal>(?:u8|u|U|L)?"(?:\\[\s\S]|[^"\\])*"|(?:u8|u|U|L)?\'(?:\\[\s\S]|[^\'\\])*\')|(?P<id>[^\W\d]\w*)|(?P<number>(?:\d|\.\d)(?:[eEpP][+-]|[\w.\'])*)|(?P<operator><=>|>>=|<<=|->\*|\.\.\.|::|->|\+\+|--|&&|\|\||==|!=|<=|>=|<<|>>|\+=|-=|\*=|/=|%=|&=|\|=|\^=|[^\s])', re.UNICODE)

class InsufficientData(ValueError):
    pass

def digest(value):
    return hashlib.sha256(value).hexdigest()

def tokenize(code, identifiers=False):
    """Lexical approximation: remove comments; optionally normalize non-keyword IDs.
    Literals/numbers are intentionally retained in both token baselines.
    Encounter-order normalization is NOT scope-aware C++ parsing.
    """
    names, result = {}, []
    for match in TOKEN_RE.finditer(code):
        if match.group('comment') is not None:
            continue
        token = match.group()
        if identifiers and match.group('id') and token not in KEYWORDS:
            token = names.setdefault(token, f'IDENT_{len(names)}')
        result.append(token)
    return result

def raw_tokens(code):
    return tokenize(code)

def normalized_tokens(code):
    return tokenize(code, True)

def parse_time(value):
    if value in (None, ''):
        return None
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('timestamp must be finite')
        return float(value)
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('ISO timestamps must include timezone')
    return dt.timestamp()

def read_records(path):
    records, ids = [], set()
    with Path(path).open(encoding='utf-8') as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError(f'Line {line_no}: expected a JSON object')
            for key in ('id', 'author_id', 'problem_id', 'code', 'source'):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError(f'Line {line_no}: nonempty string required: {key}')
            if item['id'] in ids:
                raise ValueError(f'Line {line_no}: duplicate record ID')
            if len(item['code']) > 500_000:
                raise ValueError(f'Line {line_no}: source exceeds 500000 characters')
            # All candidates are known-author originals, NOT AI/unknown-author samples.
            if item.get('label') not in (None, 'normal', 'human', 'original', 'clean'):
                raise ValueError(f'Line {line_no}: label is not a known-author normal sample')
            ids.add(item['id'])
            item = {k: item[k] for k in ('id', 'author_id', 'problem_id', 'code', 'source')}
            original = json.loads(line)
            item['time'] = parse_time(original.get('timestamp'))
            item['code_hash'] = digest(item['code'].replace('\r\n', '\n').replace('\r', '\n').encode())
            item['token_hash'] = digest(json.dumps(raw_tokens(item['code']), ensure_ascii=False).encode())
            if not raw_tokens(item['code']):
                raise ValueError(f'Line {line_no}: no code tokens')
            records.append(item)
    return sorted(records, key=lambda r: r['id'])

def remove_clones(records):
    """Quarantine every member of an exact or raw-token clone family.
    This conservative step uses no outcome labels and also removes within-split clones.
    """
    exact = Counter(r['code_hash'] for r in records)
    token = Counter(r['token_hash'] for r in records)
    excluded = [dict(id=r['id'], reason='exact_or_raw_token_clone') for r in records
                if exact[r['code_hash']] > 1 or token[r['token_hash']] > 1]
    excluded_ids = {r['id'] for r in excluded}
    return [r for r in records if r['id'] not in excluded_ids], excluded

def problem_assignment(problems, seed):
    problems = sorted(problems)
    np.random.default_rng(seed).shuffle(problems)
    train_n = min(len(problems) - 2, max(2, int(len(problems) * .6)))
    dev_n = min(len(problems) - train_n - 1, max(1, int(len(problems) * .2)))
    return {p: 'train' if i < train_n else 'dev' if i < train_n + dev_n else 'test'
            for i, p in enumerate(problems)}

def split_records(records, seed, mode='auto'):
    if len({r['author_id'] for r in records}) < 4:
        raise InsufficientData('Need at least 4 true authors after clone quarantine.')
    problems = sorted({r['problem_id'] for r in records})
    if len(problems) < 6:
        raise InsufficientData('Need at least 6 distinct problems after clone quarantine.')
    complete_time = all(r['time'] is not None for r in records)
    if mode in ('time', 'time_grouped') and not complete_time:
        raise InsufficientData('Strict time split requires a timestamp on every record.')
    mode = 'time' if mode == 'auto' and complete_time else ('group_problem' if mode == 'auto' else mode)
    splits = {name: [] for name in ('train', 'dev', 'test')}
    excluded = []
    if mode in ('time', 'time_grouped'):
        times = sorted(set(r['time'] for r in records))
        if len(times) < 3:
            raise InsufficientData('Need at least 3 distinct timestamps.')
        first = times[max(0, math.ceil(len(times) * .6) - 1)]
        second = times[max(1, math.ceil(len(times) * .8) - 1)]
        if mode == 'time_grouped':
            # Independently assign problems BEFORE filtering their records by time.
            # No record, feature or model outcome chooses its problem's group.
            assignment = problem_assignment(problems, seed)
            for r in records:
                zone = 'train' if r['time'] < first else 'dev' if r['time'] < second else 'test'
                if assignment[r['problem_id']] == zone:
                    splits[zone].append(r)
                else:
                    excluded.append({'id': r['id'], 'reason': 'outside_assigned_problem_time_window'})
            boundaries = {'train_end_exclusive_unix': first, 'dev_end_exclusive_unix': second,
                          'problem_assignment': assignment, 'ordering': 'train < T1 <= dev < T2 <= test'}
        else:
            # Original conservative protocol: quarantine entire straddling problems.
            groups = defaultdict(list)
            for r in records:
                groups[r['problem_id']].append(r)
            for group in groups.values():
                zones = {'train' if r['time'] <= first else 'dev' if r['time'] <= second else 'test' for r in group}
                if len(zones) != 1:
                    excluded.extend(dict(id=r['id'], reason='problem_straddles_time_boundary') for r in group)
                else:
                    splits[zones.pop()].extend(group)
            boundaries = {'train_end_unix': first, 'dev_end_unix': second}
    else:
        assignment = problem_assignment(problems, seed)
        for r in records:
            splits[assignment[r['problem_id']]].append(r)
        boundaries = None
    for split in splits.values():
        split.sort(key=lambda r: r['id'])
    return splits, excluded, mode, boundaries

def make_pairs(candidates, eligible_authors, seed, negatives):
    rng = np.random.default_rng(seed)
    pairs = []
    for record in candidates:
        author = record['author_id']
        if author not in eligible_authors:
            continue
        pairs.append({'candidate_id': record['id'], 'true_author': author,
                      'claimed_author': author, 'mismatch': 0})
        others = [a for a in eligible_authors if a != author]
        for other in rng.choice(others, min(negatives, len(others)), replace=False):
            pairs.append({'candidate_id': record['id'], 'true_author': author,
                          'claimed_author': str(other), 'mismatch': 1})
    return pairs

def select_threshold(normal_scores, target_fpr):
    # Strict score > threshold. The order statistic controls observed dev FPR;
    # it gives no distribution-free guarantee on the future population FPR.
    if not normal_scores:
        raise InsufficientData('No covered normal development claims for calibration.')
    scores = sorted(normal_scores)
    allowed = math.floor(target_fpr * len(scores))
    return scores[max(0, len(scores) - allowed - 1)]

def point_metrics(pairs, threshold):
    clean = [p for p in pairs if p.get('score') is not None]
    normal = [p for p in clean if p['mismatch'] == 0]
    wrong = [p for p in clean if p['mismatch'] == 1]
    return {'fpr': float(np.mean([p['score'] > threshold for p in normal])) if normal else None,
            'tpr': float(np.mean([p['score'] > threshold for p in wrong])) if wrong else None,
            'roc_auc': float(roc_auc_score([p['mismatch'] for p in clean], [p['score'] for p in clean])) if normal and wrong else None,
            'normal_claims': len(normal), 'wrong_author_claims': len(wrong),
            'covered_pairs': len(clean), 'total_pairs': len(pairs),
            'pair_coverage': len(clean) / len(pairs) if pairs else 0.0,
            'covered_candidates': len({p['candidate_id'] for p in clean})}

def distribution(values):
    if not values:
        return {'count': 0, 'minimum': None, 'p25': None, 'median': None, 'p75': None, 'p90': None, 'p95': None, 'maximum': None}
    quantiles = np.quantile(values, [0, .25, .5, .75, .9, .95, 1])
    return dict(count=len(values), **{name: float(value) for name, value in zip(('minimum', 'p25', 'median', 'p75', 'p90', 'p95', 'maximum'), quantiles)})

def author_distribution(pairs, threshold):
    groups = defaultdict(list)
    for pair in pairs:
        if pair['mismatch'] == 0:
            groups[pair['true_author']].append(pair)
    counts, totals, coverages, rates = [], [], [], []
    for normal in groups.values():
        covered = [p for p in normal if p.get('score') is not None]
        counts.append(len(covered))
        totals.append(len(normal))
        coverages.append(len(covered) / len(normal))
        if covered and threshold is not None:
            rates.append(float(np.mean([p['score'] > threshold for p in covered])))
    return {'author_clusters': len(groups), 'normal_fpr': distribution(rates),
            'covered_normal_count': distribution(counts), 'normal_count': distribution(totals),
            'normal_coverage': distribution(coverages), 'authors_with_zero_coverage': sum(n == 0 for n in counts),
            'authors_with_fewer_than_20_covered_normals': sum(n < 20 for n in counts),
            'note': 'Aggregates only, no author IDs or rankings. Small per-author counts make FPR/worst-case estimates unstable; not student assessment.'}

def length_strata(pairs, threshold, token_lengths):
    buckets = {'under_100_tokens': [], '100_to_499_tokens': [], 'at_least_500_tokens': []}
    for pair in pairs:
        count = token_lengths[pair['candidate_id']]
        key = 'under_100_tokens' if count < 100 else '100_to_499_tokens' if count < 500 else 'at_least_500_tokens'
        buckets[key].append(pair)
    result = {}
    for key, pp in buckets.items():
        metrics = point_metrics(pp, threshold if threshold is not None else float('inf'))
        if threshold is None:
            for metric in ('fpr', 'tpr', 'roc_auc'):
                metrics[metric] = None
        metrics['candidate_count'] = len({p['candidate_id'] for p in pp})
        metrics['note'] = 'Fixed raw non-comment token-length bucket; descriptive only, sparse groups are unstable.'
        result[key] = metrics
    return result

def cluster_intervals(pairs, threshold, seed, replicates):
    authors = sorted({p['true_author'] for p in pairs if p.get('score') is not None})
    if len(authors) < 10 or replicates < 100:
        return {'status': 'insufficient', 'reason': 'Need >=10 covered true-author clusters and >=100 bootstrap replicates.', 'author_clusters': len(authors)}
    groups = {a: [p for p in pairs if p['true_author'] == a] for a in authors}
    rng, values = np.random.default_rng(seed), defaultdict(list)
    for _ in range(replicates):
        sample = [p for a in rng.choice(authors, len(authors), replace=True) for p in groups[a]]
        metrics = point_metrics(sample, threshold)
        for key in ('fpr', 'tpr', 'roc_auc'):
            if metrics[key] is not None:
                values[key].append(metrics[key])
    return {'status': 'approximate', 'author_clusters': len(authors), 'replicates': replicates,
            'method': '95% percentile bootstrap by true author; threshold fixed; shared claimed-author enrollment dependence not modeled',
            'intervals': {k: [float(v) for v in np.quantile(x, [.025, .975])] for k, x in values.items()}}

def tfidf_scores(name, train, candidates, pairs):
    if name == 'character_tfidf':
        vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(3, 5), lowercase=False, max_features=100_000, dtype=np.float64)
    else:
        tokenizer = raw_tokens if name == 'raw_token_tfidf' else normalized_tokens
        vectorizer = TfidfVectorizer(tokenizer=tokenizer, token_pattern=None, lowercase=False,
                                     ngram_range=(1, 3), max_features=100_000, dtype=np.float64)
    # ONLY enrollment training documents determine vocabulary and IDF.
    x = vectorizer.fit_transform([r['code'] for r in train])
    z = vectorizer.transform([r['code'] for r in candidates])
    by_author = defaultdict(list)
    for i, r in enumerate(train):
        by_author[r['author_id']].append(i)
    profiles = {a: normalize(np.asarray(x[indices].mean(axis=0))) for a, indices in by_author.items()}
    positions = {r['id']: i for i, r in enumerate(candidates)}
    for pair in pairs:
        row = z[positions[pair['candidate_id']]]
        profile = profiles[pair['claimed_author']]
        pair['score'] = None if row.nnz == 0 or not np.any(profile) else float(np.clip(1.0 - (row @ profile.T)[0, 0], 0, 1))
    vocabulary = sorted((term, int(index)) for term, index in vectorizer.vocabulary_.items())
    return {'vocabulary_size': len(vocabulary), 'vocabulary_sha256': digest(json.dumps(vocabulary).encode()),
            'idf_sha256': digest(vectorizer.idf_.tobytes()), 'fit_partition': 'train',
            'ngram_range': list(vectorizer.ngram_range), 'max_features': 100000}

def structural_scores(train, candidates, pairs, min_enrollment):
    module_path = Path(__file__).with_name('structural_features.py')
    spec = importlib.util.spec_from_file_location('code_style_structural_features', module_path)
    structural = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(structural)
    try:
        analysis = structural.transform([r['code'] for r in train + candidates])
    except structural.DependencyUnavailable as exc:
        raise ValueError(f'Requested structural baseline unavailable: {exc}') from exc
    training_analysis = analysis[:len(train)]
    candidate_analysis = analysis[len(train):]
    accepted_counts = Counter(r['author_id'] for r, a in zip(train, training_analysis) if a['accepted'])
    indices = [i for i, (r, a) in enumerate(zip(train, training_analysis))
               if a['accepted'] and accepted_counts[r['author_id']] >= min_enrollment]
    meta = {'extractor_version': structural.VERSION, 'extractor_sha256': digest(module_path.read_bytes()),
            'dependencies': structural.dependency_status(), 'partition': 'raw_including_templates',
            'fit_partition': 'train', 'minimum_accepted_reference_problems': min_enrollment,
            'train_input': len(train), 'train_parse_accepted': sum(a['accepted'] for a in training_analysis),
            'train_used': len(indices), 'candidate_input': len(candidates),
            'candidate_parse_accepted': sum(a['accepted'] for a in candidate_analysis),
            'parse_rejections': [{'id': r['id'], 'stage': stage, 'reasons': a['rejection_reasons'], 'quality': a['quality']}
                                 for stage, rr, aa in (('train', train, training_analysis), ('candidate', candidates, candidate_analysis))
                                 for r, a in zip(rr, aa) if not a['accepted']],
            'transformer': {'norm': 'l2', 'use_idf': True, 'smooth_idf': True, 'sublinear_tf': False}}
    if not indices:
        for pair in pairs:
            pair['score'] = None
        meta['vocabulary_size'] = 0
        return meta
    vectorizer = DictVectorizer(sparse=True, sort=True)
    transformer = TfidfTransformer(norm='l2', use_idf=True, smooth_idf=True, sublinear_tf=False)
    x = transformer.fit_transform(vectorizer.fit_transform([training_analysis[i]['features'] for i in indices]))
    z = transformer.transform(vectorizer.transform([a['features'] for a in candidate_analysis]))
    by_author = defaultdict(list)
    for row, index in enumerate(indices):
        by_author[train[index]['author_id']].append(row)
    profiles = {author: normalize(np.asarray(x[rows].mean(axis=0))) for author, rows in by_author.items()}
    positions = {r['id']: i for i, r in enumerate(candidates)}
    for pair in pairs:
        i = positions[pair['candidate_id']]
        row, profile = z[i], profiles.get(pair['claimed_author'])
        pair['score'] = None if not candidate_analysis[i]['accepted'] or row.nnz == 0 or profile is None else float(np.clip(1.0 - (row @ profile.T)[0, 0], 0, 1))
    vocabulary = sorted((term, int(index)) for term, index in vectorizer.vocabulary_.items())
    meta.update(vocabulary_size=len(vocabulary), vocabulary_sha256=digest(json.dumps(vocabulary).encode()),
                idf_sha256=digest(transformer.idf_.tobytes()))
    return meta

def lexical_scores(engine, train, candidates, pairs):
    script = "const fs=require('fs');const e=require(process.argv[1]);const r=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(r.map(x=>({id:x.id,a:e.analyze(x.code)}))));"
    all_records = train + candidates
    process = subprocess.run(['node', '-e', script, str(engine.resolve())], input=json.dumps(all_records), text=True, capture_output=True, timeout=300, check=True)
    analyses = {r['id']: r['a'] for r in json.loads(process.stdout)}
    profiles = defaultdict(lambda: defaultdict(list))
    train_problems = defaultdict(set)
    # Input is reduced to one reference per author/problem before reaching here.
    for r in train:
        train_problems[r['author_id']].add(r['problem_id'])
        for f in analyses[r['id']]['features']:
            if f['rate'] is not None:
                profiles[r['author_id']][f['id']].append(f['rate'])
    for pair in pairs:
        a = analyses[pair['candidate_id']]
        ref = profiles[pair['claimed_author']]
        comparable = [abs(f['rate'] - float(np.mean(ref[f['id']]))) for f in a['features']
                      if f['rate'] is not None and f['opportunities'] >= 3 and len(ref[f['id']]) >= 3]
        enough = a['summary']['lexicallyComplete'] and a['summary']['bodyTokenCount'] >= 80 and len(train_problems[pair['claimed_author']]) >= 5 and len(comparable) >= 4
        pair['score'] = float(np.mean(comparable)) if enough else None
    return {'engine_sha256': digest(engine.read_bytes()), 'features': 12, 'fit_partition': 'train',
            'minimum_reference_problems': 5, 'minimum_candidate_body_tokens': 80, 'minimum_comparable_features': 4}

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

def run(args):
    out = Path(args.out).resolve()
    repository = Path(__file__).resolve().parents[1]
    if out == repository or repository in out.parents:
        raise ValueError('--out must be outside this source tree: outputs can contain private dataset identifiers.')
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('--out must be empty to avoid mixing experiment runs.')
    config = {k: getattr(args, k) for k in ('seed', 'split', 'min_enrollment', 'max_enrollment', 'negatives', 'target_fpr', 'bootstrap', 'synthetic', 'lexical', 'structural')}
    config.update(version=VERSION, tokenizer_version=TOKENIZER_VERSION, dataset_sha256=digest(Path(args.data).read_bytes()),
                  runner_sha256=digest(Path(__file__).read_bytes()),
                  python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__, sklearn=sklearn.__version__)
    write_json(out / 'config.json', config)
    try:
        records = read_records(args.data)
        if any(r['source'].lower().startswith('synthetic') for r in records) and not args.synthetic:
            raise ValueError('Synthetic input requires --synthetic; engineering tests cannot be presented as real evaluation.')
        provenance = {'sources': sorted({r['source'] for r in records}), 'input_records': len(records),
                      'known_author_assumption': 'author_id must identify a real submitter, never problem/language/class labels; dataset documentation must independently establish this.',
                      'synthetic_engineering_test': args.synthetic, 'external_metadata': None}
        if args.provenance:
            provenance['external_metadata'] = json.loads(Path(args.provenance).read_text(encoding='utf-8'))
        write_json(out / 'provenance.json', provenance)
        clean, excluded = remove_clones(records)
        splits, split_excluded, mode, boundaries = split_records(clean, args.seed, args.split)
        excluded += split_excluded
        split_counts = {k: len(v) for k, v in splits.items()}
        if any(not v for v in splits.values()):
            raise InsufficientData('At least one partition is empty; strict split is not relaxed.')
        # Equalize repeated submissions: one earliest (or ID-first) reference per author/problem.
        references = {}
        for r in sorted(splits['train'], key=lambda r: (r['time'] if r['time'] is not None else 0, r['id'])):
            key = (r['author_id'], r['problem_id'])
            if key in references:
                excluded.append({'id': r['id'], 'reason': 'repeated_enrollment_author_problem'})
            else:
                references[key] = r
        train_counts = Counter(r['author_id'] for r in references.values())
        eligible = sorted(a for a, n in train_counts.items() if n >= args.min_enrollment)
        if len(eligible) < 4:
            raise InsufficientData('Need >=4 authors with enough distinct enrollment problems; no split retries or seed search.')
        train = [r for r in references.values() if r['author_id'] in eligible]
        if args.max_enrollment is not None:
            selected = []
            for author in eligible:
                history = [r for r in train if r['author_id'] == author]
                if all(r['time'] is not None for r in history):
                    history.sort(key=lambda r: (-r['time'], r['id']))
                else:
                    history.sort(key=lambda r: (digest(f"{args.seed}\0{r['id']}".encode()), r['id']))
                selected.extend(history[:args.max_enrollment])
                excluded.extend({'id': r['id'], 'reason': 'enrollment_cap'} for r in history[args.max_enrollment:])
            train = selected
        for r in references.values():
            if r['author_id'] not in eligible:
                excluded.append({'id': r['id'], 'reason': 'insufficient_enrollment_author'})
        splits['train'] = sorted(train, key=lambda r: r['id'])
        candidate_counts = {}
        for name in ('dev', 'test'):
            candidate_counts[name] = len(splits[name])
            for r in splits[name]:
                if r['author_id'] not in eligible:
                    excluded.append({'id': r['id'], 'reason': 'candidate_without_eligible_enrollment'})
            splits[name] = [r for r in splits[name] if r['author_id'] in eligible]
            if len({r['author_id'] for r in splits[name]}) < 4:
                raise InsufficientData(f'{name} needs >=4 authors with enrollment; no evaluation is produced.')
        manifest = {'split_mode': mode, 'time_boundaries': boundaries,
                    'temporal_evaluation': mode in ('time', 'time_grouped'), 'excluded': excluded,
                    'partitions': {name: [{k: r[k] for k in ('id', 'author_id', 'problem_id', 'time', 'code_hash', 'token_hash')} for r in rs] for name, rs in splits.items()}}
        write_json(out / 'split_manifest.json', manifest)
        warnings = ['Known submitter identity is an assumption, not independent proof of who wrote the source.',
                    'Wrong-author claims model author mismatch, not AI use. Scores are not AI probabilities.',
                    'Exact and raw-token clones are quarantined; semantic/renamed near-clones and shared templates may remain.',
                    'Bootstrap intervals omit dataset selection and threshold-calibration uncertainty; shared enrollment creates residual dependence.',
                    'TF-IDF retains problem/algorithm signals. Group-disjoint problems reduce but do not eliminate confounding.']
        if len(eligible) < 10 or any(len({r['problem_id'] for r in splits[k]}) < 3 for k in ('dev', 'test')):
            warnings.append('Small author/problem coverage: exploratory measurements only, not a validated accuracy claim.')
        if mode not in ('time', 'time_grouped'):
            warnings.append('Timestamps missing or group split explicitly selected: no chronological/future generalization claim is valid.')
        if args.synthetic:
            warnings.append('SYNTHETIC ENGINEERING SELF-TEST ONLY: these metrics are not empirical detection evidence.')
        if args.provenance is None:
            warnings.append('No external provenance supplied: author identity, licensing and dataset construction are unverified.')
        efficiency = {'input_records': len(records), 'after_clone_quarantine': len(clean),
                      'after_problem_time_split': split_counts, 'after_enrollment_filter': {k: len(v) for k, v in splits.items()},
                      'retained_fraction': sum(len(v) for v in splits.values()) / len(records),
                      'exclusion_reasons': dict(Counter(r['reason'] for r in excluded))}
        results = {'status': 'ok', 'data_efficiency': efficiency, 'evidence_level': 'synthetic_engineering_only' if args.synthetic else 'exploratory_benchmark', 'synthetic_engineering_test': args.synthetic, 'split_mode': mode,
                   'warnings': warnings, 'eligible_authors': len(eligible), 'enrollment': {'requested_max': args.max_enrollment, 'minimum_retained': min(Counter(r['author_id'] for r in train).values()), 'maximum_retained': max(Counter(r['author_id'] for r in train).values())}, 'candidate_eligibility': {k: {'eligible': len(splits[k]), 'before_enrollment_filter': candidate_counts[k]} for k in ('dev', 'test')}, 'baselines': {}}
        names = ['character_tfidf', 'raw_token_tfidf', 'normalized_token_tfidf']
        if args.lexical:
            names.append('lexical_12')
        if args.structural:
            names.append('raw_cst_tfidf')
        predictions = []
        test_token_lengths = {r['id']: len(raw_tokens(r['code'])) for r in splits['test']}
        for name in names:
            dev_pairs = make_pairs(splits['dev'], eligible, args.seed + 1, args.negatives)
            test_pairs = make_pairs(splits['test'], eligible, args.seed + 2, args.negatives)
            pairs = dev_pairs + test_pairs
            candidates = splits['dev'] + splits['test']
            if name == 'raw_cst_tfidf':
                model = structural_scores(train, candidates, pairs, args.min_enrollment)
            elif name == 'lexical_12':
                engine = Path(args.engine) if args.engine else repository / 'dist' / 'engine.js'
                model = lexical_scores(engine, train, candidates, pairs)
            else:
                model = tfidf_scores(name, train, candidates, pairs)
            normal_dev = [p['score'] for p in dev_pairs if p['mismatch'] == 0 and p['score'] is not None]
            if not normal_dev:
                results['baselines'][name] = {'status': 'insufficient_coverage', 'model': model,
                                              'dev': point_metrics(dev_pairs, float('inf')), 'test': point_metrics(test_pairs, float('inf')),
                                              'threshold': None, 'note': 'No calibrated decision or performance claim; metric rates below are suppressed.'}
                for part in ('dev', 'test'):
                    for key in ('fpr', 'tpr', 'roc_auc'):
                        results['baselines'][name][part][key] = None
                results['baselines'][name]['test_author_distribution'] = author_distribution(test_pairs, None)
                results['baselines'][name]['test_length_strata'] = length_strata(test_pairs, None, test_token_lengths)
                for partition, pp in (('dev', dev_pairs), ('test', test_pairs)):
                    predictions.extend(dict(p, baseline=name, partition=partition, flagged=None) for p in pp)
                continue
            threshold = select_threshold(normal_dev, args.target_fpr)
            baseline = {'status': 'ok', 'model': model, 'threshold': threshold, 'decision': 'mismatch if score > frozen threshold',
                        'calibration': {'partition': 'dev', 'normal_claims_only': True, 'count': len(normal_dev), 'target_empirical_fpr': args.target_fpr,
                                        'note': 'No guarantee of population/test FPR. Fewer than 100 normal dev claims is a small calibration set.'},
                        'dev': point_metrics(dev_pairs, threshold), 'test': point_metrics(test_pairs, threshold),
                        'test_cluster_ci': cluster_intervals(test_pairs, threshold, args.seed + 3, args.bootstrap),
                        'test_author_distribution': author_distribution(test_pairs, threshold),
                        'test_length_strata': length_strata(test_pairs, threshold, test_token_lengths)}
            results['baselines'][name] = baseline
            for partition, pp in (('dev', dev_pairs), ('test', test_pairs)):
                predictions.extend(dict(p, baseline=name, partition=partition, flagged=None if p['score'] is None else p['score'] > threshold) for p in pp)
        write_json(out / 'results.json', results)
        with (out / 'predictions.jsonl').open('w', encoding='utf-8') as handle:
            for p in predictions:
                handle.write(json.dumps(p, ensure_ascii=False, allow_nan=False) + '\n')
        hashes = {p.name: digest(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()}
        write_json(out / 'artifact_hashes.json', hashes)
        return results
    except (InsufficientData, ValueError) as exc:
        write_json(out / 'results.json', {'status': 'refused', 'reason': str(exc), 'synthetic_engineering_test': args.synthetic})
        if not (out / 'split_manifest.json').exists():
            write_json(out / 'split_manifest.json', {'status': 'incomplete', 'reason': str(exc),
                'split_mode': locals().get('mode', args.split), 'time_boundaries': locals().get('boundaries'),
                'partitions': {name: [{k: r[k] for k in ('id', 'author_id', 'problem_id', 'time', 'code_hash', 'token_hash')} for r in rs] for name, rs in locals().get('splits', {}).items()},
                'excluded': locals().get('excluded', [])})
        hashes = {p.name: digest(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file() and p.name != 'artifact_hashes.json'}
        write_json(out / 'artifact_hashes.json', hashes)
        raise

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', required=True, help='External JSONL, one known-author original per record')
    p.add_argument('--out', required=True, help='Empty directory outside source tree; may contain sensitive IDs')
    p.add_argument('--seed', type=int, default=20261012)
    p.add_argument('--split', choices=('auto', 'time', 'time_grouped', 'group_problem'), default='auto')
    p.add_argument('--min-enrollment', type=int, default=3, help='Distinct historical reference problems per author (>=2)')
    p.add_argument('--max-enrollment', type=int, help='Cap historical distinct-problem references per author; default all. Must be >= min-enrollment.')
    p.add_argument('--negatives', type=int, default=3, help='Fixed seeded wrong-author claims per candidate')
    p.add_argument('--target-fpr', type=float, default=.05)
    p.add_argument('--bootstrap', type=int, default=500)
    p.add_argument('--provenance', help='External JSON with license, identity evidence, collection procedure, limitations')
    p.add_argument('--synthetic', action='store_true', help='Mark all metrics as engineering-only synthetic self-test')
    p.add_argument('--lexical', action='store_true', help='Also run 12 lexical habits using Node and the existing engine')
    p.add_argument('--structural', action='store_true', help='Also run fixed raw CST counts + train-only TF-IDF with pinned optional parsers')
    p.add_argument('--engine', help='Optional engine.js location for --lexical')
    return p

def main():
    p = parser()
    args = p.parse_args()
    if args.max_enrollment is not None and args.max_enrollment < args.min_enrollment:
        p.error('max-enrollment must be >= min-enrollment to keep candidate eligibility fixed.')
    if args.min_enrollment < 2 or args.negatives < 1 or not 0 <= args.target_fpr < 1 or args.bootstrap < 0:
        p.error('Require min-enrollment >=2, negatives >=1, 0<=target-fpr<1 and bootstrap >=0.')
    try:
        result = run(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'Benchmark refused/failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({'status': result['status'], 'synthetic': args.synthetic, 'split_mode': result['split_mode'], 'baselines': list(result['baselines'])}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
