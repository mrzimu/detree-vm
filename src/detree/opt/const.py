"""
Constant folding and constant-branch elimination.

See `docs/spec/ir.md` §Optimizer passes. Division by zero and `INT64_MIN / -1`
are never folded: hiding either would hide a machine error under a constant.
"""

from __future__ import annotations

from ..ir import nodes
from ..ir.verify import op_of
from ..spec.isa import Op
from .common import map_program, map_stmt_exprs

MASK64 = (1 << 64) - 1
SIGN64 = 1 << 63


def _u(value: int) -> int:
    return value & MASK64


def _s(value: int) -> int:
    value &= MASK64
    return value - (1 << 64) if value & SIGN64 else value


def _trunc_div(a: int, b: int) -> int:
    if b == 0:
        return 0
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


def _binary(op: Op, left: int, right: int) -> int | None:
    a = _s(left)
    b = _s(right)
    if op == Op.ADD:
        return _s(a + b)
    if op == Op.SUB:
        return _s(a - b)
    if op == Op.MUL:
        return _s(a * b)
    if op == Op.DIV_S:
        if b == 0 or (a == -(1 << 63) and b == -1):
            return None
        return _s(_trunc_div(a, b))
    if op == Op.DIV_U:
        if _u(right) == 0:
            return None
        return _s(_u(left) // _u(right))
    if op == Op.MOD_S:
        if b == 0 or (a == -(1 << 63) and b == -1):
            return None
        return _s(a - _trunc_div(a, b) * b)
    if op == Op.MOD_U:
        if _u(right) == 0:
            return None
        return _s(_u(left) % _u(right))
    if op == Op.SHL:
        return _s(_u(left) << (_u(right) & 63))
    if op == Op.SHR_S:
        return _s(_s(left) >> (_u(right) & 63))
    if op == Op.SHR_U:
        return _s(_u(left) >> (_u(right) & 63))
    if op == Op.AND:
        return _s(_u(left) & _u(right))
    if op == Op.OR:
        return _s(_u(left) | _u(right))
    if op == Op.XOR:
        return _s(_u(left) ^ _u(right))
    return None


def _compare(op: Op, left: int, right: int) -> int:
    a = _s(left)
    b = _s(right)
    if op == Op.CMP_EQ:
        return int(_u(left) == _u(right))
    if op == Op.CMP_NE:
        return int(_u(left) != _u(right))
    if op == Op.CMP_LT_S:
        return int(a < b)
    if op == Op.CMP_LE_S:
        return int(a <= b)
    if op == Op.CMP_GT_S:
        return int(a > b)
    if op == Op.CMP_GE_S:
        return int(a >= b)
    if op == Op.CMP_LT_U:
        return int(_u(left) < _u(right))
    if op == Op.CMP_LE_U:
        return int(_u(left) <= _u(right))
    if op == Op.CMP_GT_U:
        return int(_u(left) > _u(right))
    if op == Op.CMP_GE_U:
        return int(_u(left) >= _u(right))
    return 0


def _fold(node: nodes.Expr, program: nodes.Program) -> nodes.Expr:
    if isinstance(node, nodes.Unary):
        if not isinstance(node.x, nodes.Const):
            return node
        value = node.x.value
        op = op_of(node.op)
        if op == Op.NEG:
            return nodes.Const(_s(-value))
        if op == Op.NOT:
            return nodes.Const(_s(~value))
        if op == Op.NOT_B:
            return nodes.Const(1 if _u(value) == 0 else 0)
        return node
    if isinstance(node, nodes.Binary):
        if not isinstance(node.a, nodes.Const) or not isinstance(node.b, nodes.Const):
            return node
        value = _binary(op_of(node.op), node.a.value, node.b.value)
        return node if value is None else nodes.Const(value)
    if isinstance(node, nodes.Compare):
        if not isinstance(node.a, nodes.Const) or not isinstance(node.b, nodes.Const):
            return node
        return nodes.Const(_compare(op_of(node.op), node.a.value, node.b.value))
    if isinstance(node, nodes.Cast):
        if not isinstance(node.x, nodes.Const):
            return node
        value = _u(node.x.value)
        bits = node.width
        mask = (1 << bits) - 1
        value &= mask
        if node.kind == "sext" and value & (1 << (bits - 1)):
            value |= ~mask & MASK64
        return nodes.Const(_s(value))
    if isinstance(node, nodes.StrEq):
        if isinstance(node.a, nodes.StrConst) and isinstance(node.b, nodes.StrConst):
            try:
                equal = (
                    program.constants[node.a.index].text
                    == program.constants[node.b.index].text
                )
            except IndexError:
                return node
            return nodes.Const(int(equal))
    return node


def const_fold(program: nodes.Program) -> bool:
    """
    Fold pure constant expressions in every function.

    Only expressions without stream, buffer, state, or call effects fold.
    """
    changed = False

    def stmt(stmt: nodes.Stmt) -> object:
        nonlocal changed
        before = repr(stmt)
        map_stmt_exprs(stmt, lambda node: _fold(node, program))
        if repr(stmt) != before:
            changed = True
        return stmt

    map_program(program, stmt)
    return changed


def const_branch(program: nodes.Program) -> bool:
    """
    Keep one arm of an `If` whose condition is constant.

    The dropped arm must not contain a read or a call that the other arm
    relies on; the pass only picks the live branch.
    """
    changed = False

    def stmt(statement: nodes.Stmt) -> object:
        nonlocal changed
        if isinstance(statement, nodes.If) and isinstance(statement.cond, nodes.Const):
            changed = True
            return statement.then_body if _u(statement.cond.value) else statement.else_body
        if isinstance(statement, nodes.While) and isinstance(statement.cond, nodes.Const):
            if _u(statement.cond.value) == 0:
                changed = True
                return None
        if isinstance(statement, nodes.ForRange):
            start = statement.start
            stop = statement.stop
            if isinstance(start, nodes.Const) and isinstance(stop, nodes.Const):
                span = _s(stop.value) - _s(start.value)
                if statement.step > 0 and span <= 0:
                    changed = True
                    return None
                if statement.step < 0 and span >= 0:
                    changed = True
                    return None
        return statement

    map_program(program, stmt)
    return changed


__all__ = ["const_branch", "const_fold"]
