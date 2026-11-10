"""
Case definitions for the golden corpus.

Each case is one module, one basket, and the entry offsets. The corpus writer
runs them through the Python reference and records the observable result; the
replay test compares every backend against those bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from module_builder import Builder, ins

from detree.spec.isa import BufferKind, FieldKind, Op, Typecode
from detree.spec.module import Module


@dataclass(frozen=True)
class Case:
    name: str
    module: Module
    data: bytes
    offsets: tuple[int, ...]
    cursor_offset: int = 0


def _one(name, builder, data, offsets=None, cursor_offset=0):
    if offsets is None:
        offsets = (0, len(data))
    return Case(name, builder.build(), data, tuple(offsets), cursor_offset)


def scalars() -> Case:
    builder = Builder()
    types = [
        ("u8", "uint8", Typecode.UINT8, ">B", 250),
        ("u16", "uint16", Typecode.UINT16, ">H", 60000),
        ("u32", "uint32", Typecode.UINT32, ">I", 4_000_000_000),
        ("u64", "uint64", Typecode.UINT64, ">Q", 2**63 + 5),
        ("i8", "int8", Typecode.INT8, ">b", -120),
        ("i16", "int16", Typecode.INT16, ">h", -30000),
        ("i32", "int32", Typecode.INT32, ">i", -2_000_000_000),
        ("i64", "int64", Typecode.INT64, ">q", -(2**60)),
        ("f32", "float32", Typecode.FLOAT32, ">f", 1.5),
        ("f64", "float64", Typecode.FLOAT64, ">d", -2.25),
    ]
    code = []
    data = b""
    for name, dtype, typecode, fmt, value in types:
        buffer_id = builder.buffer(name, dtype)
        code.append(ins(Op.SREAD, rd=0, imm1=typecode))
        code.append(ins(Op.BUF_APPEND, imm1=buffer_id, ra=0))
        data += struct.pack(fmt, value)
    code.append(ins(Op.RET, ra=0))
    builder.function("read", code)
    return _one("scalars", builder, data)


def arithmetic() -> Case:
    builder = Builder()
    out = builder.buffer("out", "int64")
    code = [
        ins(Op.CONST, rd=0, imm1=7),
        ins(Op.CONST, rd=1, imm1=3),
    ]
    for op in (
        Op.ADD,
        Op.SUB,
        Op.MUL,
        Op.DIV_S,
        Op.DIV_U,
        Op.MOD_S,
        Op.MOD_U,
        Op.SHL,
        Op.SHR_U,
        Op.AND,
        Op.OR,
        Op.XOR,
    ):
        code.append(ins(op, rd=2, ra=0, rb=1))
        code.append(ins(Op.BUF_APPEND, imm1=out, ra=2))
    code.extend(
        [
            ins(Op.NEG, rd=2, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.NOT, rd=2, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.NOT_B, rd=2, ra=0),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.CONST, rd=0, imm1=-(2**63)),
            ins(Op.CONST, rd=1, imm1=-1),
            ins(Op.DIV_S, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.MOD_S, rd=2, ra=0, rb=1),
            ins(Op.BUF_APPEND, imm1=out, ra=2),
            ins(Op.RET, ra=0),
        ]
    )
    builder.function("read", code, reg_count=3)
    return _one("arithmetic", builder, b"")


def comparisons() -> Case:
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
    return _one("comparisons", builder, b"")


def control() -> Case:
    builder = Builder()
    out = builder.buffer("out", "int64")
    builder.function(
        "main",
        [
            ins(Op.CONST, rd=0, imm1=4),
            ins(Op.CONST, rd=1, imm1=4),
            ins(Op.BR_EQ, ra=0, rb=1, imm1=5),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=0),
            ins(Op.JMP, imm1=6),
            ins(Op.BUF_APPEND_CONST, imm1=out, imm2=1),
            ins(Op.CONST, rd=0, imm1=21),
            ins(Op.CALL, rd=0, ra=0, imm1=1),
            ins(Op.BUF_APPEND, imm1=out, ra=0),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    builder.function(
        "double",
        [
            ins(Op.ADD, rd=0, ra=0, rb=0),
            ins(Op.RET, ra=0),
        ],
        arity=1,
        reg_count=1,
    )
    return _one("control", builder, b"")


def streams() -> Case:
    builder = Builder()
    out = builder.buffer("out", "int64")
    state = builder.state("name", init=-1, is_string=True)
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
    return _one("streams", builder, data)


def skips() -> Case:
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
            ins(Op.SKIP_TOBJECT),
            ins(Op.SKIP_OBJHDR),
            ins(Op.CURSOR, rd=1),
            ins(Op.BUF_APPEND, imm1=out, ra=1),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    data = (
        b"abc"
        + b"de"
        + b"\x00\x00"
        + b"x\x00"
        + b"\x00" * 6
        + struct.pack(">I", 0x10)
        + b"\x00" * 2
        + struct.pack(">I", 0x40000000)
        + struct.pack(">I", 0xFFFFFFFF)
        + b"cls\x00"
    )
    return _one("skips", builder, data)


def offsets() -> Case:
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
    return _one(
        "offsets",
        builder,
        b"\x00" * 8,
        offsets=(0, 4, 8),
        cursor_offset=16,
    )


def bulk() -> Case:
    builder = Builder()
    ints = builder.buffer("ints", "int32")
    floats = builder.buffer("floats", "float32")
    shorts = builder.buffer("shorts", "int16")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=3),
            ins(Op.READ_BULK, imm1=ints, ra=0, imm2=Typecode.INT32),
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.CONST, rd=1, imm1=8),
            ins(Op.READ_STRIDED, imm1=floats, ra=0, rb=1, imm2=Typecode.FLOAT32),
            ins(Op.READ_APPEND, imm1=shorts, imm2=Typecode.INT16),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    data = (
        struct.pack(">iii", 1, -2, 3)
        + struct.pack(">f", 1.5)
        + b"pad!"
        + struct.pack(">f", -2.5)
        + struct.pack(">h", 7)
    )
    return _one("bulk", builder, data)


def bulk_until() -> Case:
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
    return _one("bulk_until", builder, struct.pack(">iii", 7, -8, 9))


def strings() -> Case:
    """
    A length-prefixed string in each of the two offset forms.
    """
    builder = Builder()
    offsets = builder.buffer("offsets", "int64", BufferKind.OFFSETS, init=(0,))
    chars = builder.buffer("chars", "uint8")
    builder.function(
        "read",
        [
            ins(Op.READ_STRING, rb=0, imm1=chars, imm2=offsets),
            ins(Op.READ_STRING, rb=1, imm1=chars, imm2=offsets),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )
    data = b"\x02hi" + b"\xff" + struct.pack(">I", 3) + b"abc"
    return _one("strings", builder, data)


def fields() -> Case:
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
    return _one("fields", builder, record + record2)


def buffers_states_refs() -> Case:
    builder = Builder()
    data = builder.buffer("data", "int16")
    last = builder.buffer("last", "int64")
    length = builder.buffer("length", "int64")
    offsets = builder.buffer("offsets", "int64", init=(0,))
    counter = builder.state("counter", 10)
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
            ins(Op.STATE_GET, rd=0, imm1=counter),
            ins(Op.BUF_APPEND, imm1=length, ra=0),
            ins(Op.CONST, rd=0, imm1=5),
            ins(Op.STATE_SET, imm1=counter, ra=0),
            ins(Op.STATE_IADD, imm1=counter, imm2=-2),
            ins(Op.CONST, rd=0, imm1=5),
            ins(Op.REF_HAS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=last, ra=1),
            ins(Op.CONST, rd=1, imm1=7),
            ins(Op.REF_SET_OBJ, ra=0, rb=1),
            ins(Op.REF_GET_OBJ, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=last, ra=1),
            ins(Op.REF_GET_CLASS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=last, ra=1),
            ins(Op.CONST, rd=1, imm1=3),
            ins(Op.REF_SET_CLASS, ra=0, rb=1),
            ins(Op.REF_GET_CLASS, rd=1, ra=0),
            ins(Op.BUF_APPEND, imm1=last, ra=1),
            ins(Op.REF_COUNT, rd=1),
            ins(Op.BUF_APPEND, imm1=last, ra=1),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    return _one("buffers_states_refs", builder, struct.pack(">hh", 7, -8))


def multi_event() -> Case:
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
    return _one(
        "multi_event",
        builder,
        struct.pack(">iii", 1, 2, 3),
        offsets=(0, 4, 8, 12),
    )


def cross_dtype() -> Case:
    builder = Builder()
    out = builder.buffer("out", "float64")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=2),
            ins(Op.READ_BULK, imm1=out, ra=0, imm2=Typecode.FLOAT32),
            ins(Op.RET, ra=0),
        ],
    )
    return _one("cross_dtype", builder, struct.pack(">ff", 1.5, -2.5))


def underflow() -> Case:
    builder = Builder()
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.UINT32),
            ins(Op.RET, ra=0),
        ],
    )
    return _one("underflow", builder, b"\x00\x00")


def invalid_byte_count() -> Case:
    builder = Builder()
    builder.function(
        "read",
        [
            ins(Op.SREAD_FNB, rd=0),
            ins(Op.RET, ra=0),
        ],
    )
    return _one("invalid_byte_count", builder, struct.pack(">I", 5))


def length_mismatch() -> Case:
    builder = Builder()
    builder.function("read", [ins(Op.CONST, rd=0, imm1=1), ins(Op.RET, ra=0)])
    return _one("length_mismatch", builder, b"", offsets=(0, 1))


def assertion() -> Case:
    builder = Builder()
    message = builder.string("must be true")
    text = builder.string("same")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=0),
            ins(Op.ASSERT_CURSOR_EQ, ra=0, imm1=message),
            ins(Op.STR_CONST, rd=0, imm1=text),
            ins(Op.STR_CONST, rd=1, imm1=text),
            ins(Op.ASSERT_STR_EQ, ra=0, rb=1, imm1=message),
            ins(Op.CONST, rd=0, imm1=0),
            ins(Op.ASSERT, ra=0, imm1=message),
            ins(Op.RET, ra=0),
        ],
        reg_count=2,
    )
    return _one("assertion", builder, b"")


def throw() -> Case:
    builder = Builder()
    message = builder.string("boom")
    builder.function("read", [ins(Op.THROW, imm1=message)])
    return _one("throw", builder, b"")


def stack_overflow() -> Case:
    builder = Builder()
    builder.function("recurse", [ins(Op.CALL, rd=0, ra=0, imm1=0)])
    return _one("stack_overflow", builder, b"")


def variable_length_array() -> Case:
    """
    A jagged array: each event starts with its own element count.
    """
    builder = Builder()
    values = builder.buffer("values", "int32")
    builder.function(
        "read",
        [
            ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
            ins(Op.READ_BULK, imm1=values, ra=0, imm2=Typecode.INT32),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )
    data = (
        struct.pack(">i", 3)
        + struct.pack(">iii", 10, 20, 30)
        + struct.pack(">i", 1)
        + struct.pack(">i", 40)
    )
    return _one("variable_length_array", builder, data, offsets=(0, 16, 24))


def fnbytes_array() -> Case:
    """
    One event shaped like a TTree array branch: version, fNBytes, payload.
    """
    builder = Builder()
    values = builder.buffer("values", "int32")
    builder.function(
        "read",
        [
            ins(Op.SKIP_VERSION),
            ins(Op.SREAD_FNB, rd=0),
            ins(Op.READ_BULK, imm1=values, ra=0, imm2=Typecode.INT32),
            ins(Op.RET, ra=0),
        ],
        reg_count=1,
    )
    data = b"\x00\x00" + struct.pack(">I", 0x40000003) + struct.pack(">iii", 1, -2, 3)
    return _one("fnbytes_array", builder, data)


def _counted_object_case(
    name: str, payload: bytes, inner_count: int, outer_count: int, record: bool
) -> Case:
    """
    A module that opens two nested counted regions around one scalar.

    A case that ends in an error declares no buffer: the corpus records the
    status of a failed run, not partial output.
    """
    builder = Builder()
    values = builder.buffer("values", "int32") if record else None
    inner = builder.string("inner frame")
    outer = builder.string("outer frame")
    code = [
        ins(Op.FNB_BEGIN),
        ins(Op.FNB_BEGIN),
        ins(Op.SREAD, rd=0, imm1=Typecode.INT32),
    ]
    if record:
        code.append(ins(Op.BUF_APPEND, imm1=values, ra=0))
    code += [
        ins(Op.FNB_END, imm1=inner),
        ins(Op.FNB_END, imm1=outer),
        ins(Op.RET, ra=0),
    ]
    builder.function("read", code, reg_count=1)
    data = (
        struct.pack(">I", 0x40000000 | outer_count)
        + struct.pack(">I", 0x40000000 | inner_count)
        + payload
    )
    return _one(name, builder, data)


def byte_count_frames() -> Case:
    """
    Two nested `FNB_BEGIN`/`FNB_END` pairs over one event.
    """
    return _counted_object_case(
        "byte_count_frames", struct.pack(">i", -7), inner_count=4, outer_count=8, record=True
    )


def byte_count_mismatch() -> Case:
    """
    The outer count promises one byte more than the members consume.
    """
    return _counted_object_case(
        "byte_count_mismatch",
        struct.pack(">i", -7),
        inner_count=4,
        outer_count=9,
        record=False,
    )


def byte_count_open_frame() -> Case:
    """
    A function returns with a counted region still open.
    """
    builder = Builder()
    builder.function("read", [ins(Op.FNB_BEGIN), ins(Op.RET, ra=0)], reg_count=1)
    return _one("byte_count_open_frame", builder, struct.pack(">I", 0x40000004))


CASES = (
    scalars,
    arithmetic,
    comparisons,
    control,
    streams,
    skips,
    offsets,
    bulk,
    bulk_until,
    strings,
    fields,
    buffers_states_refs,
    multi_event,
    cross_dtype,
    variable_length_array,
    fnbytes_array,
    byte_count_frames,
    byte_count_mismatch,
    byte_count_open_frame,
    underflow,
    invalid_byte_count,
    length_mismatch,
    assertion,
    throw,
    stack_overflow,
)
