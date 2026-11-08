"""
The C++ comparison backend (development only).

It is available when the `cppdetree` library can be found; it reaches level 3
when the C++ machine passes the full conformance suite. The shipped machine is
Rust. This build exists for performance comparison in development, is loaded
through the C ABI, and is never the default.
"""

from __future__ import annotations

from ..abi import capi
from ..abi.libs import library_path


class CppBackend:
    """
    The C++ comparison machine, loaded through the C ABI.

    Development only: the shipped machine is Rust.
    """

    name = "cpp"
    level = 3  # implements every opcode family in the ISA
    reason = "libcppdetree was not found"

    def available(self) -> bool:
        """
        Whether `cppdetree` can be found for this platform.

        Returns:
            `True` when the comparison library is installed.
        """
        return library_path("cpp") is not None

    def build(self, module: bytes) -> capi.CapiMachine:
        """
        Build a machine backed by the C++ library.

        Args:
            module: the module byte format of `docs/spec/module.md`.

        Returns:
            A machine that calls the comparison library.
        """
        return capi.build(module, "cpp")


__all__ = ["CppBackend"]
