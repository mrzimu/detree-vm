"""
The optimizer passes, one rewrite at a time.
"""

from __future__ import annotations

from detree.ir import (
    Assign,
    Binary,
    BufferAppend,
    BufferAppendConst,
    BufferAppendLen,
    BufferAppendOffset,
    BufferLen,
    CallStmt,
    Compare,
    Const,
    Cursor,
    ForRange,
    If,
    Local,
    ProgramBuilder,
    ReadAppend,
    ReadBulk,
    ReadBulkUntil,
    ReadFields,
    ReadStrided,
    ReadString,
    Return,
    Skip,
    SkipTObject,
    SkipVersion,
    StreamRead,
    While,
    dump,
)
from detree.opt import optimize
from detree.opt.const import const_branch, const_fold
from detree.opt.dead import dead_code, dead_function
from detree.opt.fuse import (
    bulk_fusion,
    bulk_until_fusion,
    dead_read_to_skip,
    layout_infer,
    len_append_fusion,
    literal_append,
    offset_fusion,
    read_append_fusion,
    reserve,
    skip_fusion,
    strided_fusion,
    string_fusion,
    tobject_fusion,
)
from detree.opt.inline import inline_small
from detree.opt.loops import range_unroll
from detree.opt.peephole import condition_locals
from detree.spec.isa import BufferKind, FieldKind, Op, Typecode


def read_program():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    func = builder.function("read")
    func.ret()
    return builder, out, func.function


def test_const_fold_arithmetic():
    builder, out, func = read_program()
    func.body = [
        Assign(Local("x"), Binary(Op.ADD, Const(2), Const(3))),
        BufferAppend(out, Local("x")),
    ]
    program = builder.build()

    assert const_fold(program)
    assert program.functions[0].body[0].value == Const(5)


def test_const_fold_leaves_division_by_zero():
    builder, _, func = read_program()
    func.body = [Assign(Local("x"), Binary(Op.DIV_S, Const(1), Const(0)))]
    program = builder.build()

    assert not const_fold(program)
    assert isinstance(program.functions[0].body[0].value, Binary)


def test_const_fold_leaves_min_over_minus_one():
    builder, _, func = read_program()
    func.body = [Assign(Local("x"), Binary(Op.DIV_S, Const(-(2**63)), Const(-1)))]
    program = builder.build()

    assert not const_fold(program)


def test_const_branch_keeps_one_arm():
    builder, out, func = read_program()
    func.body = [
        If(
            Const(1),
            [BufferAppend(out, Const(1))],
            [BufferAppend(out, Const(0))],
        )
    ]
    program = builder.build()

    assert const_branch(program)
    assert not any(isinstance(stmt, If) for stmt in program.functions[0].body)
    assert len(program.functions[0].body) == 1


def test_const_branch_drops_a_false_while():
    builder, _, func = read_program()
    func.body = [While(Const(0), [Skip(1)])]
    program = builder.build()

    assert const_branch(program)
    assert program.functions[0].body == []


def test_dead_code_drops_an_unused_pure_assignment():
    builder, _, func = read_program()
    func.body = [Assign(Local("x"), Const(5))]
    program = builder.build()

    assert dead_code(program)
    assert program.functions[0].body == []


def test_dead_code_keeps_a_used_assignment():
    builder, out, func = read_program()
    func.body = [
        Assign(Local("x"), Const(5)),
        BufferAppend(out, Local("x")),
    ]
    program = builder.build()

    dead_code(program)
    assert len(program.functions[0].body) == 2


def test_dead_function_drops_an_uncalled_function():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    entry = builder.function("entry")
    entry.emit(BufferAppend(out, Const(1)))
    entry.ret()
    spare = builder.function("spare")
    spare.emit(Skip(4))
    spare.ret()
    program = builder.build()

    assert dead_function(program)
    assert [function.name for function in program.functions] == ["entry"]
    assert program.entry == 0


def test_dead_function_renumbers_the_survivors():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    unused = builder.function("unused")
    unused.emit(Skip(1))
    unused.ret()
    entry = builder.function("entry")
    helper = builder.function("helper")
    helper.emit(BufferAppend(out, Const(7)))
    helper.ret()
    entry.emit(CallStmt(None, 2))
    entry.ret()
    program = builder.build(entry=1)

    assert dead_function(program)
    assert [function.name for function in program.functions] == ["entry", "helper"]
    assert program.entry == 0
    call = program.functions[0].body[0]
    assert isinstance(call, CallStmt) and call.func == 1


def test_dead_function_keeps_everything_reachable():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    entry = builder.function("entry")
    helper = builder.function("helper")
    helper.emit(BufferAppend(out, Const(7)))
    helper.ret()
    entry.emit(CallStmt(None, 1))
    entry.ret()
    program = builder.build()

    assert not dead_function(program)
    assert len(program.functions) == 2


def test_condition_locals_folds_a_flag():
    builder, out, func = read_program()
    func.body = [
        Assign(Local("flag"), Binary(Op.AND, Local("version"), Const(16384))),
        If(Local("flag"), [BufferAppend(out, Const(1))]),
    ]
    program = builder.build()

    assert condition_locals(program)
    body = program.functions[0].body
    assert [type(stmt).__name__ for stmt in body] == ["If"]
    assert body[0].cond == Binary(Op.AND, Local("version"), Const(16384))


def test_condition_locals_leaves_a_value_read_twice():
    builder, out, func = read_program()
    func.body = [
        Assign(Local("flag"), Const(1)),
        If(Local("flag"), [BufferAppend(out, Const(1))]),
        BufferAppend(out, Local("flag")),
    ]
    program = builder.build()

    assert not condition_locals(program)


def test_condition_locals_leaves_a_loop_unstable_value():
    builder = ProgramBuilder()
    chars = builder.buffer("chars", "uint8")
    func = builder.function("read")
    func.body = [
        Assign(Local("size"), BufferLen(chars)),
        While(Local("size"), [Skip(1)]),
    ]
    program = builder.build()

    assert not condition_locals(program)


def test_literal_append():
    builder, out, func = read_program()
    func.body = [BufferAppend(out, Const(7))]
    program = builder.build()

    assert literal_append(program)
    assert isinstance(program.functions[0].body[0], BufferAppendConst)
    assert program.functions[0].body[0].value == 7


def test_read_append_fusion():
    builder, out, func = read_program()
    func.body = [BufferAppend(out, StreamRead(Typecode.INT32))]
    program = builder.build()

    assert read_append_fusion(program)
    assert isinstance(program.functions[0].body[0], ReadAppend)


def test_len_append_fusion():
    builder = ProgramBuilder()
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS, init=(0,))
    chars = builder.buffer("chars", "uint8")
    func = builder.function("read")
    func.emit(BufferAppend(offsets, BufferLen(chars)))
    func.ret()
    program = builder.build()

    assert len_append_fusion(program)
    assert program.functions[0].body[0] == BufferAppendLen(offsets, chars)


def test_len_append_fusion_needs_an_offsets_buffer():
    builder = ProgramBuilder()
    data = builder.buffer("data", "int64")
    chars = builder.buffer("chars", "uint8")
    func = builder.function("read")
    func.emit(BufferAppend(data, BufferLen(chars)))
    func.ret()
    program = builder.build()

    assert not len_append_fusion(program)
    assert isinstance(program.functions[0].body[0], BufferAppend)


def test_offset_fusion():
    builder = ProgramBuilder()
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS)
    func = builder.function("read")
    func.function.body = [
        BufferAppend(
            offsets,
            Binary(Op.ADD, detree_nodes().BufferLast(offsets), Const(5)),
        )
    ]
    program = builder.build()

    assert offset_fusion(program)
    assert isinstance(program.functions[0].body[0], BufferAppendOffset)


def detree_nodes():
    from detree.ir import nodes

    return nodes


def test_tobject_fusion():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.function.body = [
        SkipVersion(),
        Assign(Local("unique"), StreamRead(Typecode.INT32)),
        Assign(Local("bits"), StreamRead(Typecode.UINT32)),
        If(Binary(Op.AND, Local("bits"), Const(16)), [Skip(2)]),
    ]
    program = builder.build()

    assert tobject_fusion(program)
    assert program.functions[0].body == [SkipTObject()]


def test_tobject_fusion_needs_the_reference_skip():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.function.body = [
        SkipVersion(),
        Assign(Local("unique"), StreamRead(Typecode.INT32)),
        Assign(Local("bits"), StreamRead(Typecode.UINT32)),
        If(Binary(Op.AND, Local("bits"), Const(16)), [Skip(4)]),
    ]
    program = builder.build()

    assert not tobject_fusion(program)


def test_skip_fusion():
    builder, _, func = read_program()
    func.body = [Skip(2), SkipVersion(), Skip(3)]
    program = builder.build()

    assert skip_fusion(program)
    assert program.functions[0].body == [Skip(7)]


def test_dead_read_to_skip():
    builder, _, func = read_program()
    func.body = [
        Assign(Local("dead"), StreamRead(Typecode.FLOAT64)),
        Assign(Local("version"), detree_nodes().StreamReadVersion()),
    ]
    program = builder.build()

    assert dead_read_to_skip(program)
    assert program.functions[0].body == [Skip(8), Skip(2)]


def test_bulk_fusion():
    builder, out, func = read_program()
    func.body = [ForRange("i", Const(0), Const(4), 1, [ReadAppend(out, Typecode.INT32)])]
    program = builder.build()

    assert bulk_fusion(program)
    stmt = program.functions[0].body[0]
    assert isinstance(stmt, ReadBulk)
    assert stmt.count == Const(4)


def test_bulk_fusion_keeps_a_runtime_count():
    """
    The count comes from the stream, so it is a value, not a constant.
    """
    builder, out, func = read_program()
    func.locals.append("n")
    func.body = [ForRange("i", Const(0), Local("n"), 1, [ReadAppend(out, Typecode.INT32)])]
    program = builder.build()

    assert bulk_fusion(program)
    stmt = program.functions[0].body[0]
    assert isinstance(stmt, ReadBulk)
    assert stmt.count == Local("n")


def test_bulk_fusion_sees_through_a_read_only_call():
    """
    A loop over a call fuses without inlining the callee.
    """
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    read = builder.function("read")
    read.emit(ReadAppend(out, Typecode.INT32))
    read.ret()
    many = builder.function("read_many", params=("count",))
    many.for_range("i", Const(0), Local("count"), 1, [CallStmt(None, 0)])
    program = builder.build()

    assert bulk_fusion(program)
    stmt = program.functions[1].body[0]
    assert isinstance(stmt, ReadBulk)
    assert stmt.count == Local("count")


def test_bulk_fusion_leaves_a_loop_that_reads_its_own_bound():
    builder, out, func = read_program()
    func.locals.append("n")
    func.body = [ForRange("i", Const(0), Local("i"), 1, [ReadAppend(out, Typecode.INT32)])]
    program = builder.build()

    assert not bulk_fusion(program)
    assert isinstance(program.functions[0].body[0], ForRange)


def test_bulk_until_fusion():
    builder, out, func = read_program()
    func.locals.append("end")
    func.body = [
        While(
            Compare(Op.CMP_LT_S, Cursor(), Local("end")),
            [ReadAppend(out, Typecode.INT32)],
        )
    ]
    program = builder.build()

    assert bulk_until_fusion(program)
    stmt = program.functions[0].body[0]
    assert isinstance(stmt, ReadBulkUntil)
    assert stmt.end == Local("end")


def test_bulk_until_fusion_leaves_another_condition():
    builder, out, func = read_program()
    func.locals.extend(["end", "n"])
    func.body = [
        While(
            Compare(Op.CMP_LT_S, Local("n"), Local("end")),
            [ReadAppend(out, Typecode.INT32)],
        )
    ]
    program = builder.build()

    assert not bulk_until_fusion(program)
    assert isinstance(program.functions[0].body[0], While)


def test_strided_fusion_over_a_flat_run():
    builder, out, func = read_program()
    func.body = [
        ReadAppend(out, Typecode.INT32),
        Skip(4),
        ReadAppend(out, Typecode.INT32),
        Skip(4),
        ReadAppend(out, Typecode.INT32),
    ]
    program = builder.build()

    assert strided_fusion(program)
    assert program.functions[0].body == [ReadStrided(out, Typecode.INT32, Const(3), Const(8))]


def test_strided_fusion_keeps_a_trailing_skip():
    builder, out, func = read_program()
    func.body = [
        ReadAppend(out, Typecode.INT32),
        Skip(4),
        ReadAppend(out, Typecode.INT32),
        Skip(4),
    ]
    program = builder.build()

    assert strided_fusion(program)
    assert program.functions[0].body == [
        ReadStrided(out, Typecode.INT32, Const(2), Const(8)),
        Skip(4),
    ]


def test_strided_fusion_leaves_a_different_element():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "int32")
    func = builder.function("read")
    func.function.body = [
        ReadAppend(a, Typecode.INT32),
        Skip(4),
        ReadAppend(b, Typecode.INT32),
    ]
    program = builder.build()

    assert not strided_fusion(program)


def test_strided_fusion_leaves_a_zero_gap():
    builder, out, func = read_program()
    func.body = [
        ReadAppend(out, Typecode.INT32),
        Skip(0),
        ReadAppend(out, Typecode.INT32),
    ]
    program = builder.build()

    assert not strided_fusion(program)


def _string_body(mode: int):
    """
    The IR for one length-prefixed string, in either form.
    """
    builder = ProgramBuilder()
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS, init=(0,))
    chars = builder.buffer("chars", "uint8")
    func = builder.function("read")
    body = [
        Assign(Local("size"), StreamRead(Typecode.UINT8)),
        If(
            Compare(Op.CMP_EQ, Local("size"), Const(255)),
            [Assign(Local("size"), StreamRead(Typecode.UINT32))],
        ),
    ]
    if mode == 0:
        body.append(ReadBulk(chars, Typecode.UINT8, Local("size")))
        body.append(BufferAppendLen(offsets, chars))
    else:
        body.append(BufferAppendOffset(offsets, Local("size")))
        body.append(ReadBulk(chars, Typecode.UINT8, Local("size")))
    body.append(Return())
    func.function.body = body
    return builder.build(), chars, offsets


def test_string_fusion_of_a_headerless_string():
    program, chars, offsets = _string_body(0)

    assert string_fusion(program)
    assert program.functions[0].body[0] == ReadString(chars, offsets, 0)


def test_string_fusion_of_a_sized_string():
    program, chars, offsets = _string_body(1)

    assert string_fusion(program)
    assert program.functions[0].body[0] == ReadString(chars, offsets, 1)


def test_string_fusion_needs_the_escape_guard():
    builder = ProgramBuilder()
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS, init=(0,))
    chars = builder.buffer("chars", "uint8")
    func = builder.function("read")
    func.function.body = [
        Assign(Local("size"), StreamRead(Typecode.UINT8)),
        ReadBulk(chars, Typecode.UINT8, Local("size")),
        BufferAppendLen(offsets, chars),
        Return(),
    ]
    program = builder.build()

    assert not string_fusion(program)


def test_layout_infer():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "int16")
    func = builder.function("read")
    func.function.body = [ReadAppend(a, Typecode.INT32), ReadAppend(b, Typecode.INT16)]
    program = builder.build()

    assert layout_infer(program)
    assert isinstance(program.functions[0].body[0], ReadFields)
    assert len(program.layouts) == 1
    assert [field.kind for field in program.layouts[0].fields] == [
        FieldKind.SCALAR,
        FieldKind.SCALAR,
    ]


def test_layout_infer_builds_skip_fields():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "int16")
    func = builder.function("read")
    func.function.body = [
        ReadAppend(a, Typecode.INT32),
        Skip(2),
        ReadAppend(b, Typecode.INT16),
    ]
    program = builder.build()

    assert layout_infer(program)
    assert [field.kind for field in program.layouts[0].fields] == [
        FieldKind.SCALAR,
        FieldKind.SKIP,
        FieldKind.SCALAR,
    ]
    assert program.layouts[0].fields[1].count == 2


def test_layout_infer_builds_a_fixed_array_field():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "float64")
    func = builder.function("read")
    func.function.body = [
        ReadAppend(a, Typecode.INT32),
        ReadBulk(b, Typecode.FLOAT64, Const(3)),
    ]
    program = builder.build()

    assert layout_infer(program)
    field = program.layouts[0].fields[1]
    assert field.kind == FieldKind.ARRAY_FIXED
    assert field.count == 3
    assert field.buffer_id == b


def test_layout_infer_reads_a_counted_loop():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "int16")
    func = builder.function("read")
    func.local("n")
    func.function.body = [
        ForRange(
            "i",
            Const(0),
            Local("n"),
            1,
            [ReadAppend(a, Typecode.INT32), ReadAppend(b, Typecode.INT16)],
        )
    ]
    program = builder.build()

    assert layout_infer(program)
    stmt = program.functions[0].body[0]
    assert isinstance(stmt, ReadFields)
    assert stmt.count == Local("n")


def test_layout_infer_needs_a_constant_bulk_count():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "float64")
    func = builder.function("read")
    func.local("k")
    func.function.body = [
        ReadAppend(a, Typecode.INT32),
        ReadBulk(b, Typecode.FLOAT64, Local("k")),
    ]
    program = builder.build()

    assert not layout_infer(program)


def test_range_unroll():
    builder, out, func = read_program()
    func.body = [ForRange("i", Const(0), Const(3), 1, [BufferAppend(out, Local("i"))])]
    program = builder.build()

    assert range_unroll(program)
    assert len(program.functions[0].body) == 3
    assert all(isinstance(stmt, BufferAppend) for stmt in program.functions[0].body)
    assert [stmt.value.value for stmt in program.functions[0].body] == [0, 1, 2]


def test_range_unroll_keeps_a_loop_variable_used_later():
    builder, out, func = read_program()
    func.body = [
        ForRange("i", Const(0), Const(3), 1, [BufferAppend(out, Local("i"))]),
        BufferAppend(out, Local("i")),
    ]
    program = builder.build()

    assert not range_unroll(program)
    assert isinstance(program.functions[0].body[0], ForRange)


def test_reserve_is_inserted_before_a_large_loop():
    builder, out, func = read_program()
    func.body = [
        ForRange(
            "i",
            Const(0),
            Const(20),
            1,
            [ReadAppend(out, Typecode.INT32), BufferAppend(out, Const(1))],
        )
    ]
    program = builder.build()

    assert reserve(program)
    assert isinstance(program.functions[0].body[0], detree_nodes().BufferReserve)
    assert program.functions[0].body[0].count == Const(20)


def test_reserve_covers_a_strided_read():
    builder, out, func = read_program()
    func.body = [ReadStrided(out, Typecode.INT32, Const(8), Const(8))]
    program = builder.build()

    assert reserve(program)
    assert isinstance(program.functions[0].body[0], detree_nodes().BufferReserve)
    assert program.functions[0].body[0].count == Const(8)


def test_inline_small_function():
    builder = ProgramBuilder()
    callee = builder.function("double", params=("v",), result="i64")
    callee.ret(Binary(Op.ADD, Local("v"), Local("v")))
    main = builder.function("main")
    main.emit(CallStmt(Local("x"), 0, Const(3)))
    main.ret()
    program = builder.build(entry=1)

    assert inline_small(program)
    assert not any(isinstance(stmt, CallStmt) for stmt in program.functions[1].body)


def test_optimize_does_not_mutate_its_input():
    builder, out, func = read_program()
    func.body = [BufferAppend(out, Const(1))]
    program = builder.build()
    before = dump(program)

    optimize(program)
    assert dump(program) == before


def test_optimize_level_zero_is_identity():
    builder, out, func = read_program()
    func.body = [BufferAppend(out, Const(1))]
    program = builder.build()

    same = optimize(program, level=0)
    assert same is program


def test_optimize_is_deterministic():
    builder, out, func = read_program()
    func.body = [
        Assign(Local("x"), Binary(Op.ADD, Const(1), Const(2))),
        BufferAppend(out, Local("x")),
    ]
    program = builder.build()

    assert dump(optimize(program)) == dump(optimize(program))


def test_optimized_program_still_verifies_and_runs():
    import struct

    import detree
    from detree.spec.isa import Typecode as TC

    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.assign(Local("x"), StreamRead(TC.INT32))
    func.append(out, Local("x"))
    func.ret()
    program = builder.build()

    raw = detree.compile(program)
    machine = detree.Machine(raw, backend="python")
    assert machine.run(struct.pack(">i", 9), [0, 4])["out"].tolist() == [9]


def test_strided_program_reads_the_same_stream_after_optimization():
    import struct

    import detree
    from detree.lower import lower

    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.function.body = [
        ReadAppend(out, Typecode.INT32),
        Skip(4),
        ReadAppend(out, Typecode.INT32),
        Skip(4),
        ReadAppend(out, Typecode.INT32),
        Return(),
    ]
    program = builder.build()

    record = b"".join(struct.pack(">i", value) + b"\0\0\0\0" for value in (1, 2, 3))[:-4]
    offsets = [0, len(record)]

    optimized = optimize(program)
    assert any(isinstance(stmt, ReadStrided) for stmt in optimized.functions[0].body)
    for module in (lower(program), lower(optimized)):
        outputs = detree.Machine(module, backend="python").run(record, offsets)
        assert outputs["out"].tolist() == [1, 2, 3]


def test_counted_object_loop_reads_the_same_stream_after_optimization():
    import struct

    import detree
    from detree.lower import lower

    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.local("n")
    func.function.body = [
        Assign(Local("n"), StreamRead(Typecode.INT32)),
        ForRange(
            "i",
            Const(0),
            Local("n"),
            1,
            [ReadAppend(out, Typecode.INT32), Skip(4)],
        ),
        Return(),
    ]
    program = builder.build()

    record = struct.pack(">i", 3)
    for value in (1, 2, 3):
        record += struct.pack(">i", value) + b"\0\0\0\0"
    offsets = [0, len(record)]

    optimized = optimize(program)
    assert any(isinstance(stmt, ReadFields) for stmt in optimized.functions[0].body)
    for module in (lower(program), lower(optimized)):
        outputs = detree.Machine(module, backend="python").run(record, offsets)
        assert outputs["out"].tolist() == [1, 2, 3]


def test_layout_program_reads_the_same_stream_after_optimization():
    import struct

    import detree
    from detree.lower import lower

    builder = ProgramBuilder()
    ints = builder.buffer("ints", "int32")
    floats = builder.buffer("floats", "float64")
    func = builder.function("read")
    func.function.body = [
        ReadAppend(ints, Typecode.INT32),
        Skip(2),
        ReadBulk(floats, Typecode.FLOAT64, Const(2)),
        ReadAppend(ints, Typecode.INT32),
        Return(),
    ]
    program = builder.build()

    record = struct.pack(">i", 7) + b"xy" + struct.pack(">dd", 1.5, 2.5) + struct.pack(">i", 8)
    offsets = [0, len(record)]

    optimized = optimize(program)
    assert any(isinstance(stmt, ReadFields) for stmt in optimized.functions[0].body)
    for module in (lower(program), lower(optimized)):
        outputs = detree.Machine(module, backend="python").run(record, offsets)
        assert outputs["ints"].tolist() == [7, 8]
        assert outputs["floats"].tolist() == [1.5, 2.5]
