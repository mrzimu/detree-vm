"""
Error taxonomy shared by every backend.

A compiled backend maps its C status to the class of the same meaning, so a
caller catches `StreamUnderflow` whichever machine raised it.
"""

from __future__ import annotations


class DetreeError(Exception):
    """
    Base class for every Detree failure.

    Args:
        message: A human-readable reason.
        function: The function name the failure happened in, when known.
        ip: The instruction index inside that function, or `-1`.
        cursor: The byte offset of the stream, or `-1`.
    """

    def __init__(
        self,
        message: str,
        function: str = "",
        ip: int = -1,
        cursor: int = -1,
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.function = function
        self.ip = ip
        self.cursor = cursor

    def __str__(self) -> str:
        parts = [self.message]
        if self.function:
            parts.append(f"function={self.function}")
        if self.ip >= 0:
            parts.append(f"ip={self.ip}")
        if self.cursor >= 0:
            parts.append(f"cursor={self.cursor}")
        return " ".join(parts)


class ModuleError(DetreeError):
    """
    The module does not decode or does not validate.

    Raised by the decoder and by `detree.spec.validate`.
    """


class ModuleValidationError(ModuleError):
    """
    The module decoded but violates a static rule.

    The bytes are well formed; the tables or the code are not executable.
    """


class StreamUnderflow(DetreeError):
    """
    A read or skip would leave the current event's data.

    The basket is shorter than the program expects.
    """


class InvalidByteCount(DetreeError):
    """
    An `fNBytes` field lacks `kByteCountMask`.

    The stream is malformed, not merely short.
    """


class ReadLengthMismatch(DetreeError):
    """
    The cursor is not at the event boundary after the entry function.

    An event did not end where the entry offsets say it should.
    """


class UnsupportedFeature(DetreeError):
    """
    A reserved opcode, layout field, or capability is not implemented.

    The machine's ISA version does not cover this module.
    """


class AssertionFailed(DetreeError):
    """
    An `ASSERT*` or `THROW` opcode failed.

    The data is not what the module expects.
    """


class StackOverflow(DetreeError):
    """
    The call depth limit was exceeded.

    `MAX_DEPTH` bounds how many frames a basket may nest.
    """


class BackendUnavailable(DetreeError):
    """
    A requested backend is absent or below the required conformance level.

    Raised before a machine is built, never during a run.
    """


__all__ = [
    "AssertionFailed",
    "BackendUnavailable",
    "DetreeError",
    "InvalidByteCount",
    "ModuleError",
    "ModuleValidationError",
    "ReadLengthMismatch",
    "StackOverflow",
    "StreamUnderflow",
    "UnsupportedFeature",
]
