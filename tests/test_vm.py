"""
Hand-built module tests for the Python reference machine.
"""

from __future__ import annotations

import struct

import pytest
from module_builder import Builder, ins

from detree.spec.errors import (
    AssertionFailed,
    DetreeError,
    StackOverflow,
    StreamUnderflow,
)
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


# -- byte-count frames ---------------------------------------------------------
def _nested_frames_module() -> bytes:
    builder = Builder()
    values = builder.buffer("values", "int32")
    inner = builder.string("inner frame")
    outer = builder.string("outer frame")
    builder.function(
        "read",
        [
            ins(Op.FNB_BEGIN),
            ins(Op.FNB_BEGIN),
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.BUF_APPEND, imm1=values, ra=0),
            ins(Op.FNB_END, imm1=inner),
            ins(Op.FNB_END, imm1=outer),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )
    return builder.build().to_bytes()


def _nested_frames_basket(outer_count: int) -> bytes:
    return (
        struct.pack(">I", 0x40000000 | outer_count)
        + struct.pack(">I", 0x40000004)
        + struct.pack(">i", 7)
    )


def test_byte_count_frames_nest_and_check():
    machine = PythonMachine(_nested_frames_module())
    basket = _nested_frames_basket(8)

    assert machine.run(basket, [0, len(basket)])["values"].tolist() == [7]


def test_byte_count_mismatch_names_both_positions():
    machine = PythonMachine(_nested_frames_module())
    basket = _nested_frames_basket(9)

    with pytest.raises(AssertionFailed) as info:
        machine.run(basket, [0, len(basket)])

    assert "outer frame (expected 13, got 12)" in str(info.value)


def test_byte_count_end_without_begin_is_an_error():
    builder = Builder()
    message = builder.string("orphan frame")
    builder.function("read", [ins(Op.FNB_END, imm1=message), ins(Op.RET, ra=0)], reg_count=1)
    machine = PythonMachine(builder.build().to_bytes())

    with pytest.raises(DetreeError, match="no open byte-count frame"):
        machine.run(b"", [0, 0])


def test_a_return_with_an_open_frame_is_an_error():
    builder = Builder()
    builder.function("read", [ins(Op.FNB_BEGIN), ins(Op.RET, ra=0)], reg_count=1)
    machine = PythonMachine(builder.build().to_bytes())

    with pytest.raises(DetreeError, match="byte-count depth 1, expected 0"):
        machine.run(struct.pack(">I", 0x40000004), [0, 4])


def test_byte_count_depth_is_capped():
    builder = Builder()
    builder.function("read", [ins(Op.FNB_BEGIN)] * 257 + [ins(Op.RET, ra=0)], reg_count=1)
    machine = PythonMachine(builder.build().to_bytes())
    basket = struct.pack(">I", 0x40000000) * 257

    with pytest.raises(StackOverflow):
        machine.run(basket, [0, len(basket)])
