"""
Fusion passes: append/read, bulk reads, offsets, skips, and layouts.

These passes are what keep the machine's dispatch loop out of the per-element
path. Each one fires only when the rewrite is observationally identical.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from ..ir import nodes
from ..ir.verify import op_of
from ..spec.isa import BufferKind, FieldKind, Op, Typecode, typecode_size
from .common import body_reads, count_reads, count_writes, map_expr


def _signed64(bits: int) -> int:
    bits &= (1 << 64) - 1
    return bits - (1 << 64) if bits & (1 << 63) else bits


def _walk(
    body: list[nodes.Stmt],
    rewrite: Callable[[list[nodes.Stmt]], list[nodes.Stmt]],
) -> list[nodes.Stmt]:
    """
    Recurse into structured bodies, then let *rewrite* change the list.

    Args:
        body: the statements to visit.
        rewrite: applied to this list after its children were rewritten.

    Returns:
        The rewritten statement list.
    """
    for stmt in body:
        if isinstance(stmt, nodes.If):
            stmt.then_body = _walk(stmt.then_body, rewrite)
            stmt.else_body = _walk(stmt.else_body, rewrite)
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            stmt.body = _walk(stmt.body, rewrite)
    return rewrite(body)


def _apply(
    program: nodes.Program,
    rewrite: Callable[[list[nodes.Stmt]], list[nodes.Stmt]],
) -> bool:
    changed = False
    for func in program.functions:
        before = repr(func.body)
        func.body = _walk(func.body, rewrite)
        if repr(func.body) != before:
            changed = True
    return changed


# -- literal append ----------------------------------------------------------


def literal_append(program: nodes.Program) -> bool:
    """
    Rewrite `append(buf, const)` to one `BufferAppendConst`.

    Saves a register and a dispatch per literal append.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        for index, stmt in enumerate(body):
            if not isinstance(stmt, nodes.BufferAppend):
                continue
            if isinstance(stmt.value, nodes.Const):
                body[index] = nodes.BufferAppendConst(stmt.buf, _signed64(stmt.value.value))
            elif isinstance(stmt.value, nodes.ConstFloat):
                body[index] = nodes.BufferAppendConst(stmt.buf, _signed64(stmt.value.bits))
        return body

    return _apply(program, rewrite)


# -- stream read + append ----------------------------------------------------


def read_append_fusion(program: nodes.Program) -> bool:
    """
    Fold `x = read(tc); append(buf, x)` into one `ReadAppend`.

    The local must be read nowhere else, or the value would be lost.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        for index, stmt in enumerate(body):
            if not isinstance(stmt, nodes.BufferAppend):
                continue
            value = stmt.value
            if isinstance(value, nodes.StreamRead):
                body[index] = nodes.ReadAppend(stmt.buf, value.typecode)
            elif isinstance(value, nodes.StreamReadVersion):
                body[index] = nodes.ReadAppend(stmt.buf, Typecode.INT16)
        return body

    return _apply(program, rewrite)


# -- offsets -----------------------------------------------------------------


def len_append_fusion(program: nodes.Program) -> bool:
    """
    `append(offsets, len(other))` becomes one `BufferAppendLen`.

    A program records the end of a variable-length region by appending the
    length of the bytes it just read; the machine has a single opcode for that,
    so the two instructions a lowering would otherwise need are worth removing.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        for index, stmt in enumerate(body):
            if not isinstance(stmt, nodes.BufferAppend):
                continue
            if program.buffers[stmt.buf].kind != BufferKind.OFFSETS:
                continue
            if not isinstance(stmt.value, nodes.BufferLen):
                continue
            body[index] = nodes.BufferAppendLen(stmt.buf, stmt.value.buf)
        return body

    return _apply(program, rewrite)


def offset_fusion(program: nodes.Program) -> bool:
    """
    Rewrite `append(offsets, last(offsets) + n)` to `BufferAppendOffset`.

    `n` is normally the length of the data buffer that just grew.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        for index, stmt in enumerate(body):
            if not isinstance(stmt, nodes.BufferAppend):
                continue
            if program.buffers[stmt.buf].kind != BufferKind.OFFSETS:
                continue
            value = stmt.value
            if not isinstance(value, nodes.Binary) or op_of(value.op) != Op.ADD:
                continue
            if isinstance(value.a, nodes.BufferLast) and value.a.buf == stmt.buf:
                body[index] = nodes.BufferAppendOffset(stmt.buf, value.b)
            elif isinstance(value.b, nodes.BufferLast) and value.b.buf == stmt.buf:
                body[index] = nodes.BufferAppendOffset(stmt.buf, value.a)
        return body

    return _apply(program, rewrite)


# -- skips -------------------------------------------------------------------


def _reference_test(cond: nodes.Expr) -> tuple[nodes.Expr, bool] | None:
    """
    The `bits & kIsReferenced` node of a guard, and whether truth skips.

    Returns:
        The tag expression and the polarity, or `None` when *cond* is not
        such a guard.
    """
    if isinstance(cond, nodes.Binary) and op_of(cond.op) == Op.AND:
        return cond, True
    if isinstance(cond, nodes.Compare) and isinstance(cond.a, nodes.Binary):
        if not isinstance(cond.b, nodes.Const) or cond.b.value != 0:
            return None
        if op_of(cond.op) == Op.CMP_NE:
            return cond.a, True
        if op_of(cond.op) == Op.CMP_EQ:
            return cond.a, False
    return None


def _reference_skip(stmt: nodes.Stmt) -> tuple[nodes.Expr, int] | None:
    """
    `if bits & kIsReferenced: skip 2` in either polarity, or `None`.

    Returns:
        The tag expression and the byte count being skipped.
    """
    if not isinstance(stmt, nodes.If):
        return None
    found = _reference_test(stmt.cond)
    if found is None:
        return None
    test, truth_skips = found
    if op_of(test.op) != Op.AND or not isinstance(test.a, nodes.Local):
        return None
    if not isinstance(test.b, nodes.Const) or test.b.value != 1 << 4:
        return None
    taken, other = (
        (stmt.then_body, stmt.else_body) if truth_skips else (stmt.else_body, stmt.then_body)
    )
    if taken != [nodes.Skip(2)] or other:
        return None
    return test.a.name


def _tobject_header(body: list[nodes.Stmt], index: int) -> tuple[int, str] | None:
    """
    The statements a skipped TObject header spans, and its bits local.

    Returns:
        The number of statements covered and the local holding the header
        bits, or `None` when the shape does not match.
    """
    if index + 3 >= len(body):
        return 0, None
    version, ident, bits, guard = body[index : index + 4]

    if isinstance(version, nodes.SkipVersion):
        pass
    elif isinstance(version, nodes.Skip) and version.bytes == 2:
        pass
    else:
        return 0, None

    if not (
        isinstance(ident, nodes.Assign)
        and isinstance(ident.target, nodes.Local)
        and isinstance(ident.value, nodes.StreamRead)
        and ident.value.typecode == Typecode.INT32
    ):
        return 0, None
    if not (
        isinstance(bits, nodes.Assign)
        and isinstance(bits.target, nodes.Local)
        and isinstance(bits.value, nodes.StreamRead)
        and bits.value.typecode == Typecode.UINT32
    ):
        return 0, None

    name = _reference_skip(guard)
    if name is None or name != bits.target.name:
        return 0, None
    if count_reads(body, ident.target.name) != 0:
        return 0, None
    if count_writes(body, name) != 1 or count_reads(body, name) != 1:
        return 0, None
    return 4, name


def tobject_fusion(program: nodes.Program) -> bool:
    """
    A skipped `TObject` header becomes one `SkipTObject`.

    The pattern is a version read, a byte count, and the reference guard.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        index = 0
        while index < len(body):
            span, _ = _tobject_header(body, index)
            if span:
                out.append(nodes.SkipTObject())
                index += span
                continue
            out.append(body[index])
            index += 1
        return out

    return _apply(program, rewrite)


def skip_fusion(program: nodes.Program) -> bool:
    """
    Merge adjacent skips (including the version skip) into one.

    Only fixed-size skips merge; a register-sized skip keeps its own node.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        for stmt in body:
            if isinstance(stmt, nodes.Skip) and stmt.bytes == 0:
                continue
            if out:
                previous = out[-1]
                total = None
                if isinstance(previous, nodes.Skip) and isinstance(stmt, nodes.Skip):
                    total = previous.bytes + stmt.bytes
                elif isinstance(previous, nodes.Skip) and isinstance(stmt, nodes.SkipVersion):
                    total = previous.bytes + 2
                elif isinstance(previous, nodes.SkipVersion) and isinstance(stmt, nodes.Skip):
                    total = stmt.bytes + 2
                elif isinstance(previous, nodes.SkipVersion) and isinstance(
                    stmt, nodes.SkipVersion
                ):
                    total = 4
                if total is not None:
                    out[-1] = nodes.Skip(total)
                    continue
            out.append(stmt)
        return out

    return _apply(program, rewrite)


# -- dead read to skip -------------------------------------------------------


def _walk_stmts(body: list[nodes.Stmt]) -> Iterator[nodes.Stmt]:
    for stmt in body:
        yield stmt
        if isinstance(stmt, nodes.If):
            yield from _walk_stmts(stmt.then_body)
            yield from _walk_stmts(stmt.else_body)
        elif isinstance(stmt, (nodes.While, nodes.ForRange)):
            yield from _walk_stmts(stmt.body)


def dead_read_to_skip(program: nodes.Program) -> bool:
    """
    Replace a scalar read whose value is unused with a skip.

    The read still has to happen; only the register and the append go away.
    """
    changed = False
    for func in program.functions:
        used = body_reads(func.body)
        changed = _dead_reads(func.body, used) or changed
    return changed


def _dead_reads(body: list, used: set[str]) -> bool:
    changed = False
    for index, stmt in enumerate(body):
        if isinstance(stmt, nodes.If):
            changed = _dead_reads(stmt.then_body, used) or changed
            changed = _dead_reads(stmt.else_body, used) or changed
            continue
        if isinstance(stmt, (nodes.While, nodes.ForRange)):
            changed = _dead_reads(stmt.body, used) or changed
            continue
        if not isinstance(stmt, nodes.Assign) or not isinstance(stmt.target, nodes.Local):
            continue
        if stmt.target.name in used:
            continue
        value = stmt.value
        if isinstance(value, nodes.StreamRead):
            body[index] = nodes.Skip(typecode_size(value.typecode))
            changed = True
        elif isinstance(value, nodes.StreamReadVersion):
            body[index] = nodes.Skip(2)
            changed = True
    return changed


# -- bulk reads --------------------------------------------------------------


def _read_of(stmt: nodes.Stmt) -> nodes.StreamRead | None:
    if isinstance(stmt, nodes.ReadAppend):
        return stmt.buf, stmt.typecode
    if isinstance(stmt, nodes.BufferAppend) and isinstance(stmt.value, nodes.StreamRead):
        return stmt.buf, stmt.value.typecode
    return None


def _read_through(program: nodes.Program, stmt: nodes.Stmt) -> int | None:
    """
    `(buf, typecode)` when *stmt* is a call to a body that only reads one value.

    A loop over a call is the shape a compiler emits for "read this element
    N times"; the call is transparent for the rewrite, so it is looked through
    instead of being inlined (the callee usually has other call sites).
    """
    read = _read_of(stmt)
    if read is not None:
        return read
    if not isinstance(stmt, nodes.CallStmt) or stmt.target is not None:
        return None
    if stmt.arg is not None:
        return None
    callee = program.functions[stmt.func]
    if callee.arity != 0 or len(callee.body) != 2:
        return None
    head, tail = callee.body
    if not isinstance(tail, nodes.Return) or tail.value is not None:
        return None
    return _read_of(head)


def _mentions(expr: nodes.Expr, name: str) -> bool:
    found = False

    def visit(node: nodes.Expr) -> nodes.Expr:
        nonlocal found
        if isinstance(node, nodes.Local) and node.name == name:
            found = True
        return node

    map_expr(expr, visit)
    return found


def _count_of(start: nodes.Expr, stop: nodes.Expr) -> nodes.Expr:
    """
    The iteration count of `range(start, stop)` as an expression.

    Returns:
        A constant when both bounds are constant, otherwise the subtraction.
    """
    if isinstance(start, nodes.Const) and start.value == 0:
        return stop
    if isinstance(start, nodes.Const) and isinstance(stop, nodes.Const):
        return nodes.Const(stop.value - start.value)
    return nodes.Binary(Op.SUB, stop, start)


def bulk_fusion(program: nodes.Program) -> bool:
    """
    A counted loop over one fixed-size read becomes one `ReadBulk`.

    The count need not be a constant: `ReadBulk` takes it from a register, so
    a loop over an element count read from the stream fuses just as well.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        index = 0
        while index < len(body):
            stmt = body[index]
            if (
                isinstance(stmt, nodes.ForRange)
                and len(stmt.body) == 1
                and stmt.step == 1
                and not _mentions(stmt.start, stmt.var)
                and not _mentions(stmt.stop, stmt.var)
                and stmt.var not in body_reads(body[index + 1 :])
            ):
                read = _read_through(program, stmt.body[0])
                if read is not None:
                    count = _count_of(stmt.start, stmt.stop)
                    out.append(nodes.ReadBulk(read[0], read[1], count))
                    index += 1
                    continue
            out.append(stmt)
            index += 1
        return out

    return _apply(program, rewrite)


def bulk_until_fusion(program: nodes.Program) -> bool:
    """
    `while cursor < end: read one element` becomes one `ReadBulkUntil`.

    The loop reads until the entry ends; the opcode does the same in one
    dispatch and rejects a span that is not a whole number of elements instead
    of overrunning the entry.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        for index, stmt in enumerate(body):
            if not isinstance(stmt, nodes.While) or len(stmt.body) != 1:
                continue
            condition = stmt.cond
            if not isinstance(condition, nodes.Compare):
                continue
            if op_of(condition.op) != Op.CMP_LT_S:
                continue
            if not isinstance(condition.a, nodes.Cursor):
                continue
            read = _read_through(program, stmt.body[0])
            if read is None:
                continue
            body[index] = nodes.ReadBulkUntil(read[0], read[1], condition.b)
        return body

    return _apply(program, rewrite)


# -- strided reads -----------------------------------------------------------


def _strided_run(
    body: list[nodes.Stmt],
    index: int,
) -> tuple[int, tuple[int, Typecode], int] | None:
    """
    The span, element, and gap of a run of reads at a fixed stride.

    The run is `read, Skip(gap), read, ...` and has to end on a read: a
    trailing skip is left in place, because `READ_STRIDED` stops after the last
    element and does not consume the gap behind it.

    Returns:
        The statement span, the `(buf, typecode)` every read shares, and the
        constant gap, or `None` when the statements are another shape.
    """
    first = _read_of(body[index])
    if first is None:
        return None
    gap = None
    pointer = index + 1
    while pointer + 1 < len(body):
        skip = body[pointer]
        following = _read_of(body[pointer + 1])
        if not isinstance(skip, nodes.Skip) or skip.bytes <= 0:
            break
        if following is None or following != first:
            break
        if gap is None:
            gap = skip.bytes
        elif gap != skip.bytes:
            break
        pointer += 2
    if gap is None:
        return None
    return pointer - index, first, gap


def strided_fusion(program: nodes.Program) -> bool:
    """
    Fold a flat run of reads separated by a constant gap into `ReadStrided`.

    A counted loop is not rewritten here: `READ_STRIDED` leaves the gap behind
    the last element unconsumed, while a loop skips after every element. The
    loop shape is a `ReadFields` over a scalar-then-skip layout instead.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        index = 0
        while index < len(body):
            run = _strided_run(body, index)
            if run is not None:
                span, (buf, typecode), gap = run
                out.append(
                    nodes.ReadStrided(
                        buf,
                        typecode,
                        nodes.Const((span + 1) // 2),
                        nodes.Const(typecode_size(typecode) + gap),
                    )
                )
                index += span
                continue
            out.append(body[index])
            index += 1
        return out

    return _apply(program, rewrite)


# -- strings -----------------------------------------------------------------

# The one-byte length that means "a four-byte count follows".
STRING_ESCAPE = 255


def _local_of(expr: nodes.Expr, name: str) -> bool:
    return isinstance(expr, nodes.Local) and expr.name == name


def _string_length(body: list[nodes.Stmt], index: int) -> int | None:
    """
    The local a string length prefix at *index* assigns, or `None`.

    The prefix is `size = read(u8)` and, when the byte is 255,
    `size = read(u32)`; both spell the length in the same local.
    """
    if index + 1 >= len(body):
        return None
    assign, guard = body[index], body[index + 1]
    if not (
        isinstance(assign, nodes.Assign)
        and isinstance(assign.target, nodes.Local)
        and isinstance(assign.value, nodes.StreamRead)
        and assign.value.typecode == Typecode.UINT8
    ):
        return None
    name = assign.target.name
    if not (isinstance(guard, nodes.If) and not guard.else_body and len(guard.then_body) == 1):
        return None
    condition = guard.cond
    if not (
        isinstance(condition, nodes.Compare)
        and op_of(condition.op) == Op.CMP_EQ
        and _local_of(condition.a, name)
        and isinstance(condition.b, nodes.Const)
        and condition.b.value == STRING_ESCAPE
    ):
        return None
    inner = guard.then_body[0]
    if not (
        isinstance(inner, nodes.Assign)
        and isinstance(inner.target, nodes.Local)
        and inner.target.name == name
        and isinstance(inner.value, nodes.StreamRead)
        and inner.value.typecode == Typecode.UINT32
    ):
        return None
    return name


def _reserve_of(body: list, index: int, name: str) -> bool:
    return (
        index < len(body)
        and isinstance(body[index], nodes.BufferReserve)
        and _local_of(body[index].count, name)
    )


def _bulk_of(stmt: nodes.Stmt, name: str) -> nodes.Stmt | None:
    if (
        isinstance(stmt, nodes.ReadBulk)
        and stmt.typecode == Typecode.UINT8
        and _local_of(stmt.count, name)
    ):
        return stmt.buf
    return None


def _string_at(
    program: nodes.Program,
    body: list[nodes.Stmt],
    index: int,
) -> tuple[int, int, int, int] | None:
    """
    `(span, chars, offsets, mode)` for a string body at *index*.

    Returns:
        The shape of the length-prefixed string, or `None` when the statements
        do not form one.
    """
    name = _string_length(body, index)
    if name is None:
        return None
    cursor = index + 2
    # The prefix itself reads the local once, in its escape guard.
    reads = 1

    if cursor < len(body) and _reserve_of(body, cursor, name):
        cursor += 1
        reads += 1
    if cursor >= len(body):
        return None

    offsets = None
    mode = None
    chars = None
    if isinstance(body[cursor], nodes.BufferAppendOffset):
        if not _local_of(body[cursor].value, name):
            return None
        offsets, mode = body[cursor].buf, 1
        reads += 2
        cursor += 1
        if _reserve_of(body, cursor, name):
            cursor += 1
            reads += 1
        if cursor >= len(body):
            return None
        chars = _bulk_of(body[cursor], name)
        cursor += 1
    else:
        chars = _bulk_of(body[cursor], name)
        reads += 1
        cursor += 1
        if cursor >= len(body) or not isinstance(body[cursor], nodes.BufferAppendLen):
            return None
        offsets, mode = body[cursor].dst, 0
        if chars != body[cursor].src:
            return None
        cursor += 1

    if chars is None or offsets is None:
        return None
    if program.buffers[offsets].kind != BufferKind.OFFSETS:
        return None
    if program.buffers[chars].dtype not in ("uint8", "bool"):
        return None
    if count_writes(body, name) != 2 or count_reads(body, name) != reads:
        return None
    return cursor - index, chars, offsets, mode


def string_fusion(program: nodes.Program) -> bool:
    """
    A length prefix, its bytes, and its offset become one `ReadString`.

    Both offset modes are recognised; the mode is copied from the append.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        index = 0
        while index < len(body):
            found = _string_at(program, body, index)
            if found is not None:
                span, chars, offsets, mode = found
                out.append(nodes.ReadString(chars, offsets, mode))
                index += span
                continue
            out.append(body[index])
            index += 1
        return out

    return _apply(program, rewrite)


# -- layouts -----------------------------------------------------------------


def _field_of(stmt: nodes.Stmt) -> nodes.LayoutField | None:
    """
    The layout field one statement reads, or `None` when it is not flat.

    A scalar read, a constant skip, and a bulk read of a constant count are the
    three shapes a `READ_FIELDS` layout can express.
    """
    if isinstance(stmt, nodes.SkipVersion):
        return nodes.LayoutField(FieldKind.SKIP, Typecode.UINT8, 2, -1)
    if isinstance(stmt, nodes.Skip):
        if stmt.bytes <= 0:
            return None
        return nodes.LayoutField(FieldKind.SKIP, Typecode.UINT8, stmt.bytes, -1)
    read = _read_of(stmt)
    if read is not None:
        buf, typecode = read
        return nodes.LayoutField(FieldKind.SCALAR, typecode, 1, buf)
    if isinstance(stmt, nodes.ReadBulk) and isinstance(stmt.count, nodes.Const):
        if stmt.count.value >= 2:
            return nodes.LayoutField(
                FieldKind.ARRAY_FIXED,
                stmt.typecode,
                stmt.count.value,
                stmt.buf,
            )
    return None


def _field_run(
    body: list[nodes.Stmt],
    index: int,
) -> tuple[tuple[nodes.LayoutField, ...], int] | None:
    """
    The fields of one flat object at *index*, and the statements they span.

    A run of skips alone is not a layout: something has to be read.

    Returns:
        The fields and the span, or `None` when the run holds fewer than two
        fields or no field carries a value.
    """
    fields = []
    pointer = index
    while pointer < len(body):
        field = _field_of(body[pointer])
        if field is None:
            break
        fields.append(field)
        pointer += 1
    if len(fields) < 2 or all(field.kind == FieldKind.SKIP for field in fields):
        return None
    return tuple(fields), pointer - index


def _new_layout(program: nodes.Program, fields: tuple) -> int:
    """
    Append a layout to the program and return its id.
    """
    layout_id = len(program.layouts)
    program.layouts.append(nodes.Layout(layout_id, fields))
    return layout_id


def _layout_loop(
    program: nodes.Program,
    body: list[nodes.Stmt],
    index: int,
) -> tuple[tuple[nodes.LayoutField, ...], nodes.Expr] | None:
    """
    The fields and the object count of a counted loop over one flat object.

    Returns:
        The fields and the loop count, or `None` when the body is not exactly
        one flat object per iteration.
    """
    stmt = body[index]
    if not isinstance(stmt, nodes.ForRange) or stmt.step != 1:
        return None
    if _mentions(stmt.start, stmt.var) or _mentions(stmt.stop, stmt.var):
        return None
    if stmt.var in body_reads(body[index + 1 :]):
        return None
    if len(stmt.body) == 1 and isinstance(stmt.body[0], nodes.ReadFields):
        inner = stmt.body[0]
        if not isinstance(inner.count, nodes.Const) or inner.count.value != 1:
            return None
        return program.layouts[inner.layout].fields, _count_of(stmt.start, stmt.stop)
    run = _field_run(stmt.body, 0)
    if run is None or run[1] != len(stmt.body):
        return None
    return run[0], _count_of(stmt.start, stmt.stop)


def layout_infer(program: nodes.Program) -> bool:
    """
    Collapse a run of flat field steps into one `ReadFields` layout.

    A standalone run becomes a layout read once; a counted loop whose whole body
    is such a run becomes a layout read with the loop count, so a list of flat
    objects costs one dispatch instead of one per field.
    """
    changed = False

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        nonlocal changed
        out = []
        index = 0
        while index < len(body):
            loop = _layout_loop(program, body, index)
            if loop is not None:
                fields, count = loop
                out.append(nodes.ReadFields(_new_layout(program, fields), count))
                index += 1
                changed = True
                continue
            run = _field_run(body, index)
            if run is not None:
                fields, span = run
                out.append(nodes.ReadFields(_new_layout(program, fields), nodes.Const(1)))
                index += span
                changed = True
                continue
            out.append(body[index])
            index += 1
        return out

    _apply(program, rewrite)
    return changed


# -- reserve -----------------------------------------------------------------


def _append_targets(program: nodes.Program, body: list) -> set:
    targets = set()
    for stmt in _walk_stmts(body):
        if isinstance(
            stmt,
            (
                nodes.BufferAppend,
                nodes.BufferAppendConst,
                nodes.BufferAppendOffset,
                nodes.ReadBulk,
                nodes.ReadStrided,
                nodes.ReadAppend,
            ),
        ):
            targets.add(stmt.buf)
        elif isinstance(stmt, nodes.ReadFields):
            targets.update(
                field.buffer_id
                for field in program.layouts[stmt.layout].fields
                if field.kind != FieldKind.SKIP
            )
        elif isinstance(stmt, nodes.BufferAppendLen):
            targets.add(stmt.dst)
    return targets


def _iterations(start: int, stop: int, step: int) -> int:
    if step > 0:
        return max(0, (stop - start + step - 1) // step)
    return max(0, (start - stop + (-step) - 1) // (-step))


def _already_reserved(out: list, target: int) -> bool:
    for stmt in reversed(out):
        if not isinstance(stmt, nodes.BufferReserve):
            break
        if stmt.buf == target:
            return True
    return False


def _layout_targets(
    program: nodes.Program,
    stmt: nodes.ReadFields,
) -> list[tuple[int, nodes.Expr]]:
    """
    The buffers a layout read grows, with the element count of each.

    Returns:
        One `(buffer, count)` pair per value-bearing field, in field order.
    """
    targets = []
    for field in program.layouts[stmt.layout].fields:
        if field.kind == FieldKind.SKIP:
            continue
        if field.kind == FieldKind.ARRAY_FIXED and field.count != 1:
            count = nodes.Binary(Op.MUL, stmt.count, nodes.Const(field.count))
        else:
            count = stmt.count
        targets.append((field.buffer_id, count))
    return targets


def _reserve_count(stmt: nodes.Stmt) -> nodes.Expr | None:
    """
    How many elements a counted loop appends, or `None` when unknown.

    Only a constant `ForRange` whose body appends one element per iteration
    yields a count.
    """
    start, stop = stmt.start, stmt.stop
    if isinstance(start, nodes.Const) and isinstance(stop, nodes.Const):
        count = _iterations(start.value, stop.value, stmt.step)
        return nodes.Const(count) if count > 0 else None
    if stmt.step != 1:
        return None
    if _mentions(start, stmt.var) or _mentions(stop, stmt.var):
        return None
    return _count_of(start, stop)


def reserve(program: nodes.Program) -> bool:
    """
    Reserve capacity before a loop or a bulk, strided, or layout read.

    The machine allocates fresh storage on every run, so without the hint a
    basket of N elements pays the doubling sequence each time. The count is a
    hint only: a machine may ignore it, and `BUF_RESERVE` has no observable
    effect.
    """

    def rewrite(body: list[nodes.Stmt]) -> list[nodes.Stmt]:
        out = []
        for stmt in body:
            if isinstance(stmt, nodes.ForRange):
                targets = _append_targets(program, stmt.body)
                if len(targets) == 1:
                    target = next(iter(targets))
                    count = _reserve_count(stmt)
                    if count is not None and not _already_reserved(out, target):
                        out.append(nodes.BufferReserve(target, count))
            elif isinstance(
                stmt, (nodes.ReadBulk, nodes.ReadStrided)
            ) and not _already_reserved(out, stmt.buf):
                out.append(nodes.BufferReserve(stmt.buf, stmt.count))
            elif isinstance(stmt, nodes.ReadFields):
                for target, count in _layout_targets(program, stmt):
                    if not _already_reserved(out, target):
                        out.append(nodes.BufferReserve(target, count))
            out.append(stmt)
        return out

    return _apply(program, rewrite)


# -- jump threading ----------------------------------------------------------


def jump_thread(program: nodes.Program) -> bool:
    """
    The IR has no jumps; the lowering peephole threads bytecode jumps.

    The pass exists so the pass table names the intent even though the IR
    cannot express the rewrite.
    """
    return False


__all__ = [
    "bulk_fusion",
    "dead_read_to_skip",
    "jump_thread",
    "layout_infer",
    "literal_append",
    "offset_fusion",
    "read_append_fusion",
    "reserve",
    "skip_fusion",
    "strided_fusion",
]
