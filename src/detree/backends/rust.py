"""
The Rust backend.

It passes conformance level 3 and passed the ADR 0008 benchmark gate. ADR 0009
makes it the only machine the release wheel contains, and the only entry in the
default policy.
"""

from __future__ import annotations

from ..abi import capi
from ..abi.libs import library_path


class RustBackend:
    """
    The shipped machine, loaded through the C ABI.

    The only machine the release wheel contains and the only default.
    """

    name = "rust"
    level = 3  # implements every opcode family in the ISA
    reason = "librustdetree was not found"

    def available(self) -> bool:
        """
        Whether the shipped library can be found for this platform.

        Returns:
            `True` when `librustdetree` is installed.
        """
        return library_path("rust") is not None

    def build(self, module: bytes) -> capi.CapiMachine:
        """
        Build a machine backed by the Rust library.

        Args:
            module: the module byte format of `docs/spec/module.md`.

        Returns:
            A machine that calls the shipped library.
        """
        return capi.build(module, "rust")


__all__ = ["RustBackend"]
