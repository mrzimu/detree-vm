"""
Verification of an IR program.

The rules are listed in `docs/spec/ir.md` §Verification rules: defined-before-use,
call targets, buffer/state/layout references, type compatibility, no float
arithmetic, and a register bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spec.isa import (
    BUFFER_DTYPES,
    INT_BUFFER_DTYPES,
    BufferKind,
    FieldKind,
    Op,
    Typecode,
)
from . import nodes
from .errors import IRValidationError

MAX_REGISTERS = 255

_BINARY_OPS = frozenset(
    {
        Op.ADD,
        Op.SUB,
        Op.MUL,
        Op.DIV_S,
        Op.DIV_U,
        Op.MOD_S,
        Op.MOD_U,
        Op.SHL,
        Op.SHR_S,
        Op.SHR_U,
        Op.AND,
        Op.OR,
        Op.XOR,
    }
)
_COMPARE_OPS = frozenset(
    {
        Op.CMP_EQ,
        Op.CMP_NE,
        Op.CMP_LT_S,
        Op.CMP_LE_S,
        Op.CMP_GT_S,
        Op.CMP_GE_S,
        Op.CMP_LT_U,
        Op.CMP_LE_U,
        Op.CMP_GT_U,
        Op.CMP_GE_U,
    }
)
_UNARY_OPS = frozenset({Op.NEG, Op.NOT, Op.NOT_B})


def op_of(value: Op | str) -> Op:
    """
    Normalize an operator written as an `Op` or as its mnemonic.

    Args:
        value: an `Op` member, or a mnemonic such as `"add"`.

    Returns:
        The corresponding `Op` member.

    Raises:
        IRValidationError: *value* is not a known operator.
    """
    if isinstance(value, Op):
        return value
    if isinstance(value, str):
        try:
            return Op[value.upper()]
        except KeyError:
            raise IRValidationError(f"unknown operator {value!r}") from None
    raise IRValidationError(f"an operator is an Op, not {type(value).__name__}")


def is_int(value_type: str) -> bool:
    """
    Whether *value_type* is an integer type.

    Args:
        value_type: an IR type name.

    Returns:
        `True` for `i64` and `bool`.
    """
    return value_type in (nodes.I64, nodes.BOOL)


def buffer_type(dtype: str) -> str:
    """
    The IR value type a buffer of *dtype* stores.

    Args:
        dtype: a NumPy dtype name.

    Returns:
        The matching IR type name.
    """
    if dtype == "bool":
        return nodes.BOOL
    if dtype == "float32":
        return nodes.F32
    if dtype == "float64":
        return nodes.F64
    return nodes.I64


def compatible(expected: str, actual: str) -> bool:
    """
    Whether *actual* can be used where *expected* is required.

    A `bool` widens to `i64`; nothing else converts implicitly.

    Args:
        expected: the type the context requires.
        actual: the type the expression produced.

    Returns:
        `True` when no cast is needed.
    """
    if expected == actual:
        return True
    if expected == nodes.I64 and actual == nodes.BOOL:
        return True
    return False


@dataclass
class _Context:
    """
    The verification state of one function: locals and their types.

    A local keeps the type of its first assignment; a conflicting
    reassignment is a validation error.
    """

    program: nodes.Program
    func: nodes.Function
    defined: set[str] = field(default_factory=set)
    types: dict[str, str] = field(default_factory=dict)

    def copy(self) -> _Context:
        return _Context(self.program, self.func, set(self.defined), dict(self.types))

    def error(self, message: str) -> None:
        raise IRValidationError(f"function {self.func.name}: {message}")


def _type_of(expr: nodes.Expr, ctx: _Context) -> str:
    program = ctx.program
    if isinstance(expr, nodes.Const):
        return nodes.I64
    if isinstance(expr, nodes.ConstFloat):
        if expr.width == 32:
            return nodes.F32
        if expr.width == 64:
            return nodes.F64
        ctx.error(f"ConstFloat width must be 32 or 64, not {expr.width}")
    if isinstance(expr, nodes.Local):
        if expr.name not in ctx.defined:
            ctx.error(f"local {expr.name!r} is read before it is assigned")
        value_type = ctx.types.get(expr.name)
        if value_type is None:
            ctx.error(f"local {expr.name!r} has no type")
        return value_type
    if isinstance(expr, nodes.StateGet):
        _check_index(expr.slot, len(program.states), ctx, "state")
        return nodes.STR if program.states[expr.slot].type == "name" else nodes.I64
    if isinstance(expr, nodes.BufferLast):
        _check_index(expr.buf, len(program.buffers), ctx, "buffer")
        return buffer_type(program.buffers[expr.buf].dtype)
    if isinstance(expr, nodes.BufferLen):
        _check_index(expr.buf, len(program.buffers), ctx, "buffer")
        return nodes.I64
    if isinstance(expr, (nodes.Cursor, nodes.DataSize, nodes.Entries, nodes.InitOffset)):
        return nodes.I64
    if isinstance(expr, nodes.OffsetAt):
        _require_int(expr.index, ctx, "OffsetAt index")
        return nodes.I64
    if isinstance(expr, nodes.OffsetSeek):
        _require_int(expr.pos, ctx, "OffsetSeek position")
        return nodes.I64
    if isinstance(expr, nodes.EventEnd):
        return nodes.I64
    if isinstance(expr, nodes.StreamRead):
        _check_typecode(expr.typecode, ctx)
        if expr.typecode == Typecode.FLOAT32:
            return nodes.F32
        if expr.typecode == Typecode.FLOAT64:
            return nodes.F64
        return nodes.I64
    if isinstance(expr, (nodes.StreamReadFNB, nodes.StreamReadVersion)):
        return nodes.I64
    if isinstance(expr, nodes.StreamReadCString):
        return nodes.STR
    if isinstance(expr, nodes.Unary):
        if op_of(expr.op) not in _UNARY_OPS:
            ctx.error(f"unknown unary operator {expr.op!r}")
        _require_int(expr.x, ctx, "unary operand")
        return nodes.I64
    if isinstance(expr, nodes.Binary):
        if op_of(expr.op) not in _BINARY_OPS:
            ctx.error(f"unknown binary operator {expr.op!r}")
        _require_int(expr.a, ctx, "binary operand")
        _require_int(expr.b, ctx, "binary operand")
        return nodes.I64
    if isinstance(expr, nodes.Compare):
        if op_of(expr.op) not in _COMPARE_OPS:
            ctx.error(f"unknown comparison operator {expr.op!r}")
        _require_int(expr.a, ctx, "comparison operand")
        _require_int(expr.b, ctx, "comparison operand")
        return nodes.BOOL
    if isinstance(expr, nodes.Cast):
        if expr.kind not in ("zext", "sext"):
            ctx.error(f"cast kind must be zext or sext, not {expr.kind!r}")
        if expr.width not in (8, 16, 32):
            ctx.error(f"cast width must be 8, 16, or 32, not {expr.width}")
        _require_int(expr.x, ctx, "cast operand")
        return nodes.I64
    if isinstance(expr, nodes.RefHas):
        _require_int(expr.tag, ctx, "reference tag")
        return nodes.BOOL
    if isinstance(expr, nodes.RefGetObj):
        _require_int(expr.tag, ctx, "reference tag")
        return nodes.I64
    if isinstance(expr, nodes.RefGetClass):
        _require_int(expr.tag, ctx, "reference tag")
        return nodes.STR
    if isinstance(expr, nodes.RefCount):
        return nodes.I64
    if isinstance(expr, nodes.StrConst):
        _check_index(expr.index, len(program.constants), ctx, "constant")
        return nodes.STR
    if isinstance(expr, nodes.StrEq):
        _require_str(expr.a, ctx, "string equality")
        _require_str(expr.b, ctx, "string equality")
        return nodes.BOOL
    if isinstance(expr, nodes.Call):
        _check_index(expr.func, len(program.functions), ctx, "function")
        callee = program.functions[expr.func]
        _check_call_arity(callee, expr.arg, ctx)
        if callee.result == nodes.VOID:
            ctx.error(f"function {callee.name!r} returns void and has no value")
        return callee.result
    ctx.error(f"unknown expression {type(expr).__name__}")


def _check_index(index: int, limit: int, ctx: _Context, what: str) -> None:
    if not isinstance(index, int) or not 0 <= index < limit:
        ctx.error(f"{what} index {index!r} does not exist")


def _check_typecode(typecode: object, ctx: _Context) -> None:
    if not isinstance(typecode, Typecode):
        ctx.error(f"typecode must be a Typecode, not {typecode!r}")


def _require_int(expr: nodes.Expr, ctx: _Context, where: str) -> None:
    value_type = _type_of(expr, ctx)
    if not is_int(value_type):
        ctx.error(f"{where} must be an integer, not {value_type}")


def _require_str(expr: nodes.Expr, ctx: _Context, where: str) -> None:
    if _type_of(expr, ctx) != nodes.STR:
        ctx.error(f"{where} must be a string")


def _check_call_arity(
    callee: nodes.Function,
    arg: nodes.Expr | None,
    ctx: _Context,
) -> None:
    if callee.arity == 0 and arg is not None:
        ctx.error(f"function {callee.name!r} takes no argument")
    if callee.arity == 1:
        if arg is None:
            ctx.error(f"function {callee.name!r} needs an argument")
        _require_int(arg, ctx, f"argument to {callee.name!r}")


def _check_msg(msg: int | str | nodes.Constant, ctx: _Context) -> None:
    if isinstance(msg, int):
        _check_index(msg, len(ctx.program.strings), ctx, "message string")
    elif isinstance(msg, nodes.Constant):
        return
    elif isinstance(msg, str):
        return
    else:
        ctx.error(f"a message is a string or an arena id, not {type(msg).__name__}")


def _set_local(name: str, value_type: str, ctx: _Context) -> None:
    previous = ctx.types.get(name)
    if previous is not None and not (
        previous == value_type
        or (is_int(previous) and is_int(value_type))
        or (previous == nodes.F32 and value_type == nodes.I64)
        or (previous == nodes.F64 and value_type == nodes.I64)
    ):
        ctx.error(f"local {name!r} is assigned {value_type}, not {previous}")
    if previous is None:
        ctx.types[name] = value_type
    ctx.defined.add(name)


def _check_body(body: list[nodes.Stmt], ctx: _Context) -> None:
    for stmt in body:
        _check_stmt(stmt, ctx)


def _check_stmt(stmt: nodes.Stmt, ctx: _Context) -> None:
    program = ctx.program
    if isinstance(stmt, (nodes.Comment, nodes.Nop)):
        return
    if isinstance(stmt, nodes.Assign):
        value_type = _type_of(stmt.value, ctx)
        if isinstance(stmt.target, nodes.Local):
            if isinstance(stmt.value, nodes.ConstFloat) and value_type in (
                nodes.F32,
                nodes.F64,
            ):
                _set_local(stmt.target.name, value_type, ctx)
            else:
                _set_local(stmt.target.name, value_type, ctx)
            return
        if isinstance(stmt.target, nodes.StateSlot):
            _check_index(stmt.target.index, len(program.states), ctx, "state")
            state = program.states[stmt.target.index]
            expected = nodes.STR if state.type == "name" else nodes.I64
            if not compatible(expected, value_type) and not (
                expected == nodes.I64 and value_type in (nodes.I64, nodes.BOOL)
            ):
                ctx.error(f"state {state.name!r} takes {expected}, not {value_type}")
            return
        ctx.error(f"unknown assignment target {type(stmt.target).__name__}")
    if isinstance(stmt, nodes.BufferAppend):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        value_type = _type_of(stmt.value, ctx)
        if value_type == nodes.STR:
            ctx.error("a string cannot be appended to an output buffer")
        return
    if isinstance(stmt, nodes.BufferAppendConst):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        if not isinstance(stmt.value, int):
            ctx.error("BufferAppendConst value must be an int")
        return
    if isinstance(stmt, nodes.BufferAppendOffset):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        if program.buffers[stmt.buf].kind != BufferKind.OFFSETS:
            ctx.error("BufferAppendOffset targets an OFFSETS buffer")
        _require_int(stmt.value, ctx, "offset increment")
        return
    if isinstance(stmt, nodes.BufferAppendLen):
        _check_index(stmt.dst, len(program.buffers), ctx, "buffer")
        _check_index(stmt.src, len(program.buffers), ctx, "buffer")
        if program.buffers[stmt.dst].kind != BufferKind.OFFSETS:
            ctx.error("BufferAppendLen targets an OFFSETS buffer")
        return
    if isinstance(stmt, nodes.BufferReserve):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        _require_int(stmt.count, ctx, "reserve count")
        return
    if isinstance(stmt, nodes.ReadBulk):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        _check_typecode(stmt.typecode, ctx)
        _require_int(stmt.count, ctx, "bulk count")
        return
    if isinstance(stmt, nodes.ReadBulkUntil):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        _check_typecode(stmt.typecode, ctx)
        _require_int(stmt.end, ctx, "bulk end")
        return
    if isinstance(stmt, nodes.ReadString):
        _check_index(stmt.chars, len(program.buffers), ctx, "buffer")
        _check_index(stmt.offsets, len(program.buffers), ctx, "buffer")
        if program.buffers[stmt.offsets].kind != BufferKind.OFFSETS:
            ctx.error("ReadString extends a buffer that is not OFFSETS")
        if program.buffers[stmt.chars].dtype not in ("uint8", "bool"):
            ctx.error("ReadString reads bytes into a non-byte buffer")
        if stmt.mode not in (0, 1):
            ctx.error("ReadString mode must be 0 or 1")
        return
    if isinstance(stmt, nodes.ReadStrided):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        _check_typecode(stmt.typecode, ctx)
        _require_int(stmt.count, ctx, "strided count")
        _require_int(stmt.stride, ctx, "stride")
        return
    if isinstance(stmt, nodes.ReadFields):
        _check_index(stmt.layout, len(program.layouts), ctx, "layout")
        _require_int(stmt.count, ctx, "layout count")
        return
    if isinstance(stmt, nodes.ReadAppend):
        _check_index(stmt.buf, len(program.buffers), ctx, "buffer")
        _check_typecode(stmt.typecode, ctx)
        return
    if isinstance(stmt, nodes.Skip):
        if not isinstance(stmt.bytes, int) or stmt.bytes < 0:
            ctx.error("Skip bytes must be a non-negative int")
        return
    if isinstance(stmt, nodes.SkipR):
        _require_int(stmt.value, ctx, "skip count")
        return
    if isinstance(
        stmt,
        (
            nodes.SkipFNB,
            nodes.SkipVersion,
            nodes.SkipTObject,
            nodes.SkipObjHdr,
            nodes.SkipCStr,
        ),
    ):
        return
    if isinstance(stmt, nodes.SetCursor):
        _require_int(stmt.value, ctx, "cursor")
        return
    if isinstance(stmt, nodes.CallStmt):
        _check_index(stmt.func, len(program.functions), ctx, "function")
        callee = program.functions[stmt.func]
        _check_call_arity(callee, stmt.arg, ctx)
        if callee.result == nodes.VOID:
            if stmt.target is not None:
                ctx.error(f"function {callee.name!r} returns void")
            return
        if stmt.target is not None:
            _set_local(stmt.target.name, callee.result, ctx)
        return
    if isinstance(stmt, nodes.If):
        _require_int(stmt.cond, ctx, "if condition")
        then_ctx = ctx.copy()
        _check_body(stmt.then_body, then_ctx)
        else_ctx = ctx.copy()
        _check_body(stmt.else_body, else_ctx)
        ctx.defined = then_ctx.defined & else_ctx.defined
        merged = {}
        for name in ctx.defined:
            left = then_ctx.types.get(name)
            right = else_ctx.types.get(name)
            if left == right:
                merged[name] = left
            elif left is None:
                merged[name] = right
            elif right is None:
                merged[name] = left
            else:
                merged[name] = None
        ctx.types = merged
        return
    if isinstance(stmt, nodes.While):
        _require_int(stmt.cond, ctx, "while condition")
        body_ctx = ctx.copy()
        _check_body(stmt.body, body_ctx)
        return
    if isinstance(stmt, nodes.ForRange):
        if not isinstance(stmt.var, str) or not stmt.var:
            ctx.error("ForRange needs a local name")
        _require_int(stmt.start, ctx, "range start")
        _require_int(stmt.stop, ctx, "range stop")
        if not isinstance(stmt.step, int) or stmt.step == 0:
            ctx.error("ForRange step must be a non-zero int")
        body_ctx = ctx.copy()
        body_ctx.defined.add(stmt.var)
        body_ctx.types[stmt.var] = nodes.I64
        _check_body(stmt.body, body_ctx)
        ctx.defined.add(stmt.var)
        ctx.types[stmt.var] = nodes.I64
        return
    if isinstance(stmt, nodes.Assert):
        _require_int(stmt.cond, ctx, "assert condition")
        _check_msg(stmt.msg, ctx)
        return
    if isinstance(stmt, nodes.AssertCursorEq):
        _require_int(stmt.value, ctx, "asserted cursor")
        _check_msg(stmt.msg, ctx)
        return
    if isinstance(stmt, nodes.AssertStrEq):
        _require_str(stmt.a, ctx, "asserted string")
        _require_str(stmt.b, ctx, "asserted string")
        _check_msg(stmt.msg, ctx)
        return
    if isinstance(stmt, nodes.Throw):
        _check_msg(stmt.msg, ctx)
        return
    if isinstance(stmt, nodes.RefSetObj):
        _require_int(stmt.tag, ctx, "reference tag")
        _require_int(stmt.value, ctx, "object index")
        return
    if isinstance(stmt, nodes.RefSetClass):
        _require_int(stmt.tag, ctx, "reference tag")
        _require_str(stmt.value, ctx, "class name")
        return
    if isinstance(stmt, nodes.Return):
        if ctx.func.result == nodes.VOID:
            if stmt.value is not None:
                ctx.error("a void function cannot return a value")
            return
        if stmt.value is None:
            ctx.error(f"function {ctx.func.name!r} must return {ctx.func.result}")
        value_type = _type_of(stmt.value, ctx)
        if not compatible(ctx.func.result, value_type):
            ctx.error(f"returns {value_type}, not {ctx.func.result}")
        return
    ctx.error(f"unknown statement {type(stmt).__name__}")


def _terminates(body: list[nodes.Stmt]) -> bool:
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (nodes.Return, nodes.Throw)):
        return True
    if isinstance(last, nodes.If):
        return _terminates(last.then_body) and _terminates(last.else_body)
    return False


def _collect_locals(body: list[nodes.Stmt], names: set[str]) -> None:
    for stmt in body:
        if isinstance(stmt, nodes.Assign) and isinstance(stmt.target, nodes.Local):
            names.add(stmt.target.name)
        elif isinstance(stmt, nodes.CallStmt) and stmt.target is not None:
            names.add(stmt.target.name)
        elif isinstance(stmt, nodes.ForRange):
            names.add(stmt.var)
            _collect_locals(stmt.body, names)
        elif isinstance(stmt, nodes.If):
            _collect_locals(stmt.then_body, names)
            _collect_locals(stmt.else_body, names)
        elif isinstance(stmt, nodes.While):
            _collect_locals(stmt.body, names)


def verify_program(program: nodes.Program) -> None:
    """
    Raise `IRValidationError` if *program* is not lowerable.

    Args:
        program: the IR program to check.

    Raises:
        IRValidationError: the program breaks a rule of `docs/spec/ir.md`.
    """
    if not isinstance(program, nodes.Program):
        raise IRValidationError(f"expected a Program, not {type(program).__name__}")
    if not program.functions:
        raise IRValidationError("program has no functions")
    if not 0 <= program.entry < len(program.functions):
        raise IRValidationError(f"entry function {program.entry} does not exist")
    if program.functions[program.entry].arity != 0:
        raise IRValidationError("the entry function must have arity 0")

    names = set()
    for index, func in enumerate(program.functions):
        if not isinstance(func.name, str) or not func.name:
            raise IRValidationError(f"function {index} has no name")
        if func.name in names:
            raise IRValidationError(f"duplicate function name {func.name!r}")
        names.add(func.name)
        if func.arity != len(func.params) or func.arity > 1:
            raise IRValidationError(f"{func.name}: arity and params disagree")
        if func.result not in nodes.TYPES:
            raise IRValidationError(f"{func.name}: unknown result type {func.result!r}")

    buffer_names = set()
    for buffer in program.buffers:
        if buffer.name in buffer_names:
            raise IRValidationError(f"duplicate buffer name {buffer.name!r}")
        buffer_names.add(buffer.name)
        if buffer.dtype not in BUFFER_DTYPES:
            raise IRValidationError(f"buffer {buffer.name!r}: dtype {buffer.dtype!r}")
        if buffer.kind not in (BufferKind.DATA, BufferKind.OFFSETS):
            raise IRValidationError(f"buffer {buffer.name!r}: kind {buffer.kind}")
        if buffer.kind != BufferKind.DATA and buffer.dtype not in INT_BUFFER_DTYPES:
            raise IRValidationError(
                f"buffer {buffer.name!r}: {buffer.kind.name} must be an integer dtype"
            )

    state_names = set()
    for state in program.states:
        if state.name in state_names:
            raise IRValidationError(f"duplicate state name {state.name!r}")
        state_names.add(state.name)
        if state.type not in ("i64", "name"):
            raise IRValidationError(f"state {state.name!r}: type {state.type!r}")

    for index, layout in enumerate(program.layouts):
        if layout.id != index:
            raise IRValidationError(f"layout {index} has id {layout.id}")
        for layout_field in layout.fields:
            if layout_field.kind == FieldKind.TOBJECT_SKIP:
                raise IRValidationError(f"layout {index}: TOBJECT_SKIP is reserved")
            if layout_field.kind not in (
                FieldKind.SCALAR,
                FieldKind.SKIP,
                FieldKind.ARRAY_FIXED,
            ):
                raise IRValidationError(f"layout {index}: field kind {layout_field.kind}")
            if layout_field.count < 0:
                raise IRValidationError(f"layout {index}: negative field count")
            if layout_field.kind != FieldKind.SKIP:
                if not 0 <= layout_field.buffer_id < len(program.buffers):
                    raise IRValidationError(
                        f"layout {index}: missing buffer {layout_field.buffer_id}"
                    )
                if not isinstance(layout_field.typecode, Typecode):
                    raise IRValidationError(f"layout {index}: unknown typecode")

    for constant in program.constants:
        if not isinstance(constant, nodes.Constant):
            raise IRValidationError("a constant is a Constant node")
    for text in program.strings:
        if not isinstance(text, str):
            raise IRValidationError("the string arena holds str values")

    for func in program.functions:
        ctx = _Context(program, func)
        for param in func.params:
            if not isinstance(param, str) or not param:
                raise IRValidationError(f"{func.name}: invalid parameter {param!r}")
            if param in ctx.types:
                raise IRValidationError(f"{func.name}: duplicate parameter {param!r}")
            ctx.defined.add(param)
            ctx.types[param] = nodes.I64
        for local in func.locals:
            if not isinstance(local, str) or not local:
                raise IRValidationError(f"{func.name}: invalid local {local!r}")
            ctx.types.setdefault(local, None)
        _check_body(func.body, ctx)
        if func.result != nodes.VOID and not _terminates(func.body):
            raise IRValidationError(f"{func.name}: a non-void function must return")
        used = set(func.params) | set(func.locals)
        _collect_locals(func.body, used)
        if len(used) > MAX_REGISTERS:
            raise IRValidationError(
                f"{func.name}: {len(used)} locals exceed the {MAX_REGISTERS}-register window"
            )


__all__ = ["IRValidationError", "buffer_type", "op_of", "verify_program"]
