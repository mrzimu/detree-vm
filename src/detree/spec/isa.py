"""
The Detree instruction set and module-format constants.

This module is the code form of `spec/isa.toml` and `docs/spec/isa.md`. It is
pure data: no NumPy, no external library, no machine. A machine in another language
implements the same numbers.

The opcode values are part of the wire format. `1.0` freezes them; until then
a change is an `ISA_MAJOR` bump.
"""

from __future__ import annotations

import struct
from enum import IntEnum, unique

# Incompatible changes bump the major version.
ISA_MAJOR = 0

# Additive changes bump the minor version.
ISA_MINOR = 1

# The first four bytes of every module.
MAGIC = b"DETR"

# Every instruction is 20 bytes: opcode, three register bytes, two i64.
INS_SIZE = 20


@unique
class Op(IntEnum):
    """
    Opcode numbers, exactly as published in `docs/spec/isa.md`.

    The numbers are wire format: changing one is an ISA version change.
    """

    # control
    HALT = 0x00
    NOP = 0x01
    LINE = 0x02
    DEBUG_PRINT = 0x03
    CONST = 0x04
    CONST_F = 0x05
    MOV = 0x06
    ZEXT32 = 0x07
    ZEXT16 = 0x08
    ZEXT8 = 0x09
    SEXT32 = 0x0A
    SEXT16 = 0x0B
    SEXT8 = 0x0C

    # arithmetic
    ADD = 0x10
    SUB = 0x11
    MUL = 0x12
    DIV_S = 0x13
    DIV_U = 0x14
    MOD_S = 0x15
    MOD_U = 0x16
    NEG = 0x17
    SHL = 0x18
    SHR_S = 0x19
    SHR_U = 0x1A
    AND = 0x1B
    OR = 0x1C
    XOR = 0x1D
    NOT = 0x1E
    NOT_B = 0x1F

    # compare
    CMP_EQ = 0x20
    CMP_NE = 0x21
    CMP_LT_S = 0x22
    CMP_LE_S = 0x23
    CMP_GT_S = 0x24
    CMP_GE_S = 0x25
    CMP_LT_U = 0x26
    CMP_LE_U = 0x27
    CMP_GT_U = 0x28
    CMP_GE_U = 0x29

    # flow
    JMP = 0x30
    JZ = 0x31
    JNZ = 0x32
    BR_EQ = 0x33
    BR_NE = 0x34
    BR_LT_S = 0x35
    BR_LE_S = 0x36
    BR_GT_S = 0x37
    BR_GE_S = 0x38
    BR_LT_U = 0x39
    BR_LE_U = 0x3A
    BR_GT_U = 0x3B
    BR_GE_U = 0x3C
    CALL = 0x3D
    RET = 0x3E

    # stream
    SREAD = 0x40
    SREAD_FNB = 0x41
    SREAD_VERSION = 0x42
    SREAD_CSTR = 0x43
    SKIP = 0x44
    SKIP_R = 0x45
    SKIP_FNB = 0x46
    SKIP_VERSION = 0x47
    SKIP_TOBJECT = 0x48
    SKIP_OBJHDR = 0x49
    SKIP_CSTR = 0x4A
    CURSOR = 0x4B
    SET_CURSOR = 0x4C
    ENTRIES = 0x4D
    INIT_OFFSET = 0x4E
    DATA_SIZE = 0x4F

    # offsets
    OFFSET_AT = 0x60
    OFFSET_SEEK = 0x61
    EVENT_END = 0x62

    # bulk
    READ_BULK = 0x70
    READ_STRIDED = 0x71
    READ_FIELDS = 0x72
    READ_APPEND = 0x73
    READ_BULK_UNTIL = 0x74
    READ_STRING = 0x75

    # buffer
    BUF_APPEND = 0x80
    BUF_APPEND_CONST = 0x81
    BUF_APPEND_OFFSET = 0x82
    BUF_APPEND_LEN = 0x83
    BUF_LAST = 0x84
    BUF_LEN = 0x85
    BUF_RESERVE = 0x86

    # state
    STATE_GET = 0x90
    STATE_SET = 0x91
    STATE_IADD = 0x92

    # ref
    REF_HAS = 0xA0
    REF_GET_OBJ = 0xA1
    REF_GET_CLASS = 0xA2
    REF_SET_OBJ = 0xA3
    REF_SET_CLASS = 0xA4
    REF_COUNT = 0xA5

    # string
    STR_CONST = 0xB0
    STR_EQ = 0xB1
    STR_STORE = 0xB2
    STR_LOAD = 0xB3

    # verify
    ASSERT = 0xC0
    ASSERT_CURSOR_EQ = 0xC1
    ASSERT_STR_EQ = 0xC2
    THROW = 0xC3


@unique
class BufferKind(IntEnum):
    """
    Storage class of a buffer.

    `DATA` buffers are appended to; `OFFSETS` buffers record event boundaries.
    """

    DATA = 0
    OFFSETS = 1
    COUNT = 2


@unique
class FieldKind(IntEnum):
    """
    Field kinds of a flat `READ_FIELDS` layout.

    A field is a scalar value, a skip, or a fixed array.
    """

    SCALAR = 0
    SKIP = 1
    ARRAY_FIXED = 2
    TOBJECT_SKIP = 3


@unique
class Typecode(IntEnum):
    """
    Scalar types a stream read can produce.

    The typecode decides how many bytes one value consumes.
    """

    UINT8 = 1
    UINT16 = 2
    UINT32 = 3
    UINT64 = 4
    INT8 = 5
    INT16 = 6
    INT32 = 7
    INT64 = 8
    FLOAT32 = 9
    FLOAT64 = 10


# `typecode -> (big-endian struct format, big-endian numpy dtype, nbytes)`.
STREAM_FORMAT = {
    Typecode.UINT8: (">B", ">u1", 1),
    Typecode.UINT16: (">H", ">u2", 2),
    Typecode.UINT32: (">I", ">u4", 4),
    Typecode.UINT64: (">Q", ">u8", 8),
    Typecode.INT8: (">b", ">i1", 1),
    Typecode.INT16: (">h", ">i2", 2),
    Typecode.INT32: (">i", ">i4", 4),
    Typecode.INT64: (">q", ">i8", 8),
    Typecode.FLOAT32: (">f", ">f4", 4),
    Typecode.FLOAT64: (">d", ">f8", 8),
}

# Typecodes that carry an IEEE-754 bit pattern in the register.
FLOAT_TYPECODES = frozenset({Typecode.FLOAT32, Typecode.FLOAT64})

# `dtype name -> typecode` used to read one value of that dtype.
DTYPE_TO_TYPECODE = {
    "uint8": Typecode.UINT8,
    "uint16": Typecode.UINT16,
    "uint32": Typecode.UINT32,
    "uint64": Typecode.UINT64,
    "int8": Typecode.INT8,
    "int16": Typecode.INT16,
    "int32": Typecode.INT32,
    "int64": Typecode.INT64,
    "float32": Typecode.FLOAT32,
    "float64": Typecode.FLOAT64,
    "bool": Typecode.UINT8,
}

# Every dtype a buffer may be declared with.
BUFFER_DTYPES = frozenset(DTYPE_TO_TYPECODE)

# Instructions whose `imm1` is an absolute instruction index.
JUMP_OPS = frozenset(
    {
        Op.JMP,
        Op.JZ,
        Op.JNZ,
        Op.BR_EQ,
        Op.BR_NE,
        Op.BR_LT_S,
        Op.BR_LE_S,
        Op.BR_GT_S,
        Op.BR_GE_S,
        Op.BR_LT_U,
        Op.BR_LE_U,
        Op.BR_GT_U,
        Op.BR_GE_U,
    }
)

# Instructions whose `imm1` names a buffer.
BUFFER_OPS = frozenset(
    {
        Op.BUF_APPEND,
        Op.BUF_APPEND_CONST,
        Op.BUF_APPEND_OFFSET,
        Op.BUF_LAST,
        Op.BUF_LEN,
        Op.BUF_RESERVE,
        Op.READ_APPEND,
    }
)

# Instructions whose `imm1` names a state slot.
STATE_OPS = frozenset(
    {
        Op.STATE_GET,
        Op.STATE_SET,
        Op.STATE_IADD,
        Op.STR_STORE,
        Op.STR_LOAD,
    }
)

# Instructions whose `imm1` names a message string.
MESSAGE_OPS = frozenset(
    {
        Op.ASSERT,
        Op.ASSERT_CURSOR_EQ,
        Op.ASSERT_STR_EQ,
        Op.THROW,
        Op.DEBUG_PRINT,
    }
)

# Instructions that read one scalar from the stream.
SCALAR_READ_OPS = frozenset({Op.SREAD, Op.READ_APPEND})

# Instructions that end a basic block.
TERMINATORS = frozenset(
    {
        Op.HALT,
        Op.JMP,
        Op.JZ,
        Op.JNZ,
        Op.BR_EQ,
        Op.BR_NE,
        Op.BR_LT_S,
        Op.BR_LE_S,
        Op.BR_GT_S,
        Op.BR_GE_S,
        Op.BR_LT_U,
        Op.BR_LE_U,
        Op.BR_GT_U,
        Op.BR_GE_U,
        Op.CALL,
        Op.RET,
        Op.THROW,
    }
)


def struct_format(typecode: Typecode | int) -> str:
    """
    The big-endian `struct` format of *typecode*.

    Args:
        typecode: a `Typecode` or its integer value.

    Returns:
        The format string `struct` expects, such as `">i"`.
    """
    return STREAM_FORMAT[Typecode(typecode)][0]


def numpy_stream_dtype(typecode: Typecode | int) -> str:
    """
    The big-endian NumPy dtype string of *typecode*.

    Args:
        typecode: a `Typecode` or its integer value.

    Returns:
        The dtype string NumPy accepts, such as `">i4"`.
    """
    return STREAM_FORMAT[Typecode(typecode)][1]


def typecode_size(typecode: Typecode | int) -> int:
    """
    The width in bytes of *typecode*.

    Args:
        typecode: a `Typecode` or its integer value.

    Returns:
        The number of bytes one value occupies.
    """
    return STREAM_FORMAT[Typecode(typecode)][2]


def pack_ins(
    op: Op | int,
    rd: int = 0,
    ra: int = 0,
    rb: int = 0,
    imm1: int = 0,
    imm2: int = 0,
) -> bytes:
    """
    Encode one instruction in its fixed 20-byte form.

    Args:
        op: the opcode.
        rd: the destination register byte.
        ra: the first source register byte.
        rb: the second source register byte.
        imm1: the first immediate; its meaning depends on *op*.
        imm2: the second immediate.

    Returns:
        The 20 encoded bytes, little-endian like the rest of the header.
    """
    return struct.pack("<BBBBqq", int(op), rd, ra, rb, int(imm1), int(imm2))


def unpack_ins(raw: bytes) -> tuple[Op, int, int, int, int, int]:
    """
    Decode one 20-byte instruction.

    Args:
        raw: exactly 20 bytes produced by `pack_ins`.

    Returns:
        `(op, rd, ra, rb, imm1, imm2)` as plain integers.
    """
    return struct.unpack("<BBBBqq", raw)


__all__ = [
    "BUFFER_DTYPES",
    "BUFFER_OPS",
    "BufferKind",
    "DTYPE_TO_TYPECODE",
    "FLOAT_TYPECODES",
    "FieldKind",
    "INS_SIZE",
    "ISA_MAJOR",
    "ISA_MINOR",
    "JUMP_OPS",
    "MAGIC",
    "MESSAGE_OPS",
    "Op",
    "SCALAR_READ_OPS",
    "STREAM_FORMAT",
    "STATE_OPS",
    "TERMINATORS",
    "Typecode",
    "numpy_stream_dtype",
    "pack_ins",
    "struct_format",
    "typecode_size",
    "unpack_ins",
]
