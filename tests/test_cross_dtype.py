"""
Cross-dtype stream conversion: a bulk read may target another dtype.
"""

from __future__ import annotations

import struct

import pytest
from module_builder import Builder, ins

from detree.spec.errors import ReadLengthMismatch, StreamUnderflow
from detree.spec.isa import FieldKind, Op, Typecode


@pytest.mark.parametrize(
    "src_typecode,fmt,values,dtype,expected",
    [
        (Typecode.FLOAT32, ">ff", (1.5, -2.5), "float64", [1.5, -2.5]),
        (Typecode.FLOAT64, ">dd", (1.5, -2.5), "float32", [1.5, -2.5]),
        (Typecode.INT16, ">hh", (1, -2), "float64", [1.0, -2.0]),
        (Typecode.FLOAT64, ">dd", (1.9, -2.9), "int32", [1, -2]),
        (Typecode.UINT8, ">BB", (200, 255), "int32", [200, 255]),
        (Typecode.INT64, ">qq", (-1, 2), "float64", [-1.0, 2.0]),
    ],
)
def test_bulk_cross_dtype(machine_factory, src_typecode, fmt, values, dtype, expected):
    builder = Builder()
    out = builder.buffer("out", dtype)
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.READ_BULK, imm1=out, ra=0, imm2=src_typecode),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(fmt, *values)

    machine = machine_factory(builder.build().to_bytes())
    outputs = machine.run(data, [0, len(data)])

    assert outputs["out"].tolist() == expected


def test_layout_array_cross_dtype(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "float64")
    layout = builder.layout(
        [builder.field(FieldKind.ARRAY_FIXED, Typecode.FLOAT32, count=2, buffer_id=out)]
    )
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.READ_FIELDS, imm1=layout, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(">ff", 1.5, -2.5)

    machine = machine_factory(builder.build().to_bytes())
    outputs = machine.run(data, [0, len(data)])

    assert outputs["out"].tolist() == [1.5, -2.5]


def test_negative_stride_is_rejected(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.CONST, rd=1, imm1=-1),
            ins(Op.READ_STRIDED, imm1=out, ra=0, rb=1, imm2=Typecode.INT32),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(StreamUnderflow):
        machine.run(b"\x00\x00\x00\x00", [0, 4])


def test_read_bulk_until_rejects_a_partial_element(machine_factory):
    """
    A span that is not a whole number of elements is a length mismatch.
    """
    builder = Builder()
    ints = builder.buffer("ints", "int32")
    builder.function(
        "read",
        [
            ins(Op.EVENT_END, rd=0),
            ins(Op.READ_BULK_UNTIL, imm1=ints, ra=0, imm2=Typecode.INT32),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )
    machine = machine_factory(builder.build().to_bytes())
    with pytest.raises(ReadLengthMismatch):
        machine.run(b"\x00\x00\x00\x01\x00\x00", (0, 6), 0)
