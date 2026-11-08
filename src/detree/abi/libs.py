"""
Locate a compiled Detree library.

Search order, first existing file wins:

1. an explicit path passed by the caller;
2. `DETREE_CPP_LIBRARY` or `DETREE_RUST_LIBRARY`;
3. the package's `_libs/` directory (an installed wheel or an editable build).

A missing library is not an error here: the registry reports the backend as
unavailable. The shipped backend does not fall back to another machine, so a
missing Rust library surfaces when the caller asks for the default backend.
"""

from __future__ import annotations

import functools
import os
import sysconfig
from pathlib import Path

# File names by backend. CMake strips the `lib` prefix; Cargo keeps it.
_LIBRARY_NAMES = {
    "cpp": (
        "cppdetree.so",
        "libcppdetree.so",
        "cppdetree.dylib",
        "libcppdetree.dylib",
        "cppdetree.dll",
    ),
    "rust": (
        "rustdetree.so",
        "librustdetree.so",
        "rustdetree.dylib",
        "librustdetree.dylib",
        "rustdetree.dll",
    ),
}

# Environment overrides, one per backend.
_ENVIRONMENT = {
    "cpp": "DETREE_CPP_LIBRARY",
    "rust": "DETREE_RUST_LIBRARY",
}

# `src/detree/_libs` in an editable checkout, `detree/_libs` in a wheel


def _installed_libs() -> list[Path]:
    """
    The site-packages `detree/_libs` directories, when they exist.

    An editable install imports the package from the work tree while the wheel
    contents (including the compiled library) are installed in site-packages.
    Searching both is what makes `pip install -e .` work.
    """
    paths = []
    try:
        locations = sysconfig.get_paths()
    except KeyError:  # pragma: no cover - a stripped-down sysconfig
        return paths
    for key in ("platlib", "purelib"):
        location = locations.get(key)
        if location:
            paths.append(Path(location) / "detree" / "_libs")
    return paths


# `detree/_libs` next to the package (a wheel) or in the source tree.
_PACKAGE_LIBS = Path(__file__).resolve().parent.parent / "_libs"


def library_names(backend: str) -> tuple[str, ...]:
    """
    Return the candidate file names for *backend*.

    Args:
        backend: a compiled backend name, such as `"rust"`.

    Returns:
        The file names tried in each directory, in order.

    Raises:
        ValueError: *backend* has no compiled library.
    """
    try:
        return _LIBRARY_NAMES[backend]
    except KeyError:
        raise ValueError(f"unknown backend {backend!r}") from None


def compiled_backends() -> tuple[str, ...]:
    """
    The backend names that are loaded from a compiled library.

    Returns:
        The backend names a shared library can provide.
    """
    return tuple(_LIBRARY_NAMES)


def _first_in(directory: Path, backend: str) -> Path | None:
    for name in library_names(backend):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


@functools.cache
def library_path(backend: str, explicit: str | os.PathLike | None = None) -> Path | None:
    """
    Return the library for *backend*, or `None` when it is not built.

    The lookup is cached per process: it stats directories through
    `sysconfig`, which costs more than decoding a small basket, and a read
    asks for the same library once per basket. A library installed later in the
    same process is not picked up; `cache_clear` exists for tests.
    """
    if explicit is not None:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None

    override = os.environ.get(_ENVIRONMENT.get(backend, ""), "")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    candidate = _first_in(_PACKAGE_LIBS, backend)
    if candidate is not None:
        return candidate

    for directory in _installed_libs():
        candidate = _first_in(directory, backend)
        if candidate is not None:
            return candidate

    return None


def cache_clear() -> None:
    """
    Forget the cached lookups; tests install a library mid-run, users do not.

    A library installed after the first lookup is invisible to `library_path`
    until this runs.
    """
    library_path.cache_clear()


def include_dir() -> Path:
    """
    Return the directory that contains `detree.h`.

    A wheel installs the header next to the compiled library, so an embedder
    does not need the repository: `detree/include/detree.h` is part of the
    release artifact even when only the Rust machine is built. A source
    checkout falls back to `kernel/include`.
    """
    candidates = [Path(__file__).resolve().parent.parent / "include"]
    for directory in _installed_libs():
        candidates.append(directory.parent / "include")
    candidates.append(Path(__file__).resolve().parents[3] / "kernel" / "include")

    for candidate in candidates:
        if (candidate / "detree.h").is_file():
            return candidate
    raise FileNotFoundError("detree.h is missing from the installation; reinstall detree-vm")


__all__ = [
    "cache_clear",
    "compiled_backends",
    "include_dir",
    "library_names",
    "library_path",
]
