# Optional C++ CST structure baseline (cpp-cst-v1)

This experimental extractor parses C++ with Tree-sitter; it never compiles the
source, runs C++, invokes macros, resolves includes, or sends source to a service.
It is a concrete syntax tree (CST), **not** a semantic AST, control-flow graph,
scope-aware alpha normalization, or proof of authorship. Problem/algorithm choice
and copied contest templates can dominate structural similarity. No claim of
resistance to deliberate imitation or reliable AI detection is established.

## Dependency and API provenance

Python >=3.10; optional pinned packages (both MIT licensed):

- [tree-sitter 0.25.2 on official PyPI](https://pypi.org/project/tree-sitter/0.25.2/),
  [official Python binding and usage API](https://github.com/tree-sitter/py-tree-sitter/tree/v0.25.2)
- [tree-sitter-cpp 0.23.4 on official PyPI](https://pypi.org/project/tree-sitter-cpp/0.23.4/),
  [official grammar license](https://github.com/tree-sitter/tree-sitter-cpp/blob/v0.23.4/LICENSE)
- [official Node API documentation](https://tree-sitter.github.io/py-tree-sitter/classes/tree_sitter.Node.html)
  (the live documentation may describe a newer binding; engineering tests verify
  the pinned implementation).

Use a virtual environment. These dependencies are deliberately optional; do not
silently replace the baseline with lexical features if they are absent.

```sh
python -m pip install --index-url https://pypi.org/simple --only-binary=:all: \
  -r experiments/requirements-structural.txt
python -m unittest discover -s tests -p 'test_structural_features.py' -v
```

If the normal Python site-packages directory is read-only, use an explicitly
writable, non-repository dependency directory and restore it on a fresh machine:

```sh
python -m pip install --index-url https://pypi.org/simple --only-binary=:all: \
  --target "$STRUCTURAL_DEPS_DIR" -r experiments/requirements-structural.txt
PYTHONPATH="$STRUCTURAL_DEPS_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  python -m unittest discover -s tests -p 'test_structural_features.py' -v
```

Set `STRUCTURAL_DEPS_DIR` to a writable absolute path before running these commands.
Dependency-free runs intentionally report eight parser tests as skipped; that is
not a passed parser verification. CI should install the optional requirements and
verify that no parser tests skip.

`--only-binary` avoids running a source package build; unsupported platforms will
fail explicitly. Dependencies are installed rather than vendored; retain their
MIT notices if distributing their code/binaries. No external source snippets or
personal code are included in this module's tests.

## Fixed feature definition

`structural_features.extract(source, include_positions=True)` accepts a Python
string or strict UTF-8 bytes. `transform(sources, include_positions=False)` returns
complete result dictionaries in input order. The implementation learns nothing.
`dependency_status()` reports expected and installed package versions; mismatched
versions raise `DependencyUnavailable` when parsing, so a grammar upgrade is an
explicit new experiment rather than unnoticed drift.

The feature view retains **named** CST nodes, removes comment subtrees, and treats
literal subtrees as opaque outer types. Preprocessor argument payloads and system
include string contents are opaque too. Identifiers remain generic grammar node
types; identifier spellings, literal values, escape counts, raw-string delimiters,
comment words and source text never appear in feature keys or values.

Counts (nonnegative integers):

- `node:<type>`: named-node type frequency
- `edge:<parent>><child>`: directed type pairs
- `path3:<grandparent>><parent>><child>`: directed length-three type paths
- `depth:<bucket>`: depths in the comment-filtered, opaque-literal named-node view,
  with the translation unit at zero
- `branch:<bucket>`: child-count distribution in that same view

Both shape distributions use fixed buckets 0, 1, 2, 3, 4, 5–8, 9–16, 17+.
`shape_summary` separately reports mean/max depth and branching and leaf fraction;
these diagnostics are not additional input features in v1. Anonymous punctuation
and operators are intentionally absent: changing `+` to `-`, for example, may
leave this representation unchanged. This is a lossy structural baseline, not an
exact-program representation. Counts retain file-size effects until downstream
normalization; long templates can dominate even after normalization.

## Quality gates and coverage

All gates were fixed before observing a real held-out score; they are engineering
safeguards, not calibrated minimum evidence requirements for author decisions.

- Empty/whitespace, invalid UTF-8, or input over 1,000,000 bytes: reject before parse.
- More than 100,000 complete-CST nodes: reject traversal (`node_limit`).
- Any parse ERROR or MISSING node, or root `has_error`: reject. A parser exception
  is `parse_failure`; a missing/mismatched parser is an explicit dependency error.
- Fewer than 12 feature-view nodes or fewer than 3 nodes with children: reject as
  `insufficient_structure`. Comment-only files are rejected. A syntactically valid
  short function can be rejected; successful parsing does not imply enough evidence.
- Every rejected document has `accepted=False` and `features={}`. This empty mapping
  is a refusal, **not a zero vector to score**. Preserve its rejection reason,
  report abstentions and coverage, and never silently remove failures from reports.

`quality` reports full-CST node count (including anonymous nodes and comments),
ERROR count and ratio, MISSING count and ratio, their summed ratio, and root error
flag. Ratios use the full-CST count as denominator, not the feature-view count or
source-byte length. `has_error` ancestors are not counted as separate ERROR nodes.
Missing anonymous punctuation is still detected. A diagnostic ratio measures
parser recovery events, not the fraction of wrong code. Some preprocessor-heavy
or extension code can be rejected despite compiling elsewhere; semantically
invalid programs can parse cleanly. Macro expansion and include resolution are
not attempted. Pre-parse/limit failures cannot provide complete parser statistics.

## Audit and privacy

`positions` gives type, original UTF-8 start/end bytes, zero-based row/byte-column
points, depth, arity, and up-to-three-type path for each retained named node.
`diagnostics` independently preserves ERROR/MISSING spans and flags. Byte ends are
exclusive; columns are byte offsets, not Unicode character or visual columns.
Positions change after formatting/renaming even when count features do not.

Node and diagnostic position lists are each capped at 5,000 entries with explicit
truncation flags; disable positions in large bulk runs. The diagnostic list is
still kept. Node-limit results are incomplete and rejected. No source text is
returned. Metadata can still reveal structural information and should follow the
same data-handling rules as the experiment; audit positions are not public samples.

## Integration without train/test leakage

The v1 partition is `raw` only. No automatic contest-template removal or changes
to the browser engine are made. Record raw-template confounding explicitly. Any
future conservative stripped partition must be registered independently, retain
source-mapping information, and report raw and stripped coverage side by side.

For an optional scikit-learn baseline:

1. Extract train and evaluation documents independently with the same fixed version.
2. Keep quality/abstention records; only accepted training documents can fit a model.
3. Fit `DictVectorizer` vocabulary on training feature mappings only. Fit a
   `TfidfTransformer` on those training counts only; transform evaluation with the
   already-fitted vocabulary/IDF. Features unseen in training are ignored.
4. Normalize the vectors and training-author centers consistently before cosine
   comparison. If an evaluation vector has no known features, abstain explicitly.
5. Set thresholds and any model choice using training/validation only. Do not fit
   vocabulary/IDF or choose structure variants using held-out test scores.
6. Compare scores only with stated comparable coverage. Rejected samples and authors
   with no usable history have no CST score, not a fabricated score of zero.

This module exports extraction, not a trained classifier or an authorship threshold.
Pipeline integration and real-data effectiveness require their own validation.

## Verification performed

On 2026-10-12, Python 3.12 with the pinned official binary wheels: 12 synthetic
engineering tests passed. Tests cover formatting/comment/rename invariance,
structural change, opaque string/literal contents, raw strings and macro payloads,
ERROR/MISSING rejection, short/comment-only refusal, empty/invalid/oversize refusal,
UTF-8 byte positions, deterministic batch extraction, and dependency failure.
Passing these checks establishes implementation behavior, not detection accuracy.
