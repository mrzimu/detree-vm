"""
The backend-neutral protocols.

A machine is the object the facade drives; a backend is the factory that builds
one from module bytes. Neither protocol mentions a concrete implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # NumPy stays out of the core import path
    import numpy as np


@runtime_checkable
class Machine(Protocol):
    """
    One module, reused basket after basket.

    `run` resets the machine first. Outputs remain valid until the caller drops
    them, even if `run` is called again.
    """

    backend: str
    isa: tuple[int, int]

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
        ...

    def state_values(self) -> Mapping[str, int | str | None]:
        """
        The state slots after the last run.

        Returns:
            One entry per declared state, name slots resolved to text.
        """
        ...

    def reset(self) -> None:
        """
        Return the machine to its just-built state.

        Fresh storage is allocated, so earlier outputs stay readable.
        """
        ...

    def close(self) -> None:
        """
        Release machine-owned storage; calling it twice is a no-op.

        Every method raises after a close.
        """
        ...


@runtime_checkable
class Backend(Protocol):
    """
    A machine implementation and its loader.

    A backend is stateless: it reports availability and builds machines, and
    keeps no per-module state of its own.
    """

    name: str
    level: int  # conformance level, 0..3
    reason: str | None  # why it is unavailable, when it is

    def available(self) -> bool:
        """
        Whether this backend can build a machine here.

        Returns:
            `True` when `build` can be called on this platform.
        """
        ...

    def build(self, module: bytes) -> Machine:
        """
        Build a machine from module bytes.

        Args:
            module: the module byte format of `docs/spec/module.md`.

        Returns:
            A fresh machine; the caller owns it.
        """
        ...


__all__ = ["Backend", "Machine"]
