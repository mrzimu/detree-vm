"""
The public machine: build one instance and run baskets on it.

A machine owns the decoded module plus every register, cursor, and buffer it
needs, so an instance is not thread-safe and belongs to whoever built it.
Building several machines and running them in parallel is the caller's
decision; nothing here shares state between instances.

NumPy and the compiled libraries stay out of the package import path: both are
imported when a machine is built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .spec.module import Module

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

    from .backends.base import Backend
    from .ir import nodes


def _module_bytes(module: bytes | bytearray | memoryview | Module) -> bytes:
    """
    Normalize a module argument to its bytes.

    Args:
        module: module bytes, or a decoded `detree.spec.module.Module`.

    Returns:
        The module's byte string.

    Raises:
        TypeError: *module* is neither bytes nor a `Module`.
    """
    if isinstance(module, Module):
        return module.to_bytes()
    if isinstance(module, (bytes, bytearray, memoryview)):
        return bytes(module)
    raise TypeError(
        f"a module is bytes or detree.spec.module.Module, not {type(module).__name__}"
    )


def _resolve(backend: str | None) -> Backend:
    """
    Resolve the backend that should build a machine.

    Args:
        backend: an explicit backend name, or `None` for the backend selected
            by `use` or the environment, then the default policy.

    Returns:
        The backend object to build from.
    """
    from .backends import registry

    if backend is None:
        selected = registry.selected()
        if selected is not None:
            return registry.get(selected)
        return registry.default()
    return registry.get(backend)


class Machine:
    """
    One machine for one module and one backend, built eagerly.

    The instance decodes and validates the module once, then serves as many
    baskets as the caller runs on it. It is not thread-safe: build one machine
    per thread that decodes, and close it when done.
    """

    def __init__(
        self,
        module: bytes | bytearray | memoryview | Module,
        backend: str | None = None,
    ) -> None:
        self._bytes = _module_bytes(module)
        self._machine = _resolve(backend).build(self._bytes)

    @property
    def backend(self) -> str:
        """
        The backend name this machine runs on.

        Returns:
            The canonical backend name, such as `"rust"`.
        """
        return self._machine.backend

    @property
    def isa(self) -> tuple[int, int]:
        """
        The `(major, minor)` ISA version this machine implements.

        Returns:
            The ISA version as a pair of integers.
        """
        return self._machine.isa

    def run(
        self,
        data: bytes | bytearray | memoryview | np.ndarray,
        offsets: Sequence[int],
        cursor_offset: int = 0,
    ) -> Mapping[str, np.ndarray]:
        """
        Read every event of one basket.

        The machine resets itself first, so the outputs describe this basket
        only and a previous run is never extended.

        Args:
            data: the decompressed basket body.
            offsets: cumulative entry offsets, at least two.
            cursor_offset: the key length used for object references.

        Returns:
            One NumPy array per declared buffer, keyed by buffer name.
        """
        return self._machine.run(data, offsets, cursor_offset)

    def state_values(self) -> Mapping[str, int | str | None]:
        """
        The state slots after the last run.

        Returns:
            One entry per declared state, name slots resolved to text.
        """
        return self._machine.state_values()

    def reset(self) -> None:
        """
        Return the machine to its just-built state.

        Fresh storage is allocated, so arrays handed out by an earlier run stay
        readable.
        """
        self._machine.reset()

    def close(self) -> None:
        """
        Release machine-owned storage; calling it twice is a no-op.

        The underlying machine ignores the second call.
        """
        self._machine.close()

    def __enter__(self) -> Machine:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def compile(  # noqa: A001 - the public name is intentional
    program: bytes | bytearray | memoryview | nodes.Program,
) -> bytes:
    """
    Lower an IR program to module bytes.

    Module bytes pass through unchanged, which keeps a mixed-version pipeline
    working. Anything else must be a `detree.ir.Program`.

    Args:
        program: an IR program, or module bytes to pass through.

    Returns:
        The module byte string.

    Raises:
        TypeError: *program* is neither module bytes nor an IR program.
    """
    if isinstance(program, (bytes, bytearray, memoryview)):
        return bytes(program)
    from .ir import nodes
    from .lower import lower
    from .opt import optimize

    if not isinstance(program, nodes.Program):
        raise TypeError(
            "detree.compile expects module bytes or a detree.ir.Program, not "
            f"{type(program).__name__}"
        )
    optimized = optimize(program)
    return lower(optimized)


__all__ = ["Machine", "compile"]
