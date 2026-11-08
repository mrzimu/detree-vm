"""
The compiler-side typed tree IR.

A compiler builds a `nodes.Program`; the optimizer rewrites it; the
lowering pass emits module bytes. The IR is not part of the machine ABI and
machines never see it. The contract is defined in `docs/spec/ir.md`.
"""

from __future__ import annotations

from . import nodes
from .builder import FunctionBuilder, ProgramBuilder
from .dump import dump
from .errors import IRValidationError
from .nodes import *  # noqa: F403
from .nodes import IR_VERSION
from .verify import verify_program

__all__ = [
    "FunctionBuilder",
    "IRValidationError",
    "IR_VERSION",
    "ProgramBuilder",
    "dump",
    "nodes",
    "verify_program",
] + list(nodes.__all__)
