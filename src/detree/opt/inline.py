"""
Inlining of small single-call-site functions.

Only a straight-line callee is inlined: its body has no assignments, no nested
control flow, and no calls, and ends in one `Return`. That keeps substitution
to the single parameter and avoids name capture. Recursive call graphs are
never inlined because a callee that calls anything is rejected.
"""

from __future__ import annotations

import copy

from ..ir import nodes
from .common import each_stmt, is_pure, map_expr, substitute_expr, substitute_stmt


def _has_call(expr: nodes.Expr) -> bool:
    found = False

    def visit(node: nodes.Expr) -> nodes.Expr:
        nonlocal found
        if isinstance(node, nodes.Call):
            found = True
        return node

    map_expr(expr, visit)
    return found


def _count_calls(program: nodes.Program) -> dict:
    """
    `(caller, callee)` -> call sites inside that caller.

    Keying by caller is what lets one small leaf be inlined into several
    callers: the growth is bounded by the callers, not by the program.
    """
    counts = {}
    for index, func in enumerate(program.functions):
        for stmt in each_stmt(func.body):
            if isinstance(stmt, nodes.CallStmt):
                key = (index, stmt.func)
                counts[key] = counts.get(key, 0) + 1
    for index, func in enumerate(program.functions):
        for stmt in each_stmt(func.body):
            for field in (
                "value",
                "count",
                "stride",
                "cond",
                "start",
                "stop",
                "arg",
                "a",
                "b",
                "index",
                "pos",
                "tag",
            ):
                expr = getattr(stmt, field, None)
                if isinstance(expr, nodes.Expr):
                    names = []

                    def visit(node: nodes.Expr, names: list[str] = names) -> nodes.Expr:
                        if isinstance(node, nodes.Call):
                            names.append(node.func)
                        return node

                    map_expr(expr, visit)
                    for name in names:
                        key = (index, name)
                        counts[key] = counts.get(key, 0) + 1
    return counts


# A callee larger than this is not inlined even when it has one call site per
# caller: the copy would grow the module for no dispatch saving.
MAX_BODY = 6


def _eligible(callee: nodes.Function) -> bool:
    if not callee.body or not isinstance(callee.body[-1], nodes.Return):
        return False
    if len(callee.body) > MAX_BODY:
        return False
    for stmt in callee.body[:-1]:
        if isinstance(
            stmt,
            (
                nodes.Assign,
                nodes.CallStmt,
                nodes.If,
                nodes.While,
                nodes.ForRange,
                nodes.Return,
                nodes.Throw,
            ),
        ):
            return False
        for field in (
            "value",
            "count",
            "stride",
            "cond",
            "start",
            "stop",
            "arg",
            "a",
            "b",
            "index",
            "pos",
            "tag",
        ):
            expr = getattr(stmt, field, None)
            if isinstance(expr, nodes.Expr) and _has_call(expr):
                return False
    value = callee.body[-1].value
    return value is None or not _has_call(value)


def _inline_call(
    program: nodes.Program,
    stmt: nodes.CallStmt,
    callee: nodes.Function,
) -> object:
    if callee.arity == 1 and (stmt.arg is None or not is_pure(stmt.arg)):
        return None
    if callee.arity == 0 and stmt.arg is not None:
        return None
    out = []
    for item in callee.body[:-1]:
        cloned = copy.deepcopy(item)
        if callee.arity == 1:
            substitute_stmt(cloned, callee.params[0], stmt.arg)
        out.append(cloned)
    returned = callee.body[-1].value
    if returned is not None:
        if stmt.target is None:
            return None
        value = (
            substitute_expr(returned, callee.params[0], stmt.arg)
            if callee.arity
            else copy.deepcopy(returned)
        )
        out.append(nodes.Assign(nodes.Local(stmt.target.name), value))
    return out


def _rewrite(body: list, program: nodes.Program, counts: dict, caller: int) -> list:
    for stmt in body:
        if isinstance(stmt, nodes.If):
            stmt.then_body = _rewrite(stmt.then_body, program, counts, caller)
            stmt.else_body = _rewrite(stmt.else_body, program, counts, caller)
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            stmt.body = _rewrite(stmt.body, program, counts, caller)

    out = []
    for stmt in body:
        if isinstance(stmt, nodes.CallStmt) and counts.get((caller, stmt.func), 0) == 1:
            callee = program.functions[stmt.func]
            if _eligible(callee):
                inlined = _inline_call(program, stmt, callee)
                if inlined is not None:
                    out.extend(inlined)
                    continue
        out.append(stmt)
    return out


def inline_small(program: nodes.Program) -> bool:
    """
    Inline a straight-line callee called at most once per caller.

    Tying the budget to the caller keeps the module from growing.
    """
    counts = _count_calls(program)
    changed = False
    for index, func in enumerate(program.functions):
        before = repr(func.body)
        func.body = _rewrite(func.body, program, counts, index)
        if repr(func.body) != before:
            changed = True
    return changed


__all__ = ["inline_small"]
