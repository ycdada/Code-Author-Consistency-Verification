#!/usr/bin/env python3
"""Corpus-source diagnostics only. Never executes code or verifies authorship."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import pickle
import platform
import re
import sys
import numpy as np
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 20261012
TAG = 'diagnostic_not_task_disjoint'
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_]\w*|\d+(?:\.\d+)?|[^\s]')
FEATURES = ['log_chars', 'log_lines', 'log_tokens', 'mean_line_length', 'blank_line_fraction', 'tab_fraction', 'space_fraction', 'comment_fraction', 'preprocessor_per_line', 'semicolon_per_token', 'brace_per_token', 'identifier_mean_length']
CONFIG = {'seed': SEED, 'human_split': [0.6, 0.2, 0.2], 'seen_ai_split': [0.8, 0.2], 'heldout_ai_split': 'all_test', 'dev_max_fpr': 0.05, 'abstain_margin': 0.1, 'char_ngram_range': [3, 5], 'char_max_features': 20000, 'char_min_df': 2, 'logistic_C': 1.0, 'logistic_class_weight': 'balanced', 'logistic_max_iter': 2000}

def digest(data):
    if isinstance(data, str): data = data.encode('utf-8')
    return hashlib.sha256(data).hexdigest()

def json_digest(obj):
    return digest(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')))

def token_hash(code):
    # Preserve literal contents, identifiers and all punctuation. Ignore comments/whitespace.
    tokens = [t for t in TOKEN.findall(code) if not t.startswith(('//', '/*'))]
    return json_digest(tokens)

@dataclass(frozen=True)
class Record:
    key: str
    code: str
    source: str
    label: int
    code_hash: str
    token_hash: str

def record(key, code, source, label):
    if not isinstance(code, str) or not code.strip() or len(code) > 500000:
        raise ValueError('Code must be nonempty text of at most 500000 characters')
    if label not in (0, 1): raise ValueError('Unknown binary source label')
    return Record(key, code, source, label, digest(code), token_hash(code))

def quality(code):
    """Frozen, label-independent heuristics; no claim of C++ parsing or validity."""
    fence_lines = [m for m in re.finditer(r"(?m)^\s*```[^\n]*$", code)]
    flags = []
    cleaned = code
    if fence_lines:
        if len(fence_lines) != 2:
            flags.append('ambiguous_fences')
            cleaned = None
        else:
            opening = fence_lines[0].group().strip()[3:].strip().lower()
            closing = fence_lines[1].group().strip()
            if opening not in ('', 'c', 'cpp', 'c++', 'cc', 'cxx') or closing != '```':
                flags.append('ambiguous_fences');cleaned = None
            else:
                cleaned = code[fence_lines[0].end():fence_lines[1].start()].strip()
                flags.append('single_code_fence')
    else: flags.append('unfenced_retained')
    candidate = cleaned if cleaned is not None else code
    toks = TOKEN.findall(candidate)
    bare = [t for t in toks if not t.startswith(('//', '/*'))]
    if len(bare) < 80: flags.append('short_under80_heuristic_tokens')
    if ';' not in bare or not any(t in bare for t in ('int','void','auto','class','struct','include')):
        flags.append('non_code_heuristic')
    # Lightweight lexical state only, deliberately not a parser.
    i=0; state='normal';quote=''
    while i < len(candidate):
        ch=candidate[i];nxt=candidate[i:i+2]
        if state=='line':
            if ch=='\n':state='normal'
        elif state=='block':
            if nxt=='*/':state='normal';i+=1
        elif state=='quote':
            if ch=='\\':i+=1
            elif ch==quote:state='normal'
        elif nxt=='//':state='line';i+=1
        elif nxt=='/*':state='block';i+=1
        elif ch in ('"', "'"):state='quote';quote=ch
        i+=1
    if state in ('quote','block'):flags.append('unclosed_lexical_heuristic')
    return cleaned, flags

def prepare_quality(rows, mode):
    kept=[];summary={};flagmap={}
    for row in rows:
        cleaned,flags=quality(row.code);flagmap[row.key]=flags
        group=summary.setdefault(row.source,{'total':0,'retained':0,'rejected':0,'flags':{}})
        group['total']+=1
        for flag in flags:group['flags'][flag]=group['flags'].get(flag,0)+1
        text=row.code if mode=='raw' else cleaned
        if not text or not text.strip():group['rejected']+=1;continue
        group['retained']+=1
        kept.append(record(row.key,text,row.source,row.label))
    return kept,summary,flagmap

def load_profiler(directory):
    rows, files = [], {}
    for path in sorted(Path(directory).glob('GCJ_*.json')):
        source = path.stem.removeprefix('GCJ_')
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError('Profiler GCJ inputs must be lists of strings; no guessed field mapping')
        files[path.name] = digest(path.read_bytes())
        rows += [record(f'{source}:{i}', text, source, int(source != 'human')) for i, text in enumerate(data)]
    if not rows or 'human' not in {x.source for x in rows}: raise ValueError('Missing human corpus')
    if len({x.source for x in rows if x.label}) < 2: raise ValueError('Need at least two AI generators')
    return rows, files

def quarantine(rows):
    """Remove every member of any exact or lexical clone group, globally, before splitting."""
    exact, token = Counter(r.code_hash for r in rows), Counter(r.token_hash for r in rows)
    rejected = [r for r in rows if exact[r.code_hash] > 1 or token[r.token_hash] > 1]
    bad = {r.key for r in rejected}
    return [r for r in rows if r.key not in bad], rejected

def stable_order(rows, seed, source):
    return sorted(rows, key=lambda r: digest(f'{seed}:{source}:{r.code_hash}'))

def split_fold(rows, heldout, seed=SEED):
    groups = defaultdict(list)
    for row in rows: groups[row.source].append(row)
    parts = {'train': [], 'dev': [], 'test': []}
    for source in sorted(groups):
        ordered = stable_order(groups[source], seed, source)
        n = len(ordered)
        if source == heldout: parts['test'] += ordered; continue
        if source == 'human':
            a, b = int(n * .6), int(n * .8)
        else: a, b = int(n * .8), n
        parts['train'] += ordered[:a]
        parts['dev'] += ordered[a:b]
        parts['test'] += ordered[b:]
    for name, samples in parts.items():
        if {r.label for r in samples} != {0, 1}: raise ValueError(f'{name} lacks both classes')
    for a, b in [('train','dev'),('train','test'),('dev','test')]:
        for attr in ['key', 'code_hash', 'token_hash']:
            if {getattr(x, attr) for x in parts[a]} & {getattr(x, attr) for x in parts[b]}:
                raise ValueError('Clone or row leakage across splits')
    return parts

def lexical(code):
    lines = code.splitlines() or ['']
    toks = TOKEN.findall(code)
    comments = [t for t in toks if t.startswith(('//','/*'))]
    ids = [t for t in toks if re.fullmatch(r'[A-Za-z_]\w*', t)]
    n, l, t = max(1,len(code)), len(lines), max(1,len(toks))
    return [math.log1p(n), math.log1p(l), math.log1p(t), n/l,
            sum(not x.strip() for x in lines)/l, code.count('\t')/n, code.count(' ')/n,
            sum(map(len,comments))/n, sum(x.lstrip().startswith('#') for x in lines)/l,
            code.count(';')/t, (code.count('{')+code.count('}'))/t,
            sum(map(len,ids))/max(1,len(ids))]

def inputs(rows, kind):
    return [r.code for r in rows] if kind == 'char_tfidf' else np.array([lexical(r.code) for r in rows])

def build_model(kind):
    lr = LogisticRegression(C=1.0, class_weight='balanced', max_iter=2000, solver='liblinear', random_state=SEED)
    if kind == 'char_tfidf':
        return make_pipeline(TfidfVectorizer(analyzer='char', ngram_range=(3,5), max_features=20000,
                                            min_df=2, lowercase=False, sublinear_tf=True), lr)
    if kind == 'length_lexical': return make_pipeline(StandardScaler(), lr)
    raise ValueError('Unknown baseline')

def choose_threshold(y, scores, max_fpr=.05):
    """Maximize dev TPR under empirical FPR ceiling; tie -> lower FPR then higher threshold."""
    y, scores = np.asarray(y), np.asarray(scores)
    if set(y) != {0,1}: raise ValueError('Dev requires human and AI samples')
    if not np.isfinite(scores).all(): raise ValueError('Nonfinite scores')
    candidates = np.r_[np.nextafter(float(scores.max()), np.inf), np.unique(scores)]
    valid = []
    for th in candidates:
        pred = scores >= th
        fpr, tpr = pred[y == 0].mean(), pred[y == 1].mean()
        if fpr <= max_fpr: valid.append((float(tpr), -float(fpr), float(th)))
    return max(valid)[2]

def wilson(k, n):
    if not n: return None
    z, p = 1.959963984540054, k/n
    den = 1+z*z/n
    centre = (p+z*z/(2*n))/den
    width = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0.,centre-width), min(1.,centre+width)]

def evaluate(y, scores, threshold, margin=.1):
    y, scores = np.asarray(y), np.asarray(scores)
    pred = scores >= threshold
    human, ai = y == 0, y == 1
    fp, tp = int(pred[human].sum()), int(pred[ai].sum())
    nh, na = int(human.sum()), int(ai.sum())
    kept = np.abs(scores-threshold) >= margin
    kh, ka = kept & human, kept & ai
    return {'n_human': nh, 'n_ai': na, 'fp': fp, 'tp': tp,
            'fpr': fp/nh if nh else None, 'tpr': tp/na if na else None,
            'roc_auc': float(roc_auc_score(y,scores)) if nh and na else None,
            'fpr_naive_wilson95': wilson(fp,nh), 'tpr_naive_wilson95': wilson(tp,na),
            'interval_warning': 'row_iid_assumption_unverified_not_author_cluster_CI',
            'abstain_margin': margin, 'coverage': float(kept.mean()) if len(y) else None,
            'human_coverage': float(kept[human].mean()) if nh else None,
            'ai_coverage': float(kept[ai].mean()) if na else None,
            'selective_fpr': float(pred[kh].mean()) if kh.any() else None,
            'selective_tpr': float(pred[ka].mean()) if ka.any() else None}

def private_manifest(rows):
    return [{'key':r.key,'source':r.source,'label':r.label,'code_sha256':r.code_hash,'token_sha256':r.token_hash} for r in rows]

def load_smoke(path):
    rows=[]
    for i,line in enumerate(Path(path).read_text().splitlines()):
        data=json.loads(line)
        if data.get('label') not in ('ai','human'): raise ValueError('Unsupported smoke label')
        rows.append(record(f'smoke:{i}',data['code'],str(data.get('model') or 'human'),int(data['label']=='ai')))
    return rows

def run(data, out, smoke=None, seed=SEED, mode="code_only", cst_quality=False):
    out, root = Path(out).resolve(), Path(__file__).resolve().parents[1]
    if out == root or root in out.parents: raise ValueError('Outputs, models and row hashes must stay outside repository')
    if out.exists() and any(out.iterdir()): raise ValueError('Output directory must be empty')
    out.mkdir(parents=True, exist_ok=True)
    raw, file_hashes = load_profiler(data)
    prepared, quality_summary, flagmap = prepare_quality(raw, mode)
    cst_summary = {}
    if cst_quality:
        from structural_features import extract, dependency_status
        cst_summary['dependencies'] = dependency_status()
        cst_summary['by_source'] = {}
        for row in prepared:
            parsed = extract(row.code, include_positions=False)
            flag = 'cst_error_free' if parsed['accepted'] else 'cst_rejected'
            flagmap[row.key].append(flag)
            group = cst_summary['by_source'].setdefault(row.source, {'total':0, 'accepted':0, 'rejected':0, 'rejection_reasons':{}})
            group['total'] += 1
            group['accepted' if parsed['accepted'] else 'rejected'] += 1
            for reason in parsed['rejection_reasons']:
                group['rejection_reasons'][reason] = group['rejection_reasons'].get(reason, 0) + 1
    rows, removed = quarantine(prepared)
    (out/'private_quarantine.json').write_text(json.dumps(private_manifest(removed),indent=2))
    smoke_prepared, smoke_quality, smoke_flags = prepare_quality(load_smoke(smoke),mode) if smoke else ([],{}, {})
    smoke_rows, smoke_removed = quarantine(smoke_prepared)
    # External smoke must not contain any clone of any fitting/evaluation corpus record.
    all_code, all_tok = {r.code_hash for r in raw + prepared}, {r.token_hash for r in raw + prepared}
    smoke_overlap=[r for r in smoke_rows if r.code_hash in all_code or r.token_hash in all_tok]
    smoke_rows=[r for r in smoke_rows if r not in smoke_overlap]
    config = dict(CONFIG, seed=seed, mode=mode, quality_protocol="label_independent_single_fence_v1", cst_quality=cst_quality)
    results={'status': TAG, 'task': 'binary_corpus_source_diagnostic_only', 'config': config,
             'config_sha256':json_digest(config), 'dataset_file_sha256':file_hashes,
             'script_sha256':digest(Path(__file__).read_bytes()),
             'versions':{'python':platform.python_version(),'numpy':np.__version__,'sklearn':sklearn.__version__},
             'cst_quality':cst_summary, 'quality_by_source':quality_summary, 'raw_counts':dict(Counter(r.source for r in raw)), 'retained_counts':dict(Counter(r.source for r in rows)),
             'quarantined_counts':dict(Counter(r.source for r in removed)),
             'metadata':{'author':False,'problem':False,'timestamp':False,'index_pairing_verified':False},
             'limitations':['No task-disjoint, author-disjoint, temporal or imitation-detection inference.',
               'Official loader treats GCJ files as independent string lists; matching indices are not proof of shared tasks.',
               'Clone filter covers exact and simple token hashes, not near-clone or semantic equivalence.',
               'Folds share human test samples and seen-generator training sets; do not pool as independent observations.',
               'No author clusters available. Wilson intervals are descriptive row-IID calculations, not valid cluster uncertainty.',
               'Source probabilities are uncalibrated corpus-classifier scores, never cheating probabilities.',
               'Fixed score abstention band is diagnostic only and provides no safety guarantee.'],
             'smoke':{'quality_by_source':smoke_quality,'n_retained':len(smoke_rows),'duplicate_quarantined':len(smoke_removed),'corpus_overlap_quarantined':len(smoke_overlap),'status':'smoke_only_not_task_disjoint'},'folds':[]}
    for heldout in sorted({r.source for r in rows if r.label}):
        parts=split_fold(rows,heldout,seed)
        manifests={p:private_manifest(v) for p,v in parts.items()}
        folder=out/heldout; folder.mkdir()
        (folder/'private_split_manifest.json').write_text(json.dumps(manifests,indent=2))
        fold={'heldout_generator':heldout,'status':TAG,
              'split_counts':{p:dict(Counter(r.source for r in v)) for p,v in parts.items()},
              'split_sha256':{p:json_digest(v) for p,v in manifests.items()},'models':{}}
        for kind in ['char_tfidf','length_lexical']:
            model=build_model(kind)
            model.fit(inputs(parts['train'],kind),[r.label for r in parts['train']])
            dev=model.predict_proba(inputs(parts['dev'],kind))[:,1]
            threshold=choose_threshold([r.label for r in parts['dev']],dev)
            test=model.predict_proba(inputs(parts['test'],kind))[:,1]
            artifact=pickle.dumps(model,protocol=5)
            (folder/f'{kind}.pkl').write_bytes(artifact)
            result={'model_sha256':digest(artifact),'threshold':threshold,
                    'dev':evaluate([r.label for r in parts['dev']],dev,threshold),
                    'test':evaluate([r.label for r in parts['test']],test,threshold)}
            result['test_quality_strata']={}
            for flag in sorted({f for r in parts['test'] for f in flagmap[r.key]}):
                indices=[i for i,r in enumerate(parts['test']) if flag in flagmap[r.key]]
                result['test_quality_strata'][flag]=evaluate([parts['test'][i].label for i in indices],test[indices],threshold)
            if smoke_rows:
                probs=model.predict_proba(inputs(smoke_rows,kind))[:,1]
                result['external_smoke']=evaluate([r.label for r in smoke_rows],probs,threshold)
            fold['models'][kind]=result
            print(json.dumps({'heldout':heldout,'model':kind,'test':result['test']},sort_keys=True),flush=True)
        results['folds'].append(fold)
    (out/'aggregate_results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False))
    return results

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--mode',choices=['raw','code_only'],default='code_only')
    parser.add_argument('--cst-quality', action='store_true', help='Optional pinned local CST parser, audit strata only; never retunes or filters primary metrics')
    parser.add_argument('--smoke');parser.add_argument('--seed',type=int,default=SEED)
    args=parser.parse_args();run(args.data,args.out,args.smoke,args.seed,args.mode,args.cst_quality)

if __name__=='__main__': main()
