"""
Stable textual dump of an IR program.

The dump is for tests and debugging, not a wire format. Its shape is described
in `docs/spec/ir.md` §Textual dump and is allowed to change when that makes a test
clearer.
"""

from __future__ import annotations

from ..spec.isa import Op
from . import nodes


def _op(value: Op | str) -> str:
    if isinstance(value, Op):
        return value.name
    return str(value).upper()


def _expr(expr: nodes.Expr) -> str:
    if isinstance(expr, nodes.Const):
        return f"const({expr.value})"
    if isinstance(expr, nodes.ConstFloat):
        return f"const_float(bits=0x{expr.bits:x}, width={expr.width})"
    if isinstance(expr, nodes.Local):
        return f"local({expr.name})"
    if isinstance(expr, nodes.StateGet):
        return f"state_get({expr.slot})"
    if isinstance(expr, nodes.BufferLast):
        return f"buffer_last({expr.buf})"
    if isinstance(expr, nodes.BufferLen):
        return f"buffer_len({expr.buf})"
    if isinstance(expr, nodes.Cursor):
        return "cursor()"
    if isinstance(expr, nodes.DataSize):
        return "data_size()"
    if isinstance(expr, nodes.Entries):
        return "entries()"
    if isinstance(expr, nodes.InitOffset):
        return "init_offset()"
    if isinstance(expr, nodes.OffsetAt):
        return f"offset_at({_expr(expr.index)})"
    if isinstance(expr, nodes.OffsetSeek):
        return f"offset_seek({_expr(expr.pos)})"
    if isinstance(expr, nodes.EventEnd):
        return "event_end()"
    if isinstance(expr, nodes.StreamRead):
        return f"stream_read({expr.typecode.name.lower()})"
    if isinstance(expr, nodes.StreamReadFNB):
        return "stream_read_fnb()"
    if isinstance(expr, nodes.StreamReadVersion):
        return "stream_read_version()"
    if isinstance(expr, nodes.StreamReadCString):
        return "stream_read_cstr()"
    if isinstance(expr, nodes.Unary):
        return f"unary({_op(expr.op)}, {_expr(expr.x)})"
    if isinstance(expr, nodes.Binary):
        return f"binary({_op(expr.op)}, {_expr(expr.a)}, {_expr(expr.b)})"
    if isinstance(expr, nodes.Compare):
        return f"compare({_op(expr.op)}, {_expr(expr.a)}, {_expr(expr.b)})"
    if isinstance(expr, nodes.Cast):
        return f"cast({expr.kind}{expr.width}, {_expr(expr.x)})"
    if isinstance(expr, nodes.RefHas):
        return f"ref_has({_expr(expr.tag)})"
    if isinstance(expr, nodes.RefGetObj):
        return f"ref_get_obj({_expr(expr.tag)})"
    if isinstance(expr, nodes.RefGetClass):
        return f"ref_get_class({_expr(expr.tag)})"
    if isinstance(expr, nodes.RefCount):
        return "ref_count()"
    if isinstance(expr, nodes.StrConst):
        return f"str_const({expr.index})"
    if isinstance(expr, nodes.StrEq):
        return f"str_eq({_expr(expr.a)}, {_expr(expr.b)})"
    if isinstance(expr, nodes.Call):
        arg = "<none>" if expr.arg is None else _expr(expr.arg)
        return f"call({expr.func}, {arg})"
    return f"<{type(expr).__name__}>"


def _target(target: nodes.Target) -> str:
    if isinstance(target, nodes.Local):
        return f"local({target.name})"
    if isinstance(target, nodes.StateSlot):
        return f"state({target.index})"
    return f"<{type(target).__name__}>"


def _msg(msg: int | str | nodes.Constant) -> str:
    if isinstance(msg, nodes.Constant):
        return repr(msg.text)
    if isinstance(msg, str):
        return repr(msg)
    return str(msg)


def _stmts(body: list, indent: int) -> list[str]:
    lines = []
    pad = "  " * indent
    for stmt in body:
        if isinstance(stmt, nodes.Assign):
            lines.append(f"{pad}assign {_target(stmt.target)} = {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.BufferAppend):
            lines.append(f"{pad}buf_append {stmt.buf} = {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.BufferAppendConst):
            lines.append(f"{pad}buf_append_const {stmt.buf} = {stmt.value}")
        elif isinstance(stmt, nodes.BufferAppendOffset):
            lines.append(f"{pad}buf_append_offset {stmt.buf} = {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.BufferAppendLen):
            lines.append(f"{pad}buf_append_len dst={stmt.dst} src={stmt.src}")
        elif isinstance(stmt, nodes.BufferReserve):
            lines.append(f"{pad}buf_reserve {stmt.buf} = {_expr(stmt.count)}")
        elif isinstance(stmt, nodes.ReadBulk):
            lines.append(
                f"{pad}read_bulk {stmt.buf} tc={stmt.typecode.name.lower()} "
                f"count={_expr(stmt.count)}"
            )
        elif isinstance(stmt, nodes.ReadBulkUntil):
            lines.append(
                f"{pad}read_bulk_until {stmt.buf} tc={stmt.typecode.name.lower()} "
                f"end={_expr(stmt.end)}"
            )
        elif isinstance(stmt, nodes.ReadString):
            lines.append(
                f"{pad}read_string chars={stmt.chars} offsets={stmt.offsets} "
                f"mode={stmt.mode}"
            )
        elif isinstance(stmt, nodes.ReadStrided):
            lines.append(
                f"{pad}read_strided {stmt.buf} tc={stmt.typecode.name.lower()} "
                f"count={_expr(stmt.count)} stride={_expr(stmt.stride)}"
            )
        elif isinstance(stmt, nodes.ReadFields):
            lines.append(f"{pad}read_fields {stmt.layout} count={_expr(stmt.count)}")
        elif isinstance(stmt, nodes.ReadAppend):
            lines.append(f"{pad}read_append {stmt.buf} tc={stmt.typecode.name.lower()}")
        elif isinstance(stmt, nodes.Skip):
            lines.append(f"{pad}skip {stmt.bytes}")
        elif isinstance(stmt, nodes.SkipR):
            lines.append(f"{pad}skip_r {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.SkipFNB):
            lines.append(f"{pad}skip_fnb")
        elif isinstance(stmt, nodes.SkipVersion):
            lines.append(f"{pad}skip_version")
        elif isinstance(stmt, nodes.SkipTObject):
            lines.append(f"{pad}skip_tobject")
        elif isinstance(stmt, nodes.SkipObjHdr):
            lines.append(f"{pad}skip_objhdr")
        elif isinstance(stmt, nodes.SkipCStr):
            lines.append(f"{pad}skip_cstr")
        elif isinstance(stmt, nodes.SetCursor):
            lines.append(f"{pad}set_cursor {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.CallStmt):
            target = "<none>" if stmt.target is None else f"local({stmt.target.name})"
            arg = "<none>" if stmt.arg is None else _expr(stmt.arg)
            lines.append(f"{pad}call_stmt {target} = call({stmt.func}, {arg})")
        elif isinstance(stmt, nodes.If):
            lines.append(f"{pad}if {_expr(stmt.cond)}:")
            lines.extend(_stmts(stmt.then_body, indent + 1))
            if stmt.else_body:
                lines.append(f"{pad}else:")
                lines.extend(_stmts(stmt.else_body, indent + 1))
        elif isinstance(stmt, nodes.While):
            lines.append(f"{pad}while {_expr(stmt.cond)}:")
            lines.extend(_stmts(stmt.body, indent + 1))
        elif isinstance(stmt, nodes.ForRange):
            lines.append(
                f"{pad}for {stmt.var} in range({_expr(stmt.start)}, "
                f"{_expr(stmt.stop)}, step={stmt.step}):"
            )
            lines.extend(_stmts(stmt.body, indent + 1))
        elif isinstance(stmt, nodes.Assert):
            lines.append(f"{pad}assert {_expr(stmt.cond)}, {_msg(stmt.msg)}")
        elif isinstance(stmt, nodes.AssertCursorEq):
            lines.append(f"{pad}assert_cursor_eq {_expr(stmt.value)}, {_msg(stmt.msg)}")
        elif isinstance(stmt, nodes.AssertStrEq):
            lines.append(
                f"{pad}assert_str_eq {_expr(stmt.a)}, {_expr(stmt.b)}, {_msg(stmt.msg)}"
            )
        elif isinstance(stmt, nodes.Throw):
            lines.append(f"{pad}throw {_msg(stmt.msg)}")
        elif isinstance(stmt, nodes.RefSetObj):
            lines.append(f"{pad}ref_set_obj {_expr(stmt.tag)}, {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.RefSetClass):
            lines.append(f"{pad}ref_set_class {_expr(stmt.tag)}, {_expr(stmt.value)}")
        elif isinstance(stmt, nodes.Return):
            value = "<none>" if stmt.value is None else _expr(stmt.value)
            lines.append(f"{pad}ret {value}")
        elif isinstance(stmt, nodes.Comment):
            lines.append(f"{pad}# {stmt.text}")
        elif isinstance(stmt, nodes.Nop):
            lines.append(f"{pad}nop")
        else:
            lines.append(f"{pad}<{type(stmt).__name__}>")
    return lines


def dump(program: nodes.Program) -> str:
    """
    Return the indented textual form of *program*.

    The output is stable for tests and debugging; it is not a wire format.

    Args:
        program: the IR program to render.

    Returns:
        The multi-line textual form.
    """
    lines = [f"program entry={program.entry}"]
    for index, buffer in enumerate(program.buffers):
        init = list(buffer.init)
        lines.append(
            f"buffer {index} name={buffer.name} dtype={buffer.dtype} "
            f"kind={buffer.kind.name} init={init}"
        )
    for index, state in enumerate(program.states):
        lines.append(f"state {index} name={state.name} init={state.init} type={state.type}")
    for index, text in enumerate(program.strings):
        lines.append(f"string {index} {text!r}")
    for index, constant in enumerate(program.constants):
        lines.append(f"constant {index} {constant.text!r}")
    for layout in program.layouts:
        lines.append(f"layout {layout.id}")
        for field in layout.fields:
            lines.append(
                f"  {field.kind.name} tc={field.typecode.name.lower()} "
                f"count={field.count} buffer={field.buffer_id}"
            )
    for index, func in enumerate(program.functions):
        lines.append(
            f"function {index} name={func.name} arity={func.arity} "
            f"result={func.result} params={list(func.params)}"
        )
        lines.extend(_stmts(func.body, 1))
    return "\n".join(lines) + "\n"


__all__ = ["dump"]
