"""
Pass manager.

Runs the passes in a stable order, to a fixed point where a pass declares itself
monotone. `DETREE_OPT=0` disables optimization; `DETREE_DUMP=ir` prints the
program before and after each pass.
"""

from __future__ import annotations

import copy
import os
import sys

from ..ir import nodes
from ..ir.dump import dump
from .const import const_branch, const_fold
from .dead import dead_code, dead_function, unreachable
from .fuse import (
    bulk_fusion,
    bulk_until_fusion,
    dead_read_to_skip,
    jump_thread,
    layout_infer,
    len_append_fusion,
    literal_append,
    offset_fusion,
    read_append_fusion,
    reserve,
    skip_fusion,
    strided_fusion,
    string_fusion,
    tobject_fusion,
)
from .inline import inline_small
from .loops import range_unroll
from .peephole import condition_locals

# The default pass order. A dump is reproducible because this order is fixed.
PASS_ORDER = (
    "const_fold",
    "const_branch",
    "tobject_fusion",
    "dead_code",
    "condition_locals",
    "literal_append",
    "read_append_fusion",
    "bulk_fusion",
    "bulk_until_fusion",
    "strided_fusion",
    "len_append_fusion",
    "offset_fusion",
    "string_fusion",
    "skip_fusion",
    "dead_read_to_skip",
    "range_unroll",
    "dead_function",
    "inline_small",
    "jump_thread",
    "unreachable",
    "layout_infer",
    "reserve",
)

PASSES = {
    "const_fold": const_fold,
    "const_branch": const_branch,
    "tobject_fusion": tobject_fusion,
    "dead_code": dead_code,
    "condition_locals": condition_locals,
    "literal_append": literal_append,
    "read_append_fusion": read_append_fusion,
    "bulk_fusion": bulk_fusion,
    "bulk_until_fusion": bulk_until_fusion,
    "strided_fusion": strided_fusion,
    "len_append_fusion": len_append_fusion,
    "offset_fusion": offset_fusion,
    "string_fusion": string_fusion,
    "skip_fusion": skip_fusion,
    "dead_read_to_skip": dead_read_to_skip,
    "range_unroll": range_unroll,
    "dead_function": dead_function,
    "inline_small": inline_small,
    "jump_thread": jump_thread,
    "unreachable": unreachable,
    "layout_infer": layout_infer,
    "reserve": reserve,
}

# The conservative set used by `level=1`.
LEVEL1 = frozenset(
    {
        "const_fold",
        "const_branch",
        "dead_code",
        "literal_append",
        "read_append_fusion",
        "len_append_fusion",
        "offset_fusion",
        "skip_fusion",
        "dead_read_to_skip",
        "unreachable",
    }
)

MAX_ROUNDS = 8


def optimize(program: nodes.Program, level: int = 2) -> nodes.Program:
    """
    Return an optimized copy of *program*.

    The input is deep-copied first, so a pass never mutates the caller's tree.
    Passes run in `PASS_ORDER` until a full round reports no change, or until
    `MAX_ROUNDS` is reached.

    Args:
        program: the IR program to rewrite.
        level: `0` disables optimization, `1` runs `LEVEL1`, `2` runs every
            pass. `DETREE_OPT=0` also disables it.

    Returns:
        The optimized program, or *program* itself when optimization is off.
    """
    if level <= 0 or os.environ.get("DETREE_OPT") == "0":
        return program
    work = copy.deepcopy(program)
    selected = [
        name for name in PASS_ORDER if (level >= 2 or name in LEVEL1) and name in PASSES
    ]
    verbose = os.environ.get("DETREE_DUMP") == "ir"
    for _ in range(MAX_ROUNDS):
        changed = False
        for name in selected:
            if verbose:
                print(f"--- ir before {name} ---", file=sys.stderr)
                print(dump(work), file=sys.stderr, end="")
            if PASSES[name](work):
                changed = True
            if verbose:
                print(f"--- ir after {name} ---", file=sys.stderr)
                print(dump(work), file=sys.stderr, end="")
        if not changed:
            break
    return work


__all__ = ["LEVEL1", "MAX_ROUNDS", "PASSES", "PASS_ORDER", "optimize"]
