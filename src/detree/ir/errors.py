"""
Compiler-side errors that are not machine errors.
"""

from __future__ import annotations

from ..spec.errors import DetreeError


class IRValidationError(DetreeError):
    """
    The IR program violates `docs/spec/ir.md` §Verification rules.

    Raised before lowering, so a malformed program never reaches a machine.
    """


__all__ = ["IRValidationError"]
