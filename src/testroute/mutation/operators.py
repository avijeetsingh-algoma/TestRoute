"""AST mutation operators.

Component: C9 (Mutation Analyzer), mutant generation.

Responsibility:
    Generates first-order mutants of a subject module, one per applicable
    site and replacement, restricted to the body of the entry-point function
    and the helper functions it calls (spec Section 6.1). Operator families:

    AOR  arithmetic operator replacement (sensible pairs, not all 42)
    ROR  relational operator replacement
    COR  conditional operators: ``and`` with ``or``, removal of ``not``
    MEM  membership and identity: ``in`` with ``not in``, ``is`` with ``is not``
    CON  constants: int n to n+1 and n-1, True with False, "" to "X"
    ASG  augmented assignment operator swaps
    RET  ``return expr`` to ``return None``
    IDX  +1 and -1 on numeric slice bounds and constant subscripts
    BRK  ``break`` with ``continue`` (both directions)

    This replaces mutmut (spec Section 3): mutmut needs ``fork``, which native
    Windows does not have.

    How a mutant is built: the site is found by walking the AST, and only the
    text span of the changed node is replaced in the original source (using
    the node's line and byte-column positions). Everything else, including
    comments and line numbers of the changed line, stays identical, so the
    mutant's line refers to the same line the coverage report uses. An
    expression replacement is wrapped in parentheses, which keeps operator
    precedence correct (``a - b * c`` with ``*`` to ``+`` becomes
    ``a - (b + c)``, not ``a - b + c``).

Receives: module source and entry-point name.
Produces: a list of :class:`GeneratedMutant` (not yet filtered).
Talks to: used by ``mutation.mutant_set``; ``mutation.tce`` filters the output.
Spec sections: 3 (mutmut row), 6.1.
Feedback IDs: FB2 (complete mutant inventory as the base of the equivalence
    pipeline), FB4 (own tool, tuned for speed).
"""

from __future__ import annotations

import ast
import copy
import difflib
from dataclasses import dataclass
from typing import Callable, Iterator

# AOR replacement pairs. Each operator maps to one or two operators that keep
# the expression type-plausible, the usual "sensible pairs" choice instead of
# every possible swap.
AOR_PAIRS: dict[type[ast.operator], tuple[type[ast.operator], ...]] = {
    ast.Add: (ast.Sub,),
    ast.Sub: (ast.Add,),
    ast.Mult: (ast.Div, ast.Add),
    ast.Div: (ast.Mult, ast.FloorDiv),
    ast.FloorDiv: (ast.Div, ast.Mult),
    ast.Mod: (ast.FloorDiv,),
    ast.Pow: (ast.Mult,),
}

# Augmented assignment swaps (spec 6.1): the same pairs as AOR.
ASG_PAIRS = AOR_PAIRS

# ROR replacements: the two boundary neighbours for ordering operators and
# the negation for equality.
ROR_PAIRS: dict[type[ast.cmpop], tuple[type[ast.cmpop], ...]] = {
    ast.Lt: (ast.LtE, ast.Gt),
    ast.LtE: (ast.Lt, ast.GtE),
    ast.Gt: (ast.GtE, ast.Lt),
    ast.GtE: (ast.Gt, ast.LtE),
    ast.Eq: (ast.NotEq,),
    ast.NotEq: (ast.Eq,),
}

# Membership and identity swaps.
MEM_PAIRS: dict[type[ast.cmpop], tuple[type[ast.cmpop], ...]] = {
    ast.In: (ast.NotIn,),
    ast.NotIn: (ast.In,),
    ast.Is: (ast.IsNot,),
    ast.IsNot: (ast.Is,),
}

FAMILIES: tuple[str, ...] = ("AOR", "ROR", "COR", "MEM", "CON", "ASG", "RET", "IDX", "BRK")


@dataclass(frozen=True)
class GeneratedMutant:
    """One generated mutant before TCE and duplicate filtering.

    Attributes:
        operator: Operator family (one of FAMILIES).
        line: 1-based line of the changed node.
        col: 0-based character column of the changed node.
        original_line: That line in the original module.
        mutated_line: The same line in the mutant (for a multi-line node, the
            first line of the replacement).
        diff: Unified diff of original against mutant.
        source: Complete mutated module source.
        description: Short text, for example ``< -> <=``.
    """

    operator: str
    line: int
    col: int
    original_line: str
    mutated_line: str
    diff: str
    source: str
    description: str


@dataclass(frozen=True)
class _Site:
    """A planned mutation: which node (by walk index) and how to change it."""

    operator: str
    node_index: int
    apply: Callable[[ast.AST], ast.AST]
    description: str
    statement: bool = False


def target_functions(tree: ast.Module, entry_point: str) -> list[ast.FunctionDef]:
    """The entry point plus every top-level function it calls, transitively.

    Helpers are found by direct calls to module-level names (``helper(x)``),
    which is how HumanEval solutions use helpers.

    Raises:
        ValueError: if the entry point is not a top-level function.
    """
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if entry_point not in functions:
        raise ValueError(f"entry point {entry_point} not found")
    selected: list[str] = []
    pending = [entry_point]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.append(name)
        for node in ast.walk(functions[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
                pending.append(node.func.id)
    # Keep source order so mutant ids follow the file from top to bottom.
    return [functions[n] for n in functions if n in selected]


def _body_nodes(func: ast.FunctionDef) -> Iterator[ast.AST]:
    """Every node inside the function body (not its signature or decorators)."""
    for statement in func.body:
        yield from ast.walk(statement)


def _int_value(node: ast.AST) -> int | None:
    """Integer value of ``5`` or ``-5`` literals; None otherwise (bools excluded)."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        if type(node.operand.value) is int:
            return -node.operand.value
    return None


def _int_literal(value: int) -> ast.expr:
    """AST for an integer literal, negative values as unary minus."""
    return ast.UnaryOp(ast.USub(), ast.Constant(-value)) if value < 0 else ast.Constant(value)


def _is_docstring(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)


def _plan_sites(func: ast.FunctionDef, nodes: list[ast.AST]) -> list[_Site]:
    """List every mutation site in one function, in walk order."""
    index = {id(n): i for i, n in enumerate(nodes)}
    # Constants handled by IDX (subscript and slice bounds) and docstrings are
    # skipped by CON so no change is generated twice.
    skip_constants: set[int] = set()
    docstrings = {id(s.value) for s in func.body[:1] if _is_docstring(s)}
    for node in nodes:
        if isinstance(node, ast.Subscript):
            target = node.slice
            parts = [target.lower, target.upper] if isinstance(target, ast.Slice) else [target]
            for part in parts:
                if part is not None and _int_value(part) is not None:
                    skip_constants.add(id(part))
                    if isinstance(part, ast.UnaryOp):
                        skip_constants.add(id(part.operand))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and _int_value(node) is not None:
            # -5 is mutated as one literal (to -4 and -6), not via its operand.
            skip_constants.add(id(node.operand))

    # Nodes inside f-strings are skipped: on Python 3.11 their positions are
    # not reliable, so a text splice could corrupt the string.
    in_fstring = {id(inner) for n in nodes if isinstance(n, ast.JoinedStr) for inner in ast.walk(n)}

    sites: list[_Site] = []
    for node in nodes:
        if id(node) in in_fstring:
            continue
        i = index[id(node)]
        if isinstance(node, ast.BinOp) and type(node.op) in AOR_PAIRS:
            for new in AOR_PAIRS[type(node.op)]:
                sites.append(_Site("AOR", i, _set_attr("op", new), f"{_op(node.op)} -> {_op(new())}"))
        elif isinstance(node, ast.AugAssign) and type(node.op) in ASG_PAIRS:
            for new in ASG_PAIRS[type(node.op)]:
                sites.append(_Site("ASG", i, _set_attr("op", new), f"{_op(node.op)}= -> {_op(new())}=", True))
        elif isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                family, pairs = ("ROR", ROR_PAIRS) if type(op) in ROR_PAIRS else ("MEM", MEM_PAIRS)
                for new in pairs.get(type(op), ()):
                    sites.append(_Site(family, i, _set_cmp(k, new), f"{_op(op)} -> {_op(new())}"))
        elif isinstance(node, ast.BoolOp):
            new = ast.Or if isinstance(node.op, ast.And) else ast.And
            sites.append(_Site("COR", i, _set_attr("op", new), f"{_op(node.op)} -> {_op(new())}"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append(_Site("COR", i, lambda n: n.operand, "remove not"))
        elif isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                sites.append(_Site("RET", i, lambda n: ast.Return(ast.Constant(None)), "return value -> None", True))
        elif isinstance(node, ast.Break):
            sites.append(_Site("BRK", i, lambda n: ast.Continue(), "break -> continue", True))
        elif isinstance(node, ast.Continue):
            sites.append(_Site("BRK", i, lambda n: ast.Break(), "continue -> break", True))
        elif isinstance(node, ast.Subscript):
            target = node.slice
            if isinstance(target, ast.Slice):
                for field_name in ("lower", "upper"):
                    part = getattr(target, field_name)
                    value = _int_value(part) if part is not None else None
                    if value is not None:
                        for delta in (1, -1):
                            sites.append(_Site("IDX", index[id(part)], _replace_int(value + delta),
                                               f"slice {field_name} {value} -> {value + delta}"))
            else:
                value = _int_value(target)
                if value is not None:
                    for delta in (1, -1):
                        sites.append(_Site("IDX", index[id(target)], _replace_int(value + delta),
                                           f"index {value} -> {value + delta}"))
        if id(node) in skip_constants or id(node) in docstrings:
            continue
        value = _int_value(node)
        if value is not None and (isinstance(node, ast.Constant) or isinstance(node, ast.UnaryOp)):
            for delta in (1, -1):
                sites.append(_Site("CON", i, _replace_int(value + delta), f"{value} -> {value + delta}"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            sites.append(_Site("CON", i, lambda n: ast.Constant(not n.value), f"{node.value} -> {not node.value}"))
        elif isinstance(node, ast.Constant) and node.value == "" and isinstance(node.value, str):
            sites.append(_Site("CON", i, lambda n: ast.Constant("X"), '"" -> "X"'))
    return sites


def _set_attr(name: str, new_type: type) -> Callable[[ast.AST], ast.AST]:
    def apply(node: ast.AST) -> ast.AST:
        setattr(node, name, new_type())
        return node

    return apply


def _set_cmp(k: int, new_type: type) -> Callable[[ast.AST], ast.AST]:
    def apply(node: ast.AST) -> ast.AST:
        node.ops[k] = new_type()
        return node

    return apply


def _replace_int(value: int) -> Callable[[ast.AST], ast.AST]:
    return lambda node: _int_literal(value)


_OP_TEXT = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
    ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not", ast.And: "and", ast.Or: "or",
}


def _op(op: ast.AST) -> str:
    return _OP_TEXT.get(type(op), type(op).__name__)


def _splice(source: str, node: ast.AST, replacement: str) -> str:
    """Replace the exact text span of ``node`` in ``source``.

    ``col_offset`` values from ``ast`` are UTF-8 byte offsets, so each line is
    handled as bytes to stay correct for non-ASCII source text.
    """
    lines = source.splitlines(keepends=True)
    first, last = node.lineno - 1, node.end_lineno - 1  # type: ignore[attr-defined]
    head = lines[first].encode("utf-8")[: node.col_offset].decode("utf-8")
    tail = lines[last].encode("utf-8")[node.end_col_offset :].decode("utf-8")  # type: ignore[attr-defined]
    return "".join(lines[:first]) + head + replacement + tail + "".join(lines[last + 1 :])


def generate_mutants(source: str, entry_point: str) -> list[GeneratedMutant]:
    """Generate every first-order mutant of the entry point and its helpers.

    Args:
        source: Module source (``\\n`` line endings).
        entry_point: Function under test.

    Returns:
        Mutants in source order, before TCE and duplicate filtering. Mutants
        whose spliced source does not parse are dropped (cannot happen for the
        operators above, but checked so a malformed mutant never reaches a run).

    Raises:
        ValueError: if the entry point is missing.
        SyntaxError: if ``source`` does not parse.

    Side effects:
        None.
    """
    tree = ast.parse(source)
    original_lines = source.splitlines()
    mutants: list[GeneratedMutant] = []
    for func in target_functions(tree, entry_point):
        nodes = list(_body_nodes(func))
        for site in _plan_sites(func, nodes):
            node = nodes[site.node_index]
            new_node = site.apply(copy.deepcopy(node))
            text = ast.unparse(new_node)
            if not site.statement:
                text = f"({text})"
            mutated = _splice(source, node, text)
            try:
                ast.parse(mutated)
            except SyntaxError:
                continue
            mutated_lines = mutated.splitlines()
            line = node.lineno
            diff = "".join(
                difflib.unified_diff(
                    source.splitlines(keepends=True), mutated.splitlines(keepends=True),
                    fromfile="original.py", tofile="mutant.py", n=1,
                )
            )
            mutants.append(
                GeneratedMutant(
                    operator=site.operator,
                    line=line,
                    col=node.col_offset,
                    original_line=original_lines[line - 1],
                    mutated_line=mutated_lines[line - 1],
                    diff=diff,
                    source=mutated,
                    description=site.description,
                )
            )
    return mutants
