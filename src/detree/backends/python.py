"""
The Python reference backend (development only).

The reference machine is always available; its conformance level rises with the
implementation. It never depends on a compiled library. The shipped machine is
Rust; this backend exists as the development oracle and is never the default.
"""

from __future__ import annotations

from ..vm.python import PythonMachine


class PythonBackend:
    """
    The pure-Python reference machine; always available.

    The development oracle: no compiled library is involved.
    """

    name = "python"
    level = 3  # implements every opcode family in the ISA
    reason = None

    def available(self) -> bool:
        """
        The reference machine needs no compiled library.

        Returns:
            Always `True`.
        """
        return True

    def build(self, module: bytes) -> PythonMachine:
        """
        Build a reference machine from module bytes.

        Args:
            module: the module byte format of `docs/spec/module.md`.

        Returns:
            A pure-Python machine for the module.
        """
        return PythonMachine(module)


__all__ = ["PythonBackend"]
