"""
The pure-Python reference machine (development only).

This is the oracle the shipped Rust machine is compared against. It decodes the
module's bytes on construction, exactly as a foreign implementation would, and
uses the standard library only. NumPy is imported lazily when a buffer is handed
to the caller. No deployment path selects this build by default.
"""

from __future__ import annotations

import array
import struct
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ..spec.errors import (
    AssertionFailed,
    DetreeError,
    InvalidByteCount,
    ReadLengthMismatch,
    StackOverflow,
    StreamUnderflow,
    UnsupportedFeature,
)
from ..spec.isa import (
    FLOAT_TYPECODES,
    STREAM_FORMAT,
    FieldKind,
    Op,
    Typecode,
    typecode_size,
)
from ..spec.module import BufferDecl, Ins, Module
from ..spec.validate import validate_module

if TYPE_CHECKING:  # NumPy stays out of the core import path
    import numpy as np

MASK64 = (1 << 64) - 1
SIGN64 = 1 << 63
MAX_DEPTH = 256

kNewClassTag = 0xFFFFFFFF
kByteCountMask = 0x40000000
kIsReferenced = 1 << 4

# `typecode -> struct format` used to load the register value. Floats are
# loaded as their raw bit pattern of the same width.
REGISTER_FORMAT = {tc: fmt for tc, (fmt, _, _) in STREAM_FORMAT.items()}
REGISTER_FORMAT[Typecode.FLOAT32] = ">I"
REGISTER_FORMAT[Typecode.FLOAT64] = ">Q"

# numpy dtype name -> `array.array` typecode.
_TYPECODES = {
    "bool": "B",
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "uint64": "Q",
    "int64": "q",
    "float32": "f",
    "float64": "d",
}

_ITEMSIZE = {
    "bool": 1,
    "uint8": 1,
    "int8": 1,
    "uint16": 2,
    "int16": 2,
    "uint32": 4,
    "int32": 4,
    "uint64": 8,
    "int64": 8,
    "float32": 4,
    "float64": 8,
}


def to_signed(value: int) -> int:
    """
    The signed value a 64-bit slot holds.

    Args:
        value: the raw slot bits.

    Returns:
        The same bits read as a two's-complement integer.
    """
    value &= MASK64
    return value - (1 << 64) if value & SIGN64 else value


def _trunc_div(a: int, b: int) -> int:
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


def _wrap_int(dtype: str, value: int) -> int:
    bits = _ITEMSIZE[dtype] * 8
    mask = (1 << bits) - 1
    value = int(value) & mask
    if dtype.startswith("int") and value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


def _coerce_register(dtype: str, value: int) -> int | float:
    """
    Store a register value the way a buffer of *dtype* stores it.

    Args:
        dtype: the buffer's dtype name.
        value: the register's 64-bit slot.

    Returns:
        The value as the buffer's storage representation.
    """
    if dtype == "bool":
        return 1 if value else 0
    if dtype.startswith(("uint", "int")):
        return _wrap_int(dtype, value)
    if dtype == "float32":
        if isinstance(value, float):
            return value
        return struct.unpack("<f", struct.pack("<I", int(value) & 0xFFFFFFFF))[0]
    if isinstance(value, float):
        return float(value)
    return struct.unpack("<d", struct.pack("<Q", int(value) & MASK64))[0]


def _coerce_stream(
    dtype: str,
    typecode: Typecode,
    value: int | float,
) -> int | float:
    """
    Store a decoded stream value the way a buffer of *dtype* stores it.

    Args:
        dtype: the buffer's dtype name.
        typecode: the type code the stream value was read with.
        value: the decoded value.

    Returns:
        The value as the buffer's storage representation.
    """
    if dtype == "bool":
        return 1 if value else 0
    if dtype.startswith(("uint", "int")):
        if typecode in FLOAT_TYPECODES:
            value = int(value)  # truncate toward zero
        return _wrap_int(dtype, value)
    return float(value)


class _Buffer:
    """
    A growable buffer of one dtype over a stdlib `array.array`.

    `array.array` keeps the storage contiguous, so `view` can hand out a NumPy
    view without copying.
    """

    __slots__ = ("_dtype", "decl", "dtype", "storage")

    def __init__(self, decl: BufferDecl) -> None:
        self.decl = decl
        self.dtype = decl.dtype
        self._dtype = _TYPECODES[decl.dtype]
        self.storage = array.array(self._dtype)
        for value in decl.init:
            self.append_register(value)

    @property
    def count(self) -> int:
        """
        The number of elements in the buffer.

        Returns:
            The current element count.
        """
        return len(self.storage)

    @property
    def last(self) -> int:
        """
        The last element as an integer slot.

        Returns:
            The last value, or `0` for an empty buffer.
        """
        if not self.storage:
            return 0
        value = self.storage[-1]
        return int(value)

    def append_register(self, value: int | float) -> None:
        """
        Append a register value, coerced to the buffer's dtype.

        Args:
            value: the value to append.
        """
        self.storage.append(_coerce_register(self.dtype, value))

    def append_stream(
        self,
        data: bytes | memoryview,
        offset: int,
        count: int,
        typecode: Typecode,
    ) -> None:
        """
        Append big-endian values decoded from *data*.

        Args:
            data: the basket bytes.
            offset: where the first value starts.
            count: how many values to read.
            typecode: the type code of the stream values.
        """
        if count <= 0:
            return
        fmt = STREAM_FORMAT[typecode][0]
        values = struct.unpack_from(f">{count}{fmt[-1]}", data, offset)
        if self.dtype == "bool":
            self.storage.extend(1 if value else 0 for value in values)
        elif self.dtype.startswith(("uint", "int")):
            if typecode in FLOAT_TYPECODES:
                values = (int(value) for value in values)
            self.storage.extend(_wrap_int(self.dtype, value) for value in values)
        else:
            self.storage.extend(float(value) for value in values)

    def view(self) -> np.ndarray:
        """
        A NumPy view of the buffer's memory.

        The import stays inside the method so the core package never pulls in
        NumPy at import time.

        Returns:
            A read-only array sharing the buffer's storage.
        """
        import numpy as np

        return np.frombuffer(self.storage, dtype=np.dtype(self.dtype))


class PythonMachine:
    """
    A machine for one module, reused basket after basket.

    The reference implementation of the `detree.backends.base.Machine`
    protocol; it is the oracle the compiled machines are compared against.
    """

    backend = "python"
    isa = (0, 2)

    def __init__(self, raw: bytes) -> None:
        self.module = Module.from_bytes(raw)
        validate_module(self.module)
        self._dispatch = _DISPATCH
        self._buffers = []
        self.reset()

    # -- public -----------------------------------------------------------
    def reset(self) -> None:
        """
        Return to the state just after construction: nothing read yet.

        Fresh buffer storage is allocated, so arrays handed out by an earlier
        run stay readable.
        """
        self.data = memoryview(b"")
        self.data_len = 0
        self.offsets = []
        self.cursor = 0
        self.initial_cursor_position = 0
        self.event = 0
        self.entry_end = 0
        self.frames = []
        self.nb_stack = []
        self.regs = []
        self.func = self.module.funcs[self.module.entry]
        self.ip = 0
        self.halted = True

        self._buffers = [_Buffer(decl) for decl in self.module.buffers]
        self.states = [int(state.init) & MASK64 for state in self.module.states]
        self.refs = {}
        self.strings = []
        self._str_ids = {}
        for text in self.module.strings:
            self._intern(text)

    def run(
        self,
        data: bytes | bytearray | memoryview | np.ndarray,
        offsets: Sequence[int],
        cursor_offset: int = 0,
    ) -> Mapping[str, np.ndarray]:
        """
        Read every event of one basket.

        Args:
            data: the decompressed basket body.
            offsets: cumulative entry offsets, at least two.
            cursor_offset: the key length used for object references.

        Returns:
            One NumPy array per declared buffer, keyed by buffer name.
        """
        self._execute(data, offsets, cursor_offset)
        return {
            decl.name: buffer.view()
            for decl, buffer in zip(self.module.buffers, self._buffers)
        }

    def run_bytes(
        self,
        data: bytes | bytearray | memoryview | np.ndarray,
        offsets: Sequence[int],
        cursor_offset: int = 0,
    ) -> dict[str, tuple[str, int, bytes]]:
        """
        Read a basket without importing NumPy.

        Args:
            data: the decompressed basket body.
            offsets: cumulative entry offsets, at least two.
            cursor_offset: the key length used for object references.

        Returns:
            A mapping from buffer name to `(dtype, count, bytes)`; the golden
            corpus writer uses this so it stays a standard-library tool.
        """
        self._execute(data, offsets, cursor_offset)
        return {
            decl.name: (
                decl.dtype,
                len(buffer.storage),
                buffer.storage.tobytes(),
            )
            for decl, buffer in zip(self.module.buffers, self._buffers)
        }

    def _execute(
        self, data: bytes | memoryview, offsets: Sequence[int], cursor_offset: int
    ) -> None:
        self.reset()

        self.data = memoryview(data).cast("B")
        self.data_len = len(self.data)
        self.offsets = [int(x) for x in offsets]
        if len(self.offsets) < 2:
            raise ReadLengthMismatch("a basket needs at least two entry offsets")
        self.initial_cursor_position = int(cursor_offset)

        entry = self.module.funcs[self.module.entry]
        self.cursor = 0
        for event in range(len(self.offsets) - 1):
            self.event = event
            self.entry_end = self.offsets[event + 1]
            self.frames = []
            self.nb_stack = []
            self.regs = [0] * entry.reg_count
            self.func = entry
            self.ip = 0
            self.halted = False
            self._loop()
            if self.cursor != self.entry_end:
                raise ReadLengthMismatch(
                    f"event {event}: expected {self.entry_end} bytes, read {self.cursor}",
                    function=entry.name,
                    cursor=self.cursor,
                )

    def state_values(self) -> dict[str, int | str | None]:
        """
        The state slots after the last run.

        Returns:
            One entry per declared state, name slots resolved to text.
        """
        values = {}
        for decl, raw in zip(self.module.states, self.states):
            values[decl.name] = self._string_of(raw) if decl.is_string else raw
        return values

    def close(self) -> None:
        """
        Drop machine-owned storage; calling it twice is a no-op.

        The machine stays usable for `state_values` until it is dropped.
        """
        self._buffers = []
        self.data = memoryview(b"")

    # -- machinery --------------------------------------------------------
    def _loop(self) -> None:
        code = self.module.code
        while True:
            func = self.func
            if self.ip >= func.code_len:
                raise DetreeError(
                    f"{func.name}: execution ran past the end of the function",
                    function=func.name,
                    ip=self.ip,
                    cursor=self.cursor,
                )
            ins = code[func.code_off + self.ip]
            self.ip += 1
            handler = self._dispatch.get(ins.op)
            if handler is None:
                raise UnsupportedFeature(
                    f"unknown opcode {int(ins.op):#x}",
                    function=func.name,
                    ip=self.ip - 1,
                    cursor=self.cursor,
                )
            handler(self, ins)
            if self.halted:
                return

    def _error(self, cls: type[DetreeError], message: str) -> None:
        return cls(message, function=self.func.name, ip=self.ip - 1, cursor=self.cursor)

    def _need(self, nbytes: int) -> None:
        if self.cursor + nbytes > self.data_len:
            raise self._error(
                StreamUnderflow,
                f"need {nbytes} bytes, {self.data_len - self.cursor} left",
            )

    def _read(self, fmt: str, size: int) -> int:
        self._need(size)
        value = struct.unpack_from(fmt, self.data, self.cursor)[0]
        self.cursor += size
        return value

    # -- control and constants -------------------------------------------
    def _h_halt(self, ins: Ins) -> None:
        self.halted = True

    def _h_nop(self, ins: Ins) -> None:
        pass

    def _h_line(self, ins: Ins) -> None:
        pass

    def _h_debug_print(self, ins: Ins) -> None:
        pass

    def _h_const(self, ins: Ins) -> None:
        self.regs[ins.rd] = ins.imm1 & MASK64

    def _h_const_f(self, ins: Ins) -> None:
        self.regs[ins.rd] = ins.imm1 & MASK64

    def _h_mov(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.regs[ins.ra]

    def _h_zext(self, ins: Ins, bits: int) -> None:
        self.regs[ins.rd] = self.regs[ins.ra] & ((1 << bits) - 1)

    def _h_sext(self, ins: Ins, bits: int) -> None:
        value = self.regs[ins.ra] & ((1 << bits) - 1)
        if value & (1 << (bits - 1)):
            value -= 1 << bits
        self.regs[ins.rd] = value & MASK64

    def _h_zext8(self, ins: Ins) -> None:
        self._h_zext(ins, 8)

    def _h_zext16(self, ins: Ins) -> None:
        self._h_zext(ins, 16)

    def _h_zext32(self, ins: Ins) -> None:
        self._h_zext(ins, 32)

    def _h_sext8(self, ins: Ins) -> None:
        self._h_sext(ins, 8)

    def _h_sext16(self, ins: Ins) -> None:
        self._h_sext(ins, 16)

    def _h_sext32(self, ins: Ins) -> None:
        self._h_sext(ins, 32)

    # -- integer arithmetic ------------------------------------------------
    def _h_add(self, ins: Ins) -> None:
        self.regs[ins.rd] = (self.regs[ins.ra] + self.regs[ins.rb]) & MASK64

    def _h_sub(self, ins: Ins) -> None:
        self.regs[ins.rd] = (self.regs[ins.ra] - self.regs[ins.rb]) & MASK64

    def _h_mul(self, ins: Ins) -> None:
        self.regs[ins.rd] = (self.regs[ins.ra] * self.regs[ins.rb]) & MASK64

    def _h_div_s(self, ins: Ins) -> None:
        a, b = to_signed(self.regs[ins.ra]), to_signed(self.regs[ins.rb])
        self.regs[ins.rd] = 0 if b == 0 else _trunc_div(a, b) & MASK64

    def _h_div_u(self, ins: Ins) -> None:
        a, b = self.regs[ins.ra], self.regs[ins.rb]
        self.regs[ins.rd] = 0 if b == 0 else (a // b) & MASK64

    def _h_mod_s(self, ins: Ins) -> None:
        a, b = to_signed(self.regs[ins.ra]), to_signed(self.regs[ins.rb])
        self.regs[ins.rd] = 0 if b == 0 else (a - _trunc_div(a, b) * b) & MASK64

    def _h_mod_u(self, ins: Ins) -> None:
        a, b = self.regs[ins.ra], self.regs[ins.rb]
        self.regs[ins.rd] = 0 if b == 0 else (a % b) & MASK64

    def _h_neg(self, ins: Ins) -> None:
        self.regs[ins.rd] = (-self.regs[ins.ra]) & MASK64

    def _h_shl(self, ins: Ins) -> None:
        self.regs[ins.rd] = (self.regs[ins.ra] << (self.regs[ins.rb] & 63)) & MASK64

    def _h_shr_s(self, ins: Ins) -> None:
        self.regs[ins.rd] = (to_signed(self.regs[ins.ra]) >> (self.regs[ins.rb] & 63)) & MASK64

    def _h_shr_u(self, ins: Ins) -> None:
        self.regs[ins.rd] = (self.regs[ins.ra] & MASK64) >> (self.regs[ins.rb] & 63)

    def _h_and(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.regs[ins.ra] & self.regs[ins.rb]

    def _h_or(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.regs[ins.ra] | self.regs[ins.rb]

    def _h_xor(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.regs[ins.ra] ^ self.regs[ins.rb]

    def _h_not(self, ins: Ins) -> None:
        self.regs[ins.rd] = ~self.regs[ins.ra] & MASK64

    def _h_not_b(self, ins: Ins) -> None:
        self.regs[ins.rd] = 0 if self.regs[ins.ra] else 1

    # -- comparison --------------------------------------------------------
    def _h_cmp_eq(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] == self.regs[ins.rb])

    def _h_cmp_ne(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] != self.regs[ins.rb])

    def _h_cmp_lt_s(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(to_signed(self.regs[ins.ra]) < to_signed(self.regs[ins.rb]))

    def _h_cmp_le_s(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(to_signed(self.regs[ins.ra]) <= to_signed(self.regs[ins.rb]))

    def _h_cmp_gt_s(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(to_signed(self.regs[ins.ra]) > to_signed(self.regs[ins.rb]))

    def _h_cmp_ge_s(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(to_signed(self.regs[ins.ra]) >= to_signed(self.regs[ins.rb]))

    def _h_cmp_lt_u(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] < self.regs[ins.rb])

    def _h_cmp_le_u(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] <= self.regs[ins.rb])

    def _h_cmp_gt_u(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] > self.regs[ins.rb])

    def _h_cmp_ge_u(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] >= self.regs[ins.rb])

    # -- control flow ------------------------------------------------------
    def _h_jmp(self, ins: Ins) -> None:
        self.ip = ins.imm1

    def _h_jz(self, ins: Ins) -> None:
        if self.regs[ins.ra] == 0:
            self.ip = ins.imm1

    def _h_jnz(self, ins: Ins) -> None:
        if self.regs[ins.ra] != 0:
            self.ip = ins.imm1

    def _h_br_eq(self, ins: Ins) -> None:
        if self.regs[ins.ra] == self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_br_ne(self, ins: Ins) -> None:
        if self.regs[ins.ra] != self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_br_lt_s(self, ins: Ins) -> None:
        if to_signed(self.regs[ins.ra]) < to_signed(self.regs[ins.rb]):
            self.ip = ins.imm1

    def _h_br_le_s(self, ins: Ins) -> None:
        if to_signed(self.regs[ins.ra]) <= to_signed(self.regs[ins.rb]):
            self.ip = ins.imm1

    def _h_br_gt_s(self, ins: Ins) -> None:
        if to_signed(self.regs[ins.ra]) > to_signed(self.regs[ins.rb]):
            self.ip = ins.imm1

    def _h_br_ge_s(self, ins: Ins) -> None:
        if to_signed(self.regs[ins.ra]) >= to_signed(self.regs[ins.rb]):
            self.ip = ins.imm1

    def _h_br_lt_u(self, ins: Ins) -> None:
        if self.regs[ins.ra] < self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_br_le_u(self, ins: Ins) -> None:
        if self.regs[ins.ra] <= self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_br_gt_u(self, ins: Ins) -> None:
        if self.regs[ins.ra] > self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_br_ge_u(self, ins: Ins) -> None:
        if self.regs[ins.ra] >= self.regs[ins.rb]:
            self.ip = ins.imm1

    def _h_call(self, ins: Ins) -> None:
        if len(self.frames) >= MAX_DEPTH:
            raise self._error(StackOverflow, f"call depth above {MAX_DEPTH}")
        callee = self.module.funcs[ins.imm1]
        argument = self.regs[ins.ra] if ins.ra < len(self.regs) else 0
        self.frames.append((self.func, self.ip, ins.rd, self.regs, len(self.nb_stack)))
        self.regs = [0] * callee.reg_count
        if callee.arity:
            self.regs[0] = argument
        self.func = callee
        self.ip = 0

    def _h_ret(self, ins: Ins) -> None:
        value = self.regs[ins.ra] if ins.ra < len(self.regs) else 0
        if not self.frames:
            self._require_frame_depth(0)
            self.halted = True
            return
        func, ip, dst, regs, nb_depth = self.frames.pop()
        self._require_frame_depth(nb_depth)
        regs[dst] = value
        self.func, self.ip, self.regs = func, ip, regs

    def _require_frame_depth(self, expected: int) -> None:
        """
        Fail when a return leaves a byte-counted region open.

        A function that opens a frame and returns before closing it would hand
        the frame to its caller, where the next `FNB_END` would check the wrong
        position.
        """
        if len(self.nb_stack) != expected:
            raise self._error(
                DetreeError,
                f"return with byte-count depth {len(self.nb_stack)}, expected {expected}",
            )

    # -- stream access ------------------------------------------------------
    def _h_sread(self, ins: Ins) -> None:
        typecode = Typecode(ins.imm1)
        _, _, size = STREAM_FORMAT[typecode]
        self.regs[ins.rd] = self._read(REGISTER_FORMAT[typecode], size) & MASK64

    def _h_sread_fnb(self, ins: Ins) -> None:
        value = self._read(">I", 4)
        if not value & kByteCountMask:
            raise self._error(InvalidByteCount, f"fNBytes={value:#x} lacks kByteCountMask")
        self.regs[ins.rd] = value & ~kByteCountMask

    def _h_sread_version(self, ins: Ins) -> None:
        self.regs[ins.rd] = self._read(">h", 2) & MASK64

    def _h_skip(self, ins: Ins) -> None:
        self._need(ins.imm1)
        self.cursor += ins.imm1

    def _h_skip_r(self, ins: Ins) -> None:
        nbytes = self.regs[ins.ra]
        self._need(nbytes)
        self.cursor += nbytes

    def _h_skip_fnb(self, ins: Ins) -> None:
        value = self._read(">I", 4)
        if not value & kByteCountMask:
            raise self._error(InvalidByteCount, f"fNBytes={value:#x} lacks kByteCountMask")

    def _h_fnb_begin(self, ins: Ins) -> None:
        value = self._read(">I", 4)
        if not value & kByteCountMask:
            raise self._error(InvalidByteCount, f"fNBytes={value:#x} lacks kByteCountMask")
        if len(self.nb_stack) >= MAX_DEPTH:
            raise self._error(StackOverflow, f"byte-count depth above {MAX_DEPTH}")
        self.nb_stack.append(self.cursor + (value & ~kByteCountMask))

    def _h_fnb_end(self, ins: Ins) -> None:
        if not self.nb_stack:
            raise self._error(DetreeError, "FNB_END with no open byte-count frame")
        expected = self.nb_stack.pop()
        if self.cursor != expected:
            raise self._error(
                AssertionFailed,
                f"{self._msg(ins.imm1)} (expected {expected}, got {self.cursor})",
            )

    def _h_skip_version(self, ins: Ins) -> None:
        self._need(2)
        self.cursor += 2

    def _h_skip_tobject(self, ins: Ins) -> None:
        self._need(6)
        self.cursor += 6
        bits = self._read(">I", 4)
        if bits & kIsReferenced:
            self._need(2)
            self.cursor += 2

    def _h_skip_objhdr(self, ins: Ins) -> None:
        value = self._read(">I", 4)
        if not value & kByteCountMask:
            raise self._error(InvalidByteCount, f"fNBytes={value:#x} lacks kByteCountMask")
        tag = self._read(">I", 4)
        if tag == kNewClassTag:
            self._skip_cstr()

    def _h_skip_cstr(self, ins: Ins) -> None:
        self._skip_cstr()

    def _skip_cstr(self) -> None:
        start = self.cursor
        data = self.data
        limit = self.data_len
        while self.cursor < limit and data[self.cursor] != 0:
            self.cursor += 1
        if self.cursor >= limit:
            self.cursor = start
            raise self._error(StreamUnderflow, "unterminated string")
        self.cursor += 1

    def _h_cursor(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.cursor

    def _h_set_cursor(self, ins: Ins) -> None:
        value = self.regs[ins.ra]
        if value > self.data_len:
            raise self._error(StreamUnderflow, f"cursor {value} outside the basket")
        self.cursor = value

    def _h_entries(self, ins: Ins) -> None:
        self.regs[ins.rd] = len(self.offsets) - 1

    def _h_init_offset(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.initial_cursor_position & MASK64

    def _h_data_size(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.offsets[self.event + 1] - self.offsets[self.event]

    # -- event offsets -------------------------------------------------------
    def _h_offset_at(self, ins: Ins) -> None:
        index = self.regs[ins.ra]
        if index >= len(self.offsets):
            raise self._error(ReadLengthMismatch, f"entry offset {index} does not exist")
        self.regs[ins.rd] = self.offsets[index] & MASK64

    def _h_offset_seek(self, ins: Ins) -> None:
        value = to_signed(self.regs[ins.ra])
        index = bisect_right(self.offsets, value)
        if index >= len(self.offsets):
            raise self._error(ReadLengthMismatch, f"no entry offset after {value}")
        self.regs[ins.rd] = self.offsets[index] & MASK64

    def _h_event_end(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.offsets[self.event + 1] & MASK64

    # -- coarse-grained reads ------------------------------------------------
    def _h_read_bulk(self, ins: Ins) -> None:
        count = to_signed(self.regs[ins.ra])
        if count <= 0:
            return
        buffer = self._buffers[ins.imm1]
        typecode = Typecode(ins.imm2)
        size = typecode_size(typecode)
        self._need(count * size)
        buffer.append_stream(self.data, self.cursor, count, typecode)
        self.cursor += count * size

    def _h_read_bulk_until(self, ins: Ins) -> None:
        end = to_signed(self.regs[ins.ra])
        typecode = Typecode(ins.imm2)
        size = typecode_size(typecode)
        span = end - self.cursor
        if span < 0:
            raise self._error(
                ReadLengthMismatch,
                f"bulk end {end} is behind the cursor {self.cursor}",
            )
        if span % size:
            raise self._error(
                ReadLengthMismatch,
                f"{span} bytes are not a whole number of {typecode.name.lower()} elements",
            )
        if span == 0:
            return
        self._need(span)
        self._buffers[ins.imm1].append_stream(self.data, self.cursor, span // size, typecode)
        self.cursor = end

    def _h_read_string(self, ins: Ins) -> None:
        chars = self._buffers[ins.imm1]
        offsets = self._buffers[ins.imm2]
        size = self._read(">B", 1)
        if size == 255:
            size = self._read(">I", 4)
        self._need(size)
        chars.append_stream(self.data, self.cursor, size, Typecode.UINT8)
        self.cursor += size
        end = chars.count if ins.rb == 0 else (offsets.last + size) & MASK64
        offsets.append_register(end)

    def _h_read_strided(self, ins: Ins) -> None:
        count = to_signed(self.regs[ins.ra])
        stride = to_signed(self.regs[ins.rb])
        if count <= 0:
            return
        buffer = self._buffers[ins.imm1]
        typecode = Typecode(ins.imm2)
        size = typecode_size(typecode)
        if stride < size:
            raise self._error(StreamUnderflow, f"stride {stride} below element size {size}")
        self._need((count - 1) * stride + size)
        base = self.cursor
        for index in range(count):
            buffer.append_stream(self.data, base + index * stride, 1, typecode)
        self.cursor = base + (count - 1) * stride + size

    def _h_read_fields(self, ins: Ins) -> None:
        count = to_signed(self.regs[ins.ra])
        layout = self.module.layouts[ins.imm1]
        for _ in range(max(count, 0)):
            for field in layout.fields:
                if field.kind == FieldKind.SKIP:
                    self._need(field.count)
                    self.cursor += field.count
                elif field.kind == FieldKind.SCALAR:
                    _, _, size = STREAM_FORMAT[field.typecode]
                    bits = self._read(REGISTER_FORMAT[field.typecode], size)
                    self._buffers[field.buffer_id].append_register(bits)
                elif field.kind == FieldKind.ARRAY_FIXED:
                    _, _, size = STREAM_FORMAT[field.typecode]
                    self._need(field.count * size)
                    self._buffers[field.buffer_id].append_stream(
                        self.data, self.cursor, field.count, field.typecode
                    )
                    self.cursor += field.count * size
                else:
                    raise self._error(
                        UnsupportedFeature, f"layout field kind {field.kind.name}"
                    )

    def _h_read_append(self, ins: Ins) -> None:
        buffer = self._buffers[ins.imm1]
        typecode = Typecode(ins.imm2)
        _, _, size = STREAM_FORMAT[typecode]
        buffer.append_register(self._read(REGISTER_FORMAT[typecode], size))

    # -- buffer access --------------------------------------------------------
    def _h_buf_append(self, ins: Ins) -> None:
        self._buffers[ins.imm1].append_register(self.regs[ins.ra])

    def _h_buf_append_const(self, ins: Ins) -> None:
        self._buffers[ins.imm1].append_register(ins.imm2)

    def _h_buf_append_offset(self, ins: Ins) -> None:
        buffer = self._buffers[ins.imm1]
        buffer.append_register(buffer.last + self.regs[ins.ra])

    def _h_buf_append_len(self, ins: Ins) -> None:
        self._buffers[ins.imm1].append_register(self._buffers[ins.imm2].count)

    def _h_buf_last(self, ins: Ins) -> None:
        self.regs[ins.rd] = self._buffers[ins.imm1].last & MASK64

    def _h_buf_len(self, ins: Ins) -> None:
        self.regs[ins.rd] = self._buffers[ins.imm1].count

    def _h_buf_reserve(self, ins: Ins) -> None:
        pass

    # -- program state --------------------------------------------------------
    def _h_state_get(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.states[ins.imm1] & MASK64

    def _h_state_set(self, ins: Ins) -> None:
        self.states[ins.imm1] = self.regs[ins.ra] & MASK64

    def _h_state_iadd(self, ins: Ins) -> None:
        self.states[ins.imm1] = (self.states[ins.imm1] + ins.imm2) & MASK64

    # -- object references -----------------------------------------------------
    def _h_ref_has(self, ins: Ins) -> None:
        self.regs[ins.rd] = int(self.regs[ins.ra] in self.refs)

    def _h_ref_get_obj(self, ins: Ins) -> None:
        entry = self.refs.get(self.regs[ins.ra])
        self.regs[ins.rd] = 0 if entry is None else entry[1] & MASK64

    def _h_ref_get_class(self, ins: Ins) -> None:
        entry = self.refs.get(self.regs[ins.ra])
        self.regs[ins.rd] = MASK64 if entry is None else entry[2] & MASK64

    def _h_ref_set_obj(self, ins: Ins) -> None:
        self.refs[self.regs[ins.ra]] = (0, self.regs[ins.rb], -1)

    def _h_ref_set_class(self, ins: Ins) -> None:
        self.refs[self.regs[ins.ra]] = (1, -1, self.regs[ins.rb])

    def _h_ref_count(self, ins: Ins) -> None:
        self.regs[ins.rd] = len(self.refs)

    # -- strings ---------------------------------------------------------------
    def _intern(self, text: str) -> int:
        ident = self._str_ids.get(text)
        if ident is None:
            ident = len(self.strings)
            self.strings.append(text)
            self._str_ids[text] = ident
        return ident

    def _string_of(self, ident: int) -> str:
        if ident == MASK64:
            return None
        index = int(ident)
        if 0 <= index < len(self.strings):
            return self.strings[index]
        return f"<string {ident}>"

    def _h_sread_cstr(self, ins: Ins) -> None:
        start = self.cursor
        data = self.data
        limit = self.data_len
        while self.cursor < limit and data[self.cursor] != 0:
            self.cursor += 1
        if self.cursor >= limit:
            self.cursor = start
            raise self._error(StreamUnderflow, "unterminated string")
        text = data[start : self.cursor].tobytes().decode()
        self.cursor += 1
        self.regs[ins.rd] = self._intern(text)

    def _h_str_const(self, ins: Ins) -> None:
        self.regs[ins.rd] = ins.imm1

    def _h_str_eq(self, ins: Ins) -> None:
        left = self._string_of(self.regs[ins.ra])
        right = self._string_of(self.regs[ins.rb])
        self.regs[ins.rd] = int(left is not None and left == right)

    def _h_str_store(self, ins: Ins) -> None:
        self.states[ins.imm1] = self.regs[ins.ra] & MASK64

    def _h_str_load(self, ins: Ins) -> None:
        self.regs[ins.rd] = self.states[ins.imm1] & MASK64

    # -- verification ------------------------------------------------------------
    def _msg(self, ident: int) -> str:
        if 0 <= ident < len(self.module.strings):
            return self.module.strings[ident]
        return f"<message {ident}>"

    def _h_assert(self, ins: Ins) -> None:
        if not self.regs[ins.ra]:
            raise self._error(AssertionFailed, self._msg(ins.imm1))

    def _h_assert_cursor_eq(self, ins: Ins) -> None:
        if self.cursor != self.regs[ins.ra]:
            raise self._error(
                AssertionFailed,
                f"{self._msg(ins.imm1)} (expected {self.regs[ins.ra]}, got {self.cursor})",
            )

    def _h_assert_str_eq(self, ins: Ins) -> None:
        left = self._string_of(self.regs[ins.ra])
        right = self._string_of(self.regs[ins.rb])
        if left is None or left != right:
            raise self._error(
                AssertionFailed,
                f"{self._msg(ins.imm1)} (expected {right!r}, got {left!r})",
            )

    def _h_throw(self, ins: Ins) -> None:
        raise self._error(AssertionFailed, self._msg(ins.imm1))


_DISPATCH = {
    Op.HALT: PythonMachine._h_halt,
    Op.NOP: PythonMachine._h_nop,
    Op.LINE: PythonMachine._h_line,
    Op.DEBUG_PRINT: PythonMachine._h_debug_print,
    Op.CONST: PythonMachine._h_const,
    Op.CONST_F: PythonMachine._h_const_f,
    Op.MOV: PythonMachine._h_mov,
    Op.ZEXT32: PythonMachine._h_zext32,
    Op.ZEXT16: PythonMachine._h_zext16,
    Op.ZEXT8: PythonMachine._h_zext8,
    Op.SEXT32: PythonMachine._h_sext32,
    Op.SEXT16: PythonMachine._h_sext16,
    Op.SEXT8: PythonMachine._h_sext8,
    Op.ADD: PythonMachine._h_add,
    Op.SUB: PythonMachine._h_sub,
    Op.MUL: PythonMachine._h_mul,
    Op.DIV_S: PythonMachine._h_div_s,
    Op.DIV_U: PythonMachine._h_div_u,
    Op.MOD_S: PythonMachine._h_mod_s,
    Op.MOD_U: PythonMachine._h_mod_u,
    Op.NEG: PythonMachine._h_neg,
    Op.SHL: PythonMachine._h_shl,
    Op.SHR_S: PythonMachine._h_shr_s,
    Op.SHR_U: PythonMachine._h_shr_u,
    Op.AND: PythonMachine._h_and,
    Op.OR: PythonMachine._h_or,
    Op.XOR: PythonMachine._h_xor,
    Op.NOT: PythonMachine._h_not,
    Op.NOT_B: PythonMachine._h_not_b,
    Op.CMP_EQ: PythonMachine._h_cmp_eq,
    Op.CMP_NE: PythonMachine._h_cmp_ne,
    Op.CMP_LT_S: PythonMachine._h_cmp_lt_s,
    Op.CMP_LE_S: PythonMachine._h_cmp_le_s,
    Op.CMP_GT_S: PythonMachine._h_cmp_gt_s,
    Op.CMP_GE_S: PythonMachine._h_cmp_ge_s,
    Op.CMP_LT_U: PythonMachine._h_cmp_lt_u,
    Op.CMP_LE_U: PythonMachine._h_cmp_le_u,
    Op.CMP_GT_U: PythonMachine._h_cmp_gt_u,
    Op.CMP_GE_U: PythonMachine._h_cmp_ge_u,
    Op.JMP: PythonMachine._h_jmp,
    Op.JZ: PythonMachine._h_jz,
    Op.JNZ: PythonMachine._h_jnz,
    Op.BR_EQ: PythonMachine._h_br_eq,
    Op.BR_NE: PythonMachine._h_br_ne,
    Op.BR_LT_S: PythonMachine._h_br_lt_s,
    Op.BR_LE_S: PythonMachine._h_br_le_s,
    Op.BR_GT_S: PythonMachine._h_br_gt_s,
    Op.BR_GE_S: PythonMachine._h_br_ge_s,
    Op.BR_LT_U: PythonMachine._h_br_lt_u,
    Op.BR_LE_U: PythonMachine._h_br_le_u,
    Op.BR_GT_U: PythonMachine._h_br_gt_u,
    Op.BR_GE_U: PythonMachine._h_br_ge_u,
    Op.CALL: PythonMachine._h_call,
    Op.RET: PythonMachine._h_ret,
    Op.SREAD: PythonMachine._h_sread,
    Op.SREAD_FNB: PythonMachine._h_sread_fnb,
    Op.SREAD_VERSION: PythonMachine._h_sread_version,
    Op.SREAD_CSTR: PythonMachine._h_sread_cstr,
    Op.SKIP: PythonMachine._h_skip,
    Op.SKIP_R: PythonMachine._h_skip_r,
    Op.SKIP_FNB: PythonMachine._h_skip_fnb,
    Op.SKIP_VERSION: PythonMachine._h_skip_version,
    Op.SKIP_TOBJECT: PythonMachine._h_skip_tobject,
    Op.SKIP_OBJHDR: PythonMachine._h_skip_objhdr,
    Op.SKIP_CSTR: PythonMachine._h_skip_cstr,
    Op.CURSOR: PythonMachine._h_cursor,
    Op.SET_CURSOR: PythonMachine._h_set_cursor,
    Op.ENTRIES: PythonMachine._h_entries,
    Op.INIT_OFFSET: PythonMachine._h_init_offset,
    Op.DATA_SIZE: PythonMachine._h_data_size,
    Op.FNB_BEGIN: PythonMachine._h_fnb_begin,
    Op.FNB_END: PythonMachine._h_fnb_end,
    Op.OFFSET_AT: PythonMachine._h_offset_at,
    Op.OFFSET_SEEK: PythonMachine._h_offset_seek,
    Op.EVENT_END: PythonMachine._h_event_end,
    Op.READ_BULK: PythonMachine._h_read_bulk,
    Op.READ_BULK_UNTIL: PythonMachine._h_read_bulk_until,
    Op.READ_STRING: PythonMachine._h_read_string,
    Op.READ_STRIDED: PythonMachine._h_read_strided,
    Op.READ_FIELDS: PythonMachine._h_read_fields,
    Op.READ_APPEND: PythonMachine._h_read_append,
    Op.BUF_APPEND: PythonMachine._h_buf_append,
    Op.BUF_APPEND_CONST: PythonMachine._h_buf_append_const,
    Op.BUF_APPEND_OFFSET: PythonMachine._h_buf_append_offset,
    Op.BUF_APPEND_LEN: PythonMachine._h_buf_append_len,
    Op.BUF_LAST: PythonMachine._h_buf_last,
    Op.BUF_LEN: PythonMachine._h_buf_len,
    Op.BUF_RESERVE: PythonMachine._h_buf_reserve,
    Op.STATE_GET: PythonMachine._h_state_get,
    Op.STATE_SET: PythonMachine._h_state_set,
    Op.STATE_IADD: PythonMachine._h_state_iadd,
    Op.REF_HAS: PythonMachine._h_ref_has,
    Op.REF_GET_OBJ: PythonMachine._h_ref_get_obj,
    Op.REF_GET_CLASS: PythonMachine._h_ref_get_class,
    Op.REF_SET_OBJ: PythonMachine._h_ref_set_obj,
    Op.REF_SET_CLASS: PythonMachine._h_ref_set_class,
    Op.REF_COUNT: PythonMachine._h_ref_count,
    Op.STR_CONST: PythonMachine._h_str_const,
    Op.STR_EQ: PythonMachine._h_str_eq,
    Op.STR_STORE: PythonMachine._h_str_store,
    Op.STR_LOAD: PythonMachine._h_str_load,
    Op.ASSERT: PythonMachine._h_assert,
    Op.ASSERT_CURSOR_EQ: PythonMachine._h_assert_cursor_eq,
    Op.ASSERT_STR_EQ: PythonMachine._h_assert_str_eq,
    Op.THROW: PythonMachine._h_throw,
}

__all__ = ["PythonMachine", "to_signed"]
