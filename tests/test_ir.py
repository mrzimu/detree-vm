"""
The IR builder, dump, and verifier.
"""

from __future__ import annotations

import pytest

from detree.ir import (
    Binary,
    BufferAppend,
    Call,
    Const,
    ConstFloat,
    Local,
    ProgramBuilder,
    StreamRead,
    dump,
    nodes,
    verify_program,
)
from detree.ir.errors import IRValidationError
from detree.spec.isa import BufferKind, Op, Typecode


def sample():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.assign(func.local("x"), StreamRead(Typecode.INT32))
    func.append(out, Local("x"))
    func.ret()
    return builder.build(), out


def test_builder_assigns_ids_by_declaration_order():
    builder = ProgramBuilder()
    a = builder.buffer("a", "int32")
    b = builder.buffer("b", "float64")
    x = builder.state("x", 1)
    y = builder.state("y", init=-1, type="name")
    text = builder.string("hello")
    again = builder.string("hello")

    assert (a, b) == (0, 1)
    assert (x, y) == (0, 1)
    assert text == again == 0
    assert builder.program.buffers[1].name == "b"


def test_constant_is_deduplicated_and_in_the_arena():
    builder = ProgramBuilder()
    first = builder.constant("abc")
    second = builder.constant("abc")
    third = builder.constant("def")

    assert (first, second, third) == (0, 0, 1)
    assert builder.program.strings == ["abc", "def"]


def test_dump_is_stable_and_readable():
    program, _ = sample()
    text = dump(program)

    assert text.startswith("program entry=0\n")
    assert "buffer 0 name=out dtype=int32 kind=DATA" in text
    assert "function 0 name=read arity=0 result=void" in text
    assert "assign local(x) = stream_read(int32)" in text
    assert "buf_append 0 = local(x)" in text
    assert dump(program) == text


def test_verify_accepts_a_minimal_program():
    program, _ = sample()
    verify_program(program)


def test_verify_rejects_an_undefined_local():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.append(out, Local("missing"))
    func.ret()

    with pytest.raises(IRValidationError, match="read before"):
        verify_program(builder.build())


def test_verify_rejects_float_arithmetic():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.assign(
        func.local("x"),
        Binary(Op.ADD, ConstFloat(0, 64), ConstFloat(0, 64)),
    )
    func.ret()

    with pytest.raises(IRValidationError, match="integer"):
        verify_program(builder.build())


def test_verify_rejects_a_float_condition():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.if_(ConstFloat(0, 32), [nodes.Nop()])
    func.ret()

    with pytest.raises(IRValidationError, match="integer"):
        verify_program(builder.build())


def test_verify_rejects_a_bad_call():
    builder = ProgramBuilder()
    callee = builder.function("one", params=("v",), result="i64")
    callee.ret(Local("v"))
    main = builder.function("main")
    main.assign(main.local("x"), Call(0))
    main.ret()

    with pytest.raises(IRValidationError, match="argument"):
        verify_program(builder.build(entry=1))


def test_verify_rejects_a_bad_buffer_reference():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.emit(BufferAppend(3, Const(1)))
    func.ret()

    with pytest.raises(IRValidationError, match="buffer"):
        verify_program(builder.build())


def test_verify_accepts_a_narrow_offsets_buffer():
    builder = ProgramBuilder()
    offsets = builder.buffer("offsets", "int16", kind=BufferKind.OFFSETS, init=(0,))
    func = builder.function("read")
    func.emit(nodes.BufferAppendOffset(offsets, Const(4)))
    func.ret()

    verify_program(builder.build())


def test_verify_rejects_a_float_structural_buffer():
    builder = ProgramBuilder()
    builder.buffer("offsets", "float64", kind=BufferKind.OFFSETS)
    func = builder.function("read")
    func.ret()

    with pytest.raises(IRValidationError, match="integer"):
        verify_program(builder.build())


def test_verify_rejects_a_bool_offsets_buffer():
    builder = ProgramBuilder()
    builder.buffer("offsets", "bool", kind=BufferKind.OFFSETS)
    func = builder.function("read")
    func.ret()

    with pytest.raises(IRValidationError, match="integer"):
        verify_program(builder.build())


def test_verify_rejects_an_entry_with_parameters():
    builder = ProgramBuilder()
    func = builder.function("main", params=("v",))
    func.ret()
    with pytest.raises(IRValidationError, match="entry"):
        verify_program(builder.build())


def test_verify_rejects_a_missing_return():
    builder = ProgramBuilder()
    value = builder.function("value", result="i64")
    value.if_(Const(1), [nodes.Return(Const(1))], [])
    main = builder.function("main")
    main.ret()

    with pytest.raises(IRValidationError, match="must return"):
        verify_program(builder.build(entry=1))


def test_verify_accepts_float_copy_and_append():
    builder = ProgramBuilder()
    out = builder.buffer("out", "float64")
    func = builder.function("read")
    func.assign(func.local("f"), ConstFloat(0x3FF8000000000000, 64))
    func.append(out, Local("f"))
    func.ret()

    verify_program(builder.build())


def test_verify_allows_bool_where_i64_is_expected():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    func = builder.function("read")
    func.assign(func.local("flag"), nodes.Compare(Op.CMP_EQ, Const(1), Const(1)))
    func.append(out, Local("flag"))
    func.ret()

    verify_program(builder.build())
