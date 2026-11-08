"""
The Python reference machine.

The reference implementation is the oracle every compiled backend is compared
against. It uses the standard library only and decodes the module's bytes on
construction, exactly as a foreign implementation would.
"""

from __future__ import annotations

from .python import PythonMachine

__all__ = ["PythonMachine"]
