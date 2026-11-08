"""
Register allocation.

`_linearize` assigns a virtual register with a stack discipline: locals
keep one register, temporaries are reused from a free list, and every register
is bounded by the expression depth. This module finalizes the allocation and
enforces the machine's 255-register window.
"""

from __future__ import annotations

from ..ir.errors import IRValidationError
from .linearize import LinearFunction

MAX_REGISTERS = 255


def finalize_registers(linear: LinearFunction) -> int:
    """
    Return the function's register count, or fail if it is too large.

    Args:
        linear: the linearized function after allocation.

    Returns:
        The number of registers the machine must reserve.

    Raises:
        IRValidationError: the count is zero or exceeds `MAX_REGISTERS`.
    """
    if linear.reg_count < 1:
        raise IRValidationError(f"{linear.name}: a function needs one register")
    if linear.reg_count > MAX_REGISTERS:
        raise IRValidationError(
            f"{linear.name}: {linear.reg_count} registers exceed {MAX_REGISTERS}"
        )
    return linear.reg_count


__all__ = ["MAX_REGISTERS", "finalize_registers"]
