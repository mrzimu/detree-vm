"""
Loop unrolling and counted-loop normalization.

Unrolling is bounded by body size and iteration count, and never changes the
loop variable's value after the loop: a loop whose variable is read later is
left alone.
"""

from __future__ import annotations

import copy

from ..ir import nodes
from .common import body_reads, each_stmt, substitute_stmt

MAX_ITERATIONS = 8
MAX_BODY = 4


def _iterations(start: int, stop: int, step: int) -> int:
    if step > 0:
        return max(0, (stop - start + step - 1) // step)
    return max(0, (start - stop + (-step) - 1) // (-step))


def _writes(body: list[nodes.Stmt], var: str) -> bool:
    for stmt in each_stmt(body):
        if isinstance(stmt, nodes.Assign) and isinstance(stmt.target, nodes.Local):
            if stmt.target.name == var:
                return True
        elif isinstance(stmt, nodes.CallStmt) and stmt.target is not None:
            if stmt.target.name == var:
                return True
        elif isinstance(stmt, nodes.ForRange) and stmt.var == var:
            return True
    return False


def _rewrite(body: list) -> list:
    for stmt in body:
        if isinstance(stmt, nodes.If):
            stmt.then_body = _rewrite(stmt.then_body)
            stmt.else_body = _rewrite(stmt.else_body)
        elif isinstance(stmt, nodes.While):
            stmt.body = _rewrite(stmt.body)
        elif isinstance(stmt, nodes.ForRange):
            stmt.body = _rewrite(stmt.body)

    out = []
    for index, stmt in enumerate(body):
        if isinstance(stmt, nodes.ForRange) and len(stmt.body) <= MAX_BODY:
            start = stmt.start.value if isinstance(stmt.start, nodes.Const) else None
            stop = stmt.stop.value if isinstance(stmt.stop, nodes.Const) else None
            if start is not None and stop is not None:
                count = _iterations(start, stop, stmt.step)
                if count <= MAX_ITERATIONS and stmt.var not in body_reads(body[index + 1 :]):
                    if not _writes(stmt.body, stmt.var):
                        for iteration in range(count):
                            value = start + iteration * stmt.step
                            for inner in stmt.body:
                                cloned = copy.deepcopy(inner)
                                substitute_stmt(cloned, stmt.var, nodes.Const(value))
                                out.append(cloned)
                        continue
        out.append(stmt)
    return out


def range_unroll(program: nodes.Program) -> bool:
    """
    Unroll a small constant `ForRange` whose variable is dead afterwards.

    Args:
        program: the program to rewrite in place.

    Returns:
        `True` when any loop was unrolled.
    """
    changed = False
    for func in program.functions:
        before = repr(func.body)
        func.body = _rewrite(func.body)
        if repr(func.body) != before:
            changed = True
    return changed


__all__ = ["range_unroll"]
