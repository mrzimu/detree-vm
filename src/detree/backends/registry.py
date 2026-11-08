"""
Backend registry and selection.

Precedence: an explicit argument, then `use`, then `DETREE_BACKEND`,
then the default policy. An explicit request that is unavailable raises; it
never falls back.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING

from ..spec.errors import BackendUnavailable
from . import policy
from .cpp import CppBackend
from .python import PythonBackend
from .rust import RustBackend

if TYPE_CHECKING:
    from .base import Backend

# Built-in backends by name. Third-party backends register through the
# `detree.backends` entry-point group and are added lazily.
_BUILTINS = {
    "python": PythonBackend(),
    "cpp": CppBackend(),
    "rust": RustBackend(),
}

# The backend selected by `use` in the current context.
_SELECTED: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "detree_backend", default=None
)

# Environment variable that overrides the default policy.
ENVIRONMENT = "DETREE_BACKEND"

# Accepted names, including aliases.
_ALIASES = {
    "python": "python",
    "py": "python",
    "cpp": "cpp",
    "c++": "cpp",
    "rust": "rust",
    "rs": "rust",
}


def names() -> tuple[str, ...]:
    """
    The known backend names.

    Returns:
        The built-in names, in registration order.
    """
    return tuple(_BUILTINS)


def available() -> list[tuple[str, int, str | None]]:
    """
    One row per known backend.

    Returns:
        `(name, conformance level, reason)` rows; *reason* is `None` when the
        backend can build a machine here.
    """
    rows = []
    for name, backend in _BUILTINS.items():
        reason = None if backend.available() else backend.reason or "not built"
        rows.append((name, backend.level, reason))
    return rows


def get(name: str) -> Backend:
    """
    Return a backend by name.

    Args:
        name: a backend name or alias.

    Returns:
        The registered backend.

    Raises:
        BackendUnavailable: *name* is unknown, or the backend is not built.
    """
    try:
        canonical = _ALIASES[name.strip().lower()]
    except (AttributeError, KeyError):
        raise BackendUnavailable(
            f"unknown backend {name!r}; known: {sorted(_ALIASES)}"
        ) from None

    backend = _BUILTINS[canonical]
    if not backend.available():
        raise BackendUnavailable(
            f"backend {canonical!r} is unavailable: {backend.reason or 'not built'}"
        )
    return backend


def default() -> Backend:
    """
    The shipped backend, or a precise error when it is not usable.

    `policy.PREFERENCE` holds the shipped machine only, so this function
    either returns it or explains why it cannot run. A missing library is never
    papered over with the Python reference: a broken installation has to be
    visible.

    Returns:
        The backend the release wheel guarantees.

    Raises:
        BackendUnavailable: the shipped library is missing or not at level 3.
    """
    for name in policy.PREFERENCE:
        backend = _BUILTINS.get(name)
        if backend is None:
            raise BackendUnavailable(
                f"shipped backend {name!r} is not registered; this build is broken"
            )
        if backend.available() and backend.level >= 3:
            return backend
        if not backend.available():
            reason = backend.reason or "not built"
        else:
            reason = f"it is at conformance level {backend.level}, not L3"
        raise BackendUnavailable(
            f"shipped backend {name!r} is unavailable: {reason}. "
            "Reinstall detree-vm, or select another backend explicitly for "
            'development (DETREE_BACKEND=python or backend="python").'
        )
    raise BackendUnavailable("no backend is configured as the default")


def shipped() -> Backend:
    """
    The backend the release wheel guarantees (see `policy.SHIPPED`).

    Returns:
        The shipped backend object, whether or not its library is present.
    """
    return _BUILTINS[policy.SHIPPED]


@contextlib.contextmanager
def use(name: str) -> Iterator[None]:
    """
    Select *name* for the duration of the context.

    Args:
        name: a backend name or alias.

    Yields:
        Nothing; the selection applies inside the `with` block.
    """
    token = _SELECTED.set(_ALIASES[name.strip().lower()])
    try:
        yield
    finally:
        _SELECTED.reset(token)


def selected() -> str | None:
    """
    The name selected by `use` or the environment.

    Returns:
        The selected backend name, or `None` when nothing was selected.
    """
    return _SELECTED.get() or os.environ.get(ENVIRONMENT) or None


__all__ = [
    "ENVIRONMENT",
    "available",
    "default",
    "get",
    "names",
    "policy",
    "selected",
    "shipped",
    "use",
]
