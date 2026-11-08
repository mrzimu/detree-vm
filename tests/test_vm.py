"""
Hand-built module tests for the Python reference machine.
"""

from __future__ import annotations

import struct

import pytest
from module_builder import Builder, ins

from detree.spec.errors import StreamUnderflow
from detree.spec.isa import BufferKind, Op, Typecode
from detree.spec.module import Module
from detree.vm import PythonMachine


def test_module_roundtrip_is_exact():
    builder = Builder()
    data = builder.buffer("data", "uint32")
    code = [ins(Op.SREAD, rd=0, imm1=Typecode.UINT32), ins(Op.BUF_APPEND, imm1=data, ra=0)]
    code.append(ins(Op.RET, ra=0))
    builder.function("read", code)
    module = builder.build()

    raw = module.to_bytes()
    again = Module.from_bytes(raw)

    assert again.to_bytes() == raw
    assert again.content_hash() == module.content_hash()


def test_scalar_read_appends():
    builder = Builder()
    data = builder.buffer("data", "uint32")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.UINT32),
            ins(Op.BUF_APPEND, imm1=data, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    machine = PythonMachine(builder.build().to_bytes())

    outputs = machine.run(b"\x00\x00\x00\x2a", [0, 4])

    assert outputs["data"].tolist() == [42]


def test_offsets_and_bulk_float():
    builder = Builder()
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS, init=(0,))
    data = builder.buffer("data", "float64")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.UINT32),
            ins(Op.BUF_APPEND_OFFSET, imm1=offsets, ra=0),
            ins(Op.READ_BULK, imm1=data, imm2=Typecode.FLOAT64, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    machine = PythonMachine(builder.build().to_bytes())
    basket = struct.pack(">I", 2) + struct.pack(">dd", 1.5, 2.5)

    outputs = machine.run(basket, [0, len(basket)])

    assert outputs["offsets"].tolist() == [0, 2]
    assert outputs["data"].tolist() == [1.5, 2.5]


def test_arithmetic_and_state():
    builder = Builder()
    data = builder.buffer("data", "int64")
    counter = builder.state("counter", 10)
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=5),
            ins(Op.CONST, rd=1, imm1=7),
            ins(Op.ADD, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=data, ra=2),
            ins(Op.STATE_IADD, imm1=counter, imm2=3),
            ins(Op.RET, ra=2),
        ],
        reg_count=3,
    )
    machine = PythonMachine(builder.build().to_bytes())

    outputs = machine.run(b"", [0, 0])

    assert outputs["data"].tolist() == [12]
    assert machine.state_values()["counter"] == 13


def test_stream_underflow_is_reported():
    builder = Builder()
    builder.function("read", [ins(Op.SREAD, rd=0, imm1=Typecode.UINT32), ins(Op.RET, ra=0)])
    machine = PythonMachine(builder.build().to_bytes())

    with pytest.raises(StreamUnderflow):
        machine.run(b"\x00\x00", [0, 2])
