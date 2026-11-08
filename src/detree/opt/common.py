"""
Shared helpers for the optimizer passes.

The pass manager deep-copies a program once and passes the copy through
mutating passes; these helpers make the tree walk and the effect queries
uniform so a pass states only its rewrite.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import fields

from ..ir import nodes


def map_expr(expr: nodes.Expr, fn: Callable[[nodes.Expr], nodes.Expr]) -> nodes.Expr:
    """
    Rewrite *expr* bottom-up.

    Args:
        expr: the root expression.
        fn: called on each node after its children were rewritten; returns the
            replacement.

    Returns:
        The rewritten expression.
    """
    if not isinstance(expr, nodes.Expr):
        return expr
    for field in fields(expr):
        value = getattr(expr, field.name)
        if isinstance(value, nodes.Expr):
            setattr(expr, field.name, map_expr(value, fn))
    return fn(expr)


def map_stmt_exprs(
    stmt: nodes.Stmt,
    fn: Callable[[nodes.Expr], nodes.Expr],
) -> None:
    """
    Rewrite the expression fields of one statement in place.

    Targets and nested bodies are left alone, so a caller decides where the
    rewrite descends.

    Args:
        stmt: the statement to rewrite.
        fn: the expression rewrite, as in `map_expr`.
    """
    for field in fields(stmt):
        if field.name in ("target", "body", "then_body", "else_body"):
            continue
        value = getattr(stmt, field.name)
        if isinstance(value, nodes.Expr):
            setattr(stmt, field.name, map_expr(value, fn))


def map_body(
    body: list[nodes.Stmt],
    fn: Callable[[nodes.Stmt], object],
) -> list[nodes.Stmt]:
    """
    Rewrite a statement list, recursing into structured bodies first.

    *fn* returns a statement, a list of statements, or `None` to drop it.

    Args:
        body: the statements to rewrite.
        fn: the per-statement rewrite.

    Returns:
        The rewritten statement list.
    """
    out = []
    for stmt in body:
        if isinstance(stmt, nodes.If):
            stmt.then_body = map_body(stmt.then_body, fn)
            stmt.else_body = map_body(stmt.else_body, fn)
        elif isinstance(stmt, nodes.While):
            stmt.body = map_body(stmt.body, fn)
        elif isinstance(stmt, nodes.ForRange):
            stmt.body = map_body(stmt.body, fn)
        replacement = fn(stmt)
        if replacement is None:
            continue
        if isinstance(replacement, list):
            out.extend(replacement)
        else:
            out.append(replacement)
    return out


def map_program(
    program: nodes.Program,
    fn: Callable[[nodes.Stmt], object],
) -> bool:
    """
    Apply *fn* to every function body.

    Args:
        program: the program to rewrite in place.
        fn: the per-statement rewrite, as in `map_body`.

    Returns:
        `True` when any body changed.
    """
    changed = False
    for func in program.functions:
        before = repr(func.body)
        func.body = map_body(func.body, fn)
        if repr(func.body) != before:
            changed = True
    return changed


def each_stmt(body: list[nodes.Stmt]) -> Iterator[nodes.Stmt]:
    """
    Yield every statement in *body*.

    Nested bodies are visited in source order, after their parent.

    Args:
        body: the statements to walk.

    Yields:
        Each statement, including those inside `If`, `While`, and `ForRange`.
    """
    for stmt in body:
        yield stmt
        if isinstance(stmt, nodes.If):
            yield from each_stmt(stmt.then_body)
            yield from each_stmt(stmt.else_body)
        elif isinstance(stmt, nodes.While):
            yield from each_stmt(stmt.body)
        elif isinstance(stmt, nodes.ForRange):
            yield from each_stmt(stmt.body)


def count_reads(body: list[nodes.Stmt], name: str) -> int:
    """
    How often *name* is read in *body*.

    Args:
        body: the statements to search.
        name: the local's name.

    Returns:
        The read count, nested bodies and expressions included.
    """
    total = 0

    def visit(expr: nodes.Expr) -> nodes.Expr:
        nonlocal total
        if isinstance(expr, nodes.Local) and expr.name == name:
            total += 1
        return expr

    for stmt in each_stmt(body):
        map_stmt_exprs(stmt, visit)
    return total


def count_writes(body: list[nodes.Stmt], name: str) -> int:
    """
    How often *name* is assigned in *body*.

    Args:
        body: the statements to search.
        name: the local's name.

    Returns:
        The assignment count, nested bodies included.
    """
    total = 0
    for stmt in each_stmt(body):
        if isinstance(stmt, nodes.Assign) and isinstance(stmt.target, nodes.Local):
            total += stmt.target.name == name
        elif isinstance(stmt, nodes.CallStmt) and isinstance(stmt.target, nodes.Local):
            total += stmt.target.name == name
    return total


def expr_locals(expr: nodes.Expr, out: set[str] | None = None) -> set[str]:
    """
    The local names *expr* reads.

    Args:
        expr: the expression to inspect.
        out: a set to merge into, when the caller accumulates.

    Returns:
        The local names read by the expression.
    """
    out = set() if out is None else out
    if not isinstance(expr, nodes.Expr):
        return out
    if isinstance(expr, nodes.Local):
        out.add(expr.name)
    for field in fields(expr):
        value = getattr(expr, field.name)
        if isinstance(value, nodes.Expr):
            expr_locals(value, out)
    return out


def body_reads(body: list[nodes.Stmt]) -> set[str]:
    """
    Locals read anywhere in *body*.

    Args:
        body: the statements to inspect.

    Returns:
        The names read, nested expressions included.
    """
    out = set()

    def visit(expr: nodes.Expr) -> nodes.Expr:
        expr_locals(expr, out)
        return expr

    for stmt in body:
        _stmt_exprs(stmt, visit)
        if isinstance(stmt, nodes.If):
            out.update(body_reads(stmt.then_body))
            out.update(body_reads(stmt.else_body))
        elif isinstance(stmt, nodes.While):
            out.update(body_reads(stmt.body))
        elif isinstance(stmt, nodes.ForRange):
            out.update(body_reads(stmt.body))
    return out


def _stmt_exprs(
    stmt: nodes.Stmt,
    visit: Callable[[nodes.Expr], nodes.Expr],
) -> None:
    if isinstance(stmt, nodes.Assign):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.CallStmt):
        if stmt.arg is not None:
            map_expr(stmt.arg, visit)
    elif isinstance(stmt, nodes.BufferAppend):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.BufferAppendOffset):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.BufferReserve):
        map_expr(stmt.count, visit)
    elif isinstance(stmt, nodes.ReadBulk):
        map_expr(stmt.count, visit)
    elif isinstance(stmt, nodes.ReadBulkUntil):
        map_expr(stmt.end, visit)
    elif isinstance(stmt, nodes.ReadStrided):
        map_expr(stmt.count, visit)
        map_expr(stmt.stride, visit)
    elif isinstance(stmt, nodes.ReadFields):
        map_expr(stmt.count, visit)
    elif isinstance(stmt, nodes.SkipR):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.SetCursor):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.If):
        map_expr(stmt.cond, visit)
    elif isinstance(stmt, nodes.While):
        map_expr(stmt.cond, visit)
    elif isinstance(stmt, nodes.ForRange):
        map_expr(stmt.start, visit)
        map_expr(stmt.stop, visit)
    elif isinstance(stmt, nodes.Assert):
        map_expr(stmt.cond, visit)
    elif isinstance(stmt, nodes.AssertCursorEq):
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.AssertStrEq):
        map_expr(stmt.a, visit)
        map_expr(stmt.b, visit)
    elif isinstance(stmt, (nodes.RefSetObj, nodes.RefSetClass)):
        map_expr(stmt.tag, visit)
        map_expr(stmt.value, visit)
    elif isinstance(stmt, nodes.Return) and stmt.value is not None:
        map_expr(stmt.value, visit)


def is_pure(expr: nodes.Expr | None) -> bool:
    """
    Whether evaluating *expr* has no effect.

    Args:
        expr: the expression to inspect.

    Returns:
        `True` when the expression touches no stream, buffer, state, or call.
    """
    if expr is None:
        return True
    if isinstance(
        expr,
        (
            nodes.StreamRead,
            nodes.StreamReadFNB,
            nodes.StreamReadVersion,
            nodes.StreamReadCString,
            nodes.Call,
        ),
    ):
        return False
    for field in fields(expr):
        value = getattr(expr, field.name)
        if isinstance(value, nodes.Expr) and not is_pure(value):
            return False
    return True


def substitute_expr(
    expr: nodes.Expr,
    name: str,
    replacement: nodes.Expr,
) -> nodes.Expr:
    """
    Replace `Local(name)` with *replacement* in a copy of *expr*.

    Args:
        expr: the expression to copy and rewrite.
        name: the local's name.
        replacement: the expression to substitute.

    Returns:
        The rewritten copy; *expr* itself is untouched.
    """
    import copy

    cloned = copy.deepcopy(expr)

    def replace(node: nodes.Expr) -> nodes.Expr:
        if isinstance(node, nodes.Local) and node.name == name:
            return copy.deepcopy(replacement)
        return node

    return map_expr(cloned, replace)


def substitute_stmt(
    stmt: nodes.Stmt,
    name: str,
    replacement: nodes.Expr,
) -> None:
    """
    Replace reads of a local in one statement, in place.

    Args:
        stmt: the statement to rewrite.
        name: the local's name.
        replacement: the expression to substitute.
    """
    if isinstance(stmt, nodes.Assign):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.CallStmt) and stmt.arg is not None:
        stmt.arg = substitute_expr(stmt.arg, name, replacement)
    elif isinstance(stmt, nodes.BufferAppend):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.BufferAppendOffset):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.BufferReserve):
        stmt.count = substitute_expr(stmt.count, name, replacement)
    elif isinstance(stmt, nodes.ReadBulk):
        stmt.count = substitute_expr(stmt.count, name, replacement)
    elif isinstance(stmt, nodes.ReadBulkUntil):
        stmt.end = substitute_expr(stmt.end, name, replacement)
    elif isinstance(stmt, nodes.ReadStrided):
        stmt.count = substitute_expr(stmt.count, name, replacement)
        stmt.stride = substitute_expr(stmt.stride, name, replacement)
    elif isinstance(stmt, nodes.ReadFields):
        stmt.count = substitute_expr(stmt.count, name, replacement)
    elif isinstance(stmt, nodes.SkipR):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.SetCursor):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.If):
        stmt.cond = substitute_expr(stmt.cond, name, replacement)
    elif isinstance(stmt, nodes.While):
        stmt.cond = substitute_expr(stmt.cond, name, replacement)
    elif isinstance(stmt, nodes.ForRange):
        stmt.start = substitute_expr(stmt.start, name, replacement)
        stmt.stop = substitute_expr(stmt.stop, name, replacement)
    elif isinstance(stmt, nodes.Assert):
        stmt.cond = substitute_expr(stmt.cond, name, replacement)
    elif isinstance(stmt, nodes.AssertCursorEq):
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.AssertStrEq):
        stmt.a = substitute_expr(stmt.a, name, replacement)
        stmt.b = substitute_expr(stmt.b, name, replacement)
    elif isinstance(stmt, (nodes.RefSetObj, nodes.RefSetClass)):
        stmt.tag = substitute_expr(stmt.tag, name, replacement)
        stmt.value = substitute_expr(stmt.value, name, replacement)
    elif isinstance(stmt, nodes.Return) and stmt.value is not None:
        stmt.value = substitute_expr(stmt.value, name, replacement)


def is_terminator(stmt: nodes.Stmt) -> bool:
    """
    Whether *stmt* ends its block.

    Only `return` and `throw` terminate; a branch is handled by the callers,
    which recurse into both arms.
    """
    return isinstance(stmt, (nodes.Return, nodes.Throw))


__all__ = [
    "body_reads",
    "each_stmt",
    "expr_locals",
    "is_pure",
    "is_terminator",
    "map_body",
    "map_expr",
    "map_program",
    "substitute_expr",
    "substitute_stmt",
]
