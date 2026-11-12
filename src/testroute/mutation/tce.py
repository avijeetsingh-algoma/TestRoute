"""Trivial compiler equivalence (TCE) and duplicate detection.

Component: C9 (Mutation Analyzer), static equivalence filter.

Responsibility:
    Implements spec Section 6.2. A mutant is dropped before any scoring when
    * its normalized AST dump or its compiled bytecode equals the original
      module's: label TCE_EQUIVALENT (the compiler proves it changes nothing);
    * or it equals an earlier mutant's: label DUPLICATE.
    Both comparisons ignore line numbers and positions, which the text splice
    may shift for later lines, and compare nested code objects recursively.
    Bytecode comparison catches what the AST comparison cannot, because
    CPython's AST optimizer folds some forms before compiling; for example
    ``not (a not in b)`` compiles exactly like ``a in b``.

Receives: original source and generated mutants.
Produces: a label per mutant (``""`` for kept mutants) and, for duplicates,
    the id they duplicate.
Talks to: used by ``mutation.mutant_set``.
Spec sections: 6.2.
Feedback IDs: FB2 (equivalent mutants are removed, not counted as survivors).
"""

from __future__ import annotations

import ast
import types
from typing import Any

TCE_EQUIVALENT = "TCE_EQUIVALENT"
DUPLICATE = "DUPLICATE"


def ast_signature(source: str) -> str:
    """AST dump without positions (``include_attributes`` is False by default)."""
    return ast.dump(ast.parse(source))


def _code_signature(code: types.CodeType) -> tuple[Any, ...]:
    """Position-free signature of a code object, recursing into nested code."""
    consts = tuple(_code_signature(c) if isinstance(c, types.CodeType) else (type(c).__name__, repr(c)) for c in code.co_consts)
    return (code.co_name, code.co_code, consts, code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars)


def bytecode_signature(source: str) -> tuple[Any, ...]:
    """Signature of the compiled module, independent of line numbers and file name."""
    return _code_signature(compile(source, "<module>", "exec", dont_inherit=True, optimize=0))


def classify_static(original: str, mutant_sources: list[str]) -> list[tuple[str, int | None]]:
    """Label each mutant as kept, TCE_EQUIVALENT or DUPLICATE.

    Args:
        original: Original module source.
        mutant_sources: Mutated module sources, in mutant id order.

    Returns:
        One (label, index_of_duplicated_mutant) pair per mutant; the label is
        "" for a kept mutant and the index is set only for DUPLICATE.

    Raises:
        SyntaxError: if a source does not compile (generation already
            guarantees it does).

    Side effects:
        None.
    """
    original_keys = {ast_signature(original), bytecode_signature(original)}
    seen: dict[Any, int] = {}
    labels: list[tuple[str, int | None]] = []
    for index, source in enumerate(mutant_sources):
        keys = (ast_signature(source), bytecode_signature(source))
        if any(k in original_keys for k in keys):
            labels.append((TCE_EQUIVALENT, None))
            continue
        duplicate_of = next((seen[k] for k in keys if k in seen), None)
        if duplicate_of is not None:
            labels.append((DUPLICATE, duplicate_of))
            continue
        for k in keys:
            seen[k] = index
        labels.append(("", None))
    return labels
