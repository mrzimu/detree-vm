"""
`ctypes` binding to the C ABI.

This module declares the structures and the `detree_*` entry points of
`kernel/include/detree.h`. The declarations are pinned by a layout test:
a C probe prints `sizeof`/`offsetof` and the test compares it with these
definitions.

The machine below is a thin adapter, not an implementation: it converts Python
objects to C arguments, calls one entry point per basket, and wraps the
returned bytes in NumPy arrays that keep the C owner alive. All semantics live
on the other side of the boundary.
"""

from __future__ import annotations

import ctypes
import functools
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

from ..spec.errors import (
    AssertionFailed,
    BackendUnavailable,
    DetreeError,
    InvalidByteCount,
    ModuleError,
    ReadLengthMismatch,
    StackOverflow,
    StreamUnderflow,
    UnsupportedFeature,
)
from .libs import library_path

# `DETREE_C_API_VERSION` in the header.
C_API_VERSION = 1

# `detree_status` value -> the Python class of the same meaning. A status of
# 1 (module error) is special-cased at run time, where it means "the module is
# malformed", not "the module failed validation".
STATUS_TO_EXCEPTION = {
    0: None,  # DETREE_OK
    1: ModuleError,  # DETREE_MODULE_ERROR (decode or validation)
    2: StreamUnderflow,
    3: InvalidByteCount,
    4: ReadLengthMismatch,
    5: UnsupportedFeature,
    6: AssertionFailed,
    7: StackOverflow,
    8: DetreeError,
    9: DetreeError,  # DETREE_BAD_ARGUMENT
}


class _DetreeError(ctypes.Structure):
    """
    Mirrors `detree_error` in `detree.h`.

    Pointers are borrowed, so they are kept as addresses.
    """

    _fields_ = [
        ("status", ctypes.c_int32),
        ("reserved", ctypes.c_int32),
        ("ip", ctypes.c_int64),
        ("cursor", ctypes.c_int64),
        ("message", ctypes.c_void_p),
        ("message_length", ctypes.c_size_t),
        ("function", ctypes.c_void_p),
        ("function_length", ctypes.c_size_t),
    ]


class _DetreeOutput(ctypes.Structure):
    """
    Mirrors `detree_output` in `detree.h`.

    `owner` is retained until the array built from these bytes is dropped.
    """

    _fields_ = [
        ("data", ctypes.c_void_p),
        ("length", ctypes.c_size_t),
        ("itemsize", ctypes.c_size_t),
        ("owner", ctypes.c_void_p),
    ]


class _DetreeState(ctypes.Structure):
    """
    Mirrors `detree_state` in `detree.h`.

    `name` and `text` are borrowed from the machine.
    """

    _fields_ = [
        ("name", ctypes.c_void_p),
        ("name_length", ctypes.c_size_t),
        ("is_name", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
        ("value", ctypes.c_uint64),
        ("text", ctypes.c_void_p),
        ("text_length", ctypes.c_size_t),
    ]


_U8P = ctypes.POINTER(ctypes.c_uint8)
_I64P = ctypes.POINTER(ctypes.c_int64)


class _Owner:
    """
    Keeps one retained buffer storage alive, and releases it on collection.

    The ABI hands out borrowed bytes; keeping the owner is what lets an array
    outlive the machine's next run.
    """

    __slots__ = ("_library", "_ptr")

    def __init__(self, library: _Library, ptr: int) -> None:
        self._library = library
        self._ptr = int(ptr)

    def __del__(self) -> None:
        ptr = getattr(self, "_ptr", None)
        if not ptr:
            return
        self._ptr = None
        try:
            self._library.lib.detree_owner_release(ptr)
        except Exception:  # pragma: no cover - interpreter shutdown
            pass


class _OwnedArray(np.ndarray):
    """
    A NumPy view whose `_owner` keeps the C storage alive.

    The attribute is set after the view is built, which a NumPy subclass
    allows because the class declares no `__slots__`.
    """


def _text(ptr: int | None, length: int) -> str:
    if not ptr:
        return ""
    return ctypes.string_at(ptr, length).decode("utf-8", "replace")


def _u8_view(data: bytes | bytearray | memoryview | np.ndarray) -> np.ndarray:
    """
    *data* as a contiguous `uint8` array, without copying what already is one.

    A basket is tens of kilobytes and the machine only reads it, so copying it
    per call is pure overhead: the returned array owns or borrows the caller's
    buffer, and the caller keeps it alive for the duration of the call.
    """
    if isinstance(data, np.ndarray):
        if data.dtype == np.uint8 and data.flags["C_CONTIGUOUS"]:
            return data
        return np.ascontiguousarray(data, dtype=np.uint8)
    return np.frombuffer(memoryview(data).cast("B"), dtype=np.uint8)


def _i64_view(offsets: Sequence[int] | np.ndarray) -> np.ndarray:
    """
    *offsets* as a contiguous `int64` array, copying only when it is not one.

    Entry offsets are the one argument with one element per event, so building
    them element by element in Python costs more than decoding the basket.
    """
    if isinstance(offsets, np.ndarray):
        if offsets.dtype == np.int64 and offsets.flags["C_CONTIGUOUS"]:
            return offsets
        return np.ascontiguousarray(offsets, dtype=np.int64)
    return np.ascontiguousarray(offsets, dtype=np.int64)


class _Library:
    """
    One loaded shared library, with the prototypes it is expected to export.

    Construction checks the C ABI version and the implementation name, so a
    library with the wrong identity fails here instead of inside a basket.
    """

    def __init__(self, path: Path, backend: str) -> None:
        self.path = path
        self.backend = backend
        try:
            self.lib = ctypes.CDLL(str(path))
        except OSError as error:  # pragma: no cover - depends on the platform
            raise BackendUnavailable(f"cannot load {path}: {error}") from error

        self._declare_prototypes()

        version = self.lib.detree_c_api_version()
        if version != C_API_VERSION:
            raise BackendUnavailable(
                f"{path} implements C ABI {version}, expected {C_API_VERSION}"
            )
        name = self.lib.detree_backend_name()
        if name is None or name.decode("ascii") != backend:
            found = "<none>" if name is None else name.decode("ascii", "replace")
            raise BackendUnavailable(f"{path} is the {found!r} machine, not {backend!r}")

    # -- declarations -----------------------------------------------------
    def _declare_prototypes(self) -> None:
        lib = self.lib

        def declare(name: str, restype: object, argtypes: list[object]) -> None:
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes

        declare("detree_c_api_version", ctypes.c_uint32, [])
        declare("detree_backend_name", ctypes.c_char_p, [])
        declare("detree_backend_version", ctypes.c_char_p, [])
        declare("detree_isa_major", ctypes.c_uint16, [])
        declare("detree_isa_minor", ctypes.c_uint16, [])
        declare("detree_capabilities", ctypes.c_uint64, [])

        declare("detree_machine_new", ctypes.c_void_p, [_U8P, ctypes.c_size_t])
        declare("detree_machine_free", None, [ctypes.c_void_p])
        declare("detree_machine_reset", None, [ctypes.c_void_p])
        declare("detree_machine_isa_major", ctypes.c_uint16, [ctypes.c_void_p])
        declare("detree_machine_isa_minor", ctypes.c_uint16, [ctypes.c_void_p])

        declare("detree_buffer_count", ctypes.c_size_t, [ctypes.c_void_p])
        declare(
            "detree_buffer_name",
            ctypes.c_void_p,
            [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )
        declare(
            "detree_buffer_dtype",
            ctypes.c_void_p,
            [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )

        declare(
            "detree_run",
            ctypes.c_int32,
            [
                ctypes.c_void_p,
                _U8P,
                ctypes.c_size_t,
                _I64P,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ],
        )
        declare(
            "detree_read",
            ctypes.c_int32,
            [
                ctypes.c_void_p,
                _U8P,
                ctypes.c_size_t,
                _I64P,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(_DetreeOutput),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
            ],
        )
        declare(
            "detree_acquire_outputs",
            ctypes.c_size_t,
            [ctypes.c_void_p, ctypes.POINTER(_DetreeOutput), ctypes.c_size_t],
        )
        declare("detree_owner_release", None, [ctypes.c_void_p])

        declare("detree_state_count", ctypes.c_size_t, [ctypes.c_void_p])
        declare(
            "detree_state_name",
            ctypes.c_void_p,
            [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)],
        )
        declare(
            "detree_get_states",
            ctypes.c_size_t,
            [ctypes.c_void_p, ctypes.POINTER(_DetreeState), ctypes.c_size_t],
        )
        declare("detree_last_error", _DetreeError, [])

    # -- helpers ----------------------------------------------------------
    def error(self, module_error: bool = False) -> DetreeError:
        """
        The last failure as a Python exception, with the machine's context.

        Args:
            module_error: report a decode or validation failure as
                `ModuleError` regardless of the status code.

        Returns:
            The exception to raise.
        """
        error = self.lib.detree_last_error()
        message = _text(error.message, error.message_length)
        function = _text(error.function, error.function_length)
        cls = STATUS_TO_EXCEPTION.get(error.status) or DetreeError
        if module_error:
            cls = ModuleError
        elif error.status == 1:
            # A module error raised during a run means execution left the
            # function, which the reference reports as the base class.
            cls = DetreeError
        return cls(message, function=function, ip=error.ip, cursor=error.cursor)

    def machine(self, module: bytes) -> CapiMachine:
        """
        Build a machine from module bytes.

        Args:
            module: the module byte format of `docs/spec/module.md`.

        Returns:
            A machine bound to this library.

        Raises:
            ModuleError: the module does not decode or does not validate.
        """
        raw = _u8_view(module)
        ptr = self.lib.detree_machine_new(ctypes.cast(raw.ctypes.data, _U8P), raw.size)
        if not ptr:
            raise self.error(module_error=True)
        return CapiMachine(self, ptr)


class CapiMachine:
    """
    The backend-neutral machine protocol over one compiled library.

    One instance wraps one `detree_machine`; it is not thread-safe, because
    the machine's registers and buffers are plain C state behind the pointer.
    """

    def __init__(self, library: _Library, ptr: int) -> None:
        self._library = library
        self._ptr = int(ptr)
        self.backend = library.backend
        self.isa = (
            int(library.lib.detree_machine_isa_major(self._ptr)),
            int(library.lib.detree_machine_isa_minor(self._ptr)),
        )

        lib = library.lib
        self._buffer_names = []
        self._buffer_dtypes = []
        for index in range(lib.detree_buffer_count(self._ptr)):
            length = ctypes.c_size_t()
            name = lib.detree_buffer_name(self._ptr, index, ctypes.byref(length))
            self._buffer_names.append(_text(name, length.value))
            dtype = lib.detree_buffer_dtype(self._ptr, index, ctypes.byref(length))
            self._buffer_dtypes.append(_text(dtype, length.value))

        self._state_names = []
        self._state_is_name = []
        for index in range(lib.detree_state_count(self._ptr)):
            length = ctypes.c_size_t()
            name = lib.detree_state_name(self._ptr, index, ctypes.byref(length))
            self._state_names.append(_text(name, length.value))
            self._state_is_name.append(False)
        self._read_state_kinds()

    def _read_state_kinds(self) -> None:
        """
        Read the name/integer kind once, so `state_values` stays simple.

        One `detree_get_states` call at build time costs less than one per
        basket.
        """
        count = len(self._state_names)
        if not count:
            return
        states = (_DetreeState * count)()
        written = self._library.lib.detree_get_states(self._ptr, states, count)
        for index in range(written):
            self._state_is_name[index] = bool(states[index].is_name)

    # -- protocol ---------------------------------------------------------
    def run(
        self,
        data: bytes | bytearray | memoryview | np.ndarray,
        offsets: Sequence[int] | np.ndarray,
        cursor_offset: int = 0,
    ) -> Mapping[str, np.ndarray]:
        """
        Run one basket and take its outputs.

        Args:
            data: the decompressed basket body.
            offsets: cumulative entry offsets, at least two.
            cursor_offset: the key length used for object references.

        Returns:
            One read-only array per buffer, keyed by buffer name.
        """
        self._require_open()
        raw = _u8_view(data)
        entries = _i64_view(offsets)

        count = len(self._buffer_names)
        outputs = (_DetreeOutput * count)()
        written = ctypes.c_size_t(0)
        status = self._library.lib.detree_read(
            self._ptr,
            ctypes.cast(raw.ctypes.data, _U8P),
            raw.size,
            entries.ctypes.data_as(_I64P),
            entries.size,
            int(cursor_offset),
            outputs,
            count,
            ctypes.byref(written),
        )
        if status != 0:
            raise self._library.error()

        result = {}
        for index in range(written.value):
            output = outputs[index]
            dtype = np.dtype(self._buffer_dtypes[index])
            if output.length == 0:
                if output.owner:
                    self._library.lib.detree_owner_release(output.owner)
                result[self._buffer_names[index]] = np.empty(0, dtype=dtype)
                continue

            nbytes = output.length * output.itemsize
            owner = _Owner(self._library, output.owner)
            source = (ctypes.c_char * nbytes).from_address(output.data)
            array = np.frombuffer(source, dtype=dtype).view(_OwnedArray)
            array._owner = owner
            result[self._buffer_names[index]] = array
        return result

    def state_values(self) -> Mapping[str, int | str | None]:
        """
        The state slots after the last run, with name slots resolved.

        Returns:
            One entry per declared state.
        """
        self._require_open()
        count = len(self._state_names)
        if not count:
            return {}
        states = (_DetreeState * count)()
        written = self._library.lib.detree_get_states(self._ptr, states, count)
        values = {}
        for index in range(written):
            state = states[index]
            if state.is_name:
                values[self._state_names[index]] = (
                    _text(state.text, state.text_length) if state.text else None
                )
            else:
                values[self._state_names[index]] = int(state.value)
        return values

    def reset(self) -> None:
        """
        Reset the machine; buffer storage is reallocated, not cleared.

        Arrays handed out by an earlier run stay readable after this call.
        """
        self._require_open()
        self._library.lib.detree_machine_reset(self._ptr)

    def close(self) -> None:
        """
        Free the machine; calling it twice is a no-op.

        After the call every method raises `DetreeError`.
        """
        ptr = self._ptr
        self._ptr = 0
        if ptr:
            self._library.lib.detree_machine_free(ptr)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # pragma: no cover - interpreter shutdown
            pass

    def _require_open(self) -> None:
        if not self._ptr:
            raise DetreeError("machine is closed")


@functools.cache
def _load(path: str, backend: str) -> _Library:
    return _Library(Path(path), backend)


def load(
    backend: str = "cpp",
    explicit: str | os.PathLike | None = None,
) -> _Library:
    """
    Load the compiled library for *backend*.

    Args:
        backend: the implementation name to load.
        explicit: an explicit library path, tried before the search order.

    Returns:
        The loaded library, cached per process.

    Raises:
        BackendUnavailable: the library is missing, or has the wrong identity.
    """
    path = library_path(backend, explicit)
    if path is None:
        raise BackendUnavailable(f"{backend} library was not found")
    return _load(str(path), backend)


def build(
    module: bytes,
    backend: str = "cpp",
    explicit: str | os.PathLike | None = None,
) -> CapiMachine:
    """
    Decode *module* with the compiled machine named by *backend*.

    Args:
        module: the module byte format of `docs/spec/module.md`.
        backend: the implementation name to call.
        explicit: an explicit library path, tried before the search order.

    Returns:
        A machine bound to that library.
    """
    return load(backend, explicit).machine(module)


__all__ = [
    "C_API_VERSION",
    "CapiMachine",
    "STATUS_TO_EXCEPTION",
    "build",
    "load",
]
