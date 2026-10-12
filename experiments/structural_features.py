"""Optional, text-free C++ CST features. Never compiles or executes input."""
from collections import Counter
from importlib import metadata

VERSION = "cpp-cst-v1"
DEPENDENCIES = {"tree-sitter": "0.25.2", "tree-sitter-cpp": "0.23.4"}
MIN_NODES = 12
MIN_BRANCH_NODES = 3
MAX_BYTES = 1_000_000
MAX_NODES = 100_000
MAX_POSITIONS = 5_000


class DependencyUnavailable(RuntimeError):
    """Optional parser is missing, mismatched, or cannot initialize."""


def dependency_status():
    versions = {}
    for name in DEPENDENCIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {"available": versions == DEPENDENCIES, "expected": dict(DEPENDENCIES),
            "installed": versions}


def _parser():
    status = dependency_status()
    if not status["available"]:
        raise DependencyUnavailable(f"Install exact optional dependencies: {DEPENDENCIES}; found {status['installed']}")
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_cpp
        return Parser(Language(tree_sitter_cpp.language()))
    except (ImportError, OSError, TypeError, ValueError) as exc:
        raise DependencyUnavailable("Could not initialize the pinned C++ grammar") from exc


def _opaque(kind):
    # Literal internal tokenization (escapes, raw delimiters, contents) must not
    # become lexical features. Keep only the outer syntactic literal type.
    return kind.endswith("literal") or kind in {"true", "false", "null", "nullptr", "system_lib_string", "preproc_arg"}


def _bucket(n):
    if n <= 4:
        return str(n)
    if n <= 8:
        return "5-8"
    if n <= 16:
        return "9-16"
    return "17+"


def _span(node):
    return {"type": node.type, "start_byte": node.start_byte, "end_byte": node.end_byte,
            "start_point": list(node.start_point), "end_point": list(node.end_point)}


def extract(source, *, include_positions=True):
    """Return counts plus quality/audit data; rejected inputs have no features.

    UTF-8 byte offsets and zero-based row/byte-column points refer to raw input.
    No vocabulary, IDF, thresholds, or model parameters are learned here.
    Missing optional dependencies raise DependencyUnavailable, never a fallback.
    """
    if not isinstance(source, (str, bytes)):
        raise TypeError("source must be str or UTF-8 bytes")
    result = {"version": VERSION, "partition": "raw", "accepted": False,
              "rejection_reasons": [], "features": {}, "quality": {},
              "shape_summary": {}, "positions": [], "diagnostics": [],
              "positions_truncated": False,
              "limitations": ["CST, not semantic AST or scope binding", "raw template confounding", "problem/algorithm confounding"]}
    try:
        raw = source.encode("utf-8") if isinstance(source, str) else source
        raw.decode("utf-8", errors="strict")
    except UnicodeError:
        result["rejection_reasons"] = ["invalid_utf8"]
        return result
    result["quality"]["source_bytes"] = len(raw)
    if not raw.strip():
        result["rejection_reasons"] = ["empty_input"]
        return result
    if len(raw) > MAX_BYTES:
        result["rejection_reasons"] = ["input_too_large"]
        return result
    parser = _parser()
    try:
        tree = parser.parse(raw)
    except (ValueError, RuntimeError) as exc:
        result["rejection_reasons"] = ["parse_failure"]
        result["quality"]["exception_type"] = type(exc).__name__
        return result
    # Scan the complete CST, including anonymous MISSING nodes. Do not count
    # merely has_error ancestors as individual errors.
    stack = [tree.root_node]
    total = errors = missing = 0
    while stack:
        node = stack.pop()
        total += 1
        if total > MAX_NODES:
            result["rejection_reasons"] = ["node_limit"]
            result["quality"]["cst_nodes_scanned"] = total
            return result
        errors += bool(node.is_error)
        missing += bool(node.is_missing)
        if node.is_error or node.is_missing:
            if len(result["diagnostics"]) < MAX_POSITIONS:
                result["diagnostics"].append({**_span(node), "is_error": node.is_error,
                                              "is_missing": node.is_missing})
        stack.extend(reversed(node.children))
    result["quality"].update(cst_nodes=total, error_nodes=errors, missing_nodes=missing,
        error_ratio=errors / total, missing_ratio=missing / total,
        error_or_missing_ratio=(errors + missing) / total,
        root_has_error=tree.root_node.has_error,
        diagnostics_truncated=errors + missing > len(result["diagnostics"]))
    counts = Counter()
    depths, arities = [], []
    stack = [(tree.root_node, (), 0)]
    while stack:
        node, ancestors, depth = stack.pop()
        if node.type == "comment":
            continue
        kind = node.type
        children = [] if _opaque(kind) else [n for n in node.named_children if n.type != "comment"]
        path = (*ancestors[-2:], kind)
        counts["node:" + kind] += 1
        if ancestors:
            counts["edge:" + ">".join(path[-2:])] += 1
        if len(path) == 3:
            counts["path3:" + ">".join(path)] += 1
        counts["depth:" + _bucket(depth)] += 1
        counts["branch:" + _bucket(len(children))] += 1
        depths.append(depth)
        arities.append(len(children))
        if include_positions and len(result["positions"]) < MAX_POSITIONS:
            result["positions"].append({**_span(node), "depth": depth, "branch_count": len(children),
                                        "type_path": list(path)})
        stack.extend((child, path, depth + 1) for child in reversed(children))
    n = len(depths)
    branches = sum(x > 0 for x in arities)
    result["quality"].update(feature_nodes=n, branch_nodes=branches)
    result["positions_truncated"] = include_positions and n > MAX_POSITIONS
    result["shape_summary"] = {"max_depth": max(depths, default=0),
        "mean_depth": sum(depths) / n if n else 0, "max_branch": max(arities, default=0),
        "mean_branch": sum(arities) / n if n else 0,
        "leaf_fraction": sum(x == 0 for x in arities) / n if n else 0}
    if tree.root_node.has_error or errors or missing:
        result["rejection_reasons"].append("parse_error_or_missing")
    if n < MIN_NODES or branches < MIN_BRANCH_NODES:
        result["rejection_reasons"].append("insufficient_structure")
    result["accepted"] = not result["rejection_reasons"]
    if result["accepted"]:
        result["features"] = dict(sorted(counts.items()))
    return result


def transform(sources, *, include_positions=False):
    """Batch extraction with full per-row acceptance/quality, in input order."""
    if isinstance(sources, (str, bytes)):
        raise TypeError("sources must be an iterable of source documents")
    return [extract(source, include_positions=include_positions) for source in sources]
