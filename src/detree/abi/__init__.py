"""
The Python side of the C ABI.

One `ctypes` binding serves both compiled backends: it is parameterized by the
loaded library, not by the implementation. The C contract is defined in
`docs/spec/c-api.md` and in `kernel/include/detree.h`.
"""

from __future__ import annotations

from .libs import include_dir, library_path

__all__ = ["include_dir", "library_path"]
