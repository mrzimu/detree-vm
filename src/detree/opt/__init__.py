"""
Optimizer passes over the IR.

The pass list and their preconditions are in `docs/spec/ir.md` §Optimizer passes.
A pass is a pure function from a program to a rewritten program; the manager
decides the order and whether to iterate to a fixed point.
"""

from __future__ import annotations

from .manager import LEVEL1, MAX_ROUNDS, PASS_ORDER, PASSES, optimize

__all__ = ["LEVEL1", "MAX_ROUNDS", "PASSES", "PASS_ORDER", "optimize"]
