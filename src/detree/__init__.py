"""
Detree: a domain-specific bytecode VM for TTree binary streams.

The package is layered so the machine can be reimplemented and measured:

- `detree.spec` — the module format, opcode tables, validator, errors;
- `detree.ir` — the typed tree IR a compiler builds;
- `detree.opt` — optimizer passes over the IR;
- `detree.lower` — IR to module bytes;
- `detree.vm` — the Python reference machine;
- `detree.abi` — the `ctypes` binding to the compiled machines;
- `detree.backends` — the backend registry and adapters.

The public surface is two calls: `compile` turns an IR program into module
bytes, and `Machine` builds one machine from those bytes. The instance belongs
to the caller, who decides how many to build and which thread runs each.
Importing the package is cheap: NumPy and the compiled libraries are imported
when a machine is built, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

try:  # written by setuptools_scm at build time
    from ._version import version as __version__
except ImportError:  # a wheel or source checkout without the generated file
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("detree-vm")
    except PackageNotFoundError:
        __version__ = "0.1.0.dev0"

from .machine import Machine, compile

# The public API, in documentation order.
PUBLIC_API = (
    "Machine",
    "compile",
    "include_dir",
)


def include_dir() -> Path:
    """
    Return the directory that contains `detree.h`.

    The release wheel ships the header, so a C or Rust embedder can build
    against the library it installed without a source checkout.
    """
    from .abi.libs import include_dir as _include_dir

    return _include_dir()


__all__ = [
    "PUBLIC_API",
    "Machine",
    "__version__",
    "compile",
    "include_dir",
]
