"""
Static validation agrees across the three implementations.
"""

from __future__ import annotations

import pytest
from module_builder import Builder, ins

from detree.spec.errors import ModuleError
from detree.spec.isa import BufferKind, FieldKind, Op, Typecode


def rejects(machine_factory, builder):
    with pytest.raises(ModuleError):
        machine_factory(builder.build().to_bytes())


def test_arity_one_needs_a_register(machine_factory):
    builder = Builder()
    builder.function("call", [ins(Op.RET, ra=0)], arity=1, reg_count=0)
    rejects(machine_factory, builder)


def test_negative_skip(machine_factory):
    builder = Builder()
    builder.function("read", [ins(Op.SKIP, imm1=-1), ins(Op.RET, ra=0)])
    rejects(machine_factory, builder)


def test_jump_outside_the_function(machine_factory):
    builder = Builder()
    builder.function("read", [ins(Op.JMP, imm1=99)])
    rejects(machine_factory, builder)


def test_duplicate_buffer_names(machine_factory):
    builder = Builder()
    builder.buffer("out", "int32")
    builder.buffer("out", "int32")
    builder.function("read", [ins(Op.RET, ra=0)])
    rejects(machine_factory, builder)


def test_offsets_buffer_rejects_a_float_dtype(machine_factory):
    builder = Builder()
    builder.buffer("offsets", "float64", kind=BufferKind.OFFSETS)
    builder.function("read", [ins(Op.RET, ra=0)])
    rejects(machine_factory, builder)


def test_offsets_buffer_rejects_bool(machine_factory):
    builder = Builder()
    builder.buffer("offsets", "bool", kind=BufferKind.OFFSETS)
    builder.function("read", [ins(Op.RET, ra=0)])
    rejects(machine_factory, builder)


def test_tobject_skip_is_reserved(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    layout = builder.layout(
        [builder.field(FieldKind.TOBJECT_SKIP, Typecode.UINT8, buffer_id=out)]
    )
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.READ_FIELDS, imm1=layout, ra=0),
            ins(Op.RET, ra=0),
        ],
    )
    rejects(machine_factory, builder)


def test_unknown_typecode_immediate(machine_factory):
    builder = Builder()
    out = builder.buffer("out", "int32")
    builder.function(
        "read",
        [
            ins(Op.CONST, rd=0, imm1=1),
            ins(Op.READ_BULK, imm1=out, ra=0, imm2=0x101),
            ins(Op.RET, ra=0),
        ],
    )
    rejects(machine_factory, builder)


def test_bad_magic(machine_factory):
    builder = Builder()
    builder.function("read", [ins(Op.RET, ra=0)])
    raw = bytearray(builder.build().to_bytes())
    raw[:4] = b"XXXX"

    with pytest.raises(ModuleError):
        machine_factory(bytes(raw))


def test_truncated_module(machine_factory):
    with pytest.raises(ModuleError):
        machine_factory(b"DETR\x00\x00")
