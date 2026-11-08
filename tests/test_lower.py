"""
Lowering the IR to module bytes and running the result on every backend.
"""

from __future__ import annotations

import struct

import detree
from detree.ir import (
    Assign,
    Binary,
    BufferAppend,
    Call,
    Compare,
    Const,
    Local,
    ProgramBuilder,
    ReadFields,
    StateGet,
    StateSlot,
    StrConst,
    StreamRead,
    StreamReadCString,
    StrEq,
    nodes,
)
from detree.lower import lower, lower_module
from detree.spec.isa import FieldKind, Op, Typecode
from detree.spec.module import Module
from detree.spec.validate import validate_module


def test_scalar_program_on_every_backend(machine_factory):
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.assign(func.local("x"), StreamRead(Typecode.INT32))
    func.append(out, Local("x"))
    func.ret()
    program = builder.build()

    raw = lower(program)
    validate_module(Module.from_bytes(raw))
    machine = machine_factory(raw)

    assert machine.run(struct.pack(">i", 42), [0, 4])["out"].tolist() == [42]


def test_control_flow_and_call_on_every_backend(machine_factory):
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    callee = builder.function("double", params=("v",), result="i64")
    callee.ret(Binary(Op.ADD, Local("v"), Local("v")))
    main = builder.function("read")
    main.assign(main.local("x"), Const(5))
    main.if_(
        Compare(Op.CMP_GT_S, Local("x"), Const(3)),
        [BufferAppend(out, Const(1))],
        [BufferAppend(out, Const(0))],
    )
    main.assign(main.local("y"), Call(0, Local("x")))
    main.append(out, Local("y"))
    main.for_range("i", Const(0), Const(3), 1, [BufferAppend(out, Local("i"))])
    main.ret()
    program = builder.build(entry=1)

    raw = lower(program)
    machine = machine_factory(raw)

    assert machine.run(b"", [0, 0])["out"].tolist() == [1, 10, 0, 1, 2]


def test_while_loop_on_every_backend(machine_factory):
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    func = builder.function("read")
    func.assign(func.local("i"), Const(0))
    func.assign(func.local("total"), Const(0))
    func.while_(
        Compare(Op.CMP_LT_S, Local("i"), Const(5)),
        [
            Assign(
                Local("total"),
                Binary(Op.ADD, Local("total"), Local("i")),
            ),
            Assign(Local("i"), Binary(Op.ADD, Local("i"), Const(1))),
        ],
    )
    func.append(out, Local("total"))
    func.ret()
    program = builder.build()

    machine = machine_factory(lower(program))

    assert machine.run(b"", [0, 0])["out"].tolist() == [10]


def test_state_string_and_references(machine_factory):
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    name = builder.state("name", init=-1, type="name")
    text = builder.constant("abc")
    func = builder.function("read")
    func.assign(StateSlot(name), StreamReadCString())
    func.append(out, StrEq(StateGet(name), StrConst(text)))
    func.emit(nodes.RefSetObj(Const(5), Const(7)))
    func.append(out, nodes.RefGetObj(Const(5)))
    func.append(out, nodes.RefHas(Const(5)))
    func.append(out, StrEq(nodes.RefGetClass(Const(5)), StrConst(text)))
    func.emit(nodes.RefSetClass(Const(6), StrConst(text)))
    func.append(out, StrEq(nodes.RefGetClass(Const(6)), StrConst(text)))
    func.ret()
    program = builder.build()

    machine = machine_factory(lower(program))
    outputs = machine.run(b"abc\x00", [0, 4])

    assert outputs["out"].tolist() == [1, 7, 1, 0, 1]
    assert machine.state_values()["name"] == "abc"


def test_a_discarded_call_does_not_clobber_the_caller_argument(machine_factory):
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    entry = builder.function("read")
    callee = builder.function("callee", result="i64")
    caller = builder.function("caller", params=("x",), result="i64")

    callee.ret(Const(5))
    caller.emit(nodes.CallStmt(None, 1, None))
    caller.ret(Local("x"))
    entry.append(out, Call(2, Const(42)))
    entry.ret()

    machine = machine_factory(lower(builder.build(entry=0)))

    assert machine.run(b"", [0, 0])["out"].tolist() == [42]


def test_layout_read_on_every_backend(machine_factory):
    builder = ProgramBuilder()
    ints = builder.buffer("ints", "int32")
    shorts = builder.buffer("shorts", "int16")
    layout = builder.layout(
        [
            builder_field(FieldKind.SCALAR, Typecode.INT32, 1, ints),
            builder_field(FieldKind.SKIP, Typecode.UINT8, 2, -1),
            builder_field(FieldKind.ARRAY_FIXED, Typecode.INT16, 2, shorts),
        ]
    )
    func = builder.function("read")
    func.emit(ReadFields(layout, Const(2)))
    func.ret()
    program = builder.build()

    record = struct.pack(">i", 10) + b"xy" + struct.pack(">hh", 1, 2)
    machine = machine_factory(lower(program))
    outputs = machine.run(record + record, [0, len(record) * 2])

    assert outputs["ints"].tolist() == [10, 10]
    assert outputs["shorts"].tolist() == [1, 2, 1, 2]


def builder_field(kind, typecode, count, buffer_id):
    return nodes.LayoutField(kind, typecode, count, buffer_id)


def test_lowering_is_deterministic():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int64")
    func = builder.function("read")
    func.assign(func.local("x"), Binary(Op.ADD, Const(1), Const(2)))
    func.append(out, Local("x"))
    func.ret()
    program = builder.build()

    first = lower(program)
    second = lower(program)

    assert first == second
    assert Module.from_bytes(first).to_bytes() == first


def test_compile_facade():
    builder = ProgramBuilder()
    out = builder.buffer("out", "int32")
    func = builder.function("read")
    func.emit(BufferAppend(out, StreamRead(Typecode.INT32)))
    func.ret()
    program = builder.build()

    raw = detree.compile(program)
    machine = detree.Machine(raw, backend="python")
    assert machine.run(struct.pack(">i", 5), [0, 4])["out"].tolist() == [5]
    assert detree.compile(raw) == raw


def test_lower_module_reports_register_counts():
    builder = ProgramBuilder()
    func = builder.function("read")
    func.assign(func.local("x"), Const(1))
    func.ret()
    module = lower_module(builder.build())

    assert module.funcs[0].reg_count >= 1
    assert module.regs_max >= module.funcs[0].reg_count
