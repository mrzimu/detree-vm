"""
Dead-code and unreachable-code elimination.

A pass never removes a read, skip, buffer, state, reference, or string mutation,
even when its result is dead.
"""

from __future__ import annotations

from ..ir import nodes
from .common import body_reads, each_stmt, is_pure, is_terminator, map_stmt_exprs


def _terminates(body: list[nodes.Stmt]) -> bool:
    if not body:
        return False
    last = body[-1]
    if is_terminator(last):
        return True
    if isinstance(last, nodes.If):
        return _terminates(last.then_body) and _terminates(last.else_body)
    return False


def _strip(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
    out = []
    live = True
    for stmt in body:
        if not live:
            continue
        if isinstance(stmt, nodes.If):
            stmt.then_body = _strip(stmt.then_body)
            stmt.else_body = _strip(stmt.else_body)
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            stmt.body = _strip(stmt.body)
        out.append(stmt)
        if is_terminator(stmt):
            live = False
        elif (
            isinstance(stmt, nodes.If)
            and _terminates(stmt.then_body)
            and _terminates(stmt.else_body)
        ):
            live = False
    return out


def _strip_program(program: nodes.Program) -> bool:
    changed = False
    for func in program.functions:
        before = repr(func.body)
        func.body = _strip(func.body)
        if repr(func.body) != before:
            changed = True
    return changed


def unreachable(program: nodes.Program) -> bool:
    """
    Drop statements after a terminator, recursively.

    Args:
        program: the program to rewrite in place.

    Returns:
        `True` when any body shrank.
    """
    return _strip_program(program)


def _callee_of(node: nodes.Stmt | nodes.Expr) -> int | None:
    """
    The function id a call names, or `None` for anything else.

    Statements and expressions are accepted so one helper serves both walks.
    """
    if isinstance(node, (nodes.CallStmt, nodes.Call)):
        return node.func
    return None


def _calls(body: list[nodes.Stmt]) -> set[int]:
    """
    Every function id called from *body*.

    Calls inside expressions count too, because a callee reached through an
    argument is still reachable.
    """
    found = set()

    def visit(expr: nodes.Expr) -> nodes.Expr:
        callee = _callee_of(expr)
        if callee is not None:
            found.add(callee)
        return expr

    for stmt in each_stmt(body):
        callee = _callee_of(stmt)
        if callee is not None:
            found.add(callee)
        map_stmt_exprs(stmt, visit)
    return found


def dead_function(program: nodes.Program) -> bool:
    """
    Drop functions the entry point cannot reach, and renumber the rest.

    A compiler emits the same four entry points for every program unit, while
    the machine only ever calls `program.entry`. Keeping the uncalled ones
    costs module size and, worse, inflates the call count the inliner sees.
    """
    reachable = {program.entry}
    pending = [program.entry]
    while pending:
        for callee in _calls(program.functions[pending.pop()].body):
            if callee not in reachable:
                reachable.add(callee)
                pending.append(callee)

    if len(reachable) == len(program.functions):
        return False

    kept = sorted(reachable)
    mapping = {old: new for new, old in enumerate(kept)}
    program.functions = [program.functions[old] for old in kept]
    program.entry = mapping[program.entry]

    def remap(expr: nodes.Expr) -> nodes.Expr:
        callee = _callee_of(expr)
        if callee is not None:
            expr.func = mapping[callee]
        return expr

    for func in program.functions:
        for stmt in each_stmt(func.body):
            if isinstance(stmt, nodes.CallStmt):
                stmt.func = mapping[stmt.func]
            map_stmt_exprs(stmt, remap)
    return True


def _drop_pure(body: list[nodes.Stmt], used: set[str]) -> list[nodes.Stmt]:
    out = []
    for stmt in body:
        if isinstance(stmt, nodes.If):
            stmt.then_body = _drop_pure(stmt.then_body, used)
            stmt.else_body = _drop_pure(stmt.else_body, used)
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            stmt.body = _drop_pure(stmt.body, used)
        if (
            isinstance(stmt, nodes.Assign)
            and isinstance(stmt.target, nodes.Local)
            and stmt.target.name not in used
            and is_pure(stmt.value)
        ):
            continue
        out.append(stmt)
    return out


def dead_code(program: nodes.Program) -> bool:
    """
    Drop unreachable statements and assignments whose result is never read.

    Args:
        program: the program to rewrite in place.

    Returns:
        `True` when the program shrank.
    """
    changed = _strip_program(program)
    for func in program.functions:
        before = repr(func.body)
        used = body_reads(func.body)
        func.body = _drop_pure(func.body, used)
        if repr(func.body) != before:
            changed = True
    return changed


__all__ = ["dead_code", "unreachable"]
