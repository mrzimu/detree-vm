"""
Cross-backend conformance: the same module must mean the same thing.

Every test here is written once and runs on each machine the build provides:
the Python reference, the C++ library, and the Rust library. A skipped backend
is reported by pytest; a backend that disagrees is a failure, not a skip.
"""

from __future__ import annotations

import struct

import pytest
from module_builder import Builder, ins

from detree.spec.errors import (
    AssertionFailed,
    DetreeError,
    InvalidByteCount,
    ReadLengthMismatch,
    StackOverflow,
    StreamUnderflow,
)
from detree.spec.isa import BufferKind, FieldKind, Op, Typecode


def run(machine_factory, builder, data=b"", offsets=None, cursor_offset=0):
    """
    Build the module, run one basket, and return `(outputs, states, machine)`.
    """
    machine = machine_factory(builder.build().to_bytes())
    if offsets is None:
        offsets = [0, len(data)]
    outputs = machine.run(data, offsets, cursor_offset)
    return outputs, machine.state_values(), machine


# ---------------------------------------------------------------------------
# Scalars, coercions, and initialization


@pytest.mark.parametrize(
    "typecode,dtype,fmt,value",
    [
        (Typecode.UINT8, "uint8", ">B", 255),
        (Typecode.UINT16, "uint16", ">H", 65535),
        (Typecode.UINT32, "uint32", ">I", 4_000_000_000),
        (Typecode.UINT64, "uint64", ">Q", 2**64 - 2),
        (Typecode.INT8, "int8", ">b", -128),
        (Typecode.INT16, "int16", ">h", -32768),
        (Typecode.INT32, "int32", ">i", -2_000_000_000),
        (Typecode.INT64, "int64", ">q", -(2**63)),
        (Typecode.FLOAT32, "float32", ">f", 1.5),
        (Typecode.FLOAT64, "float64", ">d", -2.25),
    ],
)
def test_scalar_typecodes(machine_factory, typecode, dtype, fmt, value):
    builder = Builder()
    out = builder.buffer("out", dtype)
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=typecode),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(fmt, value)

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [value]


@pytest.mark.parametrize(
    "src_typecode,src_fmt,src_value,dtype,expected",
    [
        (Typecode.UINT32, ">I", 0x1FF, "uint8", 0xFF),
        (Typecode.UINT32, ">I", 0x1FF, "int8", -1),
        (Typecode.UINT32, ">I", 0x1FF, "uint16", 0x1FF),
        (Typecode.UINT32, ">I", 0x1FF, "int64", 0x1FF),
        (Typecode.INT16, ">h", -2, "uint8", 254),
        (Typecode.INT16, ">h", -2, "uint64", 2**64 - 2),
        (Typecode.FLOAT32, ">f", 1.5, "float32", 1.5),
        (Typecode.FLOAT64, ">d", 2.5, "float64", 2.5),
    ],
)
def test_register_coercions(
    machine_factory, src_typecode, src_fmt, src_value, dtype, expected
):
    builder = Builder()
    out = builder.buffer("out", dtype)
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=src_typecode),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(src_fmt, src_value)

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [expected]


@pytest.mark.parametrize(
    "dtype,init,expected",
    [
        ("bool", (0, 1, 7), [0, 1, 1]),
        ("uint8", (0, 255, 256), [0, 255, 0]),
        ("int8", (0, -1, 127, 128), [0, -1, 127, -128]),
        ("uint64", (0, -1), [0, 2**64 - 1]),
        ("int64", (0, -1, -(2**63)), [0, -1, -(2**63)]),
    ],
)
def test_buffer_init_values(machine_factory, dtype, init, expected):
    builder = Builder()
    builder.buffer("out", dtype, init=init)
    builder.function("read", [ins(Op.RET, ra=0)])

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == expected


def test_float_buffer_init_uses_bit_patterns(machine_factory):
    builder = Builder()
    builder.buffer("out", "float64", init=(0, 1, -1))
    builder.function("read", [ins(Op.RET, ra=0)])

    outputs, _, _ = run(machine_factory, builder, b"")

    expected = [
        struct.unpack("<d", struct.pack("<Q", value))[0] for value in (0, 1, 2**64 - 1)
    ]
    assert [struct.pack("<d", value) for value in outputs["out"].tolist()] == [
        struct.pack("<d", value) for value in expected
    ]


def test_buffer_kinds_are_declared(machine_factory):
    builder = Builder()
    data = builder.buffer("data", "int32")
    offsets = builder.buffer("offsets", "int64", kind=BufferKind.OFFSETS)
    builder.function(
        "read",
        [
            ins(Op.BUF_APPEND_CONST, imm1=data, imm2=1),
            ins(Op.BUF_APPEND_CONST, imm1=offsets, imm2=2),
            ins(Op.RET, ra=0),
        ],
    )

    outputs, _, _ = run(machine_factory, builder, b"")
    assert outputs["data"].tolist() == [1]
    assert outputs["offsets"].tolist() == [2]


def _value_from_bits(dtype, bits):
    """The value a buffer of *dtype* reports after appending *bits*."""
    if dtype == "bool":
        return True
    if dtype == "float32":
        return struct.unpack("<f", struct.pack("<I", bits))[0]
    if dtype == "float64":
        return struct.unpack("<d", struct.pack("<Q", bits))[0]
    return bits


_BUFFER_DTYPES = [
    "bool",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "int8",
    "int16",
    "int32",
    "int64",
    "float32",
    "float64",
]
_INT_DTYPES = [name for name in _BUFFER_DTYPES if name not in ("bool", "float32", "float64")]


@pytest.mark.parametrize(
    "kind,dtype",
    [(BufferKind.DATA, name) for name in _BUFFER_DTYPES]
    + [(BufferKind.OFFSETS, name) for name in _INT_DTYPES],
)
def test_every_buffer_dtype_takes_its_kind(machine_factory, kind, dtype):
    builder = Builder()
    out = builder.buffer("out", dtype, kind=kind)
    builder.function(
        "read",
        [
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=3),
            ins(Op.RET, ra=0),
        ],
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].dtype.name == dtype
    assert outputs["out"].tolist() == [_value_from_bits(dtype, 3)]


def test_narrow_offsets_buffer(machine_factory):
    builder = Builder()
    offsets = builder.buffer("offsets", "int16", kind=BufferKind.OFFSETS, init=(0,))
    lens = builder.buffer("lens", "int32")
    data = builder.buffer("data", "int32")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=300),
            ins(Op.BUF_APPEND_OFFSET, imm1=offsets, ra=0),
            ins(Op.BUF_APPEND_OFFSET, imm1=offsets, ra=0),
            ins(Op.CONST, rd=1, imm1=40000),
            ins(Op.BUF_APPEND_OFFSET, imm1=offsets, ra=1),
            ins(Op.BUF_LAST, rd=2, imm1=offsets),
            ins(Op.BUF_APPEND, imm1=data, ra=2),
            ins(Op.BUF_APPEND_CONST, imm1=lens, imm2=7),
            ins(Op.BUF_LEN, rd=3, imm1=offsets),
            ins(Op.BUF_APPEND, imm1=lens, ra=3),
            ins(Op.RET, ra=0),
        ],
        reg_count=4,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    # 600 + 40000 does not fit int16, and every machine wraps it the same way.
    assert outputs["offsets"].tolist() == [0, 300, 600, -24936]
    assert outputs["data"].tolist() == [-24936]
    assert outputs["lens"].tolist() == [7, 4]


# ---------------------------------------------------------------------------
# Integer arithmetic and comparison


def test_integer_arithmetic(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    code = [
        ins(Op.CONST, rd=0, imm1=7),
        ins(Op.CONST, rd=1, imm1=3),
        ins(Op.ADD, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.SUB, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.MUL, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.DIV_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.DIV_U, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.MOD_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.MOD_U, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.NEG, rd=2, ra=0),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.SHL, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.SHR_U, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.AND, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.OR, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.XOR, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.NOT, rd=2, ra=0),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.NOT_B, rd=2, ra=0),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.NOT_B, rd=2, ra=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.RET, ra=0),
    ]
    builder.function("read", code, reg_count=3)

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [
        10,
        4,
        21,
        2,
        2,
        1,
        1,
        -7,
        56,
        0,
        3,
        7,
        4,
        -8,
        0,
        0,
    ]


def test_arithmetic_edges(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    code = [
        ins(Op.CONST, rd=0, imm1=5),
        ins(Op.CONST, rd=1, imm1=0),
        ins(Op.DIV_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.MOD_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.CONST, rd=0, imm1=-(2**63)),
        ins(Op.CONST, rd=1, imm1=-1),
        ins(Op.DIV_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.MOD_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.RET, ra=0),
    ]
    builder.function("read", code, reg_count=3)

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [0, 0, -(2**63), 0]


def test_signed_and_unsigned_shifts(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    code = [
        ins(Op.CONST, rd=0, imm1=-8),
        ins(Op.CONST, rd=1, imm1=1),
        ins(Op.SHR_S, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.SHR_U, rd=2, ra=0, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.CONST, rd=1, imm1=64),
        ins(Op.SHL, rd=2, ra=1, rb=1),
        ins(Op.BUF_APPEND, imm1=out, ra=2),
        ins(Op.RET, ra=0),
    ]
    builder.function("read", code, reg_count=3)

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [-4, 2**63 - 4, 64]


def test_comparisons(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    code = [
        ins(Op.CONST, rd=0, imm1=-1),
        ins(Op.CONST, rd=1, imm1=1),
    ]
    for op in (
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
    ):
        code.append(ins(op, rd=2, ra=0, rb=1))
        code.append(ins(Op.BUF_APPEND, imm1=out, ra=2))
    code.append(ins(Op.RET, ra=0))
    builder.function("read", code, reg_count=3)

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [0, 1, 1, 1, 0, 0, 0, 0, 1, 1]


# ---------------------------------------------------------------------------
# Control flow


@pytest.mark.parametrize(
    "op,a,b,taken",
    [
        (Op.BR_EQ, 3, 3, True),
        (Op.BR_EQ, 3, 4, False),
        (Op.BR_NE, 3, 4, True),
        (Op.BR_LT_S, -1, 1, True),
        (Op.BR_LE_S, 1, 1, True),
        (Op.BR_GT_S, -1, 1, False),
        (Op.BR_GE_S, 1, 1, True),
        (Op.BR_LT_U, -1, 1, False),
        (Op.BR_LE_U, 1, 1, True),
        (Op.BR_GT_U, -1, 1, True),
        (Op.BR_GE_U, 1, 1, True),
    ],
)
def test_conditional_branches(machine_factory, op, a, b, taken):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=a),
            ins(Op.CONST, rd=1, imm1=b),
            ins(op, ra=0, rb=1, imm1=5),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=0),
            ins(Op.JMP, imm1=6),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=1),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [1 if taken else 0]


def test_unconditional_and_zero_branches(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=0),
            ins(Op.JZ, ra=0, imm1=5),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=1),
            ins(Op.JMP, imm1=5),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=2),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=3),
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.JNZ, ra=0, imm1=10),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=4),
            ins(Op.RET, ra=0),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=5),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [3, 5]


def test_call_and_return(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "main",
        [
            ins(Op.CONST, rd=0, imm1=21),
            ins(Op.CALL, rd=0, ra=0, imm1=1),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    builder.function(
        "double",
        [
            ins(Op.ADD, rd=0, ra=0, rb=0),
            ins(Op.RET, ra=0),
        ],
        arity=1,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [42]


def test_void_function_and_entry_return(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "main",
        [
            ins(Op.CALL, rd=0, ra=0, imm1=1),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=9),
            ins(Op.RET, ra=0),
        ],
    )
    builder.function(
        "noop",
        [
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=7),
            ins(Op.RET, ra=0),
        ],
        arity=0,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [7, 9]


def test_stack_overflow(machine_factory):
    builder = Builder()
    builder.function("recurse", [ins(Op.CALL, rd=0, ra=0, imm1=0)])

    machine = machine_factory(builder.build().to_bytes())
    with pytest.raises(StackOverflow):
        machine.run(b"", [0, 0])


# ---------------------------------------------------------------------------
# Stream access


def test_fnb_version_and_cstr(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    state = builder.state("name", is_string=True)
    text = builder.string("abc")
    builder.function(
        "read",
        [
            ins(Op.SREAD_FNB, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.SREAD_VERSION, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.SREAD_CSTR, rd=0),
            ins(Op.STR_STORE, imm1=state, ra=0),
            ins(Op.STR_CONST, rd=1, imm1=text),
            ins(Op.STR_EQ, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.STR_LOAD, rd=1, imm1=state),
            ins(Op.STR_EQ, rd=2, ra=1, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.RET, ra=0),
        ],
        reg_count=3,
    )
    data = struct.pack(">I", 0x40000005) + struct.pack(">H", 0xFFFF) + b"abc\x00"

    outputs, states, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [5, -1, 1, 1]
    assert states["name"] == "abc"


def test_string_missing_is_none(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    state = builder.state("name", init=-1, is_string=True)
    text = builder.string("x")
    builder.function(
        "read",
        [
            ins(Op.STR_CONST, rd=0, imm1=text),
            ins(Op.STR_LOAD, rd=1, imm1=state),
            ins(Op.STR_EQ, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.RET, ra=0),
        ],
        reg_count=3,
    )

    outputs, states, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [0]
    assert states["name"] is None


def test_skips_and_cursor(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.SKIP, imm1=3),
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.SKIP_R, ra=0),
            ins(Op.SKIP_VERSION),
            ins(Op.SKIP_CSTR),
            ins(Op.CURSOR, rd=1),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.SET_CURSOR, ra=0),
            ins(Op.CURSOR, rd=1),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.CONST, rd=0, imm1=9),
            ins(Op.SET_CURSOR, ra=0),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    data = b"abc" + b"de" + b"\x00\x00" + b"x\x00"

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [9, 2]


def test_skip_object_headers(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "uint32")
    builder.function(
        "read",
        [
            ins(Op.SKIP_TOBJECT),
            ins(Op.SKIP_OBJHDR),
            ins(Op.SREAD, rd=0, imm1=Typecode.UINT32),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    data = (
        b"\x00" * 6
        + struct.pack(">I", 0x10)
        + b"\x00" * 2
        + struct.pack(">I", 0x40000000)
        + struct.pack(">I", 0xFFFFFFFF)
        + b"cls\x00"
        + struct.pack(">I", 1234)
    )

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [1234]


def test_invalid_byte_count(machine_factory):
    builder = Builder()
    builder.function(
        "read",
        [
            ins(Op.SREAD_FNB, rd=0),
            ins(Op.RET, ra=0),
        ],
    )
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(InvalidByteCount):
        machine.run(struct.pack(">I", 5), [0, 4])


def test_stream_underflow(machine_factory):
    builder = Builder()
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.UINT32),
            ins(Op.RET, ra=0),
        ],
    )
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(StreamUnderflow):
        machine.run(b"\x00\x00", [0, 2])


# ---------------------------------------------------------------------------
# Event offsets


def test_event_offset_ops(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.SKIP, imm1=4),
            ins(Op.ENTRIES, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.INIT_OFFSET, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.DATA_SIZE, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.CONST, rd=1, imm1=0),
            ins(Op.OFFSET_AT, rd=0, ra=1),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.CONST, rd=1, imm1=2),
            ins(Op.OFFSET_SEEK, rd=0, ra=1),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.EVENT_END, rd=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    data = b"\x00\x00\x00\x00" + b"\x00" * 4

    outputs, _, _ = run(machine_factory, builder, data, offsets=[0, 4, 8], cursor_offset=16)

    # Two events; the event loop calls the entry once per event.
    assert outputs["out"].tolist() == [
        2,
        16,
        4,
        0,
        4,
        4,
        2,
        16,
        4,
        0,
        4,
        8,
    ]


def test_event_length_mismatch(machine_factory):
    builder = Builder()
    builder.function("read", [ins(Op.RET, ra=0)])
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(ReadLengthMismatch) as info:
        machine.run(b"", [0, 1])

    assert info.value.function == "read"
    assert info.value.ip == -1
    assert info.value.cursor == 0


def test_offset_seek_is_signed(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.SKIP, imm1=4),
            ins(Op.CONST, rd=0, imm1=-1),
            ins(Op.OFFSET_SEEK, rd=0, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    data = b"\x00\x00\x00\x00"

    outputs, _, _ = run(machine_factory, builder, data, offsets=[0, 4])

    assert outputs["out"].tolist() == [0]


def test_buf_reserve_is_only_a_hint(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=-1),
            ins(Op.BUF_RESERVE, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == []


def test_execution_past_the_end(machine_factory):
    builder = Builder()
    builder.function("read", [ins(Op.NOP)])
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(DetreeError) as info:
        machine.run(b"", [0, 0])

    assert info.value.function == "read"
    assert info.value.ip == 1
    assert info.value.cursor == 0


# ---------------------------------------------------------------------------
# Bulk reads and buffer operations


def test_bulk_reads(machine_factory):
    builder = Builder()
    counts = builder.buffer("counts", "int64")
    ints = builder.buffer("ints", "int32")
    floats = builder.buffer("floats", "float64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=3),
            ins(Op.READ_BULK, imm1=ints, ra=0, imm2=Typecode.INT32),
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.READ_BULK, imm1=floats, ra=0, imm2=Typecode.FLOAT64),
            ins(Op.BUF_APPEND_CONST, imm1=counts, imm2=3),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(">iii", 1, -2, 3) + struct.pack(">dd", 1.5, -2.5)

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["ints"].tolist() == [1, -2, 3]
    assert outputs["floats"].tolist() == [1.5, -2.5]
    assert outputs["counts"].tolist() == [3]


def test_strided_read(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=3),
            ins(Op.CONST, rd=1, imm1=8),
            ins(Op.READ_STRIDED, imm1=out, ra=0, rb=1, imm2=Typecode.INT32),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    data = (
        struct.pack(">i", 10)
        + b"pad!"
        + struct.pack(">i", 20)
        + b"pad!"
        + struct.pack(">i", 30)
    )

    outputs, _, _ = run(machine_factory, builder, data)

    assert outputs["out"].tolist() == [10, 20, 30]


def test_read_append_and_buffer_ops(machine_factory):
    builder = Builder()
    data = builder.buffer("data", "int16")
    last = builder.buffer("last", "int64")
    length = builder.buffer("length", "int64")
    offsets = builder.buffer("offsets", "int64", init=(0,))
    builder.function(
        "read",
        [
            ins(Op.READ_APPEND, imm1=data, imm2=Typecode.INT16),
            ins(Op.READ_APPEND, imm1=data, imm2=Typecode.INT16),
            ins(Op.BUF_LAST, rd=0, imm1=data),
            ins(Op.BUF_APPEND, imm1=last, ra=0),
            ins(Op.BUF_LEN, rd=0, imm1=data),
            ins(Op.BUF_APPEND, imm1=length, ra=0),
            ins(Op.BUF_APPEND_CONST, imm1=offsets, imm2=5),
            ins(Op.CONST, rd=0, imm1=3),
            ins(Op.BUF_APPEND_OFFSET, imm1=offsets, ra=0),
            ins(Op.BUF_APPEND_LEN, imm1=length, imm2=data),
            ins(Op.BUF_RESERVE, imm1=data, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    raw = struct.pack(">hh", 7, -8)

    outputs, _, _ = run(machine_factory, builder, raw)

    assert outputs["data"].tolist() == [7, -8]
    assert outputs["last"].tolist() == [-8]
    assert outputs["length"].tolist() == [2, 2]
    assert outputs["offsets"].tolist() == [0, 5, 8]


def test_read_fields(machine_factory):
    builder = Builder()
    ints = builder.buffer("ints", "int32")
    shorts = builder.buffer("shorts", "int16")
    floats = builder.buffer("floats", "float64")
    layout = builder.layout(
        [
            builder.field(FieldKind.SCALAR, Typecode.INT32, buffer_id=ints),
            builder.field(FieldKind.SKIP, count=3),
            builder.field(FieldKind.ARRAY_FIXED, Typecode.INT16, count=2, buffer_id=shorts),
            builder.field(FieldKind.SCALAR, Typecode.FLOAT64, buffer_id=floats),
        ]
    )
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.READ_FIELDS, imm1=layout, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    record = struct.pack(">i", 10) + b"pad" + struct.pack(">hh", 1, 2) + struct.pack(">d", 1.5)
    record2 = (
        struct.pack(">i", 20) + b"pad" + struct.pack(">hh", 3, 4) + struct.pack(">d", 2.5)
    )

    outputs, _, _ = run(machine_factory, builder, record + record2)

    assert outputs["ints"].tolist() == [10, 20]
    assert outputs["shorts"].tolist() == [1, 2, 3, 4]
    assert outputs["floats"].tolist() == [1.5, 2.5]


# ---------------------------------------------------------------------------
# States and references


def test_states(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "uint64")
    counter = builder.state("counter", 10)
    builder.function(
        "read",
        [
            ins(Op.STATE_GET, rd=0, imm1=counter),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.CONST, rd=0, imm1=5),
            ins(Op.STATE_SET, imm1=counter, ra=0),
            ins(Op.STATE_IADD, imm1=counter, imm2=-2),
            ins(Op.STATE_GET, rd=0, imm1=counter),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )

    outputs, states, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [10, 3]
    assert states["counter"] == 3


def test_references(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=5),
            ins(Op.REF_HAS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.CONST, rd=1, imm1=7),
            ins(Op.REF_SET_OBJ, ra=0, rb=1),
            ins(Op.REF_GET_OBJ, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.REF_GET_CLASS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.CONST, rd=1, imm1=3),
            ins(Op.REF_SET_CLASS, ra=0, rb=1),
            ins(Op.REF_GET_OBJ, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.REF_GET_CLASS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.REF_COUNT, rd=1),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.CONST, rd=0, imm1=6),
            ins(Op.REF_GET_OBJ, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.REF_GET_CLASS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )

    outputs, _, _ = run(machine_factory, builder, b"")

    assert outputs["out"].tolist() == [0, 7, -1, -1, 3, 1, 0, -1]


# ---------------------------------------------------------------------------
# Verification opcodes


def test_asserts_and_throw(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int64")
    ok = builder.string("ok")
    bad = builder.string("bad")
    left = builder.string("left")
    right = builder.string("right")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.ASSERT, ra=0, imm1=ok),
            ins(Op.SKIP, imm1=2),
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.ASSERT_CURSOR_EQ, ra=0, imm1=ok),
            ins(Op.STR_CONST, rd=0, imm1=left),
            ins(Op.STR_CONST, rd=1, imm1=left),
            ins(Op.ASSERT_STR_EQ, ra=0, rb=1, imm1=ok),
            ins(Op.STR_CONST, rd=1, imm1=right),
            ins(Op.STR_EQ, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.THROW, imm1=bad),
        ],
        reg_count=3,
    )
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(AssertionFailed) as info:
        machine.run(b"ab", [0, 2])

    assert info.value.message == "bad"


def test_assert_failure(machine_factory):
    builder = Builder()
    message = builder.string("must be true")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=0),
            ins(Op.ASSERT, ra=0, imm1=message),
            ins(Op.RET, ra=0),
        ],
    )
    machine = machine_factory(builder.build().to_bytes())

    with pytest.raises(AssertionFailed) as info:
        machine.run(b"", [0, 0])

    assert info.value.message == "must be true"
    assert info.value.function == "read"


# ---------------------------------------------------------------------------
# Output lifetime and repeated runs


def test_outputs_survive_the_next_run(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    machine = machine_factory(builder.build().to_bytes())

    first = machine.run(struct.pack(">i", 1), [0, 4])["out"]
    second = machine.run(struct.pack(">i", 2), [0, 4])["out"]

    assert first.tolist() == [1]
    assert second.tolist() == [2]


def test_multiple_events(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    total = builder.state("total", 0)
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.STATE_IADD, imm1=total, imm2=1),
            ins(Op.RET, ra=0),
        ],
    )
    data = struct.pack(">iii", 1, 2, 3)

    outputs, states, _ = run(machine_factory, builder, data, offsets=[0, 4, 8, 12])

    assert outputs["out"].tolist() == [1, 2, 3]
    assert states["total"] == 3
