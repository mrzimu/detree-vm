"""
Local rewrites that remove the copies a structured IR leaves behind.

The passes here look at one statement list at a time: a value is assigned to a
local and read once, immediately after, as a condition. Folding that local into
the condition removes an assignment, a register, and -- for a boolean chain --
the normalization instructions a frontend emits to keep a flag 0/1.

Nothing here reasons across functions or across loop iterations; the guards say
exactly which local and which value are safe.
"""

from __future__ import annotations

from ..ir import nodes
from .common import count_reads, count_writes, is_pure

# Expressions whose value cannot change while a loop runs. `is_pure` allows
# the cursor, buffer and state accessors too, which do change, so a condition
# fold that crosses a loop needs this stricter set.
_STABLE = (
    nodes.Const,
    nodes.ConstFloat,
    nodes.Local,
    nodes.Unary,
    nodes.Binary,
    nodes.Compare,
    nodes.Cast,
    nodes.StrConst,
    nodes.StrEq,
)


def _stable(expr: nodes.Expr | None) -> bool:
    if expr is None:
        return True
    if not isinstance(expr, _STABLE):
        return False
    if isinstance(expr, (nodes.Unary, nodes.Cast)):
        return _stable(expr.x)
    if isinstance(expr, (nodes.Binary, nodes.Compare, nodes.StrEq)):
        return _stable(expr.a) and _stable(expr.b)
    return True


def _fold(body: list) -> bool:
    changed = False
    for stmt in body:
        if isinstance(stmt, nodes.If):
            changed = _fold(stmt.then_body) or changed
            changed = _fold(stmt.else_body) or changed
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            changed = _fold(stmt.body) or changed

    assigned: dict[str, tuple[int, object]] = {}
    for index, stmt in enumerate(body):
        if isinstance(stmt, nodes.Assign) and isinstance(stmt.target, nodes.Local):
            assigned[stmt.target.name] = (index, stmt.value)

    drop: set[int] = set()
    for index, stmt in enumerate(body):
        if not isinstance(stmt, (nodes.If, nodes.While)):
            continue
        condition = stmt.cond
        if not isinstance(condition, nodes.Local):
            continue
        entry = assigned.get(condition.name)
        if entry is None or entry[0] >= index:
            continue
        value = entry[1]
        if count_writes(body, condition.name) != 1:
            continue
        if count_reads(body, condition.name) != 1:
            continue
        if not is_pure(value):
            continue
        if isinstance(stmt, nodes.While) and not _stable(value):
            continue
        stmt.cond = value
        drop.add(entry[0])
        changed = True

    if drop:
        body[:] = [stmt for index, stmt in enumerate(body) if index not in drop]
    return changed


def condition_locals(program: nodes.Program) -> bool:
    """
    Fold a single-use local assigned just before a condition into it.

    Args:
        program: the program to rewrite in place.

    Returns:
        `True` when any branch or loop condition was folded.
    """
    changed = False
    for func in program.functions:
        changed = _fold(func.body) or changed
    return changed


__all__ = ["condition_locals"]
