"""
Canonical module encoder and decoder.

The format is defined in `docs/spec/module.md`. `to_bytes` and `from_bytes` are
exact inverses, and `to_bytes` is deterministic: the same program produces the
same bytes in every process. `content_hash` is the SHA-256 of those bytes.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from enum import Enum

from .errors import ModuleError
from .isa import (
    INS_SIZE,
    ISA_MAJOR,
    ISA_MINOR,
    MAGIC,
    BufferKind,
    FieldKind,
    Op,
    Typecode,
    pack_ins,
    unpack_ins,
)

# `StateDecl` flags, one byte in the wire format.
STATE_NAME = 0x01


@dataclass(frozen=True)
class Ins:
    """
    One instruction in its executable form.

    Operands are already resolved to register numbers and table indices;
    `encode` produces the wire bytes.
    """

    op: Op
    rd: int = 0
    ra: int = 0
    rb: int = 0
    imm1: int = 0
    imm2: int = 0

    def encode(self) -> bytes:
        """
        Encode this instruction into its 20-byte form.

        Returns:
            The 20 wire bytes of `docs/spec/module.md`.
        """
        return pack_ins(self.op, self.rd, self.ra, self.rb, self.imm1, self.imm2)


@dataclass(frozen=True)
class BufferDecl:
    """
    A buffer the module fills.

    *init* is the declared initial content, restored at every reset.
    """

    name: str
    dtype: str
    kind: BufferKind = BufferKind.DATA
    init: tuple = ()


@dataclass(frozen=True)
class StateDecl:
    """
    A scalar that survives across events.

    *is_string* marks a name slot, resolved through the string arena.
    """

    name: str
    init: int = 0
    is_string: bool = False


@dataclass(frozen=True)
class LayoutField:
    """
    One field of a flat `READ_FIELDS` layout.

    *count* is 1 for a scalar, and *buffer_id* is set only for a fixed array.
    """

    kind: FieldKind
    typecode: Typecode = Typecode.UINT8
    count: int = 1
    buffer_id: int = -1


@dataclass(frozen=True)
class Layout:
    """
    A flat sequence of scalar, skip, and fixed-array fields.

    Field order is the order they appear in the stream.
    """

    id: int
    fields: tuple = ()


@dataclass(frozen=True)
class FuncDecl:
    """
    The header of one function; its code lives in `Module.code`.

    `code_off` and `code_len` index that shared instruction list.
    """

    id: int
    name: str
    arity: int
    reg_count: int
    code_off: int
    code_len: int

    @property
    def end(self) -> int:
        """
        The instruction index one past the function's last instruction.

        Returns:
            `code_off + code_len`.
        """
        return self.code_off + self.code_len


@dataclass
class Module:
    """
    A complete, self-describing unit of bytecode.

    Everything a machine needs is here: code, declarations, constants, and the
    ISA version the bytes were produced for.
    """

    entry: int = 0
    regs_max: int = 0
    buffers: list = field(default_factory=list)
    states: list = field(default_factory=list)
    strings: list = field(default_factory=list)
    layouts: list = field(default_factory=list)
    funcs: list = field(default_factory=list)
    code: list = field(default_factory=list)
    isa_major: int = ISA_MAJOR
    isa_minor: int = ISA_MINOR
    magic: bytes = MAGIC

    # -- introspection ----------------------------------------------------
    def buffer_names(self) -> list[str]:
        """
        The declared buffer names.

        Returns:
            One name per buffer, in declaration order.
        """
        return [buffer.name for buffer in self.buffers]

    def func_by_name(self, name: str) -> FuncDecl:
        """
        The function declaration called *name*.

        Args:
            name: the function name, as declared.

        Returns:
            The matching declaration.

        Raises:
            KeyError: no function has that name.
        """
        for func in self.funcs:
            if func.name == name:
                return func
        raise KeyError(name)

    # -- serialization ----------------------------------------------------
    def to_bytes(self) -> bytes:
        """
        Canonical byte encoding, deterministic for a given module.

        Returns:
            The module bytes of `docs/spec/module.md`.
        """
        out = bytearray()
        out += self.magic
        out += struct.pack("<HHHH", self.isa_major, self.isa_minor, self.entry, self.regs_max)
        out += struct.pack(
            "<IIIII",
            len(self.buffers),
            len(self.states),
            len(self.strings),
            len(self.layouts),
            len(self.funcs),
        )

        for buffer in self.buffers:
            out += _pack_text(buffer.name)
            out += _pack_text(buffer.dtype)
            out += struct.pack("<B", int(buffer.kind))
            out += struct.pack("<I", len(buffer.init))
            for value in buffer.init:
                out += struct.pack("<q", int(value))

        for state in self.states:
            out += _pack_text(state.name)
            out += struct.pack("<qB", int(state.init), _state_flags(state))

        for text in self.strings:
            out += _pack_text(text)

        for layout in self.layouts:
            out += struct.pack("<II", layout.id, len(layout.fields))
            for layout_field in layout.fields:
                out += struct.pack(
                    "<BBii",
                    int(layout_field.kind),
                    int(layout_field.typecode),
                    layout_field.count,
                    layout_field.buffer_id,
                )

        for func in self.funcs:
            out += struct.pack("<I", func.id)
            out += _pack_text(func.name)
            out += struct.pack(
                "<BHHII", func.arity, func.reg_count, 0, func.code_off, func.code_len
            )

        out += struct.pack("<I", len(self.code))
        for ins in self.code:
            out += ins.encode()

        return bytes(out)

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> Module:
        """
        Decode what `to_bytes` produced.

        Args:
            raw: module bytes; a `Module` must be encoded with `to_bytes`
                first.

        Returns:
            The decoded module. Validation is a separate step.

        Raises:
            TypeError: *raw* is not a bytes-like object.
            ModuleError: the bytes do not decode.
        """
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError(
                "a module crosses the boundary as bytes, not "
                f"{type(raw).__name__}; call to_bytes() first"
            )
        decoder = _Decoder(bytes(raw))

        magic = decoder.take(4)
        isa_major, isa_minor, entry, regs_max = decoder.unpack("<HHHH")
        n_buffers, n_states, n_strings, n_layouts, n_funcs = decoder.unpack("<IIIII")

        _check_count(n_buffers, 7, decoder.remaining(), "buffers")
        _check_count(n_states, 11, decoder.remaining(), "states")
        _check_count(n_strings, 2, decoder.remaining(), "strings")
        _check_count(n_layouts, 4, decoder.remaining(), "layouts")
        _check_count(n_funcs, 15, decoder.remaining(), "functions")

        buffers = []
        for _ in range(n_buffers):
            name = decoder.text()
            dtype = decoder.text()
            (kind,) = decoder.unpack("<B")
            (n_init,) = decoder.unpack("<I")
            _check_count(n_init, 8, decoder.remaining(), "buffer init values")
            init = decoder.unpack(f"<{n_init}q") if n_init else ()
            buffers.append(
                BufferDecl(
                    name=name,
                    dtype=dtype,
                    kind=_decode_enum(BufferKind, kind, f"buffer {name!r} kind"),
                    init=tuple(init),
                )
            )

        states = []
        for _ in range(n_states):
            name = decoder.text()
            init, flags = decoder.unpack("<qB")
            if flags & ~STATE_NAME:
                raise ModuleError(f"state {name!r}: unknown flags {flags:#x}")
            states.append(StateDecl(name=name, init=init, is_string=bool(flags & STATE_NAME)))

        strings = [decoder.text() for _ in range(n_strings)]

        layouts = []
        for _ in range(n_layouts):
            layout_id, n_fields = decoder.unpack("<II")
            _check_count(n_fields, 10, decoder.remaining(), "layout fields")
            fields = []
            for _ in range(n_fields):
                kind, typecode, count, buffer_id = decoder.unpack("<BBii")
                fields.append(
                    LayoutField(
                        kind=_decode_enum(FieldKind, kind, f"layout {layout_id} field kind"),
                        typecode=_decode_enum(
                            Typecode, typecode, f"layout {layout_id} typecode"
                        ),
                        count=count,
                        buffer_id=buffer_id,
                    )
                )
            layouts.append(Layout(id=layout_id, fields=tuple(fields)))

        funcs = []
        for _ in range(n_funcs):
            (func_id,) = decoder.unpack("<I")
            name = decoder.text()
            arity, reg_count, _reserved, code_off, code_len = decoder.unpack("<BHHII")
            funcs.append(
                FuncDecl(
                    id=func_id,
                    name=name,
                    arity=arity,
                    reg_count=reg_count,
                    code_off=code_off,
                    code_len=code_len,
                )
            )

        (n_code,) = decoder.unpack("<I")
        _check_count(n_code, INS_SIZE, decoder.remaining(), "instructions")
        code = []
        for index in range(n_code):
            op, rd, ra, rb, imm1, imm2 = unpack_ins(decoder.take(INS_SIZE))
            code.append(
                Ins(
                    op=_decode_enum(Op, op, f"instruction {index} opcode"),
                    rd=rd,
                    ra=ra,
                    rb=rb,
                    imm1=imm1,
                    imm2=imm2,
                )
            )

        if not decoder.at_end():
            raise ModuleError(f"{decoder.remaining()} trailing bytes after the module")

        return cls(
            entry=entry,
            regs_max=regs_max,
            buffers=buffers,
            states=states,
            strings=strings,
            layouts=layouts,
            funcs=funcs,
            code=code,
            isa_major=isa_major,
            isa_minor=isa_minor,
            magic=magic,
        )

    def content_hash(self) -> str:
        """
        SHA-256 of `to_bytes`; the cache key and the dedup key.

        Returns:
            The lowercase hexadecimal digest.
        """
        return hashlib.sha256(self.to_bytes()).hexdigest()


def _state_flags(state: StateDecl) -> int:
    """
    The one-byte flag field of a state declaration.

    Args:
        state: the declaration to encode.

    Returns:
        `STATE_NAME` for a name slot, `0` otherwise.
    """
    return STATE_NAME if state.is_string else 0


class _Decoder:
    """
    A cursor over serialized module bytes; the inverse of `to_bytes`.

    Every read either advances or raises `ModuleError`, so a truncated module
    never yields a partially decoded value.
    """

    __slots__ = ("pos", "raw")

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.pos = 0

    def take(self, size: int) -> bytes:
        """
        Consume *size* bytes.

        Args:
            size: how many bytes to read.

        Returns:
            The consumed slice.

        Raises:
            ModuleError: fewer than *size* bytes remain.
        """
        end = self.pos + size
        if end > len(self.raw):
            raise ModuleError(
                f"module is truncated: wanted {size} bytes at {self.pos}, "
                f"only {len(self.raw) - self.pos} left"
            )
        chunk = self.raw[self.pos : end]
        self.pos = end
        return chunk

    def unpack(self, fmt: str) -> tuple:
        """
        Read one `struct`-formatted value.

        Args:
            fmt: a `struct` format, without a byte-order prefix.

        Returns:
            The unpacked fields.
        """
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))

    def text(self) -> str:
        """
        Read one length-prefixed UTF-8 string.

        Returns:
            The decoded text.

        Raises:
            ModuleError: the bytes are not valid UTF-8.
        """
        (size,) = self.unpack("<H")
        try:
            return self.take(size).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ModuleError(f"module text is not valid UTF-8: {error}") from None

    def at_end(self) -> bool:
        """
        Whether every byte has been consumed.

        Returns:
            `True` when the cursor sits at the end of the input.
        """
        return self.pos == len(self.raw)

    def remaining(self) -> int:
        """
        How many bytes are left.

        Returns:
            The number of unread bytes.
        """
        return len(self.raw) - self.pos


def _check_count(count: int, minimum_each: int, remaining: int, what: str) -> None:
    """
    Refuse a count the remaining bytes cannot possibly satisfy.

    Args:
        count: the declared item count.
        minimum_each: the smallest encoded size of one item.
        remaining: the bytes left in the module.
        what: the item kind, used in the message.

    Raises:
        ModuleError: *count* cannot fit in *remaining* bytes.
    """
    if count > remaining // minimum_each:
        raise ModuleError(
            f"module declares {count} {what}, which does not fit in {remaining} remaining bytes"
        )


def _decode_enum(enum_type: type[Enum], value: int, what: str) -> Enum:
    """
    Decode one wire enum value, reporting a malformed module rather than ValueError.

    Args:
        enum_type: the `IntEnum` to decode into.
        value: the wire value.
        what: the field name, used in the message.

    Returns:
        The enum member.

    Raises:
        ModuleError: *value* is not a member of *enum_type*.
    """
    try:
        return enum_type(value)
    except ValueError:
        raise ModuleError(f"{what}: unknown value {value}") from None


def _pack_text(text: str) -> bytes:
    """
    Encode one length-prefixed UTF-8 string.

    Args:
        text: the string to encode.

    Returns:
        A two-byte length followed by the UTF-8 bytes.

    Raises:
        ModuleError: the encoded text does not fit in 16 bits.
    """
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise ModuleError(f"string too long for the module format: {len(raw)} bytes")
    return struct.pack("<H", len(raw)) + raw


__all__ = [
    "BufferDecl",
    "FuncDecl",
    "Ins",
    "Layout",
    "LayoutField",
    "Module",
    "StateDecl",
]
